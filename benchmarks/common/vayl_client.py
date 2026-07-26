"""
Vayl benchmark client
=====================

Presents Vayl through the same three-call surface the mem0ai/memory-benchmarks runner
drives Mem0 through — `add(messages, user_id, timestamp)`, `search(query, user_id, top_k)`,
`delete_user(user_id)` — so the identical pipeline can be pointed at either system.

Two things are worth stating plainly, because they are where Vayl differs and a reader
should not have to reverse-engineer them from the code:

1. **Retrieval returns status.** Vayl does not delete a superseded fact; it retires it.
   `search` therefore returns both current and retired facts, each tagged, exactly as
   `LLMMemory.query` assembles its own context. An additive store has no such tag to
   return — every memory it holds looks equally current. Surfacing the tag is the thing
   being measured, so hiding it here to look more like Mem0 would defeat the exercise.

2. **Ingest is stateful per user.** A long-lived server keeps a user's memory hot rather
   than re-reading it from disk per message, so the client caches one `LLMMemory` per
   user and persists after each add. The reconciliation work is identical either way;
   this only avoids an O(n^2) reload that would measure sqlite, not Vayl.
"""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Any

from vayl.memory.llm_memory import LLMMemory, Status, embed_retrieve
from vayl.storage import store as store_mod
from vayl.storage.store import Store

_STATUS_LABEL = {
    Status.ACTIVE: "current",
    Status.SUPERSEDED: "superseded",
    Status.HISTORICAL: "historical",
    Status.FLAGGED: "flagged",
}


class VaylClient:
    """Async facade over the synchronous Vayl runtime."""

    def __init__(self, db_path: str, concurrency: int = 4, include_history: bool = True):
        self.db_path = db_path
        self.include_history = include_history
        self.store = Store(db_path)
        self._mem: dict[str, LLMMemory] = {}
        self._hist: dict[str, list] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sem = asyncio.Semaphore(concurrency)
        self.add_calls = 0
        self.search_calls = 0

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def __aenter__(self) -> VaylClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self._mem.clear()
        self._hist.clear()

    # ── internals ────────────────────────────────────────────────────────────
    def _memory(self, user_id: str) -> LLMMemory:
        m = self._mem.get(user_id)
        if m is None:
            m = self.store.load(user_id)
            self._mem[user_id] = m
        return m

    def _history(self, user_id: str) -> list:
        """Load the retired statements that `Store.load` deliberately leaves on disk.

        `load()` is a hot path: it reads only ACTIVE + FLAGGED so its cost is O(active)
        rather than O(all history ever written). That is the right call for the write path,
        which never reads history. It is the wrong set for a *read* that may be asked about
        the past — "what did we use before", "when did she stop" — and on this benchmark it
        hid 87 HISTORICAL and 12 SUPERSEDED facts from the answerer entirely.

        Fetched once per user and cached. Never written back: these rows are appended to the
        ranking pool only, so a later save() cannot rewrite history it did not load.
        """
        cached = self._hist.get(user_id)
        if cached is not None:
            return cached
        st = self.store
        try:
            rows = st.db.execute(
                f"SELECT {store_mod._COLS} FROM statements "
                "WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? "
                "AND status NOT IN ('ACTIVE','FLAGGED_CONFLICT') ORDER BY id",
                (st.tenant, user_id, "", "")).fetchall()
            out = [st._row_to_statement(r) for r in rows]
        except Exception:
            out = []                      # a read must never fail for want of history
        self._hist[user_id] = out
        return out

    # ── the three calls the runner needs ─────────────────────────────────────
    async def add(self, messages: list[dict], user_id: str, timestamp: int | None = None,
                  date_str: str = "") -> dict:
        """Ingest one chunk of conversation turns.

        `messages` is a list of {"role"/"speaker", "content"} dicts. They are rendered as a
        short transcript and handed to Vayl as a single observation; the extractor pulls
        every fact out of it and reconciles each against what is already known.

        `date_str` is prepended to the transcript. A conversation turn almost never states
        its own date — "I went last Tuesday" is only resolvable against when it was said —
        so without this the extractor has no way to record *when* anything happened, and
        every question of the form "when did X" is unanswerable no matter how good
        retrieval is. Mem0's harness passes the same timestamp into its add(); this makes
        ours actually reach the model.
        """
        text = _render(messages)
        if not text.strip():
            return {"facts": 0}
        if date_str:
            text = f"[Conversation on {date_str}]\n{text}"

        source = f"locomo@{timestamp}" if timestamp else "locomo"
        async with self._sem, self._locks[user_id]:
            def work() -> int:
                m = self._memory(user_id)
                applied = m.add(text, source=source)
                self.store.save(user_id, m)
                return len(applied or [])
            n = await asyncio.to_thread(work)
        self.add_calls += 1
        return {"facts": n}

    async def search(self, query: str, user_id: str, top_k: int = 200) -> list[dict]:
        """Rank stored facts against the query and return the top_k, status-tagged."""
        async with self._locks[user_id]:
            def work() -> list[dict]:
                m = self._memory(user_id)
                # Vectors live on disk until a ranking pass needs them (see store.hydrate_embeddings).
                if len(m.statements) > top_k:
                    try:
                        self.store.hydrate_embeddings(user_id, m.statements)
                    except Exception:
                        pass                  # degrade to lexical ranking; never fail a read
                pool = m.statements + (self._history(user_id) if self.include_history else [])
                try:
                    ranked = embed_retrieve(query, pool, top_k)
                except Exception:
                    ranked = pool[:top_k]
                out = []
                for s in ranked:
                    if not self.include_history and s.status is not Status.ACTIVE:
                        continue
                    when = _when(s.source)
                    # A Vayl statement is TWO things: a normalized slot that makes
                    # reconciliation possible, and the sentence it came from. Sending only
                    # the slot throws away every detail normalization strips — the slot
                    # "attended_recently" is true and useless next to "I went yesterday".
                    # Mem0's harness hands its answerer full memory text; sending less than
                    # we hold would understate Vayl, not test it.
                    out.append({
                        "id": s.id,
                        "memory": _memory_line(s, when),
                        "when": when,
                        "raw": (s.raw or "")[:400],
                        "subject": s.subject,
                        "value": s.value,
                        "status": _STATUS_LABEL.get(s.status, str(s.status)),
                        "scope": s.scope,
                        "confidence": s.confidence,
                        "supersedes": s.supersedes,
                        "source": s.source,
                        "score": 0.0,        # rank order is the signal; no distance is exposed
                    })
                return out
            res = await asyncio.to_thread(work)
        self.search_calls += 1
        return res

    async def delete_user(self, user_id: str) -> bool:
        async with self._locks[user_id]:
            def work() -> bool:
                self.store.delete_all(user_id)
                self._mem.pop(user_id, None)
                return True
            return await asyncio.to_thread(work)

    # ── reporting ────────────────────────────────────────────────────────────
    def stats(self, user_id: str) -> dict:
        """Facts stored vs facts still current — the ratio that separates a reconciling
        store from an additive one. An additive store reports these as equal by construction."""
        m = self._memory(user_id)
        active = sum(1 for s in m.statements if s.status is Status.ACTIVE)
        return {
            "stored": len(m.statements),
            "current": active,
            "retired": len(m.statements) - active,
        }


def _memory_line(s, when: str) -> str:
    """Render one statement for the answerer: the reconciled slot, when it was said, and
    the sentence it was extracted from.

    The slot carries the reconciliation verdict (this value is current, that one is
    retired); the raw sentence carries the detail the slot normalized away. Questions
    about *state* are answered by the slot, questions about *detail* by the sentence, and
    a memory that keeps both should present both.
    """
    head = f"{s.subject}: {s.value}"
    if when:
        head += f"  (said on {when})"
    raw = (s.raw or "").strip()
    return f'{head}\n    said: "{raw[:400]}"' if raw else head


def _when(source: str | None) -> str:
    """Recover the observation date from the provenance stamp written by add().

    Vayl records *when a fact was told to it* on every statement. Surfacing that at read
    time costs nothing and answers a whole class of question ("when did X happen") that is
    otherwise unanswerable — the value alone says "attended recently", which is true and
    useless.
    """
    if not source or not source.startswith("locomo@"):
        return ""
    try:
        from datetime import datetime, timezone
        ts = int(source.split("@", 1)[1])
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %B %Y")
    except (ValueError, OSError, OverflowError):
        return ""


def _render(messages: list[dict]) -> str:
    """Flatten turns into a transcript line-per-turn."""
    lines = []
    for msg in messages:
        who = msg.get("speaker") or msg.get("role") or ""
        content = (msg.get("content") or msg.get("text") or "").strip()
        if not content:
            continue
        lines.append(f"{who}: {content}" if who else content)
    return "\n".join(lines)


def format_search_results(results: list[dict]) -> list[dict]:
    """Normalize for the result file — mirrors the upstream helper of the same name."""
    return [
        {k: r.get(k) for k in ("id", "memory", "status", "score", "source") if k in r}
        for r in results
    ]


def default_db_path(project_name: str) -> str:
    base = os.environ.get("VAYL_BENCH_DB_DIR", "benchmarks/.dbs")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{project_name}.db")
