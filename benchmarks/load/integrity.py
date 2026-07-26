"""
Integrity-under-contention load test.

The sibling `concurrency.py` compares synthetic lock STRATEGIES on the Store to measure what the
old process-global lock cost. This one is different in purpose: it drives the EXACT production path
the MCP write tools use after that lock was removed, and asserts that concurrency did not CORRUPT
anything. A fast run that forked the audit chain or collided ids is a FAILURE here, not a success.

The production write path, hammered by many threads at once:

    with store.db.space_lock(space_key):        # the real lock — in-process on SQLite,
        m = store.load(space)                    #   cross-process advisory lock on Postgres.
        m._apply(fact)                           # per-space id counter, seeded from MAX(id);
        store.save(space)                        #   connection is thread-local under the hood.
    audit.record(...)                            # hash-chained, self-serializing append

It deliberately CONTENDS: many workers hammer a SMALL pool of hot spaces, so `space_lock` is fought
over rather than spread thin. Then it checks the invariants the dropped global lock used to protect:

  * 0 errors
  * no duplicate statement id within any space         — per-space id allocation stayed correct
  * <=1 ACTIVE value per (subject, scope) per space    — same-slot invariant held under concurrency
  * audit.verify_chain() ok, every row present         — the chain never forked

…and reports throughput / tail latency alongside. It is deterministic and fully local: the embedder
and QA are in-process stubs, so there is NO network, NO API key, NO LLM — this isolates Vayl's own
locking and storage behaviour from the external calls that dominate wall-clock in production.

    python -m benchmarks.load.integrity
    python -m benchmarks.load.integrity --spaces 12 --ops 8000 --write-frac 0.5 --workers 1,4,8,16

Run it under a free-threaded build to see the lock removal actually buy parallelism:
    PYTHONGIL=0 python -m benchmarks.load.integrity      # (on a 3.13t/3.14t interpreter)

Exit code is non-zero if any level detects corruption, so it can gate CI.
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import tempfile
import threading
import time

from vayl.memory import llm_memory
from vayl.security.audit import Audit
from vayl.storage import store as store_mod
from vayl.storage.store import Store

# ── deterministic in-process stubs: no network, no API key, no LLM ──
_DIM = 16


def _fake_embed(texts):
    out = []
    for t in texts:
        h = hash(t)
        out.append([((h >> (i * 3)) & 7) / 7.0 for i in range(_DIM)])
    return out


store_mod._embed = _fake_embed
llm_memory._embed = _fake_embed
llm_memory._qa = lambda ctx, q: (ctx or "")[:80]          # no LLM on the read path either

DRUGS = ["warfarin", "metformin", "atorvastatin", "lisinopril", "aspirin",
         "furosemide", "metoprolol", "amlodipine", "omeprazole", "levothyroxine"]


def _fact(subject, value):
    return {"subject": subject, "value": value, "action": "ADD", "kind": "state",
            "scope": "global", "time_ref": "present", "confidence": 0.95}


def pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def seed(store, spaces, facts_each):
    """Pre-populate each space so reads and reconciliation have a realistic working set."""
    for p in range(spaces):
        uid = f"patient_{p}"
        m = store.load(uid)
        for i in range(facts_each):
            m._apply(_fact(f"med_{DRUGS[i % len(DRUGS)]}", f"{DRUGS[i % len(DRUGS)]} {25 * (i + 1)} mg"),
                     "seed")
        store.save(uid, m)


def worker(store, audit, spaces, remaining, write_frac, latencies, errors, stop_at, rng_seed):
    rng = random.Random(rng_seed)
    while True:
        if time.perf_counter() >= stop_at or remaining[0] <= 0:
            return
        remaining[0] -= 1
        uid = f"patient_{rng.randrange(spaces)}"
        key = f"default\x1f{uid}\x1f\x1f"                  # _space_key(uid) — tenant\x1fuser\x1fagent\x1frun
        is_write = rng.random() < write_frac
        t0 = time.perf_counter()
        try:
            if is_write:
                with store.db.space_lock(key):            # THE production write path
                    m = store.load(uid)
                    drug = rng.choice(DRUGS)
                    m._apply(_fact(f"med_{drug}", f"{drug} {rng.choice([25, 50, 100])} mg"), "load")
                    store.save(uid, m)
                audit.record("remember", uid, detail=drug)  # concurrent hash-chain append
            else:
                m = store.load(uid)                       # read: no lock, thread-local connection
                m.query("what medications is the patient on?")
            latencies["write" if is_write else "read"].append((time.perf_counter() - t0) * 1000)
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")


def check_integrity(store, audit):
    """The invariants the dropped global lock used to protect. Returns (problems, chain_report)."""
    problems = []
    db = store.db
    dupes = db.execute(
        "SELECT user_id, id, COUNT(*) c FROM statements "
        "GROUP BY tenant_id,user_id,agent_id,run_id,id HAVING c > 1").fetchall()
    if dupes:
        problems.append(f"DUPLICATE IDS in {len(dupes)} (space,id) pairs, e.g. {dupes[:3]}")
    slot = db.execute(
        "SELECT user_id, subject, scope, COUNT(*) c FROM statements WHERE status='ACTIVE' "
        "GROUP BY tenant_id,user_id,agent_id,run_id,subject,scope HAVING c > 1").fetchall()
    if slot:
        problems.append(f"SAME-SLOT VIOLATION: {len(slot)} slots with >1 ACTIVE, e.g. {slot[:3]}")
    rep = audit.verify_chain()
    if not rep["ok"]:
        problems.append(f"AUDIT CHAIN BROKEN at {rep['broken_at']}: {rep['reason']}")
    return problems, rep


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spaces", type=int, default=12, help="hot patients (small = high contention)")
    ap.add_argument("--facts-each", type=int, default=15, help="seed facts per space")
    ap.add_argument("--ops", type=int, default=8000, help="ops per worker-count level")
    ap.add_argument("--write-frac", type=float, default=0.5)
    ap.add_argument("--workers", default="1,4,8,16")
    ap.add_argument("--max-seconds", type=float, default=18.0, help="per-level time cap")
    args = ap.parse_args()

    db = tempfile.mkdtemp() + "/integrity.db"
    store = Store(db)
    audit = Audit(store.db)                    # shares the DB — mirrors mcp_server's _audit
    gil_fn = getattr(sys, "_is_gil_enabled", None)
    gil = "n/a(<3.13)" if gil_fn is None else ("off" if not gil_fn() else "on")
    print(f"python {sys.version.split()[0]}  GIL={gil}  db={db}")
    print(f"seeding {args.spaces} patients x {args.facts_each} facts ...", flush=True)
    seed(store, args.spaces, args.facts_each)

    print(f"\nworkload: {args.write_frac:.0%} writes, {args.ops} ops/level, {args.spaces} hot spaces "
          f"(contended)\n")
    hdr = (f"{'workers':>8}{'ops':>8}{'thr/s':>9}{'p50ms':>8}{'p95ms':>8}{'p99ms':>8}"
           f"{'wp50':>7}{'err':>5}  integrity")
    print(hdr)
    print("-" * len(hdr))

    any_fail = False
    for w in [int(x) for x in args.workers.split(",")]:
        latencies = {"read": [], "write": []}
        errors = []
        remaining = [args.ops]
        stop_at = time.perf_counter() + args.max_seconds
        threads = [threading.Thread(target=worker,
                                    args=(store, audit, args.spaces, remaining, args.write_frac,
                                          latencies, errors, stop_at, i)) for i in range(w)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - t0
        done = len(latencies["read"]) + len(latencies["write"])
        alllat = latencies["read"] + latencies["write"]
        problems, _rep = check_integrity(store, audit)
        ok = not problems and not errors
        any_fail = any_fail or not ok
        print(f"{w:>8}{done:>8}{done / elapsed if elapsed else 0:>9.0f}"
              f"{statistics.median(alllat) if alllat else 0:>8.1f}{pct(alllat, 95):>8.1f}"
              f"{pct(alllat, 99):>8.1f}"
              f"{statistics.median(latencies['write']) if latencies['write'] else 0:>7.1f}"
              f"{len(errors):>5}  {'OK' if ok else 'FAIL'}")
        if errors:
            print(f"         ! {errors[0]}")
        for p in problems:
            print(f"         ! {p}")

    problems, rep = check_integrity(store, audit)
    total = store.db.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
    print(f"\nfinal: {total} statements, audit rows verified={rep['verified']} "
          f"(chain {'INTACT' if rep['ok'] else 'BROKEN'})")
    if problems or any_fail:
        print("FAIL — corruption detected under contention:\n  " + "\n  ".join(problems or ["see levels above"]))
        sys.exit(1)
    print("PASS — no corruption under contention (per-space ids, same-slot invariant, and "
          "audit chain all held)")


if __name__ == "__main__":
    main()
