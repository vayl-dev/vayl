# Vayl remote server — self-hosted, authenticated MCP over HTTP.
# Runs `vayl-server` as a non-root user; DB + keys live on a mounted volume at /data.
#
#   docker build -t vayl:latest .
#   docker run -p 8080:8080 -v vayl-data:/data vayl:latest
#
# NOTE: not built in this environment (no Docker available at authoring time) — validate with a real
# `docker build` before shipping. TLS is terminated by your reverse proxy / ingress; don't expose raw.
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

# Non-root runtime user; /data is the writable volume for the SQLite DB + encryption/signing keys.
RUN useradd --create-home --uid 10001 vayl && mkdir -p /data && chown vayl:vayl /data
WORKDIR /app

# Install the project + the server extra (uvicorn; starlette/httpx come via mcp).
# --retries/--timeout make the build resilient to flaky networks on client hosts.
COPY . /app
RUN pip install --no-cache-dir --retries 5 --timeout 180 ".[server]"

USER vayl
ENV VAYL_DB=/data/vayl.db \
    VAYL_HOST=0.0.0.0 \
    VAYL_PORT=8080 \
    VAYL_AUTH_REQUIRED=1 \
    VAYL_ENCRYPT=on
VOLUME ["/data"]
EXPOSE 8080

# Liveness: the unauthenticated health probe.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["vayl-server"]
