# GraphRAG and Vayl — where each one belongs

**Short version:** GraphRAG answers *"what does our corpus say?"* Vayl answers *"what is true for this
customer right now?"* They are different jobs, and most enterprise agents need both. Vayl is not a
GraphRAG system and does not try to be — it is the layer a GraphRAG stack has no answer for: facts
that **change**.

---

## Two different questions

| | GraphRAG | Vayl |
|---|---|---|
| Question it answers | What does the corpus say? | What is true right now? |
| Input | Documents — contracts, wikis, tickets, reports | Statements — "we moved off Redux", "Alice left" |
| Corpus behaviour | Largely **static**; documents don't contradict themselves mid-conversation | **Churns** constantly; today's value replaces yesterday's |
| Core operation | Retrieve + summarise across many chunks | Reconcile: supersede, retract, flag |
| Scale shape | 10⁴–10⁶ nodes, one shared corpus | Hundreds of facts, **per user/agent/tenant** |
| Failure that matters | Missing a relevant passage | Returning a **stale value as current** |

A document store has no reason to implement supersession, because a PDF does not stop being true when
a later PDF disagrees — both are simply *in the corpus*. Agent memory is the opposite: when a customer
downgrades, the old plan must stop being returned.

## What Vayl does **not** do

Stated plainly, so an evaluation doesn't waste your time:

- **No document ingestion or chunking.** Vayl ingests statements, not files. There is no loader.
- **No community detection or hierarchical summarisation** — so no *global* search. Vayl cannot
  answer "what are the main themes across these 10,000 documents?"
- **No corpus-scale graph.** Vayl's optional Neo4j projection is a bounded, per-tenant structure. We
  have measured it to **50k edges** (3.7 s bulk ingest, ~14 ms per edge write, ~34 ms 2-hop
  traversal). That is a healthy per-tenant graph; it is not a corpus knowledge graph, and we make no
  claim there.

If those are your requirements, the honest recommendation is a dedicated GraphRAG or knowledge-graph
system. Vayl sits beside those, not against them.

## The gap GraphRAG leaves

The moment a team points its graph or vector stack at *changing* facts — using it as the agent's
memory rather than its library — it inherits a failure mode the architecture was never designed to
handle. We measured it.

**Under sustained churn** (50 users × 4 facts × 4 revisions = 800 interleaved writes, then 200
current-value queries; identical model, embedder, and answer-synthesizer for every system):

| System | Returns a stale value | Facts stored / actually current |
|---|---:|---:|
| **Vayl** | **0.0%** (0/200) | **199 / 199** |
| Additive vector store | **32.5%** (65/200) | **800 / 800** |

An additive store appends a new memory on every update instead of retiring the old one — 800 memories
for 200 facts. For a single user's `primary database` (revised four times) it held **several
contradictory values**, all timestamped the same day. At read time the reader sees several
equally-current answers and cannot choose. The ambiguity compounds with every revision; a reconciling
store stays flat at one value per fact.

**On removal without replacement** — "we dropped Sentry", "Alice left" — across 14 cases including two
controls that must *not* delete:

| System | Stale value returned | Removals handled | Over-deletion controls kept |
|---|---:|---:|---:|
| **Vayl** | **0/14** | **12/12** | **2/2** |
| Additive vector store | 1/14 | 11/12 | 2/2 |
| Temporal graph | 3/14 | 10/12 | **0/2** |

Two honest notes on that table. A temporal graph **retracts well** (10 of 12) — an earlier claim of ours
that graph stores couldn't retract at all did not survive a proper benchmark, and we corrected it. Where
that approach struggled was the opposite direction: it deleted a still-true fact on a hedged *"considering
dropping Redis"*, and returned a superseded value on a replacement. Deleting too eagerly is a failure too,
which is why the controls are in the suite.

## How they compose

```
                    ┌──────────────────────────────┐
   "What do our  →  │  GraphRAG / vector store     │   documents, policies,
    contracts say?" │  (corpus knowledge)          │   contracts, wikis
                    └──────────────────────────────┘
   agent
                    ┌──────────────────────────────┐
   "What plan is →  │  Vayl (reconciling memory)   │   per-customer state
    this customer   │  supersede · retract · flag  │   that changes
    on now?"        └──────────────────────────────┘
```

They answer different questions in the same turn, and neither needs to know about the other:

```python
from vayl.api import mcp_server as vayl

# corpus knowledge — your existing GraphRAG stack, unchanged
policy = graphrag.query("What is our refund policy for annual plans?")

# current state — Vayl
vayl.remember("Customer moved from Pro to the Free plan", user_id="cust_5521")
plan = vayl.recall("what plan is the customer on?", user_id="cust_5521")   # -> "Free"
```

The agent composes both. The refund policy comes from the corpus; the customer's *current* plan comes
from memory that reconciled the downgrade. Ask a GraphRAG index the second question after three plan
changes and you get whichever chunk ranks highest.

## Which do you need?

- **Only GraphRAG** — your facts live in documents and rarely change (policy Q&A, contract search,
  research over a fixed corpus).
- **Only Vayl** — your agent tracks state per user/account/project and there is no document corpus
  (support bots, personal assistants, ops copilots, sales agents).
- **Both** — an enterprise agent that answers from company knowledge *and* remembers each customer's
  evolving situation. This is the common case, and it is where using one tool for both jobs hurts:
  the corpus layer has no supersession, so the state answers go stale.

## Honest notes

These benchmarks are **vendor-run** — we build Vayl. To limit the bias we fixed the model, embedder
and answer-synthesizer across all systems, ran each comparison system in its documented default
configuration, and released the harnesses so the numbers can be re-run
(`benchmarks/evaluations/scale_bench.py`, `retraction_battery.py`, `compare_systems.py`). Results are
single-run and run-to-run variance is visible, so small differences should not be over-read. We also
report where we do **not** win: on clean, low-churn supersession the approaches are close, and on
multi-hop relational queries a dedicated graph had the fastest reads in the study. The advantage we can
defend is narrow and specific — **churn, removal, ambiguity, and cost** — not "better memory".
