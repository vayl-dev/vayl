"""
Licensing — offline signed license entitlements and the vendor-side minting CLI.
"""

import argparse
import secrets

import pytest

from vayl.licensing import license as lic
from vayl.licensing import mint_license as ml
from vayl.security.crypto import Signer

# ══════════════════════════════════════════════════════════════════
# from test_license
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def vendor(monkeypatch):
    """An ephemeral vendor keypair; point the product's verifier at its public key."""
    seed = secrets.token_bytes(32)
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(seed).public_key_hex())
    return seed.hex()


def _claims(**over):
    c = {"customer": "Acme GmbH", "edition": "enterprise", "seats": 25,
         "features": ["sso", "kms"], "issued": "2026-01-01", "expires": "2099-01-01"}
    c.update(over)
    return c


def test_no_license_is_community_with_free_seat_cap(monkeypatch):
    monkeypatch.delenv("VAYL_LICENSE", raising=False)
    lc = lic.load(None)
    assert lc.edition == "community" and lc.valid is True
    assert lc.seat_cap == lic.COMMUNITY_SEAT_CAP
    assert lc.allows("sso") is False


def test_valid_license_unlocks_edition_seats_features(vendor):
    blob = lic.mint(vendor, _claims())
    lc = lic.load(blob)
    assert lc.valid and lc.edition == "enterprise" and lc.customer == "Acme GmbH"
    assert lc.seat_cap == 25
    assert lc.allows("sso") and lc.allows("kms") and not lc.allows("nonexistent")


def test_license_can_be_loaded_from_a_file(vendor, tmp_path):
    blob = lic.mint(vendor, _claims())
    p = tmp_path / "vayl.lic"
    p.write_text(blob)
    lc = lic.load(str(p))
    assert lc.valid and lc.seat_cap == 25


def test_tampered_claims_are_rejected(vendor):
    blob = lic.mint(vendor, _claims(seats=25))
    body_b64, sig = blob[len(lic.PREFIX):].split(".", 1)
    forged = lic.PREFIX + lic._b64e(lic._canonical(_claims(seats=9999))) + "." + sig
    lc = lic.load(forged)
    assert lc.valid is False and "signature" in lc.reason
    assert lc.seat_cap == lic.COMMUNITY_SEAT_CAP        # soft: falls back, doesn't brick


def test_wrong_vendor_key_is_rejected(vendor, monkeypatch):
    blob = lic.mint(vendor, _claims())
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(b"z" * 32).public_key_hex())
    lc = lic.load(blob)
    assert lc.valid is False and lc.allows("sso") is False


def test_expired_license_falls_back_to_community(vendor):
    blob = lic.mint(vendor, _claims(expires="2020-01-01"))
    lc = lic.load(blob)
    assert lc.valid is False and "expired" in lc.reason
    assert lc.seat_cap == lic.COMMUNITY_SEAT_CAP and not lc.allows("sso")


def test_no_vendor_key_configured_rejects_any_license(vendor, monkeypatch):
    blob = lic.mint(vendor, _claims())
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", "")
    monkeypatch.delenv("VAYL_VENDOR_PUBKEY", raising=False)
    assert lic.load(blob).valid is False


def test_garbage_blob_is_rejected(vendor):
    assert lic.load("not-a-license").valid is False
    assert lic.load(lic.PREFIX + "@@@.zzz").valid is False


def test_env_pubkey_overrides_default(monkeypatch):
    seed = secrets.token_bytes(32)
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", "")
    monkeypatch.setenv("VAYL_VENDOR_PUBKEY", Signer(seed).public_key_hex())
    lc = lic.load(lic.mint(seed.hex(), _claims()))
    assert lc.valid and lc.seat_cap == 25


# ══════════════════════════════════════════════════════════════════
# from test_mint_license
# ══════════════════════════════════════════════════════════════════

def _args(**over):
    a = {"seed": None, "customer": "Acme GmbH", "edition": "enterprise",
         "seats": "25", "features": "sso,kms", "expires": "2099-01-01"}
    a.update(over)
    return argparse.Namespace(**a)


def test_keygen_prints_a_matching_keypair(capsys):
    ml.cmd_keygen(None)
    out = capsys.readouterr().out
    hexes = [w for w in out.split() if len(w) == 64 and all(c in "0123456789abcdef" for c in w)]
    assert len(hexes) == 2, "expected a private seed and a public key"
    seed_hex, pub_hex = hexes
    # the printed public key must actually be the public half of the printed seed
    assert Signer(bytes.fromhex(seed_hex)).public_key_hex() == pub_hex


def test_mint_produces_a_license_the_product_verifies(capsys, monkeypatch):
    seed = secrets.token_bytes(32)
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(seed).public_key_hex())

    assert ml.cmd_mint(_args(seed=seed.hex())) == 0
    blob = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(lic.PREFIX)][0]

    got = lic.load(blob)                       # the customer-side verifier
    assert got.valid and got.edition == "enterprise"
    assert got.seat_cap == 25                    # "25" string arg coerced to int
    assert got.allows("sso") and got.allows("kms")   # comma-separated arg parsed to a list
    assert got.customer == "Acme GmbH"


def test_mint_without_a_seed_fails_cleanly(capsys):
    assert ml.cmd_mint(_args(seed="")) == 2     # non-zero exit, no traceback
    assert "seed" in capsys.readouterr().err.lower()


def test_mint_with_no_features_yields_an_empty_list(capsys, monkeypatch):
    seed = secrets.token_bytes(32)
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(seed).public_key_hex())
    ml.cmd_mint(_args(seed=seed.hex(), features=""))
    blob = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(lic.PREFIX)][0]
    assert lic.load(blob).claims["features"] == []   # not [""]


def test_a_license_minted_by_a_different_vendor_is_rejected(capsys, monkeypatch):
    """The whole point of signing: only OUR seed mints licences our build honours."""
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(secrets.token_bytes(32)).public_key_hex())
    ml.cmd_mint(_args(seed=secrets.token_bytes(32).hex()))   # signed by an unrelated seed
    blob = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(lic.PREFIX)][0]

    got = lic.load(blob)
    assert not got.valid and got.edition == "community"      # fail-soft, not a crash


def test_cli_mint_end_to_end(capsys, monkeypatch):
    seed = secrets.token_bytes(32)
    monkeypatch.setattr(lic, "DEFAULT_VENDOR_PUBKEY", Signer(seed).public_key_hex())
    monkeypatch.setattr("sys.argv", ["vayl-license", "mint", "--seed", seed.hex(),
                                     "--customer", "Globex", "--seats", "3",
                                     "--expires", "2099-01-01"])
    assert ml.main() == 0
    blob = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(lic.PREFIX)][0]
    got = lic.load(blob)
    assert got.valid and got.seat_cap == 3


def test_cli_requires_a_subcommand(monkeypatch):
    monkeypatch.setattr("sys.argv", ["vayl-license"])
    with pytest.raises(SystemExit):        # argparse: required subcommand
        ml.main()
