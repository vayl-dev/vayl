"""
Identity and authorization — principals, API keys, RBAC capabilities, tenant scoping, SSO.
"""

import os
import sqlite3
import tempfile
import time

import jwt
import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server as s  # noqa: E402  # noqa: E402
from vayl.auth.auth import (  # noqa: E402
    KEY_PREFIX,
    Auth,
    Capability,
    Principal,
    Role,
    _parse_scopes,  # noqa: E402
    local_admin,
)
from vayl.auth.sso import OidcConfig, OidcVerifier, looks_like_jwt, map_roles  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# from test_auth
# ══════════════════════════════════════════════════════════════════

def _db():
    return sqlite3.connect(":memory:")


def test_role_capabilities_are_as_intended():
    C = Capability
    assert Principal("a", "a", [Role.ADMIN]).can(C.ADMIN)
    assert Principal("m", "m", [Role.MEMBER]).can(C.WRITE) and Principal("m", "m", [Role.MEMBER]).can(C.DELETE)
    assert not Principal("m", "m", [Role.MEMBER]).can(C.ADMIN)         # member can't wipe-all/config
    assert Principal("g", "g", [Role.AGENT]).can(C.WRITE)
    assert not Principal("g", "g", [Role.AGENT]).can(C.DELETE)         # agent can't erase
    assert Principal("v", "v", [Role.VIEWER]).can(C.READ)
    assert not Principal("v", "v", [Role.VIEWER]).can(C.WRITE)         # viewer is read-only
    assert Principal("au", "au", [Role.AUDITOR]).can(C.VERIFY)
    assert not Principal("au", "au", [Role.AUDITOR]).can(C.READ)       # auditor: accountability only


def test_multiple_roles_union_capabilities():
    p = Principal("x", "x", [Role.VIEWER, Role.AUDITOR])
    assert p.can(Capability.READ) and p.can(Capability.VERIFY)
    assert not p.can(Capability.WRITE)


def test_local_admin_has_full_control():
    assert local_admin().can(Capability.ADMIN)


def test_create_returns_a_usable_key_and_verifies_back():
    a = Auth(_db())
    p, key = a.create("ci-agent", roles=Role.AGENT, kind="agent")
    assert key.startswith(KEY_PREFIX)
    got = a.verify(key)
    assert got is not None and got.id == p.id and got.name == "ci-agent"
    assert got.roles == [Role.AGENT]


def test_key_is_stored_hashed_never_plaintext():
    a = Auth(_db())
    _p, key = a.create("x")
    row = a.db.execute("SELECT api_key_hash FROM principals").fetchone()[0]
    assert row != key and key not in row and len(row) == 64   # sha-256 hex, not the plaintext


def test_unknown_or_malformed_key_verifies_to_none():
    a = Auth(_db())
    a.create("x")
    assert a.verify("vayl_sk_not-a-real-key") is None
    assert a.verify("garbage") is None            # missing prefix
    assert a.verify("") is None and a.verify(None) is None


def test_revoked_key_stops_working_immediately():
    a = Auth(_db())
    p, key = a.create("temp", roles=Role.MEMBER)
    assert a.verify(key) is not None
    assert a.revoke(p.id) is True
    assert a.verify(key) is None                  # disabled → no principal
    assert a.revoke(p.id) is False                # already revoked


def test_list_and_count_active():
    a = Auth(_db())
    p1, _ = a.create("a", roles=Role.ADMIN)
    a.create("b", roles=[Role.VIEWER, Role.AUDITOR])
    assert a.count_active() == 2
    a.revoke(p1.id)
    assert a.count_active() == 1
    names = {r["name"]: r for r in a.list()}
    assert names["a"]["disabled"] is True and names["b"]["roles"] == ["viewer", "auditor"]


def test_roles_default_to_member_when_unspecified_or_invalid():
    a = Auth(_db())
    _p, key = a.create("x", roles="not-a-role")   # invalid → dropped → default member
    assert a.verify(key).roles == [Role.MEMBER]


# ══════════════════════════════════════════════════════════════════
# from test_rbac
# ══════════════════════════════════════════════════════════════════

DENIED = "Access denied"


@pytest.fixture(autouse=True)
def _reset_principal():
    yield
    s.set_principal(None)   # never leak a restricted principal into another test


def as_role(*roles):
    s.set_principal(Principal("p_test", "tester", list(roles)))


def test_viewer_can_read_but_not_write():
    as_role(Role.VIEWER)
    assert DENIED not in s.get_reconcile_policy()          # READ → allowed (local, no LLM)
    assert DENIED in s.remember("we use Redux")            # WRITE → denied (short-circuits, no LLM)
    assert DENIED in s.delete("anything")                  # DELETE → denied
    assert DENIED in s.delete_all()                        # ADMIN → denied


def test_agent_can_write_but_not_erase_or_admin():
    as_role(Role.AGENT)
    assert DENIED in s.delete("x")                         # DELETE → denied
    assert DENIED in s.delete_all()                        # ADMIN → denied
    assert DENIED in s.create_principal("bob")             # ADMIN → denied
    assert DENIED not in s.verify_audit()                  # VERIFY → allowed


def test_auditor_gets_accountability_only():
    as_role(Role.AUDITOR)
    assert DENIED not in s.verify_audit()                  # VERIFY → allowed
    assert DENIED not in s.export_public_key()             # VERIFY → allowed
    assert DENIED in s.get_reconcile_policy()              # READ → denied (auditor is verify-only)
    assert DENIED in s.remember("x")                       # WRITE → denied


def test_member_can_erase_but_not_admin():
    as_role(Role.MEMBER)
    assert DENIED not in s.delete("no-such-subject")       # DELETE → allowed (local; 'No records')
    assert DENIED in s.delete_all()                        # ADMIN → denied
    assert DENIED in s.set_reconcile_policy("REVIEW")      # ADMIN → denied


def test_admin_can_manage_principals():
    as_role(Role.ADMIN)
    out = s.create_principal("ci-bot", role="agent")       # ADMIN → allowed
    assert DENIED not in out and "vayl_sk_" in out         # a real key was issued
    assert DENIED not in s.list_principals()


def test_unauthenticated_is_denied_when_auth_is_required(monkeypatch):
    # the remote server (M2) sets VAUTH_REQUIRED; with no credential, everything is denied
    monkeypatch.setattr(s, "_AUTH_REQUIRED", True)
    s.set_principal(None)
    assert "authentication required" in s.get_reconcile_policy()
    assert "authentication required" in s.verify_audit()


class _CapZero:
    edition = "enterprise"; valid = True; seat_cap = 0
    def summary(self):
        return "test edition"


def test_seat_cap_blocks_create_principal(monkeypatch):
    as_role(Role.ADMIN)
    monkeypatch.setattr(s, "_license", _CapZero())          # 0 seats → any create is over cap
    out = s.create_principal("overflow")
    assert "Seat limit reached" in out


def test_license_status_reports_community_by_default():
    as_role(Role.ADMIN)
    out = s.license_status()
    assert "Community" in out and "principals in use" in out


# ══════════════════════════════════════════════════════════════════
# from test_space_scoping
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def auth_db():
    return Auth(sqlite3.connect(":memory:"))


def test_unscoped_principal_reaches_every_space():
    """The default. A single-tenant deployment must keep working untouched."""
    p = Principal(id="p1", name="n", roles=[Role.MEMBER])
    assert p.may_access("anyone") and p.may_access("") and p.may_access(None)


def test_scoped_principal_is_confined_to_its_spaces():
    p = Principal(id="p1", name="n", roles=[Role.MEMBER], scopes=["cust_a", "cust_b"])
    assert p.may_access("cust_a") and p.may_access("cust_b")
    assert not p.may_access("cust_c")          # the whole point
    assert not p.may_access("")                # no empty-string bypass
    assert not p.may_access(None)


def test_admin_is_unrestricted_even_when_scoped():
    """Org control implies reaching every space — including to erase it (GDPR)."""
    p = Principal(id="p1", name="n", roles=[Role.ADMIN], scopes=["cust_a"])
    assert p.may_access("cust_zzz")


def test_local_admin_is_unrestricted():
    assert local_admin().may_access("whatever")


@pytest.mark.parametrize("spec,expected", [
    (None, []), ("", []), ("*", []),                            # unrestricted forms ("*" alone only)
    (["*", "a"], ["a"]), ("cust_1,*", ["cust_1"]),             # a STRAY "*" is dropped, NOT widening
    ("a,b", ["a", "b"]), (" a , b ", ["a", "b"]),               # csv, trimmed
    (["a", "", "b"], ["a", "b"]),                               # blanks dropped
])
def test_scope_specs_normalize(spec, expected):
    assert _parse_scopes(spec) == expected


def test_scopes_survive_a_key_verification_round_trip(auth_db):
    p, key = auth_db.create("agent-a", roles=Role.AGENT, scopes="cust_a,cust_b")
    assert p.scopes == ["cust_a", "cust_b"]

    back = auth_db.verify(key)                 # the path a real request takes
    assert back.scopes == ["cust_a", "cust_b"]
    assert back.may_access("cust_a") and not back.may_access("cust_c")


def test_unscoped_principal_round_trips_as_unrestricted(auth_db):
    _, key = auth_db.create("agent-b", roles=Role.AGENT)
    assert auth_db.verify(key).scopes == []
    assert auth_db.verify(key).may_access("anything")


def test_listing_reports_scopes(auth_db):
    auth_db.create("scoped", roles=Role.AGENT, scopes="cust_a")
    auth_db.create("open", roles=Role.AGENT)
    by_name = {r["name"]: r for r in auth_db.list()}
    assert by_name["scoped"]["scopes"] == ["cust_a"]
    assert by_name["open"]["scopes"] == []


def test_upgrade_leaves_existing_principals_unrestricted():
    """A pre-scopes database must not lock anyone out when the column is added."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, name TEXT, kind TEXT, "
                 "api_key_hash TEXT UNIQUE, roles TEXT, disabled INTEGER DEFAULT 0, created_at REAL)")
    conn.execute("INSERT INTO principals VALUES ('prin_old','old','agent','deadbeef','[\"agent\"]',0,0)")
    conn.commit()

    a = Auth(conn)                                # runs the ALTER; existing row gets NULL scopes
    row = next(r for r in a.list() if r["id"] == "prin_old")
    assert row["scopes"] == []                  # NULL reads as unrestricted, not as locked-out


def test_guard_denies_a_space_outside_scope(monkeypatch):
    from vayl.api import mcp_server as srv

    scoped = Principal(id="p1", name="n", roles=[Role.MEMBER], scopes=["cust_a"])
    monkeypatch.setattr(srv, "_current_principal", lambda: scoped)

    ran = []
    allowed = srv._guard("recall", lambda: ran.append("yes") or "ok",
                         cap=srv.C.READ, space="cust_a")
    assert allowed == "ok" and ran == ["yes"]

    denied = srv._guard("recall", lambda: ran.append("no") or "ok",
                        cap=srv.C.READ, space="cust_b")
    assert "Access denied" in denied
    assert ran == ["yes"]                       # the body never executed


def test_denial_does_not_leak_which_space_was_requested(monkeypatch):
    """A scoped caller must not be able to enumerate spaces by reading error text."""
    from vayl.api import mcp_server as srv
    scoped = Principal(id="p1", name="n", roles=[Role.MEMBER], scopes=["cust_a"])
    monkeypatch.setattr(srv, "_current_principal", lambda: scoped)

    msg = srv._guard("recall", lambda: "ok", cap=srv.C.READ, space="cust_secret")
    assert "cust_secret" not in msg


def test_space_check_applies_after_capability_check(monkeypatch):
    """A viewer writing outside its scope should be refused for the capability, not the scope —
    the more fundamental failure, and the one whose message is safe to be specific about."""
    from vayl.api import mcp_server as srv
    viewer = Principal(id="p1", name="n", roles=[Role.VIEWER], scopes=["cust_a"])
    monkeypatch.setattr(srv, "_current_principal", lambda: viewer)

    msg = srv._guard("remember", lambda: "ok", cap=srv.C.WRITE, space="cust_b")
    assert "capability" in msg


def test_audit_log_is_scoped_and_deployment_wide_needs_admin(monkeypatch):
    """A scoped VERIFY caller must not read another tenant's audit trail (it carries memory content),
    nor the deployment-wide log. Regression for the missing space check on audit_log."""
    from vayl.api import mcp_server as srv
    scoped = Principal(id="p1", name="n", roles=[Role.VIEWER], scopes=["cust_a"])   # has VERIFY
    monkeypatch.setattr(srv, "_current_principal", lambda: scoped)
    assert "Access denied" in srv.audit_log(user_id="cust_b")      # another tenant → scope-denied
    assert "Access denied" in srv.audit_log()                      # deployment-wide → needs admin
    assert "Access denied" not in srv.audit_log(user_id="cust_a")  # its own space is allowed


def test_verify_receipt_is_scoped_to_the_receipts_tenant(monkeypatch):
    """Receipt ids are sequential; a scoped caller must not enumerate other tenants' receipts."""
    from vayl.api import mcp_server as srv
    scoped = Principal(id="p1", name="n", roles=[Role.VIEWER], scopes=["cust_a"])
    monkeypatch.setattr(srv, "_current_principal", lambda: scoped)

    def receipt_in(scope):
        return lambda rid: {"id": rid, "signature": "x", "public_key": "y",
                            "payload": {"kind": "erasure_receipt", "scope": scope,
                                        "subject": "s", "count": 1}}
    monkeypatch.setattr(srv._receipts, "get", receipt_in("cust_b/ / "))
    assert "Access denied" in srv.verify_receipt(1)                # another tenant's receipt
    monkeypatch.setattr(srv._receipts, "get", receipt_in("cust_a/ / "))
    assert "Access denied" not in srv.verify_receipt(1)           # own tenant (INVALID sig ok, not denied)


# ══════════════════════════════════════════════════════════════════
# from test_sso
# ══════════════════════════════════════════════════════════════════

ISS = "https://idp.example/"


AUD = "vayl"


@pytest.fixture(scope="module")
def keys():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


@pytest.fixture
def cfg():
    return OidcConfig(issuer=ISS, audience=AUD, jwks_url="https://idp.example/jwks",
                      role_claim="groups", role_map={"vayl-admins": "admin", "eng": "member"},
                      default_role="viewer")


def _token(priv, **over):
    claims = {"iss": ISS, "aud": AUD, "sub": "u123", "email": "alice@acme.com",
              "exp": int(time.time()) + 3600, "groups": ["vayl-admins"]}
    claims.update(over)
    return jwt.encode(claims, priv, algorithm="RS256")


def test_valid_token_maps_to_principal_with_roles(keys, cfg):
    priv, pub = keys
    p = OidcVerifier(cfg, signing_key=pub).verify(_token(priv))
    assert p is not None
    assert p.id == "sso:u123" and p.name == "alice@acme.com" and p.kind == "human"
    assert p.roles == [Role.ADMIN]


def test_group_maps_to_member_and_unmapped_falls_to_default(keys, cfg):
    priv, pub = keys
    v = OidcVerifier(cfg, signing_key=pub)
    assert v.verify(_token(priv, groups=["eng"])).roles == [Role.MEMBER]
    assert v.verify(_token(priv, groups=["not-a-known-group"])).roles == [Role.VIEWER]   # default
    assert v.verify(_token(priv, groups=[])).roles == [Role.VIEWER]


def test_expired_token_is_rejected(keys, cfg):
    priv, pub = keys
    assert OidcVerifier(cfg, signing_key=pub).verify(_token(priv, exp=int(time.time()) - 10)) is None


def test_wrong_issuer_is_rejected(keys, cfg):
    priv, pub = keys
    assert OidcVerifier(cfg, signing_key=pub).verify(_token(priv, iss="https://evil/")) is None


def test_sso_scopes_from_claim_confine_the_principal(keys):
    """With VAYL_OIDC_SCOPE_CLAIM configured, an SSO user is tenant-confined like a scoped API key."""
    priv, pub = keys
    scoped_cfg = OidcConfig(issuer=ISS, audience=AUD, jwks_url="https://idp.example/jwks",
                            scope_claim="vayl_scopes")
    p = OidcVerifier(scoped_cfg, signing_key=pub).verify(_token(priv, vayl_scopes=["cust_a", "cust_b"]))
    assert p.scopes == ["cust_a", "cust_b"]
    assert p.may_access("cust_a") and not p.may_access("cust_c")


def test_sso_without_scope_claim_stays_unrestricted(keys, cfg):
    """No scope claim configured → single-tenant behavior preserved (unrestricted)."""
    priv, pub = keys
    p = OidcVerifier(cfg, signing_key=pub).verify(_token(priv, vayl_scopes=["cust_a"]))
    assert p.scopes == [] and p.may_access("anything")


def test_wrong_audience_is_rejected(keys, cfg):
    priv, pub = keys
    assert OidcVerifier(cfg, signing_key=pub).verify(_token(priv, aud="someone-else")) is None


def test_bad_signature_is_rejected(keys, cfg):
    priv, _pub = keys
    other_pub = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert OidcVerifier(cfg, signing_key=other_pub).verify(_token(priv)) is None    # signed by another key


def test_token_without_subject_is_rejected(keys, cfg):
    priv, pub = keys
    # jwt without sub or email → no stable identity
    tok = jwt.encode({"iss": ISS, "aud": AUD, "exp": int(time.time()) + 60}, priv, algorithm="RS256")
    assert OidcVerifier(cfg, signing_key=pub).verify(tok) is None


def test_map_roles_unions_multiple_groups(cfg):
    roles = map_roles({"groups": ["vayl-admins", "eng"]}, cfg)
    assert set(roles) == {Role.ADMIN, Role.MEMBER}


def test_looks_like_jwt_distinguishes_from_api_keys():
    assert looks_like_jwt("aaa.bbb.ccc")
    assert not looks_like_jwt("vayl_sk_abc")          # API key, not a JWT
    assert not looks_like_jwt("no-dots")
    assert not looks_like_jwt("vayl_sk_a.b.c")        # our key scheme is never treated as a JWT
