#!/usr/bin/env python3
"""
Optional at-rest encryption for Vayl's SQLite store. ON by default; `VAYL_ENCRYPT=off` disables.

Key resolution:
  • VAYL_KEY passphrase  → Argon2id-derived key, NOT stored on disk (strong: survives disk theft).
                           The KDF and its parameters are pinned in a `<salt>.kdf` marker so the
                           choice is stable; a deployment that predates the marker keeps its
                           original scrypt derivation, so existing VAYL_KEY data stays readable.
  • otherwise            → an auto-generated 32-byte key in `<db>.key` (0600), zero-setup. This
                           protects against copying the DB file, but NOT theft of the whole
                           machine (the key sits beside it) — pair with OS full-disk encryption.

Encryption is authenticated (Fernet = AES-128-CBC + HMAC). Because Fernet ciphertext is randomized,
equality queries on encrypted columns won't work, so `blind()` gives a deterministic HMAC index for
the `subject` column (reveals which rows share a subject, not the subject itself).
"""
import base64
import hashlib
import hmac
import json
import os

from vayl.security import kms

# Passphrase KDF. Fresh deployments use Argon2id — OWASP's preferred password KDF, memory-hard and
# provided by the `cryptography` dep already in use (no new package). Parameters follow OWASP's
# baseline (19 MiB, t=2, p=1); startup-only, so no effect on recall/remember latency. Deployments
# predating the marker keep scrypt so their derived key — and thus their data — is unchanged.
_ARGON2_DEFAULT = {"kdf": "argon2id", "t": 2, "m": 19456, "p": 1}   # m in KiB (19 MiB)
_SCRYPT_LEGACY = {"kdf": "scrypt", "n": 16384, "r": 8, "p": 1}


def _run_kdf(spec, pw, salt, dklen):
    if spec["kdf"] == "scrypt":
        return hashlib.scrypt(pw.encode(), salt=salt, n=spec["n"], r=spec["r"],
                              p=spec["p"], dklen=dklen)
    if spec["kdf"] == "argon2id":
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        return Argon2id(salt=salt, length=dklen, iterations=spec["t"],
                        lanes=spec["p"], memory_cost=spec["m"]).derive(pw.encode())
    raise ValueError(f"unknown VAYL_KEY KDF: {spec['kdf']!r}")


def _derive(pw, salt_path, dklen=32):
    """Derive `dklen` key bytes from the VAYL_KEY passphrase, pinning the KDF in a marker file.

    The marker (`<salt_path>.kdf`) records the exact algorithm + parameters and is authoritative
    once written, so the derived key never silently changes. Selection when no marker exists:
    a salt already on disk means a deployment that predates this → stay on scrypt (existing data
    keeps opening); a fresh deployment uses Argon2id, unless VAYL_KDF=scrypt is set."""
    marker_path = salt_path + ".kdf"
    salt_pre_existed = os.path.exists(salt_path)
    salt = kms._read_or_create(salt_path, 16)
    if os.path.exists(marker_path):
        with open(marker_path) as f:
            spec = json.load(f)
    else:
        if salt_pre_existed or os.environ.get("VAYL_KDF", "argon2id").lower() == "scrypt":
            spec = dict(_SCRYPT_LEGACY)
        else:
            spec = dict(_ARGON2_DEFAULT)
        # The marker is written 0600 but is NOT authenticated (low-severity, tracked): an attacker
        # with write access to the data dir could alter/delete it. This can't decrypt existing data —
        # a changed KDF derives a different, non-matching key — so it's an availability/tamper vector,
        # mitigated by filesystem permissions + OS full-disk encryption, not a confidentiality break.
        fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(spec, f)
    return _run_kdf(spec, pw, salt, dklen)


class Crypter:
    def __init__(self, key32):
        from cryptography.fernet import Fernet
        self._f = Fernet(base64.urlsafe_b64encode(key32))
        self._hk = key32

    def enc(self, s):
        return None if s is None else self._f.encrypt(s.encode()).decode()

    def dec(self, s):
        return None if s is None else self._f.decrypt(s.encode()).decode()

    def blind(self, s):
        """Deterministic keyed hash for equality lookups on an encrypted column. Reveals only which
        rows share a value, never the value. NOTE (low-severity, tracked): it is keyed with the same
        bytes as at-rest encryption rather than a derived sub-key (e.g. HMAC(key, "blind")); moving to
        one is a one-time re-index of existing encrypted rows, so it is deferred to avoid a migration."""
        return hmac.new(self._hk, (s or "").encode(), hashlib.sha256).hexdigest()


class Signer:
    """Ed25519 signing for tamper-evidence — the trust layer's cryptographic backbone.

    Signs audit-chain heads, erasure receipts, and knowledge attestations. The public key is
    exportable, so a THIRD PARTY can verify a signature WITHOUT the secret — the property that makes
    "prove what was known, and when" meaningful outside the box (an HMAC would need the shared secret).
    """
    def __init__(self, seed32):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self._sk = Ed25519PrivateKey.from_private_bytes(seed32)
        self._pk = self._sk.public_key()

    def sign(self, data):
        """Sign bytes (or a str) → hex signature."""
        return self._sk.sign(data if isinstance(data, bytes) else data.encode()).hex()

    def public_key_hex(self):
        from cryptography.hazmat.primitives import serialization
        return self._pk.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def verify_own(self, signature_hex, data):
        """Verify a signature this signer produced (uses its own public key)."""
        return Signer.verify(self.public_key_hex(), signature_hex, data)

    @staticmethod
    def verify(public_key_hex, signature_hex, data):
        """Third-party verification: True iff `signature_hex` over `data` is valid for `public_key_hex`."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            pk.verify(bytes.fromhex(signature_hex), data if isinstance(data, bytes) else data.encode())
            return True
        except (InvalidSignature, ValueError):
            return False


def resolve(db_path):
    """Return a Crypter (encryption on) or None (encryption EXPLICITLY off via VAYL_ENCRYPT=off).

    FAIL-CLOSED (Art. 32): if encryption is on — the default — but the key material or the
    `cryptography` package is unavailable, raise instead of silently running unencrypted.
    Plaintext must always be an explicit operator decision, never a quiet degradation."""
    if os.environ.get("VAYL_ENCRYPT", "on").lower() in ("off", "0", "false", "no"):
        return None
    pw = os.environ.get("VAYL_KEY")
    if pw:
        key = _derive(pw, db_path + ".salt")       # Argon2id (scrypt for pre-existing deployments)
    else:
        key = kms.data_key(db_path + ".key", 32)   # file (default) or Vault KMS provider (M7)
    try:
        return Crypter(key)
    except Exception as e:
        raise RuntimeError(
            f"VAYL_ENCRYPT is on but at-rest encryption is unavailable ({type(e).__name__}: {e}). "
            "Install the 'cryptography' package, or set VAYL_ENCRYPT=off to explicitly run "
            "without encryption.") from e


def resolve_signer(db_path):
    """Return a Signer for tamper-evidence, or None (signing EXPLICITLY off via VAYL_SIGN=off).
    Keyed off a 32-byte Ed25519 seed: derived from VAYL_KEY (off-disk, via Argon2id) when set, else an
    auto-generated `<db>.sign.key` (0600). Deliberately INDEPENDENT of VAYL_ENCRYPT — integrity is a
    separate guarantee from confidentiality. FAIL-CLOSED like resolve(): unsigned operation must be
    an explicit choice, not a silent downgrade."""
    if os.environ.get("VAYL_SIGN", "on").lower() in ("off", "0", "false", "no"):
        return None
    pw = os.environ.get("VAYL_KEY")
    if pw:
        seed = _derive(pw, db_path + ".sign.salt")   # distinct salt → signing seed ≠ enc key
    else:
        seed = kms.data_key(db_path + ".sign.key", 32)   # file (default) or Vault KMS provider (M7)
    try:
        return Signer(seed)
    except Exception as e:
        raise RuntimeError(
            f"Ed25519 signing is unavailable ({type(e).__name__}: {e}). Install the 'cryptography' "
            "package, or set VAYL_SIGN=off to explicitly run without signed audit/receipts.") from e
