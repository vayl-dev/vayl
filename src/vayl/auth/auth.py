#!/usr/bin/env python3
"""
Authentication + RBAC for a Vayl deployment (M1 — the first thing that makes Vayl chargeable).

Today the memory tools trust an unauthenticated `user_id` parameter — any caller can read or write
anyone's memory. This module adds real identities (principals), API keys, and role-based authorization,
so a *team* can share one deployment safely.

Model (single-tenant-per-deployment): one deployment == one org. Principals
authenticate with an API key (`vayl_sk_…`); each has one or more roles; each role grants a set of
capabilities; every tool declares the capability it needs. Multi-tenancy (many orgs, one deployment)
is deliberately NOT built here — the `tenant_id` seam lives in the store for later.

Keys are high-entropy random tokens; we store only their SHA-256 (like a GitHub PAT) and look up by it.
"""
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

from vayl.storage.db import ensure

KEY_PREFIX = "vayl_sk_"


class Capability(str, Enum):
    READ = "read"        # recall / list / history / export — see memory
    WRITE = "write"      # remember / forget / update / decisions / attest — change memory
    DELETE = "delete"    # single-subject erasure (GDPR) — destructive but a data-subject right
    VERIFY = "verify"    # audit / verify / explain / stats / health — accountability reads
    ADMIN = "admin"      # wipe-all, retention, policy config, manage principals — org control


class Role(str, Enum):
    ADMIN = "admin"      # full control of the deployment
    MEMBER = "member"    # a human team member — read/write + erasure, no org admin
    AGENT = "agent"      # a programmatic agent — read/write, no erasure, no admin
    VIEWER = "viewer"    # read-only + accountability
    AUDITOR = "auditor"  # accountability only (audit/verify/explain)


C = Capability
ROLE_CAPS = {
    Role.ADMIN:   {C.READ, C.WRITE, C.DELETE, C.VERIFY, C.ADMIN},
    Role.MEMBER:  {C.READ, C.WRITE, C.DELETE, C.VERIFY},
    Role.AGENT:   {C.READ, C.WRITE, C.VERIFY},
    Role.VIEWER:  {C.READ, C.VERIFY},
    Role.AUDITOR: {C.VERIFY},
}


@dataclass
class Principal:
    id: str
    name: str
    roles: list = field(default_factory=list)   # list[Role]
    kind: str = "agent"                          # human | agent | service
    scopes: list = field(default_factory=list)  # user_ids this principal may touch; [] = all
    tenant: str = "default"                      # the org this key belongs to — a HARD partition: the
    #                                              store filters every query by it, so tenant A never
    #                                              sees tenant B's memory even under the same user_id

    def can(self, capability):
        """True if any of this principal's roles grants the capability."""
        return any(capability in ROLE_CAPS.get(r, set()) for r in self.roles)

    def may_access(self, user_id):
        """True if this principal is allowed into `user_id`'s memory space.

        Capabilities answer "may this caller read at all"; scopes answer "whose memory".
        Without the second question a valid key for tenant A can read tenant B simply by
        passing B's user_id — the caller supplies that string and nothing checked it.

        An empty scope list means unrestricted, which keeps local/dev and single-tenant
        deployments working unchanged. ADMIN is unrestricted by role: org control implies
        being able to reach any space (and to erase it).
        """
        if not self.scopes or Role.ADMIN in self.roles:
            return True
        return str(user_id or "") in self.scopes

    @property
    def role_names(self):
        return [r.value for r in self.roles]


def local_admin():
    """The implicit trusted identity for LOCAL stdio use (a developer on their own machine). The remote
    HTTP server (M2) never falls back to this — it requires a real key."""
    return Principal(id="local", name="local", roles=[Role.ADMIN], kind="human")


def _hash_key(api_key):
    return hashlib.sha256(api_key.encode()).hexdigest()


def _mint_key():
    return KEY_PREFIX + secrets.token_urlsafe(32)


def _parse_roles(roles):
    """Accept a Role, a string, or a list of either → list[Role] (unknown names dropped)."""
    if roles is None:
        return []
    if isinstance(roles, (Role, str)):
        roles = [roles]
    out = []
    for r in roles:
        try:
            out.append(r if isinstance(r, Role) else Role(str(r)))
        except ValueError:
            continue
    return out


def _parse_scopes(scopes):
    """Normalize a scope spec to list[str]. None/""/["*"] all mean unrestricted ([])."""
    if scopes is None:
        return []
    if isinstance(scopes, str):
        scopes = [p.strip() for p in scopes.split(",")]
    out = [str(x).strip() for x in scopes if str(x).strip()]
    # "*" means unrestricted, but ONLY when it is the ENTIRE spec. A stray "*" mixed with real
    # scopes ("cust_1,*") must not silently widen to every tenant — drop the "*" and keep the rest.
    if out == ["*"]:
        return []
    return [s for s in out if s != "*"]


class Auth:
    def __init__(self, db, crypter=None):
        self.db = ensure(db)
        self.crypter = crypter   # principal names are team-member personal data → encrypt at rest
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, name TEXT, kind TEXT, "
            "api_key_hash TEXT UNIQUE, roles TEXT, disabled INTEGER DEFAULT 0, created_at REAL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_prin_key ON principals(api_key_hash)")
        # Added after the first release: existing rows get NULL, which `_parse_scopes`
        # reads as unrestricted — so an upgrade never locks an existing principal out.
        try:
            self.db.execute("ALTER TABLE principals ADD COLUMN scopes TEXT")
        except Exception:
            pass                      # already present
        # tenant: added later than scopes. Existing rows get NULL → treated as the 'default' tenant, so
        # an upgrade leaves a single-tenant deployment behaving exactly as before.
        try:
            self.db.execute("ALTER TABLE principals ADD COLUMN tenant TEXT")
        except Exception:
            pass
        self.db.commit()

    def _enc_name(self, s):
        return self.crypter.enc(s) if self.crypter and s is not None else s

    def _dec_name(self, s):
        if self.crypter and s:
            try:
                return self.crypter.dec(s)
            except Exception:
                return s   # legacy plaintext row (pre-encryption) — return as stored
        return s

    def create(self, name, roles=Role.MEMBER, kind="agent", scopes=None, tenant="default"):
        """Create a principal and return (Principal, plaintext_api_key). The key is shown ONCE — only
        its hash is stored, so it can't be recovered later (reissue by creating a new principal).

        `tenant` is the org partition the key belongs to: every store query filters by it, so a key
        for tenant A cannot reach tenant B's memory even by passing B's user_id. Leave 'default' for a
        single-tenant deployment; set it per org for a shared multi-tenant one."""
        roles = _parse_roles(roles) or [Role.MEMBER]
        scopes = _parse_scopes(scopes)
        tenant = str(tenant or "default").strip() or "default"
        pid = "prin_" + secrets.token_hex(6)
        key = _mint_key()
        self.db.execute(
            "INSERT INTO principals(id, name, kind, api_key_hash, roles, disabled, created_at, scopes, tenant) "
            "VALUES (?,?,?,?,?,0,?,?,?)",
            (pid, self._enc_name(name), kind, _hash_key(key), json.dumps([r.value for r in roles]),
             time.time(), json.dumps(scopes) if scopes else None, tenant))
        self.db.commit()
        return Principal(id=pid, name=name, roles=roles, kind=kind, scopes=scopes, tenant=tenant), key

    def verify(self, api_key):
        """Resolve an API key to a Principal, or None if unknown/disabled. The trust anchor for a request."""
        if not api_key or not api_key.startswith(KEY_PREFIX):
            return None
        row = self.db.execute(
            "SELECT id, name, kind, roles, disabled, scopes, tenant FROM principals WHERE api_key_hash=?",
            (_hash_key(api_key),)).fetchone()
        if not row or row[4]:            # unknown or disabled
            return None
        return Principal(id=row[0], name=self._dec_name(row[1]),
                         roles=_parse_roles(json.loads(row[3] or "[]")), kind=row[2],
                         scopes=_parse_scopes(json.loads(row[5]) if row[5] else None),
                         tenant=str(row[6] or "default"))

    def revoke(self, principal_id):
        """Disable a principal (its key stops working immediately). Returns True if one was disabled."""
        cur = self.db.execute("UPDATE principals SET disabled=1 WHERE id=? AND disabled=0", (principal_id,))
        self.db.commit()
        return cur.rowcount > 0

    def delete(self, principal_id):
        """HARD-delete a principal row (Art. 17 for team members) — unlike revoke, nothing is
        retained. Returns True if a row was removed."""
        cur = self.db.execute("DELETE FROM principals WHERE id=?", (principal_id,))
        self.db.commit()
        return cur.rowcount > 0

    def list(self):
        rows = self.db.execute(
            "SELECT id, name, kind, roles, disabled, created_at, scopes FROM principals "
            "ORDER BY created_at").fetchall()
        return [{"id": r[0], "name": self._dec_name(r[1]), "kind": r[2], "roles": json.loads(r[3] or "[]"),
                 "disabled": bool(r[4]), "created_at": r[5],
                 "scopes": json.loads(r[6]) if r[6] else []} for r in rows]

    def count_active(self):
        return self.db.execute("SELECT COUNT(*) FROM principals WHERE disabled=0").fetchone()[0]
