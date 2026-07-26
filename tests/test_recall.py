"""
The read path — how a recall assembles context: raw text, query-embedding cache, history reachability, and hybrid retrieval.
"""

import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory, _ctx_line, embed_retrieve
from vayl.memory.reconcile import Statement
from vayl.storage import store as store_mod
from vayl.storage.store import Store

# ══════════════════════════════════════════════════════════════════
# from test_recall_context
# ══════════════════════════════════════════════════════════════════

def fact(subject="mon", value="Sentry", action="ADD", raw="we use Sentry"):
    return {"action": action, "subject": subject, "value": value, "scope": "global",
            "confidence": 0.9, "time_ref": "present"}, raw


@pytest.fixture
def echo_qa(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)


@pytest.fixture(autouse=True)
def clear_cache():
    llm_memory._QEMB_CACHE.clear()
    yield
    llm_memory._QEMB_CACHE.clear()


def test_context_carries_the_sentence_a_fact_came_from(echo_qa):
    m = LLMMemory()
    o, raw = fact(subject="support_group", value="attended_recently",
                  raw="I went to the LGBTQ support group yesterday and it was powerful.")
    m._apply(o, raw)
    ctx = m.query("when did I go to the support group?")

    assert "support_group=attended_recently" in ctx      # the reconciled slot
    assert "yesterday" in ctx                            # the detail the slot dropped


def test_a_fact_without_raw_text_renders_as_the_bare_slot():
    from vayl.memory.reconcile import Statement
    assert _ctx_line(Statement("s@global", "db", "Postgres", "global")) == "db=Postgres"


def test_raw_text_is_truncated_so_one_verbose_turn_cannot_swamp_the_context():
    from vayl.memory.reconcile import Statement
    s = Statement("s@global", "db", "Postgres", "global")
    s.raw = "x" * 5000
    assert len(_ctx_line(s)) < 400


def test_history_lines_keep_their_tag_and_their_sentence(echo_qa, monkeypatch, tmp_path):
    from vayl.storage import store as store_mod
    from vayl.storage.store import Store
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])

    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    o, raw = fact(subject="mon", value="Sentry", raw="we use Sentry for monitoring")
    m._apply(o, raw)
    st.save("u1", m)

    m = st.load("u1")
    old = m.active()[0]
    o2, raw2 = fact(subject="mon", value="Datadog", action="SUPERSEDE", raw="we moved to Datadog")
    o2["target_id"] = old.id
    m._apply(o2, raw2)
    st.save("u1", m)

    ctx = st.load("u1").query("monitoring history?", include_history=True)
    assert "(history) mon=Sentry" in ctx
    assert "we use Sentry for monitoring" in ctx     # the retired fact keeps its sentence too


def _many(n=30):
    from vayl.memory.reconcile import Statement
    out = []
    for i in range(n):
        s = Statement(f"s{i}@global", f"k{i}", f"v{i}", "global")
        s._emb = [0.1, 0.2]
        out.append(s)
    return out


def test_identical_questions_embed_once(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: calls.append(texts) or [[0.1, 0.2]])
    stmts = _many()
    embed_retrieve("what plan is the customer on?", stmts, k=5)
    embed_retrieve("what plan is the customer on?", stmts, k=5)
    embed_retrieve("what plan is the customer on?", stmts, k=5)
    assert len(calls) == 1


def test_different_questions_embed_separately(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: calls.append(texts) or [[0.1, 0.2]])
    stmts = _many()
    embed_retrieve("question one", stmts, k=5)
    embed_retrieve("question two", stmts, k=5)
    assert len(calls) == 2


def test_cache_is_bounded(monkeypatch):
    """A long-lived server must not grow this without limit."""
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(llm_memory, "_QEMB_CACHE_MAX", 4)
    stmts = _many()
    for i in range(12):
        embed_retrieve(f"question {i}", stmts, k=5)
    assert len(llm_memory._QEMB_CACHE) <= 4


def test_cache_can_be_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: calls.append(1) or [[0.1, 0.2]])
    monkeypatch.setattr(llm_memory, "_QEMB_CACHE_MAX", 0)
    stmts = _many()
    embed_retrieve("same question", stmts, k=5)
    embed_retrieve("same question", stmts, k=5)
    assert len(calls) == 2


def test_a_failing_embedder_still_degrades_to_lexical(monkeypatch):
    """The cache must not turn a recoverable embedder outage into a failed read."""
    def boom(texts):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(llm_memory, "_embed", boom)
    stmts = _many()
    got = embed_retrieve("k7", stmts, k=5)     # must not raise
    assert [s.subject for s in got] == ["k7"]  # lexical ranking found it without the embedder
    assert llm_memory._QEMB_CACHE == {}        # a failed embed is never cached


def test_embedding_text_includes_the_source_sentence():
    """Semantic retrieval must be able to see what normalization stripped. The lexical half of
    retrieval already reads `raw`; leaving it out of the vector left the two halves looking at
    different data."""
    from vayl.memory.reconcile import Statement
    from vayl.storage.store import _embed_text

    s = Statement("s@global", "melanie_hobby", "painting", "global")
    s.raw = "Melanie: I painted a sunrise over the lake back in 2022."
    text = _embed_text(s)
    assert "melanie_hobby" in text and "painting" in text
    assert "sunrise" in text            # the detail the slot dropped


def test_embedding_text_truncates_a_verbose_turn():
    from vayl.memory.reconcile import Statement
    from vayl.storage.store import _embed_text
    s = Statement("s@global", "k", "v", "global")
    s.raw = "x" * 5000
    assert len(_embed_text(s)) < 400


def test_embedding_text_survives_a_fact_with_no_raw():
    from vayl.memory.reconcile import Statement
    from vayl.storage.store import _embed_text
    assert _embed_text(Statement("s@global", "db", "Postgres", "global")) == "db Postgres"


# ══════════════════════════════════════════════════════════════════
# from test_history_recall
# ══════════════════════════════════════════════════════════════════

def _h_fact(action="ADD", subject="mon", value="Sentry", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": 0.9, "time_ref": "present"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


@pytest.fixture
def _h_echo_qa(monkeypatch):
    """Echo the assembled context instead of calling an LLM, so tests assert on what the model
    would have been shown — the thing that actually determines whether a stale value can leak."""
    from vayl.memory import llm_memory
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])


@pytest.fixture
def store_with_history(tmp_path, _h_echo_qa):
    """A space where 'mon' was superseded Sentry -> Datadog, plus an untouched _h_fact."""
    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    m._apply(_h_fact(subject="mon", value="Sentry"), "we use Sentry")
    m._apply(_h_fact(subject="db", value="Postgres"), "db is Postgres")
    st.save("u1", m)

    m = st.load("u1")
    old = next(s for s in m.active() if s.subject == "mon")
    m._apply(_h_fact(action="SUPERSEDE", subject="mon", value="Datadog", target_id=old.id),
             "moved to Datadog")
    st.save("u1", m)
    return st


def test_default_recall_cannot_leak_a_retired_value(store_with_history):
    """The safety property. Not 'filtered from the answer' — never loaded at all."""
    ctx = store_with_history.load("u1").query("what monitoring do we use?")
    assert "Datadog" in ctx
    assert "Sentry" not in ctx


def test_default_recall_does_not_even_fetch_history(store_with_history, monkeypatch):
    """Guarantee and cost are the same mechanism: no query, no rows, no leak."""
    calls = []
    real = Store.history_statements
    monkeypatch.setattr(Store, "history_statements",
                        lambda self, *a, **k: calls.append(a) or real(self, *a, **k))
    store_with_history.load("u1").query("what monitoring do we use?")
    assert calls == []


def test_opting_in_reaches_the_past(store_with_history):
    ctx = store_with_history.load("u1").query("what did we use before?", include_history=True)
    assert "Sentry" in ctx
    assert "(history)" in ctx          # tagged as former, never presented as current
    assert "Datadog" in ctx            # the current value is still there to contrast against


def test_retired_facts_are_tagged_not_mixed_in(store_with_history):
    ctx = store_with_history.load("u1").query("monitoring history?", include_history=True)
    assert "(history) mon=Sentry" in ctx
    assert "(history) mon=Datadog" not in ctx      # the live value is not mislabelled as past


def test_history_is_fetched_once_and_cached(store_with_history, monkeypatch):
    calls = []
    real = Store.history_statements
    monkeypatch.setattr(Store, "history_statements",
                        lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    m = store_with_history.load("u1")
    m.query("before?", include_history=True)
    m.query("before again?", include_history=True)
    assert len(calls) == 1


def test_reading_history_does_not_duplicate_it_on_save(store_with_history):
    """The reason history lives in a separate pool: save() diffs against what load() returned, so a
    history row inside m.statements would look new and be re-INSERTed."""
    before = len(store_with_history.export("u1")["records"])

    m = store_with_history.load("u1")
    m.query("what did we use before?", include_history=True)   # hydrates history
    store_with_history.save("u1", m)                           # must not write it back

    after = store_with_history.export("u1")["records"]
    assert len(after) == before
    assert sum(1 for r in after if r["value"] == "Sentry") == 1


def test_history_pool_stays_out_of_the_active_set(store_with_history):
    m = store_with_history.load("u1")
    m.query("before?", include_history=True)
    assert all(s.value != "Sentry" for s in m.statements)
    assert [s.value for s in m.active()] == ["Postgres", "Datadog"]


def test_history_statements_returns_only_retired_rows(store_with_history):
    rows = store_with_history.history_statements("u1")
    assert [s.value for s in rows] == ["Sentry"]
    assert all(s.status.name in ("SUPERSEDED", "HISTORICAL") for s in rows)


def test_history_statements_is_scoped_to_its_space(store_with_history, tmp_path):
    assert store_with_history.history_statements("someone_else") == []


def test_unpersisted_memory_has_no_history_and_does_not_crash(_h_echo_qa):
    """A memory built in-process was never loaded from a store — no hydrator, no history."""
    m = LLMMemory()
    m._apply(_h_fact(subject="db", value="Postgres"), "x")
    assert "Postgres" in m.query("db?", include_history=True)


# ══════════════════════════════════════════════════════════════════
# from test_retrieve
# ══════════════════════════════════════════════════════════════════

def mk(subject, value, emb=None, raw=""):
    s = Statement(f"{subject}@global", subject, value, "global", raw=raw)
    s._emb = emb
    return s


def test_small_memory_returns_everything():
    stmts = [mk("a", "1"), mk("b", "2")]
    assert embed_retrieve("anything", stmts, k=12) is stmts     # <= k → no ranking needed


def test_lexical_surfaces_a_keyword_match_with_no_embeddings():
    # nothing is embedded → the lexical half must carry the query
    stmts = [mk("state", "Redux"), mk("monitoring", "Sentry"), mk("db", "Postgres"),
             mk("auth", "JWT"), mk("deploy", "Friday"), mk("cloud", "AWS")]
    out = embed_retrieve("what do we use for Sentry monitoring?", stmts, k=3)
    assert any(s.value == "Sentry" for s in out)                # found by keyword, no vectors at all


def test_hybrid_pulls_in_a_keyword_match_semantic_ranking_would_miss(monkeypatch):
    # question vector points straight at A; B is semantically orthogonal (cosine 0) but is the
    # keyword match. Pure-semantic top-3 = A, D, F (not B). Fusion must pull B in and drop F.
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: [[1.0, 0.0, 0.0]])
    A = mk("provider", "Acme", [1.0, 0.0, 0.0])
    B = mk("monitoring", "Sentry", [0.0, 1.0, 0.0])
    C = mk("c", "c", [0.0, 0.0, 1.0])
    D = mk("d", "d", [1.0, 1.0, 0.0])
    E = mk("e", "e", [0.0, 1.0, 1.0])
    F = mk("f", "f", [1.0, 0.0, 1.0])
    out = embed_retrieve("any Sentry alerts firing?", [A, B, C, D, E, F], k=3)

    assert B in out and A in out       # keyword match + top semantic match both make it
    assert F not in out                # reranking evicted a semantic-only tail result


def test_no_signal_falls_back_to_bounded_first_k():
    stmts = [mk("a", "1"), mk("b", "2"), mk("c", "3"), mk("d", "4")]   # no emb, no keyword overlap
    out = embed_retrieve("zzz qqq", stmts, k=2)
    assert len(out) == 2               # bounded, not the whole list
