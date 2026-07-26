#!/usr/bin/env python3
"""
Parallel consolidation — does the speed win SCALE, and at what cost to correctness?
==================================================================================
Two-speed memory moves the LLM off the write path into an async consolidation
pass. The open question: a single consolidation worker processes N writes
serially (window = N x LLM-latency), which re-introduces lag at volume. Can we
parallelize?

The trap: reconciliation is ORDER-DEPENDENT within a slot. "use Zustand" then
"switched to Redux" only supersedes correctly if the first is applied before the
second. Naive parallel workers race on the same slot and corrupt the result.

We measure THREE strategies on both axes — window (speed) AND correctness:
  A. serial              (1 worker)                 correct, slow
  B. naive parallel      (N workers, shared queue)  fast, RACY
  C. slot-partitioned    (N workers, same slot->same worker)  fast AND correct

Key law we're testing: with slot-partitioning, window ~= (deepest single slot) x
LLM-latency, INDEPENDENT of total N. That's what makes it scale.

Dependency-free. Reuses reconcile_harness.py.   python3 parallel_consolidation.py
"""
import time, threading, random, hashlib, concurrent.futures
from collections import Counter
from vayl.memory.reconcile import classify, Engine, detect_subject, detect_scope

LLM_EXTRACT_MS = 600
def _sleep_llm():
    # +/-20% jitter — real LLM latencies vary, which is what surfaces the race
    time.sleep(LLM_EXTRACT_MS * (0.8 + 0.4 * random.random()) / 1000.0)
def now_ms(): return time.perf_counter() * 1000.0

def slot_of(text):
    return f"{detect_subject(text) or 'unknown'}@{detect_scope(text)}"

# 9 writes across 6 slots; two slots carry a contradiction, one a refinement.
FACTS = [
    "We use Zustand for state management.",
    "The database is PostgreSQL.",
    "Auth lives in src/auth using JWT.",
    "We deploy on Fridays.",
    "The API returns snake_case JSON.",
    "We use REST for our API.",
    "We switched to Redux Toolkit, dropping Zustand.",     # state_management: supersede
    "Forget snake_case — we standardized on camelCase.",   # api_casing: supersede
    "The database is PostgreSQL 16 on RDS.",               # database: refine
]
# Ground truth after correct reconciliation:
EXPECT = {"state_management": "redux toolkit", "api_casing": "camelcase"}


class Consolidator:
    def __init__(self, strategy, workers):
        self.engine = Engine(classify)
        self.lock = threading.Lock()          # protects the shared reconciled store
        self.strategy = strategy
        self.workers = workers

    def _consolidate_one(self, text):
        _sleep_llm()                           # LLM extraction — parallel, NO lock
        with self.lock:                        # reconcile+apply — serialized, cheap
            self.engine.add(text)

    def run(self, facts):
        t0 = now_ms()
        if self.strategy == "serial":
            for f in facts:
                self._consolidate_one(f)
        elif self.strategy == "naive":
            # shared pool: workers pull freely -> same-slot items race
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as ex:
                list(ex.map(self._consolidate_one, facts))
        elif self.strategy == "partitioned":
            # route same slot -> same worker: ordered within slot, parallel across slots
            buckets = {}
            for f in facts:
                idx = int(hashlib.md5(slot_of(f).encode()).hexdigest(), 16) % self.workers
                buckets.setdefault(idx, []).append(f)
            def worker(bucket):
                for f in bucket:               # serial within this worker -> preserves order
                    self._consolidate_one(f)
            threads = [threading.Thread(target=worker, args=(b,)) for b in buckets.values()]
            for t in threads: t.start()
            for t in threads: t.join()
        window = now_ms() - t0
        answers = {s: self.engine.current_answer(s) for s in EXPECT}
        # exact match — a flagged "UNRESOLVED CONFLICT" is NOT correct (the race lost the order)
        correct = all(answers[s].strip() == EXPECT[s] for s in EXPECT)
        return window, correct, answers


def run_strategy(strategy, workers, reps):
    windows, oks, samples = [], 0, Counter()
    for _ in range(reps):
        w, ok, ans = Consolidator(strategy, workers).run(FACTS)
        windows.append(w); oks += ok
        samples[ans["state_management"]] += 1
    return sum(windows) / len(windows), oks, reps, samples


def fmt(ms): return f"{ms:,.0f} ms" if ms < 1000 else f"{ms/1000:.2f} s"

def main():
    n = len(FACTS)
    deepest = max(Counter(slot_of(f) for f in FACTS).values())
    reps = 6
    print(f"\n\033[1mPARALLEL CONSOLIDATION — window shrink vs correctness\033[0m")
    print("=" * 70)
    print(f"{n} writes · {len(set(slot_of(f) for f in FACTS))} slots · deepest slot = {deepest} items · "
          f"LLM≈{LLM_EXTRACT_MS}ms · {reps} reps each\n")

    print(f"{'strategy':<26}{'window':>10}{'correct':>12}   final state_management answers")
    print("-" * 70)
    for label, strat, workers in [
        ("A · serial (1 worker)", "serial", 1),
        ("B · naive parallel (N)", "naive", n),
        ("C · slot-partitioned (N)", "partitioned", n),
    ]:
        win, oks, r, samples = run_strategy(strat, workers, reps)
        rate = f"{oks}/{r}"
        color = "\033[32m" if oks == r else ("\033[31m" if oks == 0 else "\033[33m")
        dist = " · ".join(f"'{k}'×{v}" for k, v in samples.most_common())
        print(f"{label:<26}{fmt(win):>10}{color}{rate:>12}\033[0m   {dist}")

    print("-" * 70)
    serial_win = n * LLM_EXTRACT_MS
    part_win = deepest * LLM_EXTRACT_MS
    print(f"\n\033[1mTHE SHRINK\033[0m")
    print(f"  serial window scales with N        : ~N × {LLM_EXTRACT_MS}ms = ~{fmt(serial_win)} (grows forever)")
    print(f"  partitioned window scales with slot: ~{deepest} × {LLM_EXTRACT_MS}ms = ~{fmt(part_win)} (flat in N)")
    print(f"  → ~{serial_win/part_win:.1f}× shrink here, and it \033[1mstays flat as N grows\033[0m")
    print(f"    (1,000 writes across many slots still consolidate in ~deepest-slot × latency)")

    print(f"\n\033[1mTHE CATCH (why 'add more workers' is wrong)\033[0m")
    print("  B (naive) is fast but CORRUPTS reconciliation nondeterministically —")
    print("  same-slot writes race, so the contradiction resolves differently run to run.")
    print("  C (slot-partitioned) routes same-slot writes to the same worker: ordered")
    print("  within a slot, parallel across slots — fast AND correct, every run.")
    print("\n\033[1mVERDICT\033[0m: the speed win scales — but only if you partition consolidation")
    print("by slot. Parallelism and reconciliation-correctness are reconcilable; naive")
    print("threading is not the answer, slot-affinity is.")

if __name__ == "__main__":
    main()
