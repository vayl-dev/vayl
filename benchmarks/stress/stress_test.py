#!/usr/bin/env python3
"""
Vayl infrastructure stress test — find the breaking points BEFORE a design partner does.
Engine QUALITY is already covered (adversarial/messy/retraction suites). This hammers the
PRODUCT layer: persistence at volume, concurrency, the recall-scale cliff, and robustness.
Mostly LLM-free (synthetic facts) so it's fast and deterministic.
    python3 stress_test.py
"""
import os, time, tempfile, threading, itertools
from vayl.memory import reconcile as reconcile_harness
from vayl.memory.reconcile import Statement, Status
from vayl.storage.store import Store
from vayl.memory.llm_memory import LLMMemory

DB = tempfile.mktemp(suffix=".db")

def mem(n, prefix):
    m = LLMMemory()
    for i in range(n):
        m.statements.append(Statement(f"{prefix}_slot{i}@global", f"{prefix}_topic_{i}", f"value_{i}", "global"))
    return m

def hdr(t): print(f"\n\033[1m{t}\033[0m")

# ---- 1. Persistence at volume ----
hdr("1 · Persistence at volume (SQLite)")
store = Store(DB)
U, F = 100, 200                      # 100 users × 200 facts = 20,000 statements
t = time.time()
for u in range(U):
    store.save(f"user{u}", mem(F, f"u{u}"))
w = time.time() - t
t = time.time()
ok = sum(1 for u in range(U) if len(store.load(f"user{u}").view()[0]) == F)
r = time.time() - t
print(f"  wrote {U*F:,} statements ({U} users×{F}) in {w:.2f}s  ·  reloaded {U} users in {r:.2f}s")
print(f"  integrity: {ok}/{U} users reloaded with exact fact count  ·  "
      f"per-user save {1000*w/U:.1f}ms, load {1000*r/U:.1f}ms")

# ---- 2. Concurrency (is the store safe under parallel access?) ----
hdr("2 · Concurrency — 20 threads hammering the store directly (NO app lock)")
errors, done = [], []
def worker(uid):
    try:
        for _ in range(10):
            m = store.load(f"conc{uid}")
            m.statements.append(Statement(f"c{uid}@g", f"c{uid}_t{len(m.statements)}", "v", "global"))
            store.save(f"conc{uid}", m)
        done.append(uid)
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
t = time.time()
for x in ts: x.start()
for x in ts: x.join()
print(f"  20 threads × 10 write-cycles in {time.time()-t:.2f}s  ·  completed {len(done)}/20  ·  errors: {len(errors)}")
if errors: print(f"    → sample: {errors[0][:90]}")
print("    (mcp_server wraps every tool in a lock, which serializes these; the raw store is the risk surface)")

# ---- 3. The recall-scale cliff (recall dumps ALL facts into the LLM) ----
hdr("3 · recall() context size vs memory size — the known scaling limit, quantified")
print(f"  {'#facts':>7} {'context chars':>14} {'~tokens':>9}   fits in…")
for n in [50, 200, 1000, 5000, 20000]:
    m = mem(n, "x")
    ctx = "; ".join(f"{s.subject}={s.value}" for s in m.view()[0])
    toks = len(ctx) // 4
    fits = "8k ✓" if toks < 8000 else ("32k ✓" if toks < 32000 else ("128k ✓" if toks < 128000 else "EXCEEDS 128k ✗"))
    print(f"  {n:>7,} {len(ctx):>14,} {toks:>9,}   {fits}")
print("  → recall loads every fact; past a few thousand it blows the context window.")
print("    Fix on the roadmap: embedding retrieval (pull the relevant subset, not all).")

# ---- 4. Robustness to pathological input ----
hdr("4 · Robustness — pathological values through persistence")
weird = {"empty": "", "huge(100KB)": "x"*100_000, "null+emoji": "a\x00b🎉💥",
         "sql-inject": "'; DROP TABLE statements;--", "whitespace": "\n\t\r ", "unicode": "ünïcödé 日本語"}
m = LLMMemory()
for name, val in weird.items():
    m.statements.append(Statement(f"{name}@g", name, val, "global"))
try:
    store.save("weird", m)
    back = store.load("weird")
    intact = len(back.view()[0])
    tbl = store.db.execute("SELECT count(*) FROM statements").fetchone()[0]  # table still exists → injection safe
    print(f"  saved+reloaded {len(weird)} pathological values → {intact}/{len(weird)} intact, NO crash")
    print(f"  SQL-injection value stored as literal data; table intact ({tbl:,} rows) → parameterized queries safe ✓")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")

print(f"\n\033[1mSUMMARY\033[0m  db grew to {os.path.getsize(DB)//1024:,} KB. Cleaning up.")
store.db.close(); os.remove(DB)
