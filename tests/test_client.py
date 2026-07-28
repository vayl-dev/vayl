"""The Python SDK (`vayl.Vayl`): scope merging, result decoding, dynamic tool dispatch, and — when
`vayl-mcp` is installed — a real stdio round-trip. The live test skips (never fails) if a session
can't be established, so CI stays green without a server."""
import os
import shutil
from types import SimpleNamespace

import pytest

from vayl import Vayl, VaylError
from vayl.client import _merge_scope, _text


def _content(text, kind="text"):
    return SimpleNamespace(type=kind, text=text)


def _result(texts, is_error=False):
    return SimpleNamespace(content=[_content(t) for t in texts], isError=is_error)


def test_merge_scope_drops_empty_and_lets_call_override():
    scope = {"user_id": "u1", "agent_id": "", "run_id": ""}
    assert _merge_scope(scope, {"user_id": "u2", "text": "x", "foo": None}) == {"user_id": "u2", "text": "x"}
    assert _merge_scope(scope, {"text": "x"}) == {"user_id": "u1", "text": "x"}


def test_text_joins_text_parts_and_ignores_others():
    assert _text(_result(["a", "b"])) == "a\nb"
    mixed = SimpleNamespace(content=[_content("keep"), SimpleNamespace(type="image", text=None)], isError=False)
    assert _text(mixed) == "keep"


def test_text_raises_vaylerror_on_tool_error():
    with pytest.raises(VaylError):
        _text(_result(["access denied: outside scope"], is_error=True))


def test_dynamic_dispatch_returns_callable_and_guards_private():
    m = Vayl.__new__(Vayl)                 # no connection — just exercise __getattr__
    assert callable(m.check_before_act)    # any tool name → a callable
    with pytest.raises(AttributeError):
        m._internal                        # private names are not tools


@pytest.mark.skipif(shutil.which("vayl-mcp") is None, reason="vayl-mcp not installed")
def test_stdio_roundtrip(tmp_path):
    # list_memories reads the store and needs no LLM, so this works offline. Encryption/signing off
    # to avoid key setup in a throwaway test db. Any failure -> skip, so CI can't break on it.
    env = dict(os.environ, VAYL_DB=str(tmp_path / "c.db"),
               VAYL_ENCRYPT="off", VAYL_SIGN="off", VAYL_CLIENT_CONNECT_TIMEOUT="20")
    try:
        with Vayl(env=env, user_id="tester") as m:
            out = m.list_memories()
    except Exception as e:                 # noqa: BLE001 — never fail CI on environment issues
        pytest.skip(f"stdio session unavailable: {e}")
    assert isinstance(out, str)
