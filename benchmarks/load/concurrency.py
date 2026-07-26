"""
Concurrency & latency load test.

Answers the question the per-operation numbers could not: what happens to throughput and tail
latency when many patients are read and written AT THE SAME TIME. The MCP server serializes every
memory operation under one process-global lock (`_lock = threading.Lock()`), and this measures what
that costs — and what per-patient locking would recover.

It is deterministic and fully local: the embedder is replaced by a fast in-process stub and facts
are fed pre-structured (as a FHIR/EHR feed would), so NO network, NO API key, NO LLM. That isolates
Vayl's own behaviour — reconciliation, storage, and locking — from the external calls that dominate
wall-clock in production. What is left is exactly the part a load test should stress.

Each op targets a random patient (an isolated memory space), mirroring a hospital where thousands of
independent patients are touched concurrently:

  * write : load(patient) → apply one new fact → save(patient)
  * read  : load(patient) → query()

Three locking strategies are compared at rising worker counts:

  global   one shared lock around every op — what the server does today
  perpatient  one lock PER patient — patient A's write never waits on patient B's (the proposed fix)
  none     no app lock — the raw floor (SQLite still serialises its own writes)

What it found (see FINDINGS.md): throughput does NOT scale with threads under any strategy — the
single process is GIL-bound (Python reconciliation) plus SQLite's single writer, capping near a few
hundred ops/s. The global lock's real cost is TAIL LATENCY: p99 explodes into the hundreds of ms
under contention, while per-patient locking holds it ~9x lower. The scaling path is therefore
processes, not threads: shard by patient across processes on Postgres. Per-patient locking helps
latency per process; it is not a drop-in (the shared connection and a process-global id counter are
not thread-safe — the global lock secretly protects both).

    python -m benchmarks.load.concurrency
    python -m benchmarks.load.concurrency --patients 500 --ops 20000 --write-frac 0.3
"""
from __future__ import annotations

import argparse
import random
import statistics
import threading
import time
from collections import defaultdict

from vayl.memory import llm_memory
from vayl.storage import store as store_mod
from vayl.storage.store import Store

# ── deterministic in-process embedder: removes the network variable entirely ──
_DIM = 16


def _fake_embed(texts):
    out = []
    for t in texts:
        h = hash(t)
        out.append([((h >> (i * 3)) & 7) / 7.0 for i in range(_DIM)])
    return out


def _fact(subject, value):
    return {"subject": subject, "value": value, "action": "ADD", "kind": "state",
            "scope": "global", "time_ref": "present", "confidence": 0.95}


DRUGS = ["warfarin", "metformin", "atorvastatin", "lisinopril", "aspirin", "furosemide",
         "metoprolol", "amlodipine", "omeprazole", "levothyroxine"]


def seed(store, patients, facts_each):
    """Pre-populate each patient so reads and reconciliation have a realistic working set."""
    for p in range(patients):
        uid = f"patient_{p}"
        m = store.load(uid)
        for i in range(facts_each):
            m._apply(_fact(f"med_{DRUGS[i % len(DRUGS)]}", f"{DRUGS[i % len(DRUGS)]} {25 * (i + 1)} mg"),
                     "seed")
        store.save(uid, m)


class Locks:
    """The three strategies, behind one interface: a context manager keyed by patient."""

    def __init__(self, strategy):
        self.strategy = strategy
        self._global = threading.Lock()
        self._per = defaultdict(threading.Lock)
        self._per_guard = threading.Lock()

    def for_patient(self, uid):
        if self.strategy == "global":
            return self._global
        if self.strategy == "perpatient":
            with self._per_guard:
                return self._per[uid]
        return _NULL_LOCK


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_NULL_LOCK = _NullLock()


def worker(shared_store, db_path, per_worker_conn, locks, patients, n_ops, write_frac,
           latencies, errors, stop_at):
    rng = random.Random(threading.get_ident())
    # a per-worker connection removes the shared-connection bottleneck (and its thread-safety crash)
    store = Store(db_path) if per_worker_conn else shared_store
    while True:
        if time.perf_counter() >= stop_at or n_ops[0] <= 0:
            return
        n_ops[0] -= 1
        uid = f"patient_{rng.randrange(patients)}"
        is_write = rng.random() < write_frac
        t0 = time.perf_counter()
        try:
            with locks.for_patient(uid):
                m = store.load(uid)
                if is_write:
                    drug = rng.choice(DRUGS)
                    m._apply(_fact(f"med_{drug}", f"{drug} {rng.choice([25, 50, 100])} mg"), "load")
                    store.save(uid, m)
                else:
                    m.query("what medications is the patient on?")
            latencies["write" if is_write else "read"].append((time.perf_counter() - t0) * 1000)
        except Exception as exc:                              # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")


def pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def run_level(store, db_path, strategy, workers, patients, ops, write_frac, max_seconds):
    # "connper" = a connection per worker + per-patient locks (the realistic multi-connection model)
    per_worker_conn = strategy == "connper"
    lock_mode = "perpatient" if strategy == "connper" else strategy
    locks = Locks(lock_mode)
    latencies = {"read": [], "write": []}
    errors = []
    remaining = [ops]
    stop_at = time.perf_counter() + max_seconds
    threads = [threading.Thread(target=worker,
                                args=(store, db_path, per_worker_conn, locks, patients, remaining,
                                      write_frac, latencies, errors, stop_at))
               for _ in range(workers)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    done = len(latencies["read"]) + len(latencies["write"])
    alllat = latencies["read"] + latencies["write"]
    return {
        "workers": workers,
        "ops": done,
        "elapsed_s": elapsed,
        "throughput": done / elapsed if elapsed else 0.0,
        "p50": statistics.median(alllat) if alllat else 0.0,
        "p95": pct(alllat, 95),
        "p99": pct(alllat, 99),
        "read_p50": statistics.median(latencies["read"]) if latencies["read"] else 0.0,
        "write_p50": statistics.median(latencies["write"]) if latencies["write"] else 0.0,
        "errors": len(errors),
        "error_sample": errors[:3],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patients", type=int, default=200)
    ap.add_argument("--facts-each", type=int, default=20, help="seed facts per patient")
    ap.add_argument("--ops", type=int, default=10000, help="ops per (strategy, worker-count)")
    ap.add_argument("--write-frac", type=float, default=0.3)
    ap.add_argument("--workers", default="1,2,4,8,16,32")
    ap.add_argument("--strategies", default="global,perpatient")
    ap.add_argument("--max-seconds", type=float, default=30.0, help="per-level time cap")
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    # replace the embedder EVERYWHERE it is looked up
    store_mod._embed = _fake_embed
    llm_memory._embed = _fake_embed
    llm_memory._qa = lambda ctx, q: (ctx or "")[:80]          # no LLM on the read path either

    import sqlite3
    import tempfile
    db = args.db or (tempfile.mkdtemp() + "/load.db")
    store = Store(db)
    # WAL + a busy timeout: the standard pragmas for concurrent SQLite access. Without these,
    # concurrent readers block writers and "database is locked" is spurious.
    try:
        c = sqlite3.connect(db)
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.close()
    except Exception:
        pass

    print(f"seeding {args.patients} patients × {args.facts_each} facts …", flush=True)
    t0 = time.perf_counter()
    seed(store, args.patients, args.facts_each)
    print(f"  seeded in {time.perf_counter() - t0:.1f}s  (db: {db})\n")

    worker_counts = [int(w) for w in args.workers.split(",")]
    strategies = args.strategies.split(",")

    print(f"workload: {args.write_frac:.0%} writes / {1 - args.write_frac:.0%} reads, "
          f"{args.ops} ops per level, random patient per op\n")
    header = f"{'strategy':<11}{'workers':>8}{'ops':>8}{'thr/s':>9}{'p50ms':>8}{'p95ms':>8}{'p99ms':>8}{'err':>5}"
    print(header)
    print("-" * len(header))

    results = defaultdict(dict)
    for strategy in strategies:
        base_thr = None
        for w in worker_counts:
            r = run_level(store, db, strategy, w, args.patients, args.ops, args.write_frac,
                          args.max_seconds)
            results[strategy][w] = r
            if base_thr is None:
                base_thr = r["throughput"]
            scale = f"{r['throughput'] / base_thr:.1f}x" if base_thr else ""
            print(f"{strategy:<11}{w:>8}{r['ops']:>8}{r['throughput']:>9.0f}"
                  f"{r['p50']:>8.1f}{r['p95']:>8.1f}{r['p99']:>8.1f}{r['errors']:>5}   {scale}")
            if r["error_sample"]:
                print(f"           ! {r['error_sample'][0]}")
        print()

    # ── the verdict ──
    print("=" * 68)
    for strategy in strategies:
        rs = results[strategy]
        lo = rs[worker_counts[0]]["throughput"]
        hi = max(r["throughput"] for r in rs.values())
        gain = hi / lo if lo else 0
        best_w = max(rs, key=lambda w: rs[w]["throughput"])
        print(f"{strategy:<11} throughput {lo:.0f} → {hi:.0f} ops/s "
              f"({gain:.1f}× across {worker_counts[0]}→{worker_counts[-1]} workers, "
              f"best at {best_w})")
    # tail-latency comparison — the real signal
    for strategy in strategies:
        rs = results[strategy]
        hi_w = max(rs)
        print(f"{strategy:<11} p99 at 1 worker {rs[worker_counts[0]]['p99']:.0f}ms "
              f"→ at {hi_w} workers {rs[hi_w]['p99']:.0f}ms")
    print("\nThroughput is flat across worker counts: one process is GIL + SQLite bound. The lock "
          "choice moves TAIL LATENCY, not throughput. Scale by PROCESSES sharded per patient, not "
          "by threads. See benchmarks/load/FINDINGS.md.")


if __name__ == "__main__":
    main()
