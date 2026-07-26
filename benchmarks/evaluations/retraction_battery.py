#!/usr/bin/env python3
"""
Retraction battery — removal WITHOUT replacement, head to head.

§7.6 of the technical report claimed a large retraction gap, but its harness was never committed, so
the number was not reproducible. This is the runnable replacement. It isolates the one operation an
entity-relationship substrate has no natural expression for: a fact simply ENDS and nothing takes its
place ("Alice left Acme", "Acme dropped Sentry"). A supersession replaces a value; a retraction
removes one.

Fairness, matching the rest of the suite:
  * identical model, embedder, and a SINGLE shared answer-synthesizer over each store's native
    retrieval, so what is measured is retrieval + reconciliation, not answer prompt-engineering;
  * **entity-pair phrasing throughout** ("Alice works at Acme", not "we use X") so every statement is
    representable as entity->relation->entity and the graph substrate is not measured out of domain;
  * two CONTROLS that punish over-deletion — a hedged removal that must NOT delete, and a
    replacement that must supersede rather than retract. Retracting too eagerly is a failure too.

Scoring per case:
  SILENTLY-WRONG — the removed value is still returned as current (the trust-killer)
  CORRECT        — the removal is reflected (or, for controls, the still-true value is kept)
  MISSED         — neither: no stale value, but no usable answer either

Run (benchmark venv; Neo4j needed only for Graphiti):
    OPENAI_API_KEY=sk-... BENCH_MODEL=gpt-4o-mini NEO4J_PASSWORD=... \
        PYTHONPATH=. .venv-bench/bin/python benchmarks/evaluations/retraction_battery.py
Writes benchmarks/results/retraction_battery.{json,md}.
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

GONE = ["no longer", "not ", "no one", "nobody", "none", "don't", "doesn't", "do not",
        "unknown", "i don't know", "left", "dropped", "stopped", "removed", "gone"]

# (name, setup, question, expect, forbid, kind)
CASES = [
    ("employment ends", ["Alice works at Acme.", "Alice left Acme."],
     "Where does Alice work?", GONE, ["acme"], "retract"),
    ("vendor dropped", ["Acme uses Sentry for error monitoring.", "Acme dropped Sentry."],
     "What does Acme use for error monitoring?", GONE, ["sentry"], "retract"),
    ("team membership ends", ["Bob is on the Platform team.", "Bob left the Platform team."],
     "Which team is Bob on?", GONE, ["platform"], "retract"),
    ("service dependency ends", ["ServiceA calls ServiceB.", "ServiceA no longer calls ServiceB."],
     "What does ServiceA call?", GONE, ["serviceb", "service b"], "retract"),
    ("hosting stopped", ["Acme hosts on AWS.", "Acme stopped using AWS."],
     "Where does Acme host?", GONE, ["aws"], "retract"),
    ("management ends", ["Carol manages the Data team.", "Carol no longer manages the Data team."],
     "Who manages the Data team?", GONE, ["carol"], "retract"),
    ("tool removed", ["Globex uses Jira for tracking.", "Globex got rid of Jira."],
     "What does Globex use for tracking?", GONE, ["jira"], "retract"),
    ("partnership ends", ["Initech partners with Umbrella.", "Initech no longer partners with Umbrella."],
     "Who does Initech partner with?", GONE, ["umbrella"], "retract"),
    ("ownership ends", ["Dana owns the billing service.", "Dana no longer owns the billing service."],
     "Who owns the billing service?", GONE, ["dana"], "retract"),
    ("support discontinued", ["Acme supports IE11.", "Acme stopped supporting IE11."],
     "Which browser does Acme support?", GONE, ["ie11", "ie 11"], "retract"),
    ("subscription cancelled", ["Zenith subscribes to Datadog.", "Zenith cancelled its Datadog subscription."],
     "What does Zenith subscribe to?", GONE, ["datadog"], "retract"),
    ("reporting line ends", ["Erin reports to Frank.", "Erin no longer reports to Frank."],
     "Who does Erin report to?", GONE, ["frank"], "retract"),
    # ── controls: over-deletion is a failure too ──
    ("CONTROL hedge — must NOT delete", ["Acme uses Redis for caching.", "Acme is considering dropping Redis."],
     "What does Acme use for caching?", ["redis"], [], "control"),
    ("CONTROL replacement — supersede, not retract",
     ["Acme uses Sentry for error monitoring.", "Acme moved from Sentry to Rollbar."],
     "What does Acme use for error monitoring?", ["rollbar"], ["sentry"], "control"),
]


def score(text, expect, forbid, kind="retract"):
    """Scoring has to handle negation, or it scores correct answers as failures.

    A correct retraction answer NAMES the removed thing in order to deny it — "ServiceA does not call
    ServiceB" — so a bare substring test for "serviceb" flags the right answer as stale. For retract
    cases we therefore look for an explicit absence-assertion FIRST, and only count an affirmative
    mention as silently-wrong. Controls are the other way round: the value must be KEPT, so the stale
    value appearing affirmatively is the failure."""
    t = (text or "").lower()
    if kind == "retract":
        if any(e in t for e in expect):        # asserts absence → the removal was reflected
            return "CORRECT"
        if any(f in t for f in forbid):        # names it with no negation → still held as current
            return "SILENTLY-WRONG"
        return "MISSED"
    if any(f in t for f in forbid):
        return "SILENTLY-WRONG"
    if any(e in t for e in expect):
        return "CORRECT"
    return "MISSED"


def qa(context, question):
    from openai import OpenAI
    system = ("Answer using ONLY the facts provided. Facts marked (no longer valid) are retracted and "
              "must NOT be treated as current. If the facts show something ended, say so. If nothing "
              "supports a current answer, say you don't know. One short sentence.")
    r = OpenAI().chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"FACTS:\n{context or '(none)'}\n\nQUESTION: {question}"}])
    return r.choices[0].message.content.strip()


def summarize(name, results, wl, infra):
    n = len(results)
    ret = [r for r in results if r["kind"] == "retract"]
    return {"system": name, "n": n,
            "silently_wrong": sum(1 for r in results if r["outcome"] == "SILENTLY-WRONG"),
            "correct": sum(1 for r in results if r["outcome"] == "CORRECT"),
            "missed": sum(1 for r in results if r["outcome"] == "MISSED"),
            "retract_correct": sum(1 for r in ret if r["outcome"] == "CORRECT"), "retract_n": len(ret),
            "controls_correct": sum(1 for r in results if r["kind"] == "control" and r["outcome"] == "CORRECT"),
            "write_ms": 1000 * sum(wl) / len(wl) if wl else 0, "results": results, "infra": infra}


# ─────────────────────────── Vayl ───────────────────────────
def run_vayl():
    db = "/tmp/vayl_retraction.db"
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
    out, wl = [], []
    for i, (name, setup, q, expect, forbid, kind) in enumerate(CASES):
        user = f"rb_{i}"
        for msg in setup:
            t0 = time.perf_counter(); v.remember(msg, user_id=user); wl.append(time.perf_counter() - t0)
        active = v._store.load(user).active()
        ctx = "\n".join(f"- {s.subject}: {s.value}" for s in active)
        ans = qa(ctx, q)
        out.append(dict(case=name, kind=kind, outcome=score(ans, expect, forbid, kind), answer=ans, retrieved=ctx))
    return summarize("Vayl", out, wl, "SQLite — no server")


# ─────────────────────────── Mem0 ───────────────────────────
def run_mem0():
    import shutil

    from mem0 import Memory
    shutil.rmtree("/tmp/mem0_retraction", ignore_errors=True)
    mm = Memory.from_config({
        "llm": {"provider": "openai", "config": {"model": MODEL, "temperature": 0}},
        "embedder": {"provider": "openai", "config": {"model": EMBED}},
        "vector_store": {"provider": "qdrant", "config": {"path": "/tmp/mem0_retraction", "on_disk": True}}})
    out, wl = [], []
    for i, (name, setup, q, expect, forbid, kind) in enumerate(CASES):
        user = f"rb_{i}"
        for msg in setup:
            t0 = time.perf_counter(); mm.add(msg, user_id=user, infer=True); wl.append(time.perf_counter() - t0)
        res = mm.search(q, filters={"user_id": user}, top_k=10)
        items = res.get("results", res) if isinstance(res, dict) else res
        ctx = "\n".join(f"- {it.get('memory', str(it)) if isinstance(it, dict) else it}" for it in items)
        ans = qa(ctx, q)
        out.append(dict(case=name, kind=kind, outcome=score(ans, expect, forbid, kind), answer=ans, retrieved=ctx))
    return summarize("Mem0", out, wl, "Qdrant vector store")


# ─────────────────────────── Graphiti ───────────────────────────
async def run_graphiti():
    from graphiti_core import Graphiti
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client import LLMConfig, OpenAIClient
    from graphiti_core.nodes import EpisodeType
    llm = OpenAIClient(config=LLMConfig(model=MODEL, small_model=MODEL))
    emb = OpenAIEmbedder(config=OpenAIEmbedderConfig(embedding_model=EMBED))
    g = Graphiti(NEO4J_URI, NEO4J_USER, NEO4J_PW, llm_client=llm, embedder=emb)
    out, wl = [], []
    try:
        async with g.driver.session() as s:
            await s.run("MATCH (n) DETACH DELETE n")
        await g.build_indices_and_constraints()
        for i, (name, setup, q, expect, forbid, kind) in enumerate(CASES):
            gid = f"rb_{i}"
            base = datetime.now(timezone.utc)
            for j, msg in enumerate(setup):
                t0 = time.perf_counter()
                await g.add_episode(name=f"{gid}_{j}", episode_body=msg, source=EpisodeType.text,
                                    source_description="retraction", reference_time=base + timedelta(minutes=j),
                                    group_id=gid)
                wl.append(time.perf_counter() - t0)
            edges = await g.search(q, group_ids=[gid], num_results=15)
            ctx = "\n".join(f"- {e.fact}" + (" (no longer valid)" if (e.invalid_at or e.expired_at) else "")
                            for e in edges)
            ans = qa(ctx, q)
            out.append(dict(case=name, kind=kind, outcome=score(ans, expect, forbid, kind), answer=ans, retrieved=ctx))
    finally:
        await g.close()
    return summarize("Graphiti", out, wl, "Neo4j server (required)")


def main():
    print(f"Retraction battery — {len(CASES)} cases ({sum(1 for c in CASES if c[5]=='retract')} retractions "
          f"+ {sum(1 for c in CASES if c[5]=='control')} controls), model={MODEL}\n")
    systems = []
    for label, fn in [("Vayl", run_vayl), ("Mem0", run_mem0)]:
        print(f"running {label} …", flush=True)
        try:
            r = fn(); systems.append(r)
            for x in r["results"]:
                print(f"  {x['outcome']:16} {x['case']}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}"); systems.append({"system": label, "error": str(e)})
    print("running Graphiti …", flush=True)
    try:
        r = asyncio.run(run_graphiti()); systems.append(r)
        for x in r["results"]:
            print(f"  {x['outcome']:16} {x['case']}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}"); systems.append({"system": "Graphiti", "error": str(e)})

    os.makedirs("benchmarks/results", exist_ok=True)
    with open("benchmarks/results/retraction_battery.json", "w") as f:
        json.dump({"model": MODEL, "cases": len(CASES), "systems": systems}, f, indent=2)

    L = ["# Retraction battery — removal without replacement", "",
         f"**{len(CASES)} cases** ({sum(1 for c in CASES if c[5]=='retract')} retractions + "
         f"{sum(1 for c in CASES if c[5]=='control')} over-deletion controls), model `{MODEL}`, "
         "entity-pair phrasing, one shared synthesizer over each store's native retrieval.", "",
         "| System | Silently-wrong | Retractions correct | Controls kept | Missed | Write avg | Infra |",
         "|---|---:|---:|---:|---:|---:|---|"]
    for r in systems:
        if r.get("error"):
            L.append(f"| {r['system']} | — | — | — | — | — | ERROR: {r['error']} |"); continue
        L.append(f"| **{r['system']}** | {r['silently_wrong']}/{r['n']} | "
                 f"{r['retract_correct']}/{r['retract_n']} | {r['controls_correct']}/2 | "
                 f"{r['missed']}/{r['n']} | {r['write_ms']:.0f} ms | {r['infra']} |")
    L += ["", "## Per-case", ""]
    for r in systems:
        if r.get("error"):
            continue
        L.append(f"### {r['system']}")
        for x in r["results"]:
            L.append(f"- **{x['outcome']}** — {x['case']}  \n  ↳ `{(x['answer'] or '')[:120]}`")
        L.append("")
    with open("benchmarks/results/retraction_battery.md", "w") as f:
        f.write("\n".join(L))

    print("\n" + "=" * 72)
    for r in systems:
        if r.get("error"):
            print(f"{r['system']:10} ERROR: {r['error']}"); continue
        print(f"{r['system']:10} silently-wrong={r['silently_wrong']}/{r['n']}  "
              f"retractions={r['retract_correct']}/{r['retract_n']}  controls={r['controls_correct']}/2")
    print("=" * 72)
    print("Saved benchmarks/results/retraction_battery.{json,md}")


if __name__ == "__main__":
    main()
