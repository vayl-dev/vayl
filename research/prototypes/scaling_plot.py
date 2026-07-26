#!/usr/bin/env python3
"""
Scaling proof — consolidation window vs N.
==========================================
Claim under test: with DEDICATED PER-SLOT QUEUES (one queue+worker per topic),
the consolidation window is bounded by the DEEPEST single slot, not by total
writes N. So as memory grows (more writes → more distinct topics), the window
stays FLAT while a single serial worker grows linearly.

Correctness (order preserved within a slot) was proven in parallel_consolidation.py;
here we isolate the TIMING and plot window vs N.

Model: N writes spread across ceil(N/DEPTH) slots (memory diversifies as it grows),
so per-slot depth stays ~constant. LLM cost is simulated small so the benchmark
runs fast — only the SHAPE (linear vs flat) matters and it's latency-independent.
    python3 scaling_plot.py
"""
import time, threading, math
from collections import defaultdict

LATENCY_MS = 6          # simulated per-item consolidation cost (real ≈ 600ms; shape is identical)
DEPTH = 3               # items per slot (bounded — memory diversifies as it grows)
NS = [16, 32, 64, 128, 256]
REPS = 2

def _sleep(): time.sleep(LATENCY_MS / 1000.0)
def now():    return time.perf_counter() * 1000.0

def workload(N, depth=DEPTH):
    """N items across ceil(N/depth) slots, round-robin so arrivals interleave."""
    S = max(1, math.ceil(N / depth))
    items, counts, i = [], [0] * S, 0
    while len(items) < N:
        s = i % S
        if counts[s] < depth:
            items.append((s, counts[s])); counts[s] += 1
        i += 1
    return items, S

def serial_window(items):
    """One worker: consolidate everything in order. Window = N × latency."""
    t0 = now(); store = {}
    for slot, seq in items:
        _sleep()
        store.setdefault(slot, []).append(seq)
    return now() - t0

def per_slot_window(items):
    """Dedicated queue+worker per slot: parallel across slots, ordered within."""
    buckets = defaultdict(list)
    for slot, seq in items:
        buckets[slot].append(seq)               # preserves per-slot order
    lock, store = threading.Lock(), {}
    def worker(slot, seqs):
        for seq in seqs:
            _sleep()
            with lock: store.setdefault(slot, []).append(seq)
    t0 = now()
    threads = [threading.Thread(target=worker, args=(s, q)) for s, q in buckets.items()]
    for t in threads: t.start()
    for t in threads: t.join()
    return now() - t0

def bar(v, vmax, width=46):
    return "█" * max(1, round(v / vmax * width))

def main():
    print("\n\033[1mCONSOLIDATION WINDOW vs N — serial (O(N)) vs dedicated per-slot queues\033[0m")
    print("=" * 72)
    print(f"depth/slot = {DEPTH} · sim latency = {LATENCY_MS} ms/item · {REPS} reps "
          f"(at real 600ms/item, multiply by 100 — same shape)\n")

    rows = []
    for N in NS:
        items, S = workload(N)
        sw = sum(serial_window(items) for _ in range(REPS)) / REPS
        pw = sum(per_slot_window(items) for _ in range(REPS)) / REPS
        rows.append((N, S, sw, pw))

    vmax = max(r[2] for r in rows)   # scale bars to biggest serial window

    print(f"{'N':>5} {'slots':>6}   serial window                              per-slot window")
    print("-" * 72)
    for N, S, sw, pw in rows:
        print(f"{N:>5} {S:>6}   \033[31m{bar(sw, vmax):<46}\033[0m {sw:>6.0f}ms")
        print(f"{'':>12}   \033[32m{bar(pw, vmax):<46}\033[0m {pw:>6.0f}ms   ({sw/pw:.0f}× faster)")
        print()

    print("-" * 72)
    print("\033[1mSHAPE\033[0m")
    print(f"  serial   : {rows[0][2]:.0f} → {rows[-1][2]:.0f} ms as N goes {NS[0]} → {NS[-1]}  "
          f"(\033[31m~{rows[-1][2]/rows[0][2]:.0f}× — grows linearly with N\033[0m)")
    print(f"  per-slot : {rows[0][3]:.0f} → {rows[-1][3]:.0f} ms over the same range  "
          f"(\033[32m~flat — bounded by deepest slot, {DEPTH} items\033[0m)")
    print("\n\033[1mLAW\033[0m: window ≈ (deepest single slot) × latency, INDEPENDENT of N.")
    print("As memory grows, topics grow, per-slot depth stays bounded → window stays flat.")
    print("A single serial consolidator cannot do this; dedicated per-slot queues can —")
    print("and (from parallel_consolidation.py) they preserve reconciliation order, so it's")
    print("fast AND correct. That is the property that separates a toy from an architecture.")
    print("\n\033[2mCaveat: a pathological hot-slot workload (all writes on one topic) collapses to")
    print("serial — the honest bound is the busiest slot, not the average. Real memory is diverse.\033[0m")

if __name__ == "__main__":
    main()
