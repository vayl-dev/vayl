# Load and concurrency findings

Both tests are deterministic and local: the model and embedder are in-process stubs, so there is no
network, API key or LLM, and the numbers measure Vayl's own locking, storage and reconciliation.
Absolute throughput depends on the host; the *shape* across worker counts is the finding.

## Current results (v0.7.0, 2026-09-27)

Host: Apple M2, 8 cores, 8 GB. Python 3.12.13 (GIL on). SQLite. Default arguments.

### `concurrency.py`: throughput and tail latency by locking strategy

`python -m benchmarks.load.concurrency`: 200 patients × 20 seed facts, 30% writes / 70% reads,
10,000 ops per level, a random patient per op.

```
strategy    workers   thr/s   p50ms   p95ms   p99ms  err
global            1    3222     0.2     0.6     1.2    0
global            2    3139     0.2     2.7     6.8    0
global            4    3130     0.2     7.3    18.4    0
global            8    2892     0.2    18.4    44.2    0
global           16    2712     0.3    41.2   107.2    0
global           32    2535     0.3    80.9   297.1    0

perpatient        1    3005     0.2     0.7     0.8    0
perpatient        2    2812     0.6     1.3     2.4    0
perpatient        4    2165     1.6     3.4     7.7    0
perpatient        8     949     7.9    12.8    23.3    0
perpatient       16     703    21.7    33.7    47.9    0
perpatient       32     650    45.0    80.5   108.9    0
```

`perpatient` is the production model: a connection per worker and a lock per memory space. `global`
is one lock around everything, kept as the baseline.

1. **About 6× the throughput of earlier runs.** One worker now does ~3,200 ops/s, against ~550
   before 0.5.0 (see Earlier runs). The decrypt, decoded-embedding and norm caches added in 0.5.0
   removed most of the per-call cost of reloading a memory space.

2. **Threads still don't add throughput.** Neither strategy scales past one worker: one process is
   bound by the GIL (reconciliation is pure-Python CPU work) and by SQLite's single writer.

3. **With per-space locks, throughput falls as workers rise** (3,005 → 650 ops/s from 1 to 32).
   Each worker has its own SQLite connection, so writers queue on SQLite's single write lock and
   the busy timeout, and the switching costs more than the parallel reads gain. The global lock
   keeps throughput flatter (3,222 → 2,535) because only one thread touches the database at a time.

4. **Per-space locks still win on tail latency.** At 32 workers p99 is 109 ms with per-space locks
   against 297 ms with the global lock (48 vs 107 ms at 16). The global lock builds a convoy: p50
   stays at 0.3 ms while a few requests wait behind everyone else.

5. **Practical setting:** a single `vayl-server` process performs best with few concurrent requests.
   For more load, add processes rather than threads (next section).

### `integrity.py`: correctness under contention

`python -m benchmarks.load.integrity`: the real production write path (`space_lock` → load → apply →
save, plus concurrent `audit.record`) on 12 hot spaces × 15 facts, 50% writes, 8,000 ops per level.

```
workers     ops    thr/s   p50ms   p95ms   p99ms   wp50  err  integrity
      1    8000     2258     0.3     0.9     1.1    0.7    0  OK
      4    8000     1541     2.2     5.7     8.7    3.0    0  OK
      8    8000      682    10.8    22.4    32.2   12.6    0  OK
     16    8000      465    31.0    62.6    81.5   37.6    0  OK

final: 10823 statements, audit rows verified=15890 (chain INTACT)
PASS — no corruption under contention
```

1. **No corruption at any level.** Zero errors, no duplicate id within a space, at most one active
   value per subject and scope, and the audit hash chain verified intact over 15,890 concurrent
   appends.
2. **Throughput is ~4.6× the earlier run** (2,258 vs 493 ops/s at one worker), for the same reason:
   the 0.5.0 caches.
3. **Same shape as `concurrency.py`:** contended writes on one SQLite file get slower as workers are
   added, while p99 stays bounded (82 ms at 16 workers, down from 256 ms in the earlier run).

## Scaling model

Vayl scales by **processes, not threads.** Memory spaces are independent, so the workload shards
cleanly by space: run several `vayl-server` processes on Postgres, where same-space writes serialize
on a cross-process advisory lock (`pg_advisory_xact_lock` in `Database.space_lock`) and different
spaces run in parallel. This is built and covered by the Postgres integration tests
(`tests/test_postgres.py`, run in CI against a real Postgres 17).

## Not measured

Postgres throughput, multiple processes, a real network embedder or LLM (in production these
dominate latency: each `remember` and `recall` makes a model call), and sustained load. The
free-threaded (no-GIL) Python results below are from an earlier version and were not re-run.

---

# Earlier runs (before v0.5.0)

Kept for the record. These predate the 0.5.0 caches and, in part, the removal of the global lock, so
their absolute numbers are superseded by the current results above.

## Concurrency load test

Run: `python -m benchmarks.load.concurrency` (deterministic, local, no API). 200 patients × 20
seed facts, 30% writes / 70% reads, random patient per op, rising worker counts. Representative
numbers below (single machine; absolute throughput varies by host, the *shape* is the finding).

```
strategy    workers   thr/s   p50ms   p95ms   p99ms   err
global            1     540     1.7     2.2     2.7     0
global            2     558     1.7     2.2    33.7     0
global            4     496     1.7    19.8   137.2     0
global            8     483     1.8    81.1   288.9     0
global           16     549     1.7     2.2   824.8     0
connper           1     553     1.7     2.2     2.6     0
connper           2     564     3.4     5.7     7.3     0
connper           4     501     7.7    13.2    16.4     0
connper           8     371    21.2    29.4    37.8     0
connper          16     330    46.9    67.9    91.4     0
```
(`connper` = a connection per worker + per-patient locks — the realistic multi-connection model.)

### What it shows

1. **Throughput does not scale with threads.** Both strategies plateau near ~550 ops/s and then
   degrade. The single-process threaded server is GIL-bound: Vayl's reconciliation is pure-Python
   CPU work, and Python threads cannot execute bytecode in parallel. SQLite's single writer caps
   the write fraction on top of that. More threads add context-switch overhead, not capacity.

2. **The global lock's real cost is TAIL LATENCY, not throughput.** Under one lock, p99 explodes to
   825ms at 16 workers while p95 stays at 2ms — a lock convoy: most requests are instant, a few get
   catastrophically stuck behind the lock. Per-patient locking (connper) keeps p99 at 91ms — a 9×
   improvement — because patient A's op never waits on patient B's.

3. **Per-patient locking is not a drop-in.** An earlier version of this test ran per-patient locks
   on the SHARED connection and crashed (hundreds of `InterfaceError: bad parameter or other API
   misuse` + `TypeError` from the process-global id counter being reassigned per load). The global
   lock is load-bearing for CORRECTNESS: it hides (a) a SQLite connection that is not safe for
   concurrent use, and (b) `reconcile._counter`, a module-global id counter that `Store.load`
   reassigns on every call. Both must be fixed (a connection per worker, per-space id allocation)
   before ANY finer locking is safe.

### The corrected scaling model

Vayl scales by **processes, not threads.** One process has a hard per-workload throughput ceiling
(GIL + SQLite) no matter how it locks. Because patients are independent memory spaces, the workload
**shards cleanly by patient**: run N processes across M nodes, each with its own connection, on
Postgres, coordinating same-space writes with `pg_advisory_xact_lock` (already scaffolded in
`storage/db.py`). Horizontal scaling is then linear in processes.

The per-patient locking win still matters — for **latency**, applied per process — and remains worth
building. But the headline for scale is: the threaded single-process server is a dead end, and the
multi-process/Postgres/patient-sharded path is the real one. It is not built.

### Not measured

Postgres backend (numbers here are SQLite), multi-process, network embedder cost, and sustained
load beyond a per-level time cap. This measures one process's threaded behaviour, which is what the
current server actually is.

### Free-threaded Python 3.14t (no-GIL) — the GIL ceiling, broken

Re-ran the same test under a free-threaded CPython 3.14.3 build (`uv python install 3.14t`), GIL
disabled. Vayl's whole dependency tree — cryptography 49, urllib3, and the mcp stack's Rust
extensions (rpds-py, pydantic-core) — has free-threaded wheels, and the full test suite passes with
no code changes. The two-dependency surface is what makes this viable: ecosystem compatibility, the
usual blocker, is a non-issue at this dep count.

```
                     3.12 (GIL)              3.14t (free-threaded)
strategy  workers    thr/s   p99ms           thr/s   p99ms
connper         1      553     2.6             557     2.4
connper         2      564     7.3             951     3.6
connper         4      501    16.4            1134    10.1     ← 2.0x, GIL was flat
connper         8      371    37.8             428    53.8     ← SQLite/core ceiling takes over
global          4      496   137.2             541    11.8     ← lock convoy eased without GIL+lock contention
global          8      483   288.9             529    26.6
```

Findings:

1. **Free-threaded breaks the GIL ceiling.** With per-worker connections + per-patient locks
   (connper), throughput SCALES — 2.0x at 4 workers — where under the GIL it was flat. Reconciliation
   is pure-Python CPU work, and without the GIL it runs in parallel.

2. **The global lock is still a ceiling, GIL or not.** It stays flat (~540) under both interpreters
   because it serialises at the application layer. So the two fixes are COMPLEMENTARY, not
   alternatives: per-patient locking AND free-threaded Python are both required to scale a single
   process; either alone is flat.

3. **Tail latency also improves** even for the global lock (p99 289ms → 27ms at 8 workers): without
   the GIL, threads waiting on the lock no longer also contend for the interpreter.

4. **The ceiling moves, it does not vanish.** connper drops at 8 workers — SQLite's single writer and
   the machine's core count become the limit once the GIL is gone. The full single-node scaling story
   is therefore free-threaded 3.14t + per-patient locks + Postgres (multi-writer), not any one alone.

Caveat (RESOLVED): this run flagged the process-global id counter as a latent race under true
parallelism, and the shared DB connection and the audit hash-chain's append order as two more things
the global lock secretly protected. All three are now handled — ids are allocated per space (seeded
from `MAX(id)` at load), connections are thread-local, and `Audit.record` serializes its own append —
and the global lock has been removed. `integrity.py` (below) is the test that verifies the result.

---

## Integrity under contention — `integrity.py`

Run: `python -m benchmarks.load.integrity`. Unlike `concurrency.py` (which compares synthetic lock
*strategies*), this drives the REAL production write path after the global-lock removal —
`with store.db.space_lock(key): load → apply → save` plus concurrent `audit.record` — and asserts no
corruption. Many workers hammer a small pool of hot spaces so `space_lock` is genuinely contended.

Representative numbers (12 hot spaces, 50% writes; single machine — the *shape* is the finding):

```
                    GIL on (3.12)            GIL off (3.14t)
workers   thr/s   p99ms  integrity   thr/s   p99ms  integrity
   1        493    2.8      OK         496    2.7      OK
   4        402   29.3      OK         697   14.3      OK     ← 1.7x, half the tail
   8        326  102.9      OK         356   74.0      OK
  16        256  256.1      OK         276  230.6      OK
```

Findings:

1. **No corruption under contention — the point of the test.** Across every level: 0 errors, no
   duplicate id within a space, the same-slot invariant (≤1 ACTIVE per subject/scope) held, and the
   audit chain verified INTACT over 12k+ concurrent appends. The three things the global lock secretly
   protected each hold on their own now.

2. **Tail latency is bounded, far below the old global lock.** `concurrency.py`'s global lock hit
   p99 ≈ 397ms at 8 workers; the per-space model keeps p99 to ~75–103ms even in this contended run,
   with no convoy.

3. **Removing the lock is what lets free-threading add throughput.** At 4 workers, GIL-off reaches
   702 ops/s vs 402 with the GIL — because different-space reconciliation now runs in true parallel.
   Under the old global lock, GIL-off bought nothing (it serialized at the app layer regardless).

4. **The single-writer ceiling is unchanged.** At higher worker counts throughput still flattens —
   SQLite's one writer plus reconciliation cost. Scale remains: processes sharded per-space on
   Postgres. This test measures per-process correctness and tail latency, not that ceiling.
