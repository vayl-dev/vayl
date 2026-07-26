# How Vayl differs — memory that reconciles vs memory that accumulates

Agent memory tends to fall into a few shapes. This explains where Vayl sits and what it trades,
using Vayl's own measured results — not a scoreboard against named products.

## The three shapes

- **Additive memory** — every fact is appended and retrieved by similarity. Simple and broad, but a
  value that *changed* usually stays searchable, so a stale value can rank alongside (or above) the
  current one, and the model has to guess which is true now.
- **Temporal-graph memory** — facts become nodes/edges with validity intervals. This reconciles well
  and answers multi-hop relational questions, but it's heavy (a graph database plus several LLM calls
  per fact), and many designs have no clean notion of *removal* — "we dropped X" leaves X on the graph.
- **Reconciling memory (Vayl)** — reconciles **on write**. A new value supersedes the old, a removal
  retracts, an ambiguous input is flagged rather than guessed, and superseded facts move to history.
  The invariant: **at most one active value per `(subject, scope)`**. "What's true now" is a bounded,
  unambiguous read; the past is still queryable.

## Why the difference shows up at scale

On a single change, most designs get the current value right. The gap opens under **sustained churn** —
the same fact updated many times.

- An **additive** store keeps every "switched to X" it ever saw. After 800 interleaved writes it holds
  ~800 facts, *all still searchable*; at read time several look equally current and the answer can land
  on a stale one. The failure mode is **silent** — a confident, wrong "current" value.
- **Vayl** reconciles each write: one active value per slot, prior values retired to history. After the
  same 800 writes it holds **the current set as active and the rest as history** — nothing to
  disambiguate later.

Vayl's measured result on that churn test (50 users × 4 facts × up to 4 updates → 800 writes, 200
current-value queries; `benchmarks/evaluations/scale_bench.py`): **0% silently-wrong (0/200)**, one file
on disk, no server. An additive baseline's silently-wrong rate *rises* with churn, because retained
stale facts accumulate in the searchable set.

## What Vayl trades

- **Not "more accurate" on a plain contradiction.** With a strong model, most designs reconcile a
  simple "switched X → Y" fine. Vayl's edge is *structural* — the same-slot invariant means even a
  cheaper model can't leave two contradictory values live — plus **correct removal, low cost, and
  running local**.
- **Relational depth is optional, not core.** Vayl is reconciling *slot* memory; for multi-hop
  relational queries it offers an **optional** graph projection (see [`GRAPHRAG.md`](GRAPHRAG.md)) on
  top of the reconciling store — one SQLite file by default, no required graph server.
- **This measures one axis.** Current-value correctness after change is the axis Vayl is built for. A
  broad additive recall system or a dedicated knowledge graph each do things Vayl doesn't try to.

## How the benchmarks are scored (for the reproducible runs in `benchmarks/`)

- **Native retrieval → one shared answer-synthesis prompt**, identical across systems, so the
  measurement isolates *retrieval + reconciliation* quality from answer prompt-engineering.
- **Uniform scoring:** `SILENTLY-WRONG` if a stale value is surfaced as current; `CORRECT` if the
  current value is (and the stale one isn't); `MISSED` if neither.
- **Same model + embedder** for every system in a given run.

## Honest caveats

- Single-run, author-written suites — directional, not a third-party leaderboard. Vayl's **0%
  silently-wrong** is the exception: it's a deterministic property of the same-slot invariant, not a
  lucky run (`benchmarks/evaluations/messy_eval.py` reaches 0% on the 30-case messy suite too).
- Reconciliation quality no longer tracks model strength — the invariant holds on a small model as
  well as Vayl's `gpt-5-mini` default.
- The reproducible comparison harness lives in `benchmarks/` if you want to run your own numbers.
