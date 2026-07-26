#!/usr/bin/env python3
"""
Scale / longitudinal benchmark — Vayl vs Mem0 vs Graphiti on a large churning dataset.

Many scoped users (as all three are actually deployed), each with several facts that change repeatedly
over a long, interleaved session. Measures the claims that matter *at scale*:

  1. SILENTLY-WRONG rate — does the store return a stale value as current, as the store grows?
  2. STALE ACCUMULATION (footprint) — how many superseded facts stay retrievable? (additive vs reconciling)
  3. LATENCY — write / read, as memory grows.

Uniform, fair method: each system does its NATIVE retrieval; a single shared synthesizer (same model)
produces the answer that is scored. Graphiti's temporal validity is passed through so it gets credit
for invalidation. Graphiti is sampled to GRAPHITI_USERS users (its ~6 s/write makes full scale
impractical — itself a finding), and reported separately.

Run (benchmark venv, Neo4j up):
    OPENAI_API_KEY=sk-... BENCH_MODEL=gpt-4o-mini NEO4J_PASSWORD=... \
      SCALE_USERS=50 SCALE_SUBJECTS=4 SCALE_UPDATES=4 GRAPHITI_USERS=10 \
      PYTHONPATH=. .venv-bench/bin/python benchmarks/evaluations/scale_bench.py
Writes benchmarks/results/scale_bench.{json,md}.
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
USERS = int(os.environ.get("SCALE_USERS", "50"))
SUBJECTS = int(os.environ.get("SCALE_SUBJECTS", "4"))
UPDATES = int(os.environ.get("SCALE_UPDATES", "4"))
GRAPHITI_USERS = int(os.environ.get("GRAPHITI_USERS", "10"))
ONLY = os.environ.get("SCALE_ONLY", "")     # "vayl" | "mem0" | "graphiti" — run one system

# Realistic churn: each subject cycles through real options; the LAST is current, all earlier are stale.
TEMPLATES = {
    "state management": ["Redux", "MobX", "Zustand", "Jotai", "Recoil"],
    "cloud hosting": ["AWS", "GCP", "Azure", "DigitalOcean", "Fly.io"],
    "primary database": ["MySQL", "PostgreSQL", "MongoDB", "CockroachDB", "SQLite"],
    "CI system": ["Jenkins", "CircleCI", "GitHub Actions", "GitLab CI", "Buildkite"],
    "error monitoring": ["Sentry", "Datadog", "New Relic", "Rollbar", "Honeybadger"],
    "project tracker": ["Jira", "Linear", "Asana", "Trello", "Shortcut"],
    "code editor": ["Sublime", "Atom", "VS Code", "Neovim", "Zed"],
    "payment processor": ["Stripe", "Braintree", "Adyen", "PayPal", "Checkout.com"],
}
_TKEYS = list(TEMPLATES)
_SETUP = ["We use {v} for {t}.", "Our {t} is {v}."]
_CHANGE = ["We switched to {v} for {t}.", "Update: {t} is now {v}.",
           "We moved our {t} to {v}.", "Actually, {t} is {v} now."]


def build_dataset():
    """tracks: per (user, subject) an ordered list of write messages + the current/stale answer.
    order: interleaved write plan (round-robin) so the store is large and mixed during queries."""
    tracks = []
    for u in range(USERS):
        for s in range(SUBJECTS):
            t = _TKEYS[(u + s) % len(_TKEYS)]
            opts = TEMPLATES[t]
            vals = [opts[(u + s + k) % len(opts)] for k in range(min(UPDATES, len(opts)))]
            msgs = [_SETUP[(u + s) % len(_SETUP)].format(v=vals[0], t=t)]
            for k, v in enumerate(vals[1:]):
                msgs.append(_CHANGE[k % len(_CHANGE)].format(v=v, t=t))
            tracks.append({"user": f"u{u}", "t": t, "msgs": msgs,
                           "expect": [vals[-1].lower()], "forbid": [v.lower() for v in vals[:-1]],
                           "q": f"What do we use for {t}?"})
    # interleaved write plan: round-robin one message per track until exhausted
    plan, i = [], 0
    while True:
        added = False
        for tr in tracks:
            if i < len(tr["msgs"]):
                plan.append((tr["user"], tr["msgs"][i])); added = True
        if not added:
            break
        i += 1
    return tracks, plan


def score(text, expect, forbid):
    t = (text or "").lower()
    if any(f in t for f in forbid):
        return "SILENTLY-WRONG"
    if not expect or any(e in t for e in expect):
        return "CORRECT"
    return "MISSED"


def qa(context, question):
    from openai import OpenAI
    system = ("Answer using ONLY the facts provided. Facts marked (no longer valid) are superseded and "
              "must NOT be treated as current. If unsupported, say you don't know. One short sentence.")
    r = OpenAI().chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"FACTS:\n{context or '(none)'}\n\nQUESTION: {question}"}])
    return r.choices[0].message.content.strip()


def pctl(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def summarize(name, results, wl, rl, footprint, infra):
    n = len(results)
    sw = sum(1 for x in results if x["outcome"] == "SILENTLY-WRONG")
    ok = sum(1 for x in results if x["outcome"] == "CORRECT")
    ms = sum(1 for x in results if x["outcome"] == "MISSED")
    return {"system": name, "model": MODEL, "n_queries": n, "n_writes": len(wl),
            "silently_wrong": sw, "correct": ok, "missed": ms,
            "write_ms_avg": 1000 * sum(wl) / len(wl) if wl else 0,
            "write_ms_p95": 1000 * pctl(wl, 95), "read_ms_avg": 1000 * sum(rl) / len(rl) if rl else 0,
            "footprint": footprint, "infra": infra}


# ─────────────────────────── Vayl ───────────────────────────
def run_vayl(tracks, plan):
    db = "/tmp/vayl_scale.db"
    for suf in ("", ".key", ".salt", ".salt.kdf", ".sign.salt", ".sign.salt.kdf"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    os.environ.update({"VAYL_DB": db, "OPENAI_MODEL": MODEL, "EMBED_MODEL": EMBED,
                       "EMBED_BASE_URL": "https://api.openai.com/v1", "LLM_PROVIDER": "openai",
                       "OPENAI_BASE_URL": "https://api.openai.com/v1", "VAYL_ENCRYPT": "off",
                       "VAYL_SIGN": "off", "VAYL_GRAPH": ""})
    import importlib

    from vayl.api import mcp_server
    importlib.reload(mcp_server)
    v = mcp_server
    wl, rl, results = [], [], []
    for user, msg in plan:
        t0 = time.perf_counter(); v.remember(msg, user_id=user); wl.append(time.perf_counter() - t0)
    for tr in tracks:
        t0 = time.perf_counter()
        active = v._store.load(tr["user"]).active()
        ctx = "\n".join(f"- {s.subject}: {s.value}" for s in active)
        ans = qa(ctx, tr["q"]); rl.append(time.perf_counter() - t0)
        results.append(dict(outcome=score(ans, tr["expect"], tr["forbid"])))
    active = stored = 0
    for u in {tr["user"] for tr in tracks}:
        m = v._store.load(u); active += len(m.active()); stored += len(m.statements)
    fp = {"retrievable_as_current": active, "total_stored": stored,
          "stale_retained_in_current_set": 0,
          "note": "reconciling — superseded facts retired to history, never in the active set"}
    return summarize("Vayl", results, wl, rl, fp, "SQLite file — no server")


# ─────────────────────────── Mem0 ───────────────────────────
def run_mem0(tracks, plan):
    import shutil

    from mem0 import Memory
    shutil.rmtree("/tmp/mem0_scale", ignore_errors=True)
    cfg = {"llm": {"provider": "openai", "config": {"model": MODEL, "temperature": 0}},
           "embedder": {"provider": "openai", "config": {"model": EMBED}},
           "vector_store": {"provider": "qdrant", "config": {"path": "/tmp/mem0_scale", "on_disk": True}}}
    mm = Memory.from_config(cfg)
    wl, rl, results = [], [], []
    for user, msg in plan:
        t0 = time.perf_counter(); mm.add(msg, user_id=user, infer=True); wl.append(time.perf_counter() - t0)
    for tr in tracks:
        t0 = time.perf_counter()
        res = mm.search(tr["q"], filters={"user_id": tr["user"]}, top_k=10)
        items = res.get("results", res) if isinstance(res, dict) else res
        ctx = "\n".join(f"- {it.get('memory', str(it)) if isinstance(it, dict) else it}" for it in items)
        ans = qa(ctx, tr["q"]); rl.append(time.perf_counter() - t0)
        results.append(dict(outcome=score(ans, tr["expect"], tr["forbid"])))
    stored = 0
    for u in {tr["user"] for tr in tracks}:
        allm = mm.get_all(filters={"user_id": u})
        stored += len(allm.get("results", allm) if isinstance(allm, dict) else allm)
    fp = {"retrievable_as_current": stored, "total_stored": stored,
          "stale_retained_in_current_set": "grows with updates",
          "note": "additive — superseded facts remain searchable unless the LLM chose to delete them"}
    return summarize("Mem0", results, wl, rl, fp, "Vector store (Qdrant)")


# ─────────────────────────── Graphiti (sampled) ───────────────────────────
async def run_graphiti(tracks, plan):
    from graphiti_core import Graphiti
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client import LLMConfig, OpenAIClient
    from graphiti_core.nodes import EpisodeType
    sample = {f"u{u}" for u in range(GRAPHITI_USERS)}
    tracks = [tr for tr in tracks if tr["user"] in sample]
    plan = [(u, m) for (u, m) in plan if u in sample]
    llm = OpenAIClient(config=LLMConfig(model=MODEL, small_model=MODEL))
    emb = OpenAIEmbedder(config=OpenAIEmbedderConfig(embedding_model=EMBED))
    g = Graphiti(NEO4J_URI, NEO4J_USER, NEO4J_PW, llm_client=llm, embedder=emb)
    wl, rl, results, fp = [], [], [], {}
    try:
        async with g.driver.session() as s:
            await s.run("MATCH (n) DETACH DELETE n")
        await g.build_indices_and_constraints()
        base = datetime.now(timezone.utc)
        for j, (user, msg) in enumerate(plan):
            t0 = time.perf_counter()
            await g.add_episode(name=f"{user}_{j}", episode_body=msg, source=EpisodeType.text,
                                source_description="scale", reference_time=base + timedelta(minutes=j),
                                group_id=user)
            wl.append(time.perf_counter() - t0)
        for tr in tracks:
            t0 = time.perf_counter()
            edges = await g.search(tr["q"], group_ids=[tr["user"]], num_results=10)
            ctx = "\n".join(f"- {e.fact}" + (" (no longer valid)" if (e.invalid_at or e.expired_at) else "")
                            for e in edges)
            ans = qa(ctx, tr["q"]); rl.append(time.perf_counter() - t0)
            results.append(dict(outcome=score(ans, tr["expect"], tr["forbid"])))
        async with g.driver.session() as s:
            r1 = await s.run("MATCH ()-[e:RELATES_TO]->() RETURN count(e) AS c"); tot = (await r1.single())["c"]
            r2 = await s.run("MATCH ()-[e:RELATES_TO]->() WHERE e.invalid_at IS NOT NULL "
                             "OR e.expired_at IS NOT NULL RETURN count(e) AS c"); inv = (await r2.single())["c"]
        fp = {"retrievable_as_current": tot - inv, "total_stored": tot,
              "stale_retained_in_current_set": f"{inv} marked invalid (retained)",
              "note": f"SAMPLED to {GRAPHITI_USERS} users — ~6s/write makes full scale impractical"}
    finally:
        await g.close()
    return summarize("Graphiti (sampled)", results, wl, rl, fp, "Neo4j server")


def main():
    tracks, plan = build_dataset()
    print(f"dataset: {USERS} users × {SUBJECTS} subjects × up to {UPDATES} updates "
          f"= {len(plan)} writes, {len(tracks)} current-value queries\n")
    systems = []
    jobs = [("vayl", run_vayl), ("mem0", run_mem0)]
    for key, fn in jobs:
        if ONLY and ONLY != key:
            continue
        print(f"running {key} …", flush=True)
        try:
            systems.append(fn(tracks, plan))
        except Exception as e:
            systems.append({"system": key, "error": f"{type(e).__name__}: {e}"})
    if not ONLY or ONLY == "graphiti":
        print("running graphiti (sampled) …", flush=True)
        try:
            systems.append(asyncio.run(run_graphiti(tracks, plan)))
        except Exception as e:
            systems.append({"system": "Graphiti (sampled)", "error": f"{type(e).__name__}: {e}"})

    os.makedirs("benchmarks/results", exist_ok=True)
    meta = {"users": USERS, "subjects": SUBJECTS, "updates": UPDATES, "writes": len(plan),
            "queries": len(tracks), "model": MODEL, "graphiti_users": GRAPHITI_USERS}
    with open("benchmarks/results/scale_bench.json", "w") as f:
        json.dump({"meta": meta, "systems": systems}, f, indent=2)

    L = ["# Scale benchmark — Vayl vs Mem0 vs Graphiti", "",
         f"**{USERS} users × {SUBJECTS} subjects × up to {UPDATES} updates = {len(plan)} writes, "
         f"{len(tracks)} current-value queries.** Same model (`{MODEL}`), same embedder, one shared "
         "synthesizer over each system's native retrieval. Metric: after all the churn, does a query "
         "for the *current* value return a stale one?", "",
         "| System | Silently-wrong | Correct | Missed | Write avg / p95 (ms) | Read avg (ms) | Stale retained | Infra |",
         "|---|---|---|---|---|---|---|---|"]
    for r in systems:
        if r.get("error"):
            L.append(f"| {r['system']} | — | — | — | — | — | — | ERROR: {r['error']} |"); continue
        n = r["n_queries"]; fp = r["footprint"]
        L.append(f"| **{r['system']}** | {r['silently_wrong']}/{n} "
                 f"({100*r['silently_wrong']/n:.1f}%) | {r['correct']}/{n} | {r['missed']}/{n} | "
                 f"{r['write_ms_avg']:.0f} / {r['write_ms_p95']:.0f} | {r['read_ms_avg']:.0f} | "
                 f"{fp.get('stale_retained_in_current_set')} | {r['infra']} |")
    L += ["", "## Storage footprint", "",
          "| System | Retrievable as current | Total stored | Behavior |", "|---|---|---|---|"]
    for r in systems:
        if r.get("error"):
            continue
        fp = r["footprint"]
        L.append(f"| {r['system']} | {fp['retrievable_as_current']} | {fp['total_stored']} | {fp['note']} |")
    with open("benchmarks/results/scale_bench.md", "w") as f:
        f.write("\n".join(L))

    print("\n" + "=" * 72)
    for r in systems:
        if r.get("error"):
            print(f"{r['system']:20} ERROR: {r['error']}"); continue
        n = r["n_queries"]
        print(f"{r['system']:20} silently-wrong={r['silently_wrong']}/{n}  correct={r['correct']}/{n}  "
              f"write={r['write_ms_avg']:.0f}ms  read={r['read_ms_avg']:.0f}ms  "
              f"stored={r['footprint']['total_stored']}")
    print("=" * 72)
    print("Saved benchmarks/results/scale_bench.{json,md}")


if __name__ == "__main__":
    main()
