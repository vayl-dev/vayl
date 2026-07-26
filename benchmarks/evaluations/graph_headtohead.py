#!/usr/bin/env python3
"""
Graph head-to-head — Vayl's graph path vs Graphiti on MULTI-HOP RELATIONAL queries.

This is Graphiti's home turf: chaining entity->relation->entity edges to answer questions no single
fact contains ("who owns the company Bob works for?"). The scale benchmark tested slot supersession
(Vayl's axis); this tests the relational axis (Graphiti's axis) — so it's a fair test the other way.

Both systems: same model + embedder, NATIVE graph retrieval, one shared synthesizer over the retrieved
edges. Vayl's graph_query returns only VALID edges (its reconciliation); Graphiti's edges are annotated
with temporal validity. Includes a few graph-reconciliation cases (re-pointed edge, retracted relation)
so supersede/retract in the graph is measured too.

Run (benchmark venv, Neo4j up):
    OPENAI_API_KEY=sk-... BENCH_MODEL=gpt-4o-mini NEO4J_PASSWORD=... \
      PYTHONPATH=. .venv-bench/bin/python benchmarks/evaluations/graph_headtohead.py
Writes benchmarks/results/graph_headtohead.{json,md}.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

MODEL = os.environ.get("BENCH_MODEL", "gpt-4o-mini")
EMBED = os.environ.get("BENCH_EMBED", "text-embedding-3-small")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PW = os.environ.get("NEO4J_PASSWORD", "testpass123")
HOPS = int(os.environ.get("GRAPH_HOPS", "3"))   # relational queries need multi-hop; fair to both

# (name, setup facts in order, question, expect[current], forbid[stale])
SCENARIOS = [
    ("ownership chain (2-hop)",
     ["Bob works at Acme.", "Acme is owned by Globex."],
     "Who owns the company Bob works for?", ["globex"], []),
    ("transitive dependency (2-hop)",
     ["The auth service depends on the token service.", "The token service depends on Redis."],
     "What does the auth service depend on, directly or indirectly?", ["redis", "token"], []),
    ("location via employer (2-hop)",
     ["Nora works at Zenith.", "Zenith's headquarters is in Munich."],
     "Which city does Nora work in?", ["munich"], []),
    ("supplier chain (2-hop)",
     ["We buy our chips from Nvidia.", "Nvidia is based in Santa Clara."],
     "Where is our chip supplier based?", ["santa clara"], []),
    ("team → service ownership (2-hop)",
     ["Dana manages the Platform team.", "The Platform team owns the billing service."],
     "Who manages the team that owns the billing service?", ["dana"], []),
    ("co-membership (2-hop)",
     ["Helen is on the Security team.", "Ivan is on the Security team."],
     "Who is on the Security team with Helen?", ["ivan"], []),
    ("chained acquisition (3-hop)",
     ["Carol founded Initech.", "Initech was acquired by Umbrella.", "Umbrella is headquartered in London."],
     "In which city is the company that acquired Carol's company headquartered?", ["london"], []),
    ("multi-hop supply (3-hop)",
     ["Our supplier is Foxconn.", "Foxconn assembles for Apple.", "Apple is headquartered in Cupertino."],
     "Which city is our supplier's biggest client headquartered in?", ["cupertino"], []),
    ("re-pointed edge — graph supersede (2-hop)",
     ["The API gateway routes to service-v1.", "The API gateway now routes to service-v2, not v1."],
     "Where does the API gateway route?", ["v2"], ["v1"]),
    ("reporting line — graph supersede",
     ["Erin reports to Frank.", "Update: Erin now reports to Grace, not Frank."],
     "Who does Erin report to?", ["grace"], ["frank"]),
    ("relation retract",
     ["ServiceA calls ServiceB.", "ServiceA no longer calls ServiceB."],
     "Does ServiceA call ServiceB?", ["no", "not", "doesn't", "don't", "unknown"], []),
]


def score(text, expect, forbid):
    t = (text or "").lower()
    if any(f in t for f in forbid):
        return "SILENTLY-WRONG"
    if not expect or any(e in t for e in expect):
        return "CORRECT"
    return "MISSED"


def qa(context, question):
    from openai import OpenAI
    system = ("Answer the question using ONLY the facts (entity–relation–entity edges) provided, "
              "chaining across them for multi-hop questions. Edges marked (no longer valid) are "
              "superseded/retracted — not current. If the facts don't support a current answer, say you "
              "don't know. One short sentence.")
    r = OpenAI().chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"EDGES:\n{context or '(none)'}\n\nQUESTION: {question}"}])
    return r.choices[0].message.content.strip()


def summarize(name, results, wl, rl, infra):
    n = len(results)
    return {"system": name, "n": n,
            "silently_wrong": sum(1 for x in results if x["outcome"] == "SILENTLY-WRONG"),
            "correct": sum(1 for x in results if x["outcome"] == "CORRECT"),
            "missed": sum(1 for x in results if x["outcome"] == "MISSED"),
            "write_ms": 1000 * sum(wl) / len(wl) if wl else 0,
            "read_ms": 1000 * sum(rl) / len(rl) if rl else 0, "results": results, "infra": infra}


# ─────────────────────────── Vayl (graph on) ───────────────────────────
def run_vayl():
    db = "/tmp/vayl_graph_h2h.db"
    for suf in ("", ".key", ".salt", ".salt.kdf", ".sign.salt", ".sign.salt.kdf"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    os.environ.update({"VAYL_DB": db, "OPENAI_MODEL": MODEL, "EMBED_MODEL": EMBED,
                       "EMBED_BASE_URL": "https://api.openai.com/v1", "LLM_PROVIDER": "openai",
                       "OPENAI_BASE_URL": "https://api.openai.com/v1", "VAYL_ENCRYPT": "off",
                       "VAYL_SIGN": "off", "VAYL_GRAPH": "1", "NEO4J_PASSWORD": NEO4J_PW})
    import importlib

    from vayl.api import mcp_server
    importlib.reload(mcp_server)
    v = mcp_server
    if v._store.graph is None:
        raise RuntimeError("Vayl graph not attached — check Neo4j + VAYL_GRAPH=1")
    wl, rl, results = [], [], []
    for i, (name, setup, q, expect, forbid) in enumerate(SCENARIOS):
        v._store.graph.wipe()          # per-scenario isolation (graph_query is global)
        user = f"gh_{i}"
        for msg in setup:
            t0 = time.perf_counter(); v.remember(msg, user_id=user); wl.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        m = v._store.load(user)
        _ans, _seeds, edges = m.graph_query(q, hops=HOPS)   # valid edges only (Vayl's reconciliation)
        ctx = "\n".join(f"- {h} {rel} {t}" for h, rel, t in edges)
        ans = qa(ctx, q); rl.append(time.perf_counter() - t0)
        results.append(dict(scenario=name, outcome=score(ans, expect, forbid), answer=ans, retrieved=ctx))
    return summarize("Vayl (graph)", results, wl, rl, "Neo4j projection (optional; SQLite is primary)")


# ─────────────────────────── Graphiti ───────────────────────────
async def run_graphiti():
    from graphiti_core import Graphiti
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client import LLMConfig, OpenAIClient
    from graphiti_core.nodes import EpisodeType
    llm = OpenAIClient(config=LLMConfig(model=MODEL, small_model=MODEL))
    emb = OpenAIEmbedder(config=OpenAIEmbedderConfig(embedding_model=EMBED))
    g = Graphiti(NEO4J_URI, NEO4J_USER, NEO4J_PW, llm_client=llm, embedder=emb)
    wl, rl, results = [], [], []
    try:
        async with g.driver.session() as s:
            await s.run("MATCH (n) DETACH DELETE n")
        await g.build_indices_and_constraints()
        for i, (name, setup, q, expect, forbid) in enumerate(SCENARIOS):
            gid = f"gh_{i}"
            base = datetime.now(timezone.utc)
            for j, msg in enumerate(setup):
                t0 = time.perf_counter()
                await g.add_episode(name=f"{gid}_{j}", episode_body=msg, source=EpisodeType.text,
                                    source_description="graph-h2h", reference_time=base + timedelta(minutes=j),
                                    group_id=gid)
                wl.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            edges = await g.search(q, group_ids=[gid], num_results=15)
            ctx = "\n".join(f"- {e.fact}" + (" (no longer valid)" if (e.invalid_at or e.expired_at) else "")
                            for e in edges)
            ans = qa(ctx, q); rl.append(time.perf_counter() - t0)
            results.append(dict(scenario=name, outcome=score(ans, expect, forbid), answer=ans, retrieved=ctx))
    finally:
        await g.close()
    return summarize("Graphiti", results, wl, rl, "Neo4j server (required)")


def main():
    print(f"Graph head-to-head — {len(SCENARIOS)} multi-hop relational scenarios, model={MODEL}, hops={HOPS}\n")
    systems = []
    print("running Vayl (graph) …", flush=True)
    try:
        r = run_vayl(); systems.append(r)
        for x in r["results"]:
            print(f"  {x['outcome']:16} {x['scenario']}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}"); systems.append({"system": "Vayl (graph)", "error": str(e)})
    print("\nrunning Graphiti …", flush=True)
    try:
        r = asyncio.run(run_graphiti()); systems.append(r)
        for x in r["results"]:
            print(f"  {x['outcome']:16} {x['scenario']}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}"); systems.append({"system": "Graphiti", "error": str(e)})

    os.makedirs("benchmarks/results", exist_ok=True)
    with open("benchmarks/results/graph_headtohead.json", "w") as f:
        json.dump({"model": MODEL, "hops": HOPS, "scenarios": len(SCENARIOS), "systems": systems}, f, indent=2)

    L = ["# Graph head-to-head — Vayl (graph) vs Graphiti", "",
         f"**{len(SCENARIOS)} multi-hop relational scenarios** (Graphiti's home turf), same model "
         f"(`{MODEL}`), same embedder, one shared synthesizer over each system's native graph retrieval. "
         f"Up to {HOPS} hops. Includes graph supersede/retract cases.", "",
         "| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |",
         "|---|---|---|---|---|---|---|"]
    for r in systems:
        if r.get("error"):
            L.append(f"| {r['system']} | — | — | — | — | — | ERROR: {r['error']} |"); continue
        n = r["n"]
        L.append(f"| **{r['system']}** | {r['silently_wrong']}/{n} | {r['correct']}/{n} | {r['missed']}/{n} | "
                 f"{r['write_ms']:.0f} | {r['read_ms']:.0f} | {r['infra']} |")
    L += ["", "## Per-scenario", ""]
    for r in systems:
        if r.get("error"):
            continue
        L.append(f"### {r['system']}")
        for x in r["results"]:
            L.append(f"- **{x['outcome']}** — {x['scenario']}  \n  ↳ `{(x['answer'] or '')[:120]}`")
        L.append("")
    with open("benchmarks/results/graph_headtohead.md", "w") as f:
        f.write("\n".join(L))

    print("\n" + "=" * 72)
    for r in systems:
        if r.get("error"):
            print(f"{r['system']:16} ERROR: {r['error']}"); continue
        print(f"{r['system']:16} silently-wrong={r['silently_wrong']}/{r['n']}  correct={r['correct']}/{r['n']}  "
              f"missed={r['missed']}/{r['n']}  write={r['write_ms']:.0f}ms  read={r['read_ms']:.0f}ms")
    print("=" * 72)
    print("Saved benchmarks/results/graph_headtohead.{json,md}")


if __name__ == "__main__":
    main()
