"""
Security primitives — at-rest encryption, Ed25519 signing, KMS key custody, and the transport hardening checks.
"""

import base64
import json
import os
import sqlite3
import tempfile

import pytest
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server as s  # noqa: E402  # noqa: E402
from vayl.api import server as srv  # noqa: E402  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402  # noqa: E402
from vayl.memory.llm_memory import LLMMemory  # noqa: E402
from vayl.security import kms  # noqa: E402
from vayl.security.crypto import Crypter, Signer, resolve  # noqa: E402
from vayl.storage import store as store_mod  # noqa: E402
from vayl.storage.store import Store  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# from test_crypto
# ══════════════════════════════════════════════════════════════════

def fact(action="ADD", subject="salary", value="120000", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": 0.9, "time_ref": "present"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def test_crypter_roundtrip_and_blind_index():
    c = Crypter(b"0" * 32)
    assert c.dec(c.enc("hello")) == "hello"
    assert c.enc("hello") != "hello"            # ciphertext, not plaintext
    assert c.enc("x") != c.enc("x")             # randomized (Fernet IV)
    assert c.blind("s") == c.blind("s")         # blind index is deterministic
    assert c.blind("a") != c.blind("b")
    assert c.dec(None) is None and c.enc(None) is None


def test_signer_signs_and_third_party_verifies():
    s = Signer(b"7" * 32)
    sig = s.sign("what was known at T")
    # a third party verifies with ONLY the public key — no secret needed
    assert Signer.verify(s.public_key_hex(), sig, "what was known at T") is True
    assert s.verify_own(sig, "what was known at T") is True


def test_signer_rejects_tampered_payload_and_wrong_key():
    s = Signer(b"7" * 32)
    sig = s.sign("balance=100")
    assert Signer.verify(s.public_key_hex(), sig, "balance=999") is False   # payload altered
    other = Signer(b"8" * 32)
    assert Signer.verify(other.public_key_hex(), sig, "balance=100") is False  # wrong key
    assert Signer.verify(s.public_key_hex(), "00" * 64, "balance=100") is False  # garbage signature


def test_signer_is_deterministic_from_seed():
    # same seed → same identity (so a persisted key survives restarts and receipts stay verifiable)
    assert Signer(b"a" * 32).public_key_hex() == Signer(b"a" * 32).public_key_hex()
    assert Signer(b"a" * 32).public_key_hex() != Signer(b"b" * 32).public_key_hex()


@pytest.fixture
def _encrypted_env(monkeypatch):
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "correct horse battery staple")   # passphrase mode, key off-disk


def test_data_is_ciphertext_at_rest_but_plaintext_on_read(tmp_path, _encrypted_env):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    m = LLMMemory()
    m._apply(fact(subject="salary", value="120000"), "alice earns 120000")
    st.save("u1", m)

    # on disk: the sensitive columns must NOT contain the plaintext
    raw = sqlite3.connect(db).execute("SELECT subject, value, embedding FROM statements").fetchone()
    assert "salary" not in raw[0] and "120000" not in raw[1]
    assert "0.1" not in (raw[2] or "")           # embedding encrypted too

    # on read (with the key): plaintext comes back
    st2 = Store(db)
    reloaded = st2.load("u1")
    s = reloaded.active()[0]
    assert s.subject == "salary" and s.value == "120000"
    # the vector is deliberately NOT dragged through the hot path — load() only records that one
    # exists, and hydration fetches (and decrypts) it when a ranking pass actually needs it
    assert s._emb is None and s._has_emb is True
    assert st2.hydrate_embeddings("u1", reloaded.statements) == 1
    assert s._emb == pytest.approx([0.1, 0.2])   # stored as float32 — exact to ranking precision


def test_subject_queries_work_under_encryption(tmp_path, _encrypted_env):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    m = LLMMemory()
    m._apply(fact(subject="salary", value="120000"), "x")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="salary", value="135000", target_id=oid), "raise")
    m._apply(fact(subject="dept", value="Platform"), "x")
    st.save("u1", m)

    # history_rows filters by the encrypted subject via the blind index
    hist = Store(db).history_rows("u1", "salary")
    assert [h.value for h in hist] == ["120000", "135000"]

    # subjects() decrypts + groups by blind index
    subs = {s["subject"]: s for s in Store(db).subjects("u1")}
    assert subs["salary"]["records"] == 2 and subs["dept"]["records"] == 1

    # delete by subject erases the whole (encrypted) subject
    assert Store(db).delete("u1", "salary") == 2
    assert Store(db).history_rows("u1", "salary") == []
    assert [s.value for s in Store(db).load("u1").active()] == ["Platform"]


def test_wrong_key_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    db = str(tmp_path / "vayl.db")
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "the-right-key")
    m = LLMMemory(); m._apply(fact(value="secret"), "x"); Store(db).save("u1", m)

    monkeypatch.setenv("VAYL_KEY", "a-different-key")   # attacker without the key
    (tmp_path / "vayl.db.salt").unlink(missing_ok=True)  # force a fresh (wrong) derived key
    with pytest.raises(Exception):
        Store(db).load("u1")                            # cannot decrypt


def test_fresh_passphrase_uses_argon2id_and_key_is_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "correct horse battery staple")
    db = str(tmp_path / "vayl.db")

    c = resolve(db)                                       # fresh deployment → Argon2id
    assert c.dec(c.enc("secret")) == "secret"
    assert json.loads((tmp_path / "vayl.db.salt.kdf").read_text())["kdf"] == "argon2id"
    # re-resolving reuses the pinned KDF + salt → identical key, so data stays readable
    assert resolve(db).blind("x") == c.blind("x")


def test_legacy_salt_without_marker_stays_on_scrypt(tmp_path, monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "correct horse battery staple")
    db = str(tmp_path / "vayl.db")
    # a deployment predating the marker: a salt already on disk, no `.kdf` marker
    (tmp_path / "vayl.db.salt").write_bytes(os.urandom(16))

    c = resolve(db)
    assert c.dec(c.enc("secret")) == "secret"
    # scrypt is kept so the existing derived key is unchanged and old data keeps opening
    assert json.loads((tmp_path / "vayl.db.salt.kdf").read_text())["kdf"] == "scrypt"


def test_marker_pins_kdf_against_later_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "pw")
    db = str(tmp_path / "vayl.db")

    resolve(db)                                          # fresh → writes argon2id marker
    monkeypatch.setenv("VAYL_KDF", "scrypt")             # too late: marker is authoritative
    resolve(db)
    assert json.loads((tmp_path / "vayl.db.salt.kdf").read_text())["kdf"] == "argon2id"


def test_vayl_kdf_env_forces_scrypt_on_fresh_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.setenv("VAYL_KEY", "pw")
    monkeypatch.setenv("VAYL_KDF", "scrypt")             # opt out before any data exists
    db = str(tmp_path / "vayl.db")

    c = resolve(db)
    assert c.dec(c.enc("secret")) == "secret"
    assert json.loads((tmp_path / "vayl.db.salt.kdf").read_text())["kdf"] == "scrypt"


# ══════════════════════════════════════════════════════════════════
# from test_kms
# ══════════════════════════════════════════════════════════════════

def test_file_provider_creates_and_reuses_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("VAYL_KMS", raising=False)
    p = str(tmp_path / "vayl.db.key")
    k1 = kms.data_key(p, 32)
    assert len(k1) == 32 and os.path.exists(p)
    assert kms.data_key(p, 32) == k1                      # idempotent — same key on reuse


def test_vault_envelope_never_writes_plaintext_key_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setenv("VAYL_KMS", "vault")
    # simulate Vault Transit: "wrap" = reversible transform the test can invert. The real master key
    # would live in Vault; here we only need to prove Vayl stores the WRAPPED form and unwraps it.
    monkeypatch.setattr(kms, "_vault_encrypt", lambda b: "vault:v1:" + base64.b64encode(b).decode())
    monkeypatch.setattr(kms, "_vault_decrypt", lambda ct: base64.b64decode(ct.split("vault:v1:")[1]))

    base = str(tmp_path / "vayl.db.key")
    dek1 = kms.data_key(base, 32)
    assert len(dek1) == 32

    # on disk there is ONLY the wrapped blob — never the plaintext key
    assert os.path.exists(base + ".wrapped")
    assert not os.path.exists(base)                       # no plaintext <db>.key file
    on_disk = open(base + ".wrapped").read()
    assert on_disk.startswith("vault:v1:")
    assert base64.b64decode(on_disk.split("vault:v1:")[1]) == dek1   # disk holds wrapped(dek), not dek raw

    # a fresh startup reads the wrapped blob and unwraps to the SAME key
    dek2 = kms.data_key(base, 32)
    assert dek2 == dek1


def test_data_key_routes_by_provider(tmp_path, monkeypatch):
    calls = {}

    def _fake_vault(p, n):
        calls["vault"] = True
        return b"v" * n

    monkeypatch.setattr(kms, "_vault_data_key", _fake_vault)
    monkeypatch.setenv("VAYL_KMS", "vault")
    assert kms.data_key(str(tmp_path / "k"), 32) == b"v" * 32 and calls.get("vault")

    monkeypatch.setenv("VAYL_KMS", "file")
    k = kms.data_key(str(tmp_path / "k2"), 32)
    assert len(k) == 32 and k != b"v" * 32                # went through the file path, not the vault stub


def test_crypto_and_signer_work_with_a_vault_sourced_key(tmp_path, monkeypatch):
    """End-to-end through crypto: with a (mocked) Vault-sourced key, Crypter round-trips and Signer
    verifies — and neither plaintext key file exists on disk (only .wrapped)."""
    from vayl.security import crypto
    monkeypatch.setenv("VAYL_KMS", "vault")
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.delenv("VAYL_KEY", raising=False)
    monkeypatch.setattr(kms, "_vault_encrypt", lambda b: "vault:v1:" + base64.b64encode(b).decode())
    monkeypatch.setattr(kms, "_vault_decrypt", lambda ct: base64.b64decode(ct.split("vault:v1:")[1]))

    db = str(tmp_path / "vayl.db")
    c = crypto.resolve(db)
    assert c.dec(c.enc("secret")) == "secret"
    s = crypto.resolve_signer(db)
    sig = s.sign("msg")
    assert s.verify_own(sig, "msg")
    assert os.path.exists(db + ".key.wrapped") and not os.path.exists(db + ".key")       # enc key off-disk
    assert os.path.exists(db + ".sign.key.wrapped") and not os.path.exists(db + ".sign.key")  # sign key off-disk


# ══════════════════════════════════════════════════════════════════
# from test_security_hardening
# ══════════════════════════════════════════════════════════════════

def test_tool_error_does_not_leak_detail_to_client(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("connect /Users/secret/vayl.db failed: value='alice-ssn-123'")
    monkeypatch.setattr(llm_memory, "llm_extract_classify", boom)

    out = s.remember("anything", user_id="u1")
    assert "/Users/secret" not in out and "alice-ssn-123" not in out   # no path / data leak
    assert "ref " in out                                               # opaque reference instead
    ref = out.split("ref ", 1)[1].split(")")[0].strip()
    # full detail still available server-side (stderr), keyed by the same ref
    assert ref in capsys.readouterr().err


def test_config_keyerror_hint_is_still_helpful():
    # a missing-config KeyError names an ENV VAR (not data) → still surfaced to help the operator
    def go():
        raise KeyError("OPENAI_API_KEY")
    assert "OPENAI_API_KEY" in s._guard("t", go, cap=None)


def _app(monkeypatch, rate=120, maxbody=1 << 20):
    monkeypatch.setattr(srv, "_RATE_PER_MIN", rate)
    monkeypatch.setattr(srv, "_MAX_BODY", maxbody)

    async def ok(request):
        return PlainTextResponse("ok")
    inner = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"])])
    return srv.LimitsMiddleware(inner)


def test_body_cap_rejects_oversized(monkeypatch):
    with TestClient(_app(monkeypatch, maxbody=64)) as c:
        assert c.post("/x", content=b"a" * 65).status_code == 413
        assert c.post("/x", content=b"a" * 10).status_code == 200


def test_rate_limit_returns_429_after_the_window(monkeypatch):
    with TestClient(_app(monkeypatch, rate=5)) as c:
        codes = [c.get("/x").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5] == 429 and codes[6] == 429


def test_rate_limit_disabled_when_zero(monkeypatch):
    with TestClient(_app(monkeypatch, rate=0)) as c:
        assert all(c.get("/x").status_code == 200 for _ in range(30))


def test_body_cap_enforces_streamed_bytes_without_content_length(monkeypatch):
    """A chunked body carries no Content-Length, so the header check can't see it — the middleware
    must count the streamed bytes and reject. Regression for the DoS bypass."""
    async def sink(request):
        await request.body()                      # actually consume the stream
        return PlainTextResponse("ok")
    monkeypatch.setattr(srv, "_MAX_BODY", 64)
    monkeypatch.setattr(srv, "_RATE_PER_MIN", 0)
    app = srv.LimitsMiddleware(Starlette(routes=[Route("/x", sink, methods=["POST"])]))
    with TestClient(app) as c:
        big = c.post("/x", content=iter([b"a" * 100]))     # iterator body → chunked, no length
        assert big.status_code == 413
        small = c.post("/x", content=iter([b"a" * 10]))
        assert small.status_code == 200


def test_client_ip_trusts_xff_only_with_configured_proxy_hops(monkeypatch):
    scope = {"client": ("10.0.0.1", 5000)}
    headers = {b"x-forwarded-for": b"203.0.113.9, 172.16.0.1"}
    monkeypatch.setattr(srv, "_TRUSTED_PROXY_HOPS", 0)
    assert srv._client_ip(scope, headers) == "10.0.0.1"        # default: socket peer, XFF ignored
    monkeypatch.setattr(srv, "_TRUSTED_PROXY_HOPS", 1)
    assert srv._client_ip(scope, headers) == "172.16.0.1"      # 1 trusted hop → rightmost XFF entry
    monkeypatch.setattr(srv, "_TRUSTED_PROXY_HOPS", 2)
    assert srv._client_ip(scope, headers) == "203.0.113.9"     # 2 hops → the original client


def test_readyz_reports_no_detail_on_failure(monkeypatch):
    class _BrokenStore:
        class db:
            @staticmethod
            def execute(*a):
                raise RuntimeError("secret-path /db failed")
    app = srv.build_app(Starlette(routes=[]), _StubAuth(), _BrokenStore())
    with TestClient(app) as c:
        r = c.get("/readyz")
        assert r.status_code == 503 and "secret-path" not in r.text


class _StubAuth:
    def count_active(self):
        return 0

    def verify(self, _):
        return None
