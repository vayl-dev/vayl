#!/usr/bin/env python3
"""
Vayl GRAPH-recall eval — exercises the Neo4j entity-graph path (recall_related / graph_query),
the multi-hop/relational memory, not the slot recall that eval_reconcile.py covers.

Measures the same trust metric: the SILENTLY-WRONG rate (confidently returning a stale/retracted
relation) across multi-hop questions that require chaining edges.

Needs a running Neo4j (VAYL_GRAPH=1) and an embedder for the edge vector index. Run from the repo
root with OpenAI embeddings:
    VAYL_GRAPH=1 NEO4J_PASSWORD=... LLM_PROVIDER=openai OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-5-mini \
        EMBED_BASE_URL=https://api.openai.com/v1 EMBED_MODEL=text-embedding-3-small \
        PYTHONPATH=. python benchmarks/evaluations/eval_graph.py

NOTE: the graph is single-tenant in the MVP — this wipes it for a fair number, so point it at a
disposable Neo4j (not one holding data you care about).
"""
import os
import sys

os.environ.setdefault("VAYL_DB", "/tmp/vayl_graph_eval.db")
if os.path.exists(os.environ["VAYL_DB"]):
    os.remove(os.environ["VAYL_DB"])

# Multi-hop / relational scenarios — each needs the graph to CHAIN edges to answer.
SCENARIOS = [
    {"name": "ownership chain (2-hop)",
     "setup": ["Bob works at Acme.", "Acme is owned by Globex."],
     "checks": [{"q": "Who owns the company Bob works for?", "expect": ["globex"], "forbid": []}]},

    {"name": "transitive dependency (2-hop)",
     "setup": ["The auth service depends on the token service.",
               "The token service depends on Redis."],
     "checks": [{"q": "What does the auth service depend on, directly or indirectly?",
                 "expect": ["redis", "token"], "forbid": []}]},

    {"name": "graph supersede — reporting line changed",
     "setup": ["Alice reports to Carol.", "Update: Alice now reports to Dave, not Carol."],
     "checks": [{"q": "Who does Alice report to?", "expect": ["dave"], "forbid": ["carol"]}]},

    {"name": "location via employer (2-hop)",
     "setup": ["Acme's headquarters is in Berlin.", "Bob works at Acme."],
     "checks": [{"q": "Which city does Bob work in?", "expect": ["berlin"], "forbid": []}]},

    {"name": "chained acquisition (3-hop)",
     "setup": ["Carol founded Initech.", "Initech was acquired by Umbrella.",
               "Umbrella is headquartered in London."],
     "checks": [{"q": "In which city is the company that acquired Carol's company headquartered?",
                 "expect": ["london"], "forbid": []}]},

    {"name": "relation retract",
     "setup": ["ServiceA calls ServiceB.", "ServiceA no longer calls ServiceB."],
     "checks": [{"q": "Does ServiceA call ServiceB?",
                 "expect": ["no", "not", "don't", "does not", "doesn't", "unknown"], "forbid": []}]},
]


def _llm_reachable():
    try:
        from vayl.memory.llm_memory import llm_extract_classify
        llm_extract_classify("ping", [])
        return True
    except Exception as e:
        print(f"\nLLM not reachable ({type(e).__name__}: {e}). Set your LLM env and re-run.\n")
        return False


def main():
    if os.environ.get("VAYL_GRAPH", "").lower() not in ("1", "true", "yes"):
        print("Set VAYL_GRAPH=1 (and NEO4J_* if not default) to run the graph eval.")
        sys.exit(1)
    if not _llm_reachable():
        sys.exit(1)
    from vayl.api import mcp_server as v
    if v._store.graph is None:
        print("No graph attached — check Neo4j is up and VAYL_GRAPH=1.")
        sys.exit(1)
    v._store.graph.wipe()   # clean slate for a fair number (single-tenant MVP)

    total = correct = silently_wrong = missed = 0
    print(f"\nVAYL GRAPH-RECALL EVAL — model={os.environ.get('OPENAI_MODEL')}  embed={os.environ.get('EMBED_MODEL')}")
    print("=" * 72)
    for i, sc in enumerate(SCENARIOS):
        user = f"geval_{i}"
        for stmt in sc["setup"]:
            v.remember(stmt, user_id=user)
        print(f"\n{sc['name']}")
        for chk in sc["checks"]:
            ans = v.recall_related(chk["q"], user_id=user).lower()
            total += 1
            bad = [f for f in chk["forbid"] if f in ans]
            good = [e for e in chk["expect"] if e in ans] if chk["expect"] else ["(any)"]
            if bad:
                silently_wrong += 1
                mark = f"\033[31mSILENTLY-WRONG\033[0m (returned {bad})"
            elif good:
                correct += 1
                mark = "\033[32mCORRECT\033[0m"
            else:
                missed += 1
                mark = "\033[33mMISSED\033[0m (no stale value, but didn't find the answer)"
            print(f"  {mark}  Q: {chk['q']}")
            print(f"           A: {ans[:110]}")

    print("\n" + "=" * 72)
    print(f"correct={correct}  missed={missed}  silently-wrong={silently_wrong}  (of {total})")
    rate = 100 * silently_wrong / total if total else 0
    color = "\033[32m" if rate == 0 else "\033[31m"
    print(f"{color}SILENTLY-WRONG RATE: {silently_wrong}/{total} = {rate:.1f}%\033[0m   (the trust-killer — must be ~0)")
    print("=" * 72)


if __name__ == "__main__":
    main()
