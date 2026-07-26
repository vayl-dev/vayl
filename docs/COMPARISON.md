# Vayl vs Mem0 vs Graphiti — head-to-head

A same-scenario, same-model comparison of three memory systems on the question that defines a
*reconciling* memory: **when a fact changes or is withdrawn, does the store still surface the stale
value as current?**

Reproduce (benchmark venv with `mem0ai` + `graphiti-core`, Neo4j running):

```bash
OPENAI_API_KEY=sk-... BENCH_MODEL=gpt-4o-mini NEO4J_PASSWORD=... \
  PYTHONPATH=. .venv-bench/bin/python benchmarks/evaluations/compare_systems.py
# → benchmarks/results/compare_systems.{json,md}
```

## Scale — where the difference becomes decisive

The 10-scenario run above is close on correctness because two updates per fact is easy. The gap opens
under **sustained churn**: 50 users × 4 facts × up to 4 updates = **800 writes, interleaved, then 200
queries for the current value** (`benchmarks/evaluations/scale_bench.py`, same model/embedder/synthesizer).

| System | Silently-wrong | Correct | Stored / current | Write (ms) | Read (ms) | Infra |
|---|---|---|---|---|---|---|
| **Vayl** | **0.0%** (0/200) | 199/200 | **199 / 199** | 2,815 | 1,243 | SQLite — no server |
| **Mem0** | **32.5%** (65/200) | 35/200 | **800 / 800** | 3,836 | 2,214 | Qdrant |
| **Graphiti** (not this axis — relational tool; sampled) | 0/32 | 0/32 | 0 | 9,017 | 1,800 | Neo4j |

**Vayl holds 0% silently-wrong at scale; Mem0 rises to 32.5%.** The cause is visible in the raw store.
For one user's `primary database` (changed 4×), Mem0 kept **five** contradictory memories, all tagged
"as of July 22, 2026":

```
User switched to CockroachDB for their primary database as of July 22, 2026.
User moved their primary database to MySQL as of July 22, 2026.
User uses MongoDB as their primary database as of July 22, 2026.
User's primary database is now SQLite as of July 22, 2026.
...
```

Mem0's `add(infer=True)` appended a new "switched to X" memory on every update instead of retiring the
old value — so it stored **800 / 800** (4× the current facts), and at read time the synthesizer sees
several equally-current values and can't pick the latest. Vayl reconciles **on write**: one active
value per `(subject, scope)`, prior values retired to history — **199 stored / 199 current**, nothing
to disambiguate later. That is the additive-vs-reconciling difference, measured.

**Fairness notes for this run:** Mem0 ran with its standard intelligent mode (`infer=True`) — used as
documented, not nerfed; the accumulation is its real default behavior under same-session churn.
Graphiti's extraction was **unreliable at this model tier** (retrieved nothing for most queries →
0 correct); it is a relational/temporal graph that needs a stronger model, and its ~9 s/write + Neo4j
make large runs impractical — so we sampled it and do **not** claim a quality win over it here. The
decisive, fair head-to-head is Vayl vs Mem0.

## Graph / relational axis — Vayl's graph vs Graphiti (Graphiti's home turf)

The scale test above is slot supersession — Vayl's axis. This one flips it: **11 multi-hop relational
queries** (ownership chains, transitive dependencies, supplier chains, 3-hop acquisition/supply, plus
graph supersede + relation retract), where you must *chain* edges to answer. This is what Graphiti is
built for. Both systems: same model, native graph retrieval, one shared synthesizer, up to 3 hops
(`benchmarks/evaluations/graph_headtohead.py`).

| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |
|---|---|---|---|---|---|---|
| **Vayl (graph)** | **0/11** | **8/11** | 3/11 | 2,964 | 2,710 | Neo4j projection (optional — SQLite is primary) |
| **Graphiti** | 0/11 | 3/11 | 8/11 | 6,675 | 1,561 | Neo4j server (required) |

**Vayl's graph held up well even here** — it answered both 3-hop chains (acquisition → HQ city; supplier
→ client → HQ city) and every clean 2-hop read, at **0% silently-wrong**. That's the honest headline:
Vayl's *optional* graph is competitive on relational queries, not just slot reconciliation.

> **Fixed since the first run.** The first pass had Vayl at 1/11 silently-wrong on a re-pointed edge:
> the slot store superseded correctly, but the LLM named a *different head entity* for the update, so
> the graph's head-keyed retirement missed the stale edge and served it as current. Graph edges are now
> retired **by slot `subject` (head-agnostic)** whenever the slot store supersedes/retracts — so the
> graph can never serve an edge whose slot fact is inactive. Worst case it degrades to an honest "I
> don't know", never the stale value. That re-pointed case is now a MISS, not silently-wrong.

**But read this fairly — it is not "Vayl crushes Graphiti at graphs."** Graphiti's 6 misses were mostly
*extraction* failures at this model tier — it retrieved nothing and said "I don't know" (which its docs
warn about: it wants a stronger model). It was competitive where it *did* extract: both 3-hop cases and
the relation retract (which it got and Vayl fumbled). A run on gpt-4.1/gpt-4o would likely lift
Graphiti's numbers; we did not run that.

**Graph-edge reconciliation — now closed on Vayl's side.** The first run exposed that superseding a
*relationship* depended on the model emitting a consistent head entity; it didn't always, so a stale
edge could linger. That's fixed: Vayl now retires a superseded/retracted slot's edges by `subject`, so
it holds **0/11 silently-wrong** here too. Vayl's one remaining relational miss beyond retrieval gaps is
the **relation retract** (`ServiceA no longer calls ServiceB`), where the *extractor* didn't label the
message RETRACT — an upstream classification issue, not an edge-reconciliation one. Graphiti got that
retract right but was itself model-sensitive on extraction elsewhere.

**Net:** Vayl wins the reconciliation/scale axis decisively; on the relational axis it is competitive-to-
ahead at this model tier, with the caveat that Graphiti is model-sensitive and graph-edge reconciliation
is a soft spot for both. And Vayl delivers the graph as an *optional projection* on top of a reconciling
SQLite store — one file, no required server — where Graphiti *is* a Neo4j deployment.

## Method — how this is kept fair

- **Same model, same embedder for all three:** `gpt-4o-mini` + `text-embedding-3-small`. (gpt-4o-mini
  is the common denominator — Mem0 and Graphiti send `max_tokens`/`temperature`, which the gpt-5
  reasoning models reject. Vayl's own default is `gpt-5-mini`; see the caveats.)
- **Native retrieval per system → one shared synthesizer.** Each store ingests the identical setup
  messages and does its own retrieval; the retrieved facts then go through a **single, identical**
  answer-synthesis prompt. This isolates *retrieval + reconciliation* quality from answer
  prompt-engineering. Graphiti's temporal validity (`invalid_at`/`expired_at`) is passed through as
  `(no longer valid)` annotations so it gets full credit for invalidation.
- **Uniform scoring:** `SILENTLY-WRONG` if the stale value is surfaced as current; `CORRECT` if the
  current value is (and the stale one isn't); `MISSED` if neither.

## Results — 10 scenarios, gpt-4o-mini (single run, with the same-slot invariant)

| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |
|---|---|---|---|---|---|---|
| **Vayl** | **0/10** | **10/10** | 0/10 | ~2,500 | ~1,100 | SQLite file — no server |
| **Mem0** | 1/10 | 7/10 | 2/10 | ~2,600 | ~1,800 | Vector store (Qdrant) |
| **Graphiti** (not this axis) | 0/10 | 4/10 | 6/10 | ~6,200 | ~1,600 | Neo4j server |

### Storage footprint — the structural difference

After all writes: how many facts each store keeps *retrievable as current* vs total held.

| System | Retrievable as current | Total stored | Behavior |
|---|---|---|---|
| **Vayl** | 9 | 9 | reconciling — superseded/retracted facts are **retired out of the active set** |
| **Mem0** | 15 | 15 | additive — superseded facts **remain searchable** unless the LLM chose to delete them |
| **Graphiti** (not this axis) | ~2 | ~2 | temporal graph — extraction was **unreliable on gpt-4o-mini** this run (few edges persisted) |

## What the numbers actually say

**Vayl now holds 0% silently-wrong even on gpt-4o-mini.** An earlier run of this same benchmark had
Vayl leaving two contradictory values active on a slot (`state: redux` *and* `state: zustand`) when a
weak model returned a change as `ADD`. That was a real engine bug, since fixed by a deterministic
**same-slot invariant** (at most one active value per `(subject, scope)`; see
`benchmarks/evaluations/messy_eval.py` → 0% on gpt-4o-mini). Reconciliation quality no longer depends on model
strength.

**Mem0's additive retention is a latent silently-wrong risk that fires on terse updates.** Mem0
*retains* the stale facts (`hosts everything on AWS`, `uses Sentry`, `runway is 18 months`) in its
searchable set; on clean updates it answers correctly only because the shared synthesizer reads Mem0's
rich transition memories (`migrated from AWS to GCP`) and resolves them. When the update *doesn't*
restate the transition — the terse scenarios (`Actually, PostgreSQL.` / `I prefer spaces now.`) — that
resolution fails and the stale value surfaces. Vayl instead *retires* the stale fact on write, so it
never reaches retrieval. This is the structural difference the footprint shows: **Vayl 9 stored / 9
current; Mem0 15 stored / 15 still searchable** — the gap is retained stale facts.

**Graphiti pays the most to write (~6 s/episode, many LLM calls + a Neo4j server) and its extraction
was unreliable on gpt-4o-mini** — most scenarios retrieved nothing, so it couldn't answer (`MISSED`).
Its temporal invalidation is sound *when* it extracts edges; the bottleneck here is extraction
reliability at this model tier. Graphiti's docs recommend a stronger model.

## Honest caveats

- **Single run, small set (10 scenarios), author-written.** Treat as directional, not a leaderboard —
  the *correct/missed* split for Mem0 and Graphiti varies run to run. Vayl's **0 silently-wrong** is
  the exception: it's a deterministic guarantee of the same-slot invariant, not a lucky run.
- **The shared synthesizer *helps* the additive store.** It resolves stale facts Mem0 retains. Without
  it, Mem0's retained-stale would surface directly — so this design is, if anything, generous to Mem0.
- **Model tier matters.** All three ran on `gpt-4o-mini` for fairness. The same-slot invariant means
  Vayl holds 0% silently-wrong here *and* on its default `gpt-5-mini` (0% on the 30-case messy suite,
  `benchmarks/evaluations/messy_eval.py`) — reconciliation quality no longer tracks model strength.
- **Different jobs.** Mem0 is broad additive recall; Graphiti is a relational/temporal knowledge graph;
  Vayl is reconciling slot memory with an optional graph. This benchmark measures one axis they all
  claim — current-value correctness after change — not everything each is good at.
