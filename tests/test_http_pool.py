"""
Pooled HTTP transport.

Every LLM/embedding call opened a fresh TCP+TLS connection — a 50-200ms handshake per request that
dominated recall latency. `_http_json` now sends through a keep-alive urllib3 pool when available and
falls back to urllib when it is not, so the core install keeps its minimal dependency surface. These
tests pin: the pooled path parses a response, the retry/backoff logic still fires on 429/5xx in the
pooled path, a 4xx surfaces as an error, and the urllib fallback still works when the pool is absent.
"""
import urllib.error
from unittest import mock

from vayl.memory import llm_memory as L


def _req():
    import urllib.request
    return urllib.request.Request("https://api.example/v1/embeddings", data=b'{"input":"x"}',
                                  headers={"Authorization": "Bearer k", "content-type": "application/json"})


class _Resp:
    def __init__(self, status, data=b'{"ok":1}', headers=None):
        self.status = status
        self.data = data
        self.headers = headers or {}


def test_pooled_path_parses_a_response():
    pool = mock.Mock()
    pool.request.return_value = _Resp(200, b'{"data":[{"embedding":[0.1]}]}')
    with mock.patch.object(L, "_POOL", pool):
        out = L._http_json(_req(), timeout=5)
    assert out["data"][0]["embedding"] == [0.1]
    assert pool.request.call_count == 1               # one call, reusing the pool


def test_pooled_path_reuses_one_pool_across_calls():
    pool = mock.Mock()
    pool.request.return_value = _Resp(200)
    with mock.patch.object(L, "_POOL", pool):
        L._http_json(_req(), timeout=5)
        L._http_json(_req(), timeout=5)
        L._http_json(_req(), timeout=5)
    assert pool.request.call_count == 3               # same PoolManager, three sends


def test_pooled_path_retries_on_429_then_succeeds():
    pool = mock.Mock()
    pool.request.side_effect = [_Resp(429, headers={"retry-after": "0"}), _Resp(200, b'{"ok":1}')]
    with mock.patch.object(L, "_POOL", pool), mock.patch.object(L.time, "sleep"):
        out = L._http_json(_req(), timeout=5, retries=3)
    assert out == {"ok": 1} and pool.request.call_count == 2


def test_pooled_path_raises_on_4xx():
    pool = mock.Mock()
    pool.request.return_value = _Resp(401, b'{"error":"bad key"}')
    with mock.patch.object(L, "_POOL", pool):
        try:
            L._http_json(_req(), timeout=5)
            assert False, "should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 401


def test_urllib_fallback_used_when_no_pool():
    """With urllib3 absent (_POOL is None), the transparent urllib path still works."""
    fake = mock.MagicMock()
    fake.read.return_value = b'{"ok":2}'
    fake.__enter__.return_value = fake
    with mock.patch.object(L, "_POOL", None), \
         mock.patch.object(L.urllib.request, "urlopen", return_value=fake) as urlopen:
        out = L._http_json(_req(), timeout=5)
    assert out == {"ok": 2} and urlopen.call_count == 1


def test_pool_is_a_module_singleton_not_per_call():
    """A fresh PoolManager per call would defeat the point — it must be created once at import."""
    import inspect
    src = inspect.getsource(L)
    # PoolManager is constructed exactly once, at module scope
    assert src.count("PoolManager(") == 1
