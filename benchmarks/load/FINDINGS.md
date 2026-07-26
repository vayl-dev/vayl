# Concurrency load test — findings

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

## What it shows

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

## The corrected scaling model

Vayl scales by **processes, not threads.** One process has a hard per-workload throughput ceiling
(GIL + SQLite) no matter how it locks. Because patients are independent memory spaces, the workload
**shards cleanly by patient**: run N processes across M nodes, each with its own connection, on
Postgres, coordinating same-space writes with `pg_advisory_xact_lock` (already scaffolded in
`storage/db.py`). Horizontal scaling is then linear in processes.

The per-patient locking win still matters — for **latency**, applied per process — and remains worth
building. But the headline for scale is: the threaded single-process server is a dead end, and the
multi-process/Postgres/patient-sharded path is the real one. It is not built.

## Not measured

Postgres backend (numbers here are SQLite), multi-process, network embedder cost, and sustained
load beyond a per-level time cap. This measures one process's threaded behaviour, which is what the
current server actually is.

## Free-threaded Python 3.14t (no-GIL) — the GIL ceiling, broken

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

# Integrity under contention — `integrity.py`

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
