#!/usr/bin/env python3
"""
Key custody (M7) — source Vayl's data keys from a KMS instead of a plaintext file on disk.

Vayl encrypts at rest (Fernet) and signs (Ed25519) with local key material, so it needs the raw key
bytes in memory. What M7 changes is WHERE those bytes come from and whether the key ever touches disk.

Providers (VAYL_KMS):
  • file (default): a 32-byte key auto-generated in the given path (0600). Zero-setup, but the key sits
    beside the data — fine against a copied DB file, not against machine theft. Pair with FDE.
  • vault: HashiCorp Vault **Transit** envelope encryption. A random data key (DEK) is generated
    locally, WRAPPED by a master key that never leaves Vault, and only the wrapped blob is written to
    disk (`<path>.wrapped`). At startup the wrapped blob is sent to Vault to unwrap → the DEK lives in
    process memory only. **The plaintext key never touches disk; the master key never leaves Vault**,
    so key custody moves off the Vayl host — and, self-hosted, Vault keeps it on your infra (sovereignty).

Vault config (env): VAULT_ADDR, VAULT_TOKEN, VAYL_VAULT_TRANSIT_MOUNT (default "transit"),
VAYL_VAULT_TRANSIT_KEY (default "vayl"). Set up once: `vault secrets enable transit` +
`vault write -f transit/keys/vayl`.
"""
import base64
import json
import os
import urllib.request


def _read_or_create(path, nbytes):
    """A secret file, owner-only (0600). Used for the `file` provider and for scrypt salts."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    data = os.urandom(nbytes)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


def data_key(base_path, nbytes=32):
    """Return `nbytes` of key material for `base_path`, per the VAYL_KMS provider."""
    if os.environ.get("VAYL_KMS", "file").lower() == "vault":
        return _vault_data_key(base_path, nbytes)
    return _read_or_create(base_path, nbytes)


def _vault_cfg():
    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
    token = os.environ.get("VAULT_TOKEN", "")
    mount = os.environ.get("VAYL_VAULT_TRANSIT_MOUNT", "transit")
    key = os.environ.get("VAYL_VAULT_TRANSIT_KEY", "vayl")
    return addr, token, mount, key


def _vault_post(path, payload):
    addr, token, _mount, _key = _vault_cfg()
    req = urllib.request.Request(
        addr + path, data=json.dumps(payload).encode(),
        headers={"X-Vault-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _vault_encrypt(plaintext_bytes):
    """Wrap bytes with the Vault master key → an opaque 'vault:v1:…' ciphertext (safe to store)."""
    _addr, _token, mount, key = _vault_cfg()
    data = _vault_post(f"/v1/{mount}/encrypt/{key}",
                       {"plaintext": base64.b64encode(plaintext_bytes).decode()})
    return data["data"]["ciphertext"]


def _vault_decrypt(ciphertext):
    """Unwrap a 'vault:v1:…' ciphertext back to the plaintext bytes (the master key stays in Vault)."""
    _addr, _token, mount, key = _vault_cfg()
    data = _vault_post(f"/v1/{mount}/decrypt/{key}", {"ciphertext": ciphertext})
    return base64.b64decode(data["data"]["plaintext"])


def _vault_data_key(base_path, nbytes):
    wrapped_path = base_path + ".wrapped"
    if os.path.exists(wrapped_path):
        with open(wrapped_path) as f:
            return _vault_decrypt(f.read().strip())          # unwrap the stored DEK → memory only
    dek = os.urandom(nbytes)                                  # first run: mint a data key…
    wrapped = _vault_encrypt(dek)                             # …wrap it with Vault's master key…
    fd = os.open(wrapped_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(wrapped)                                     # …and store ONLY the wrapped blob on disk
    return dek
