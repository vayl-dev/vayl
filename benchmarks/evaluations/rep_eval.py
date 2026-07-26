#!/usr/bin/env python3
"""
Stability pass — run the 48-case adversarial set N times to get a silently-wrong
rate with a variance band, not a single lucky number. Also surfaces FLAKY cases
(pass sometimes, fail sometimes) — those are the honest risk, invisible in one run.
    ANTHROPIC_API_KEY=$(cat ak.txt) python3 rep_eval.py [REPS]
"""
import os, sys, concurrent.futures
from collections import defaultdict
from eval_adversarial import CASES, run_case

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 5

def main():
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY")):
        print("Set ANTHROPIC_API_KEY or GROQ_API_KEY."); return
    trials = [(rep, c) for rep in range(REPS) for c in CASES]
    print(f"\n\033[1mSTABILITY — {len(CASES)} cases × {REPS} reps = {len(trials)} trials\033[0m")
    print("=" * 66)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        out = list(ex.map(lambda rc: (rc[0], run_case(rc[1])), trials))

    per_rep_wrong = defaultdict(int)          # rep -> #silently-wrong that pass
    case_wrong = defaultdict(int)             # case id -> #reps it was wrong
    case_cat = {}
    for rep, r in out:
        case_cat[r["id"]] = r["cat"]
        if r["outcome"] == "WRONG":
            per_rep_wrong[rep] += 1
            case_wrong[r["id"]] += 1

    wrongs = [per_rep_wrong[rep] for rep in range(REPS)]
    n = len(CASES)
    rates = [100 * w / n for w in wrongs]
    total_wrong = sum(wrongs); total = len(trials)
    mean = 100 * total_wrong / total
    print(f"\nsilently-wrong per pass (of {n}): {wrongs}")
    print(f"  best  pass : {min(wrongs)}/{n} = {min(rates):.1f}%")
    print(f"  worst pass : {max(wrongs)}/{n} = {max(rates):.1f}%")
    print(f"\n\033[1mSILENTLY-WRONG RATE : {total_wrong}/{total} = {mean:.1f}%\033[0m  (mean over {REPS} reps)")

    if case_wrong:
        print(f"\n\033[33mFLAKY / failing cases (wrong in ≥1 rep):\033[0m")
        for cid, w in sorted(case_wrong.items(), key=lambda x: -x[1]):
            tag = "\033[31mSTABLE-FAIL\033[0m" if w == REPS else "\033[33mflaky\033[0m"
            print(f"  {cid} [{case_cat[cid]}] wrong {w}/{REPS} reps  {tag}")
    else:
        print(f"\n\033[32mZero silently-wrong across all {total} trials.\033[0m")

if __name__ == "__main__":
    main()
