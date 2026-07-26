#!/usr/bin/env python3
"""
License gate (M3) — signed, OFFLINE-verifiable edition & seat entitlements.

A license is a compact blob: `vayl_lic_<base64url(claims)>.<ed25519_sig_hex>`, where the vendor signs
the claims with their PRIVATE key and the product verifies with the embedded PUBLIC key — no license
server, works air-gapped. Reuses the same Ed25519 primitive as the audit/receipts (`crypto.Signer`).

Claims: {customer, edition, seats, features:[…], issued, expires}. The product ships with NO vendor
key (Community by default); to sell Enterprise you generate your vendor keypair once
(`python mint_license.py keygen`), embed the public key (VAYL_VENDOR_PUBKEY or DEFAULT_VENDOR_PUBKEY),
keep the private seed secret, and mint per-customer licenses.

SOFT enforcement (deliberate): a missing / malformed / tampered / expired / wrong-key license does NOT
brick a running deployment — it degrades to Community limits and reports why. You never want a
customer's agents to stop mid-incident because a license lapsed; you warn and cap instead.
"""
import base64
import datetime
import json
import os

from vayl.security import crypto

# Embed YOUR vendor Ed25519 public key (hex) here to ship a product that recognizes your licenses,
# or provide it at runtime via VAYL_VENDOR_PUBKEY. Empty → Community only (no license is honored).
DEFAULT_VENDOR_PUBKEY = ""
COMMUNITY_SEAT_CAP = 3          # free tier: up to this many principals per deployment
PREFIX = "vayl_lic_"


def _vendor_pubkey():
    return os.environ.get("VAYL_VENDOR_PUBKEY") or DEFAULT_VENDOR_PUBKEY or None


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canonical(claims):
    return json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()


def _expired(expires):
    """True if the ISO date `expires` (YYYY-MM-DD) is strictly before today."""
    if not expires:
        return False
    try:
        return datetime.date.fromisoformat(str(expires)) < datetime.date.today()
    except ValueError:
        return True       # unparseable expiry → treat as expired (fail safe)


class License:
    def __init__(self, edition="community", claims=None, valid=True, reason="community (no license)"):
        self.edition = edition
        self.claims = claims or {}
        self.valid = valid          # False when a license was present but rejected (→ Community caps)
        self.reason = reason

    @property
    def seat_cap(self):
        if self.edition == "community" or not self.valid:
            return COMMUNITY_SEAT_CAP
        return int(self.claims.get("seats") or COMMUNITY_SEAT_CAP)

    def allows(self, feature):
        """True if a VALID license grants `feature`. Community/rejected licenses grant nothing extra."""
        if self.edition == "community" or not self.valid:
            return False
        return feature in (self.claims.get("features") or [])

    @property
    def customer(self):
        return self.claims.get("customer")

    @property
    def expires(self):
        return self.claims.get("expires")

    def summary(self):
        if self.edition == "community" and self.valid:
            return f"Community edition — up to {self.seat_cap} principals. No license installed."
        if not self.valid:
            return (f"License REJECTED ({self.reason}) — running as Community "
                    f"(up to {self.seat_cap} principals).")
        feats = ", ".join(self.claims.get("features") or []) or "(none)"
        return (f"{self.edition.title()} edition — customer: {self.customer or '?'} · "
                f"seats: {self.seat_cap} · expires: {self.expires or 'never'} · features: {feats}.")


def community():
    return License()


def mint(private_seed_hex, claims):
    """Vendor side (needs the private seed): sign `claims` → a compact license blob string."""
    signer = crypto.Signer(bytes.fromhex(private_seed_hex))
    body = _canonical(claims)
    return PREFIX + _b64e(body) + "." + signer.sign(body)


def load(blob=None):
    """Resolve the active License. `blob` may be the license string, a path to it, or None (→ read
    VAYL_LICENSE). Verifies the signature + expiry against the vendor public key. Any problem →
    Community (soft), with `reason` explaining why."""
    blob = blob if blob is not None else os.environ.get("VAYL_LICENSE")
    if not blob:
        return community()
    if not blob.startswith(PREFIX):                      # allow a path to a license file
        path = os.path.expanduser(blob)
        if os.path.exists(path):
            with open(path) as f:
                blob = f.read().strip()
    if not blob.startswith(PREFIX):
        return License(edition="community", valid=False, reason="malformed license (bad prefix)")
    pub = _vendor_pubkey()
    if not pub:
        return License(edition="community", valid=False, reason="no vendor public key configured")
    try:
        body_b64, sig = blob[len(PREFIX):].split(".", 1)
        body = _b64d(body_b64)
        if not crypto.Signer.verify(pub, sig, body):
            return License(edition="community", valid=False, reason="invalid signature")
        claims = json.loads(body)
    except Exception as e:                               # noqa: BLE001 - any parse failure → soft deny
        return License(edition="community", valid=False, reason=f"malformed license ({e})")
    if _expired(claims.get("expires")):
        return License(edition="community", claims=claims, valid=False,
                       reason=f"expired {claims.get('expires')}")
    return License(edition=str(claims.get("edition") or "enterprise"), claims=claims,
                   valid=True, reason="valid")
