#!/usr/bin/env python3
"""
Vayl reconciliation eval — the harness that measures the metric that matters.

Unlike the unit tests (which bypass the LLM), this runs the FULL pipeline — real extractor +
reconciler + recall — against a LABELED dataset and measures the SILENTLY-WRONG rate:
confidently returning a stale or retracted value. That's the trust-killer, and it's
model-dependent, so run it on whatever model you actually deploy.

Usage — set your LLM env first, then run from the repo root:
    # strong model (recommended for real numbers)
    OPENAI_API_KEY=sk-... LLM_PROVIDER=openai OPENAI_MODEL=gpt-4o-mini python benchmarks/evaluations/eval_reconcile.py
    # free local
    LLM_PROVIDER=openai OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 \
        OPENAI_MODEL=qwen2.5:3b EMBED_MODEL=nomic-embed-text python benchmarks/evaluations/eval_reconcile.py
"""
import os
import sys

os.environ.setdefault("VAYL_DB", "/tmp/vayl_eval.db")
if os.path.exists(os.environ["VAYL_DB"]):
    os.remove(os.environ["VAYL_DB"])

# Each scenario: a sequence of statements to remember, then checks on recall.
# expect = the answer must mention one of these (current truth); forbid = it must NOT (stale/retracted).
SCENARIOS = [
    {"name": "supersede — switch tools",
     "setup": ["We use Redux for state management.",
               "We switched from Redux to Zustand for state."],
     "checks": [{"q": "What do we use for state management?", "expect": ["zustand"], "forbid": ["redux"]}]},

    {"name": "retract — drop with no replacement",
     "setup": ["We use Sentry for error monitoring.",
               "We've dropped Sentry; we have no error monitoring tool now."],
     "checks": [{"q": "What error monitoring do we use?",
                 "expect": ["don't", "do not", "none", "no ", "not ", "unknown"], "forbid": ["sentry"]}]},

    {"name": "supersede — cloud migration",
     "setup": ["We host everything on AWS.",
               "We migrated off AWS — we're on GCP now."],
     "checks": [{"q": "Where do we host our infrastructure?", "expect": ["gcp"], "forbid": ["aws"]}]},

    {"name": "supersede — deploy cadence",
     "setup": ["We deploy every Friday.",
               "Update: we deploy continuously now, not just on Fridays."],
     "checks": [{"q": "How often do we deploy?", "expect": ["contin"], "forbid": ["friday"]}]},

    {"name": "scoped coexistence (web vs mobile)",
     "setup": ["We use Redux for state in the web app.",
               "We use Zustand for state in the mobile app."],
     "checks": [{"q": "What state library does the mobile app use?", "expect": ["zustand"], "forbid": []}]},

    {"name": "unchanged fact",
     "setup": ["Our primary database is PostgreSQL."],
     "checks": [{"q": "What database do we use?", "expect": ["postgres"], "forbid": []}]},

    {"name": "multi-fact in one message",
     "setup": ["We use Stripe for payments and SendGrid for transactional email."],
     "checks": [{"q": "What do we use for payments?", "expect": ["stripe"], "forbid": []},
                {"q": "What do we use for email?", "expect": ["sendgrid"], "forbid": []}]},

    {"name": "hypothetical must not be stored",
     "setup": ["We use REST for our API.",
               "What if we moved the API to GraphQL?"],
     "checks": [{"q": "What API style do we use?", "expect": ["rest"], "forbid": ["graphql"]}]},
]


def _llm_reachable():
    try:
        from vayl.memory.llm_memory import llm_extract_classify
        llm_extract_classify("ping", [])
        return True
    except Exception as e:
        print(f"\nLLM not reachable ({type(e).__name__}: {e}).")
        print("Set your LLM env (see the module docstring) and re-run.\n")
        return False


def main():
    if not _llm_reachable():
        sys.exit(1)
    from vayl.api import mcp_server as v

    total = correct = silently_wrong = missed = 0
    print(f"\nVAYL RECONCILIATION EVAL — model={os.environ.get('OPENAI_MODEL') or os.environ.get('GROQ_MODEL') or '?'}")
    print("=" * 72)
    for i, sc in enumerate(SCENARIOS):
        user = f"eval_{i}"
        for stmt in sc["setup"]:
            v.remember(stmt, user_id=user)
        print(f"\n{sc['name']}")
        for chk in sc["checks"]:
            ans = v.recall(chk["q"], user_id=user).lower()
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
            print(f"           A: {ans[:100]}")

    print("\n" + "=" * 72)
    print(f"correct={correct}  missed={missed}  silently-wrong={silently_wrong}  (of {total})")
    rate = 100 * silently_wrong / total if total else 0
    color = "\033[32m" if rate == 0 else "\033[31m"
    print(f"{color}SILENTLY-WRONG RATE: {silently_wrong}/{total} = {rate:.1f}%\033[0m   (the trust-killer — must be ~0)")
    print("=" * 72)


if __name__ == "__main__":
    main()
