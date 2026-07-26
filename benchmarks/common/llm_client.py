"""
Answerer / judge LLM client
===========================

Thin async wrapper over the OpenAI chat-completions API for the two LLM roles the
benchmark needs outside the memory system: the *answerer* (writes an answer from
retrieved memories) and the *judge* (scores it against gold).

These are deliberately separate from the model Vayl uses internally. Vayl's extraction
model does reconciliation; the answerer and judge only read. Holding the answerer and
judge fixed while varying the memory system is the whole basis of a fair comparison.

Reasoning-model parameter handling is imported from the runtime rather than re-derived,
so the benchmark cannot drift from what production sends.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Any

from vayl.memory.llm_memory import _openai_gen_params

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _is_rate_limit(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    return "429" in str(exc) or "rate limit" in str(exc).lower()


class LLMClient:
    """Async chat client with bounded concurrency and retry."""

    def __init__(self, model: str, provider: str = "openai", concurrency: int = 4,
                 max_retries: int = 8):
        self.model = model
        self.provider = provider
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(concurrency)
        self._client: Any = None
        self.calls = 0
        self.rate_limited = 0

    def _ensure(self) -> Any:
        if self._client is None:
            if self.provider == "anthropic":
                raise NotImplementedError(
                    "anthropic provider not wired; use --provider openai or an "
                    "OpenAI-compatible OPENAI_BASE_URL")
            from openai import AsyncOpenAI
            kw: dict[str, Any] = {}
            if os.environ.get("OPENAI_BASE_URL"):
                kw["base_url"] = os.environ["OPENAI_BASE_URL"]
            self._client = AsyncOpenAI(**kw)
        return self._client

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        """One completion. Retries transient failures with exponential backoff."""
        client = self._ensure()
        params = _openai_gen_params(self.model, max_tokens)
        last: Exception | None = None
        async with self._sem:
            for attempt in range(self.max_retries):
                try:
                    resp = await client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        **params,
                    )
                    self.calls += 1
                    return (resp.choices[0].message.content or "").strip()
                except Exception as exc:                       # noqa: BLE001 - surfaced below
                    last = exc
                    if attempt == self.max_retries - 1:
                        break
                    # A 429 means the account's per-minute budget is spent, not that the
                    # request was bad. Backing off by seconds is useless — the window is a
                    # minute wide — so wait long enough to actually clear it. Half of a
                    # previous run's questions were lost to this.
                    if _is_rate_limit(exc):
                        self.rate_limited += 1
                        delay = min(60.0, 8.0 * (attempt + 1)) + random.uniform(0, 4)
                    else:
                        delay = min(30.0, 2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts: {last}")

    async def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        """Completion parsed as JSON. Returns {} when the model emits nothing parseable —
        the caller records that as an ERROR judgment rather than silently scoring 0."""
        raw = await self.complete(system, user, max_tokens)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        m = _JSON_BLOCK.search(raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {}
