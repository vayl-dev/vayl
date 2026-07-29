#!/usr/bin/env python3
"""
Vayl remote server (M2) — the HTTP transport that makes the M1 auth layer real over the network.

`vayl-mcp` runs the stdio transport for a single local user; `vayl-server` runs the Model Context
Protocol over streamable-HTTP so a TEAM can share one deployment. Every request must present an API
key (`Authorization: Bearer vayl_sk_…`); the middleware resolves it to a principal (auth.py) and binds
it for the request, so the RBAC gates from M1 apply per caller. No/invalid key → 401.

Operational notes:
  • Health endpoints `/healthz` and `/readyz` are unauthenticated (for load balancers / k8s probes).
  • TLS is terminated by your reverse proxy / ingress — run this behind one; never expose it raw.
  • Concurrency rides the single process lock (fine for pilot scale); Postgres (M6) removes the ceiling.
  • Bootstrap: a fresh DB has no principals. Create the first admin over stdio
    (`vayl-mcp` runs as local admin → `create_principal("you", role="admin")`), then use its key here.

Run:
    VAYL_DB=/data/vayl.db VAYL_HOST=0.0.0.0 VAYL_PORT=8080 vayl-server
"""
import hmac
import json
import os
import time as _time
from collections import deque
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from vayl.api import mcp_server
from vayl.auth import sso

# Coarse first-party backstops (no dependency). A real reverse proxy / WAF is still recommended.
_MAX_BODY = int(os.environ.get("VAYL_MAX_BODY", str(1 << 20)))    # 1 MiB
_RATE_PER_MIN = int(os.environ.get("VAYL_RATE_PER_MIN", "120"))   # per client IP; 0 disables
# Behind a reverse proxy the socket peer is the proxy, so per-IP limiting collapses to one bucket.
# Set this to the number of TRUSTED proxy hops in front of Vayl to read the real client from the
# right end of X-Forwarded-For; 0 (default) trusts only the socket peer (correct when exposed direct).
_TRUSTED_PROXY_HOPS = int(os.environ.get("VAYL_TRUSTED_PROXY_HOPS", "0"))
# When set, /metrics requires this bearer token (else it is open, for an internal scrape network).
_METRICS_TOKEN = os.environ.get("VAYL_METRICS_TOKEN", "")


def _client_ip(scope, headers):
    """The rate-limit key. With trusted proxies in front, take the client from X-Forwarded-For
    (rightmost minus the trusted-hop count); otherwise the socket peer. Never trust XFF when 0 hops
    are configured — it is caller-supplied and trivially spoofable."""
    if _TRUSTED_PROXY_HOPS > 0:
        chain = [h.strip() for h in headers.get(b"x-forwarded-for", b"").decode().split(",") if h.strip()]
        if len(chain) >= _TRUSTED_PROXY_HOPS:
            return chain[-_TRUSTED_PROXY_HOPS]
    return (scope.get("client") or ("?", 0))[0] or "?"


async def _reject(send, status, msg):
    body = json.dumps({"error": msg}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class LimitsMiddleware:
    """Body-size cap (declared Content-Length) + a per-IP sliding-window rate limit. A coarse DoS
    backstop applied BEFORE auth, so unauthenticated floods are cheap to shed. Stdlib only."""
    def __init__(self, app):
        self.app = app
        self._hits = {}   # ip -> deque[timestamps]  (empties evicted; dict hard-capped)

    def _rate_ok(self, ip):
        if _RATE_PER_MIN <= 0:
            return True
        now = _time.monotonic()
        dq = self._hits.setdefault(ip, deque())
        while dq and now - dq[0] > 60:
            dq.popleft()
        if not dq and len(self._hits) > 10000:     # bound the tracking table itself
            self._hits.clear()
        if len(dq) >= _RATE_PER_MIN:
            return False
        dq.append(now)
        if len(dq) == 0:
            self._hits.pop(ip, None)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        try:
            clen = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            clen = 0
        if clen > _MAX_BODY:
            return await _reject(send, 413, "request body too large")
        if not self._rate_ok(_client_ip(scope, headers)):
            return await _reject(send, 429, "rate limit exceeded")
        # A missing/false Content-Length (chunked transfer) slips past the header check above. Read the
        # body HERE, bounded by the cap, and reject before the app sees a byte — so an unauthenticated
        # chunked flood is shed at the edge. Buffering ≤ _MAX_BODY (1 MiB) is safe; MCP bodies are tiny.
        body = b""
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if len(body) > _MAX_BODY:
                    return await _reject(send, 413, "request body too large")
                more = msg.get("more_body", False)
            else:                                       # http.disconnect
                more = False

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        return await self.app(scope, replay_receive, send)


def render_prometheus(snapshot, principals_active, edition):
    """Render metrics in Prometheus text exposition format (pure → unit-testable). Counters only —
    tool calls/errors/latency, reconciliation actions, active principals, and the license edition."""
    lines = [
        "# TYPE vayl_tool_calls_total counter",
        "# TYPE vayl_tool_errors_total counter",
        "# TYPE vayl_tool_latency_avg_ms gauge",
        "# TYPE vayl_action_total counter",
        "# TYPE vayl_principals_active gauge",
    ]
    for tool, d in sorted((snapshot.get("tools") or {}).items()):
        lines.append(f'vayl_tool_calls_total{{tool="{tool}"}} {d.get("calls", 0)}')
        lines.append(f'vayl_tool_errors_total{{tool="{tool}"}} {d.get("errors", 0)}')
        lines.append(f'vayl_tool_latency_avg_ms{{tool="{tool}"}} {d.get("avg_ms", 0.0)}')
    for action, n in sorted((snapshot.get("actions") or {}).items()):
        lines.append(f'vayl_action_total{{action="{action}"}} {n}')
    lines.append(f"vayl_principals_active {principals_active}")
    lines.append(f'vayl_license_edition_info{{edition="{edition}"}} 1')
    return "\n".join(lines) + "\n"


def resolve_bearer(header_value, auth, sso_verifier=None):
    """Extract a Bearer token and resolve it to a Principal, or None. An API key (`vayl_sk_…`) goes to
    the auth store; a JWT goes to the OIDC verifier (M5 SSO) when one is configured. Pure/unit-testable."""
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if token.startswith("vayl_sk_"):
        return auth.verify(token)
    if sso_verifier is not None and sso.looks_like_jwt(token):
        return sso_verifier.verify(token)
    return None


class AuthMiddleware:
    """ASGI middleware: authenticate every request and bind the principal for its whole duration,
    so the tool-layer RBAC gate sees the right caller. Fails closed — no valid credential → 401."""
    def __init__(self, app, auth, sso_verifier=None):
        self.app = app
        self.auth = auth
        self.sso_verifier = sso_verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        principal = resolve_bearer(headers.get(b"authorization", b"").decode(),
                                   self.auth, self.sso_verifier)
        if principal is None:
            return await self._unauthorized(send)
        token = mcp_server.set_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_server.reset_principal(token)

    @staticmethod
    async def _unauthorized(send):
        body = b'{"error":"unauthorized: send Authorization: Bearer vayl_sk_..."}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})


def build_app(mcp_app, auth, store, sso_verifier=None):
    """Compose the public app: unauthenticated health routes + everything else behind auth. The
    parent app runs the MCP app's lifespan so its session manager starts."""
    async def healthz(request):
        return JSONResponse({"status": "ok"})

    async def readyz(request):
        try:
            store.db.execute("SELECT 1")
            return JSONResponse({"status": "ready"})
        except Exception:                                        # unauthenticated probe — no error detail
            return JSONResponse({"status": "not-ready"}, status_code=503)

    async def metrics(request):
        # Low-sensitivity operational counters (no memory content), but they do reveal usage volume and
        # active-principal count — keep on an internal scrape network. Set VAYL_METRICS_TOKEN to require
        # a bearer token here (compared in constant time); unset leaves it open, like the health probes.
        if _METRICS_TOKEN:
            h = request.headers.get("authorization", "")
            tok = h[7:] if h[:7].lower() == "bearer " else ""
            if not hmac.compare_digest(tok, _METRICS_TOKEN):
                return PlainTextResponse("unauthorized", status_code=401,
                                         headers={"www-authenticate": "Bearer"})
        text = render_prometheus(mcp_server._metrics.snapshot(), auth.count_active(),
                                 mcp_server._license.edition)
        return PlainTextResponse(text, media_type="text/plain; version=0.0.4")

    child_lifespan = getattr(getattr(mcp_app, "router", None), "lifespan_context", None)

    @asynccontextmanager
    async def lifespan(app):
        if child_lifespan is None:
            yield
        else:
            async with child_lifespan(mcp_app):
                yield

    app = Starlette(routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Mount("/", app=AuthMiddleware(mcp_app, auth, sso_verifier)),
    ], lifespan=lifespan)
    return LimitsMiddleware(app)   # body cap + rate limit applied before everything else


def main():
    # Over the network, auth is MANDATORY: the tool layer fails closed if a request somehow arrives
    # without a bound principal (defense in depth behind the middleware).
    mcp_server._AUTH_REQUIRED = True
    host = os.environ.get("VAYL_HOST", "127.0.0.1")
    port = int(os.environ.get("VAYL_PORT", "8080"))

    # OIDC SSO (M5): enabled only when configured AND the license grants it. API keys always work.
    sso_verifier = None
    cfg = sso.configured()
    if cfg and mcp_server._license.allows("sso"):
        sso_verifier = sso.OidcVerifier(cfg)
        print(f"  SSO: OIDC enabled (issuer {cfg.issuer})")
    elif cfg:
        print("  ⚠ OIDC configured but the license doesn't grant 'sso' — SSO disabled (API keys still work).")

    mcp_app = mcp_server.mcp.http_app(transport="http", stateless_http=True,
                                      **mcp_server._transport_security())
    app = build_app(mcp_app, mcp_server._auth, mcp_server._store, sso_verifier)

    print(f"Vayl server → http://{host}:{port}/mcp   (auth: REQUIRED · Bearer vayl_sk_… or OIDC JWT)")
    if mcp_server._auth.count_active() == 0:
        print("  ⚠ no principals yet — bootstrap an admin over stdio: run `vayl-mcp` and call "
              "create_principal('you', role='admin'), then use its key here.")
    print("  health: /healthz   ready: /readyz   ·   put TLS/ingress in front; don't expose raw.")
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
