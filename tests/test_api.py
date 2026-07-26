"""
API surface — the HTTP server, MCP tool annotations, and config.
"""

import asyncio
import os
import sqlite3
import tempfile

import pytest  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server  # noqa: E402  # noqa: E402
from vayl.api import server as srv  # noqa: E402  # noqa: E402
from vayl.auth.auth import Auth, Principal, Role  # noqa: E402  # noqa: E402
from vayl.memory.llm_memory import (  # noqa: E402
    _openai_config,
    _provider,
)

# ══════════════════════════════════════════════════════════════════
# from test_server
# ══════════════════════════════════════════════════════════════════

class _StoreStub:
    def __init__(self):
        # TestClient runs the app in a worker thread → the connection must allow cross-thread use
        self.db = sqlite3.connect(":memory:", check_same_thread=False)


@pytest.fixture
def client_and_key(monkeypatch):
    # over the network auth is mandatory; if the middleware fails to bind, the app sees no principal
    monkeypatch.setattr(mcp_server, "_AUTH_REQUIRED", True)
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _p, key = auth.create("bot", roles=Role.AGENT)

    async def whoami(request):
        # a stand-in for the MCP app: report whichever principal the middleware bound
        p = mcp_server._current_principal()
        return PlainTextResponse(p.id if p else "none")

    echo = Starlette(routes=[Route("/mcp", whoami)])
    app = srv.build_app(echo, auth, _StoreStub())
    with TestClient(app) as client:      # `with` runs startup/shutdown (lifespan)
        yield client, key


def test_health_endpoints_are_unauthenticated(client_and_key):
    client, _key = client_and_key
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["status"] == "ready"


def test_metrics_endpoint_is_open_and_prometheus_formatted(client_and_key):
    client, _key = client_and_key
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "vayl_principals_active" in r.text and "vayl_license_edition_info" in r.text


def test_metrics_requires_a_token_when_configured(client_and_key, monkeypatch):
    client, _key = client_and_key
    monkeypatch.setattr(srv, "_METRICS_TOKEN", "scrape-secret")
    assert client.get("/metrics").status_code == 401                              # no token → denied
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200 and "vayl_principals_active" in ok.text


def test_render_prometheus_shape():
    snap = {"tools": {"recall": {"calls": 5, "errors": 1, "avg_ms": 12.3}},
            "actions": {"SUPERSEDE": 3}}
    out = srv.render_prometheus(snap, principals_active=4, edition="community")
    assert 'vayl_tool_calls_total{tool="recall"} 5' in out
    assert 'vayl_tool_errors_total{tool="recall"} 1' in out
    assert 'vayl_action_total{action="SUPERSEDE"} 3' in out
    assert "vayl_principals_active 4" in out
    assert 'vayl_license_edition_info{edition="community"} 1' in out


def test_missing_key_is_401(client_and_key):
    client, _key = client_and_key
    r = client.get("/mcp")
    assert r.status_code == 401 and "unauthorized" in r.text
    assert r.headers.get("www-authenticate") == "Bearer"


def test_invalid_or_malformed_key_is_401(client_and_key):
    client, _key = client_and_key
    assert client.get("/mcp", headers={"Authorization": "Bearer vayl_sk_wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "garbage"}).status_code == 401


def test_valid_key_binds_the_principal_for_the_request(client_and_key):
    client, key = client_and_key
    r = client.get("/mcp", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.text.startswith("prin_")     # the middleware resolved AND bound it where tools read it


def test_principal_does_not_leak_between_requests(client_and_key):
    client, key = client_and_key
    client.get("/mcp", headers={"Authorization": f"Bearer {key}"})    # binds then resets
    # a subsequent unauthenticated request is still rejected (no stale principal)
    assert client.get("/mcp").status_code == 401


def test_resolve_bearer_parsing():
    auth = Auth(sqlite3.connect(":memory:"))
    p, key = auth.create("x", roles=Role.MEMBER)
    assert srv.resolve_bearer(f"Bearer {key}", auth).id == p.id
    assert srv.resolve_bearer(f"bearer {key}", auth).id == p.id      # scheme is case-insensitive
    assert srv.resolve_bearer("Bearer nope", auth) is None
    assert srv.resolve_bearer("", auth) is None
    assert srv.resolve_bearer(f"Token {key}", auth) is None          # wrong scheme


def test_resolve_bearer_routes_jwt_to_sso_and_keys_to_auth():
    auth = Auth(sqlite3.connect(":memory:"))
    _p, key = auth.create("bot", roles=Role.AGENT)

    class _FakeSSO:
        def verify(self, token):
            return Principal("sso:u", "u", [Role.VIEWER], "human") if token == "aa.bb.cc" else None

    # a JWT goes to the SSO verifier...
    assert srv.resolve_bearer("Bearer aa.bb.cc", auth, _FakeSSO()).id == "sso:u"
    # ...an API key goes to the auth store (never to SSO)...
    assert srv.resolve_bearer(f"Bearer {key}", auth, _FakeSSO()).id.startswith("prin_")
    # ...and a JWT with no SSO verifier configured is rejected
    assert srv.resolve_bearer("Bearer aa.bb.cc", auth, None) is None


# ══════════════════════════════════════════════════════════════════
# from test_mcp_annotations
# ══════════════════════════════════════════════════════════════════

DESTRUCTIVE = {"delete", "delete_all", "purge_expired", "revoke_principal"}   # irreversible


READ_ONLY = {"recall", "recall_related", "history", "get_memory", "list_memories",
             "export_memory", "audit_log", "stats", "health", "explain_decision",
             "check_before_act", "safe_recall", "verify_receipt", "verify_audit", "export_public_key",
             "get_reconcile_policy", "list_principals", "license_status",
             "pending_changes"}


WRITES = {"remember", "forget", "update_memory", "record_decision", "attest",
          "set_reconcile_policy", "create_principal",
          # Resolving a gated change edits memory but destroys nothing: confirming supersedes the
          # old value (kept as history) and rejecting keeps the proposal itself as history.
          "confirm_change", "reject_change"}   # change memory/log/config, keep history


def tools():
    return {t.name: t.annotations for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_every_tool_is_annotated():
    t = tools()
    assert set(t) == DESTRUCTIVE | READ_ONLY | WRITES
    assert all(a is not None for a in t.values())
    assert all(a.title for a in t.values())          # human-readable title for the client UI


def test_irreversible_tools_are_marked_destructive():
    t = tools()
    for name in DESTRUCTIVE:
        a = t[name]
        assert a.destructiveHint is True, f"{name} must warn clients before erasing"
        assert a.readOnlyHint is False, name


def test_reads_are_marked_read_only():
    t = tools()
    for name in READ_ONLY:
        assert t[name].readOnlyHint is True, f"{name} should be safe for a client to auto-run"


def test_writes_change_memory_but_are_not_destructive():
    # supersede/retract keep history — nothing is lost, so clients needn't hard-warn
    t = tools()
    for name in WRITES:
        a = t[name]
        assert a.readOnlyHint is False and a.destructiveHint is False, name


def test_llm_backed_tools_declare_open_world():
    t = tools()
    for name in ("remember", "recall", "health"):
        assert t[name].openWorldHint is True, f"{name} calls an external LLM/embedder"


# ══════════════════════════════════════════════════════════════════
# from test_config
# ══════════════════════════════════════════════════════════════════

_ENV = ["LLM_PROVIDER", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
        "OPENAI_BASE_URL", "OPENAI_MODEL"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


def test_out_of_the_box_is_local_ollama_no_egress():
    assert _provider() == "openai"
    base, key, model, local = _openai_config()
    assert local and "11434" in base and key == "ollama" and model == "qwen2.5:3b"


def test_openai_key_switches_to_cloud(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    base, key, model, local = _openai_config()
    assert base == "https://api.openai.com/v1" and not local
    assert key == "sk-x" and model == "gpt-5-mini"


def test_provider_inferred_from_present_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert _provider() == "anthropic"
    monkeypatch.setenv("GROQ_API_KEY", "y")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert _provider() == "groq"


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert _provider() == "groq"


def test_custom_base_url_detects_local(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    base, key, model, local = _openai_config()
    assert local and key == "ollama" and model == "qwen2.5:3b"
