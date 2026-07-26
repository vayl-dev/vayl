#!/usr/bin/env python3
"""
Cross-system reconciliation benchmark — Vayl vs Mem0 vs Graphiti, same scenarios, same model.

The question every reconciling-memory claim rests on: when a fact CHANGES or is WITHDRAWN, does the
store still surface the STALE value as current knowledge? We feed each system the identical setup
messages, ask the identical question, and scan what it returns as current for the old (forbidden)
value. Uniform scoring across all three:

  CORRECT         — expected (current) value present, stale value absent
  SILENTLY-WRONG  — the stale/forbidden value is still surfaced as current (the trust-killer)
  MISSED          — neither surfaced (no answer, but nothing stale either)

Also records write/read latency. Run in the benchmark venv (mem0ai + graphiti-core installed):
    OPENAI_API_KEY=sk-... BENCH_MODEL=gpt-4o-mini NEO4J_PASSWORD=... \
        PYTHONPATH=. .venv-bench/bin/python benchmarks/evaluations/compare_systems.py [--smoke]
Writes results to benchmarks/results/compare_systems.json and .md.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

MODEL = os.environ.get("BENCH_MODEL", "gpt-4o-mini")
EMBED = os.environ.get("BENCH_EMBED", "text-embedding-3-small")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PW = os.environ.get("NEO4J_PASSWORD", "testpass123")
SMOKE = "--smoke" in sys.argv

# (name, setup messages in order, question, expect[current], forbid[stale])
SCENARIOS = [
    ("supersede — switch tools",
     ["We use Redux for state management.", "We switched from Redux to Zustand for state."],
     "What do we use for state management?", ["zustand"], ["redux"]),
    ("supersede — cloud migration",
     ["We host everything on AWS.", "We migrated off AWS — we're on GCP now."],
     "Where do we host our infrastructure?", ["gcp"], ["aws"]),
    ("supersede — numeric update",
     ["Our runway is 18 months.", "Updated: runway is 12 months now after the hire."],
     "How much runway do we have?", ["12"], ["18"]),
    ("retract — drop with no replacement",
     ["We use Sentry for error monitoring.", "We've dropped Sentry; we have no error monitoring now."],
     "What error monitoring do we use?", ["none", "no ", "not ", "don't", "do not", "unknown", "dropped"], ["sentry"]),
    ("retract — vendor removed",
     ["We monitor with Datadog.", "We dropped the vendor; Datadog is gone."],
     "What do we use for monitoring?", ["none", "no ", "not ", "don't", "unknown", "dropped"], ["datadog"]),
    ("coexist — scoped (web vs mobile)",
     ["We use Redux for state in the web app.", "We use Zustand for state in the mobile app."],
     "What state library does the mobile app use?", ["zustand"], []),
    ("hypothetical must not be stored",
     ["We use REST for our API.", "What if we moved the API to GraphQL?"],
     "What API style do we use?", ["rest"], ["graphql"]),
    ("unchanged fact",
     ["Our primary database is PostgreSQL."],
     "What database do we use?", ["postgres"], []),
    # terse updates with NO transition language — where additive stores tend to keep both values
    ("supersede — bare replacement",
     ["My favorite database is MySQL.", "Actually, PostgreSQL."],
     "What is my favorite database?", ["postgres"], ["mysql"]),
    ("supersede — preference flip",
     ["I prefer tabs for indentation.", "I prefer spaces now."],
     "Do I prefer tabs or spaces?", ["spaces"], ["tabs"]),
]
if SMOKE:
    SCENARIOS = SCENARIOS[:2]


def score(text, expect, forbid):
    t = (text or "").lower()
    if any(f in t for f in forbid):
        return "SILENTLY-WRONG"
    if not expect or any(e in t for e in expect):
        return "CORRECT"
    return "MISSED"


# One synthesizer, shared by every system, so the comparison measures RETRIEVAL + RECONCILIATION
# quality (what each store surfaces as current), not prompt-engineering of the answer step.
def qa(context, question):
    from openai import OpenAI
    system = ("Answer the question using ONLY the facts provided. Facts marked (no longer valid) or "
              "(former) are superseded/retracted and must NOT be treated as current. If the facts "
              "don't support a current answer, say you don't know. Answer in one short sentence.")
    r = OpenAI().chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"FACTS:\n{context or '(none)'}\n\nQUESTION: {question}"}])
    return r.choices[0].message.content.strip()


# ─────────────────────────── Vayl ───────────────────────────
def run_vayl():
    db = "/tmp/vayl_cmp.db"
    for suf in ("", ".key", ".salt", ".salt.kdf", ".sign.salt", ".sign.salt.kdf"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    os.environ["VAYL_DB"] = db          # fresh store each run — no cross-run accumulation
    os.environ["OPENAI_MODEL"] = MODEL
    os.environ["EMBED_MODEL"] = EMBED
    os.environ["EMBED_BASE_URL"] = "https://api.openai.com/v1"
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
    os.environ["VAYL_ENCRYPT"] = "off"
    os.environ["VAYL_SIGN"] = "off"
    os.environ["VAYL_GRAPH"] = ""     # slot recall — the core reconciling path
    import importlib

    from vayl.api import mcp_server
    importlib.reload(mcp_server)
    v = mcp_server
    out, wl, rl = [], [], []
    for i, (name, setup, q, expect, forbid) in enumerate(SCENARIOS):
        user = f"cmp_{i}"
        for msg in setup:
            t0 = time.perf_counter(); v.remember(msg, user_id=user); wl.append(time.perf_counter() - t0)
        # native retrieval: Vayl's ACTIVE facts (supersede/retract already retired the stale ones)
        t0 = time.perf_counter()
        active = v._store.load(user).active()
        ctx = "\n".join(f"- {s.subject}: {s.value}" for s in active)
        ans = qa(ctx, q); rl.append(time.perf_counter() - t0)
        out.append(dict(scenario=name, answer=ans, outcome=score(ans, expect, forbid), retrieved=ctx))
    # footprint: superseded/retracted facts are kept as history but are NOT in the active set
    active = stored = 0
    for i in range(len(SCENARIOS)):
        m = v._store.load(f"cmp_{i}")
        active += len(m.active()); stored += len(m.statements)
    return dict(system="Vayl", model=MODEL, results=out,
                write_ms=1000 * sum(wl) / len(wl), read_ms=1000 * sum(rl) / len(rl),
                footprint={"retrievable_as_current": active, "total_stored": stored,
                           "note": "stale facts retired to history — not in the active/retrievable set"},
                infra="SQLite file (no server); graph optional")


# ─────────────────────────── Mem0 ───────────────────────────
def run_mem0():
    import shutil

    from mem0 import Memory
    shutil.rmtree("/tmp/mem0_qdrant", ignore_errors=True)   # start clean each run
    cfg = {
        "llm": {"provider": "openai", "config": {"model": MODEL, "temperature": 0}},
        "embedder": {"provider": "openai", "config": {"model": EMBED}},
        "vector_store": {"provider": "qdrant", "config": {"path": "/tmp/mem0_qdrant", "on_disk": True}},
    }
    mm = Memory.from_config(cfg)    # ONE instance (on-disk Qdrant can't be opened twice); users isolate scenarios
    out, wl, rl = [], [], []
    for i, (name, setup, q, expect, forbid) in enumerate(SCENARIOS):
        user = f"cmp_{i}"
        for msg in setup:
            t0 = time.perf_counter(); mm.add(msg, user_id=user, infer=True); wl.append(time.perf_counter() - t0)
        # native retrieval: Mem0's current memories (its LLM already inferred add/update/delete)
        t0 = time.perf_counter()
        res = mm.search(q, filters={"user_id": user}, top_k=10)
        items = res.get("results", res) if isinstance(res, dict) else res
        mems = [it.get("memory", str(it)) if isinstance(it, dict) else str(it) for it in items]
        ctx = "\n".join(f"- {m}" for m in mems)
        ans = qa(ctx, q); rl.append(time.perf_counter() - t0)
        out.append(dict(scenario=name, answer=ans, outcome=score(ans, expect, forbid),
                        retrieved=ctx, n_memories=len(mems)))
    # footprint: every memory Mem0 keeps stays in the searchable set (additive)
    stored = 0
    for i in range(len(SCENARIOS)):
        allm = mm.get_all(filters={"user_id": f"cmp_{i}"})
        items = allm.get("results", allm) if isinstance(allm, dict) else allm
        stored += len(items)
    return dict(system="Mem0", model=MODEL, results=out,
                write_ms=1000 * sum(wl) / len(wl), read_ms=1000 * sum(rl) / len(rl),
                footprint={"retrievable_as_current": stored, "total_stored": stored,
                           "note": "additive — superseded facts remain searchable unless the LLM chose to delete them"},
                infra="Vector store (Qdrant); additive with LLM-inferred update/delete")


# ─────────────────────────── Graphiti ───────────────────────────
async def run_graphiti():
    from graphiti_core import Graphiti
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client import LLMConfig, OpenAIClient
    from graphiti_core.nodes import EpisodeType
    llm = OpenAIClient(config=LLMConfig(model=MODEL, small_model=MODEL))
    emb = OpenAIEmbedder(config=OpenAIEmbedderConfig(embedding_model=EMBED))
    g = Graphiti(NEO4J_URI, NEO4J_USER, NEO4J_PW, llm_client=llm, embedder=emb)
    out, wl, rl, footprint = [], [], [], {}
    try:
        # clean slate so temporal invalidation is measured on this data only
        async with g.driver.session() as s:
            await s.run("MATCH (n) DETACH DELETE n")
        await g.build_indices_and_constraints()
        for i, (name, setup, q, expect, forbid) in enumerate(SCENARIOS):
            gid = f"cmp_{i}"
            base = datetime.now(timezone.utc)
            for j, msg in enumerate(setup):
                t0 = time.perf_counter()
                await g.add_episode(name=f"{gid}_{j}", episode_body=msg, source=EpisodeType.text,
                                    source_description="benchmark", reference_time=base + timedelta(minutes=j),
                                    group_id=gid)
                wl.append(time.perf_counter() - t0)
            # native retrieval: Graphiti edges, each annotated with its TEMPORAL validity so the
            # synthesizer can honor invalidation (Graphiti's whole mechanism for superseded facts).
            t0 = time.perf_counter()
            edges = await g.search(q, group_ids=[gid], num_results=10)
            lines = []
            for e in edges:
                stale = (e.invalid_at is not None) or (e.expired_at is not None)
                lines.append(f"- {e.fact}" + (" (no longer valid)" if stale else ""))
            ctx = "\n".join(lines)
            ans = qa(ctx, q); rl.append(time.perf_counter() - t0)
            out.append(dict(scenario=name, answer=ans, outcome=score(ans, expect, forbid),
                            retrieved=ctx, n_facts=len(edges)))
        # footprint: edges kept, with superseded ones marked invalid (retained, not deleted)
        async with g.driver.session() as s:
            r1 = await s.run("MATCH ()-[e:RELATES_TO]->() RETURN count(e) AS c")
            tot = (await r1.single())["c"]
            r2 = await s.run("MATCH ()-[e:RELATES_TO]->() WHERE e.invalid_at IS NOT NULL "
                             "OR e.expired_at IS NOT NULL RETURN count(e) AS c")
            inv = (await r2.single())["c"]
        footprint = {"retrievable_as_current": tot - inv, "total_stored": tot,
                     "note": f"{inv} edges marked invalid (temporal) but retained in the graph"}
    finally:
        await g.close()
    return dict(system="Graphiti", model=MODEL, results=out,
                write_ms=1000 * sum(wl) / len(wl), read_ms=1000 * sum(rl) / len(rl),
                footprint=footprint, infra="Neo4j server; temporal knowledge graph")


def main():
    systems = []
    for label, fn in [("Vayl", run_vayl), ("Mem0", run_mem0)]:
        print(f"\n===== {label} =====")
        try:
            r = fn(); systems.append(r)
            for x in r["results"]:
                print(f"  {x['outcome']:16} {x['scenario']}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            systems.append(dict(system=label, model=MODEL, error=f"{type(e).__name__}: {e}", results=[]))
    print("\n===== Graphiti =====")
    try:
        r = asyncio.run(run_graphiti()); systems.append(r)
        for x in r["results"]:
            print(f"  {x['outcome']:16} {x['scenario']}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        systems.append(dict(system="Graphiti", model=MODEL, error=f"{type(e).__name__}: {e}", results=[]))

    # ── aggregate + write report ──
    os.makedirs("benchmarks/results", exist_ok=True)
    report = {"model": MODEL, "embed": EMBED, "scenarios": len(SCENARIOS), "systems": systems}
    with open("benchmarks/results/compare_systems.json", "w") as f:
        json.dump(report, f, indent=2)

    def agg(r):
        rs = r.get("results", [])
        n = len(rs)
        sw = sum(1 for x in rs if x["outcome"] == "SILENTLY-WRONG")
        ok = sum(1 for x in rs if x["outcome"] == "CORRECT")
        ms = sum(1 for x in rs if x["outcome"] == "MISSED")
        return n, ok, ms, sw

    lines = ["# Reconciliation comparison — Vayl vs Mem0 vs Graphiti", "",
             f"Same {len(SCENARIOS)} scenarios, same model (`{MODEL}`), same embedder (`{EMBED}`). "
             "Metric: does the system surface the **stale** value as current knowledge after a fact "
             "changes or is withdrawn?", "",
             "| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |",
             "|---|---|---|---|---|---|---|"]
    for r in systems:
        if r.get("error"):
            lines.append(f"| {r['system']} | — | — | — | — | — | ERROR: {r['error']} |")
            continue
        n, ok, ms, sw = agg(r)
        rate = f"{100*sw/n:.1f}% ({sw}/{n})" if n else "—"
        lines.append(f"| {r['system']} | {rate} | {ok}/{n} | {ms}/{n} | "
                     f"{r.get('write_ms',0):.0f} | {r.get('read_ms',0):.0f} | {r.get('infra','')} |")
    lines += ["", "## Storage footprint — the additive-vs-reconciling difference", "",
              "After all writes: how many facts each store keeps *retrievable as current* vs total held. "
              "A reconciling store retires superseded/retracted facts out of the current set; an additive "
              "store keeps them searchable.", "",
              "| System | Retrievable as current | Total stored | Note |",
              "|---|---|---|---|"]
    for r in systems:
        fp = r.get("footprint") or {}
        if r.get("error") or not fp:
            lines.append(f"| {r['system']} | — | — | {'ERROR' if r.get('error') else 'n/a'} |")
            continue
        lines.append(f"| {r['system']} | {fp.get('retrievable_as_current','?')} | "
                     f"{fp.get('total_stored','?')} | {fp.get('note','')} |")
    lines += ["", "## Per-scenario", ""]
    for r in systems:
        if r.get("error"):
            continue
        lines.append(f"### {r['system']}")
        for x in r["results"]:
            retr = (x.get("retrieved") or "").replace("\n", " · ")[:180]
            lines.append(f"- **{x['outcome']}** — {x['scenario']}  \n"
                         f"  ↳ answer: `{(x['answer'] or '')[:160]}`  \n"
                         f"  ↳ retrieved: {retr or '(nothing)'}")
        lines.append("")
    with open("benchmarks/results/compare_systems.md", "w") as f:
        f.write("\n".join(lines))

    print("\n" + "=" * 72)
    for r in systems:
        if r.get("error"):
            print(f"{r['system']:10} ERROR: {r['error']}"); continue
        n, ok, ms, sw = agg(r)
        print(f"{r['system']:10} silently-wrong={sw}/{n}  correct={ok}/{n}  "
              f"write={r.get('write_ms',0):.0f}ms  read={r.get('read_ms',0):.0f}ms")
    print("=" * 72)
    print("Saved: benchmarks/results/compare_systems.{json,md}")


if __name__ == "__main__":
    main()
