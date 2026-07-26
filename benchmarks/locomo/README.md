# LOCOMO — methodology

This harness runs [LOCOMO](https://github.com/snap-research/locomo) against Vayl using the
same pipeline [mem0ai/memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) runs
against Mem0, so that the two sets of numbers can be placed side by side.

Everything below is stated up front rather than buried, because a vendor-run benchmark is
worth exactly as much as its disclosed methodology.

## What is held identical to upstream

| | Value | Why it matters |
|---|---|---|
| Dataset | `locomo10.json`, fetched from the original authors, SHA-256 recorded in every result file | Not redistributed or modified by us |
| Pipeline | Ingest → Search → Evaluate | Same three stages |
| Chunk size | 1 turn per ingestion call | Larger chunks would cut our ingest cost and change extraction |
| Answerer prompt | Reproduced verbatim | A better answerer prompt would inflate our score, not our memory's |
| Judge prompt (parity) | Reproduced verbatim | Same definition of "correct" |
| Cutoffs | top-10 / 20 / 50 / 200 | Same retrieval depths |
| "Correct" | judge score ≥ 0.5 | Same threshold |

Only the memory system under test differs. That is the comparison.

## What we add, and report separately

Two additions. Neither is folded into the parity number — a `--parity` run reproduces
upstream's configuration exactly, and that is the number to compare against theirs.

### 1. The adversarial category

Upstream evaluates categories 1–4 and excludes category 5, *adversarial* — the questions
whose correct answer is that the conversations do not contain the answer.

That is **446 of LOCOMO's 1,986 questions: 22.5% of the benchmark.** The remaining 1,540 is
the denominator in Mem0's published "1425/1540".

The exclusion is not arbitrary; it is required by their pipeline. Their answerer prompt
instructs, at Step 7:

> NEVER say "not specified", "not mentioned", "no record", or "the memories don't say"

An answerer under that instruction fails every adversarial question by construction, so the
category has to go. We keep it, and use an answerer that is permitted to abstain
(`ABSTAIN_ANSWER_PROMPT`). Abstention is not a technicality for an agent memory: a system
that confidently invents a value when it has none is more dangerous than one that says it
does not know, because the invented value gets acted on.

Run `--parity` to exclude it and reproduce their configuration.

### 2. The strict judge

The parity judge is lenient by design. Two of its rules matter here:

> **PARTIAL CREDIT**: If the generated answer includes AT LEAST ONE correct item from the
> gold answer's list, mark CORRECT.
>
> **EXTRA DETAIL IS FINE**: … Never penalize for being more detailed or specific.

Consider a memory that has recorded three values for one fact over time and returns all
three. The answerer writes *"Pro, then Premium, now Free."* Rule 1 marks it CORRECT because
"Free" appears; rule 3 forbids penalising the two superseded values riding along with it.

**The parity judge therefore cannot distinguish a store that returns the current value from
one that returns every value it has ever held.** That distinction is the entire reason a
reconciling memory exists, so measuring Vayl only under that judge would measure everything
except the thing it does.

The strict judge (`STRICT_JUDGE_TEMPLATE`) changes three things:

- **No partial credit.** A list answer needs a majority of the gold items.
- **Abstention is scorable.** "Not mentioned" is correct when the gold answer says so, and
  wrong when it does not.
- **A separate `stale` flag,** independent of correct/wrong. An answer can be CORRECT and
  stale — that combination is precisely what gets counted, and what the parity judge discards.

`--judge-mode both` (the default) runs both judges on every answer and reports both, plus a
staleness rate. The parity number stays the headline so it remains comparable.

## Deliberate non-improvements

Several things could raise Vayl's parity score and were not done, because each would break
comparability:

- The answerer prompt is upstream's, tuned over their iterations against their memory format,
  not ours. We did not adapt it.
- `top_k` defaults to 200. At that depth a strong answerer does much of the work that would
  otherwise fall to retrieval — upstream's own results move only 0.7pp between top-50 and
  top-200, which suggests retrieval is not the binding constraint at these depths. Lowering
  it would be a fairer test of memory and a worse test of parity. Both are reported via the
  cutoff breakdown; read top-10 if you want the retrieval-sensitive number.
- Chunk size stays at 1 turn per call even though Vayl extracts multiple facts per
  observation and would ingest more cheaply at 4.

## Running it

```bash
export OPENAI_API_KEY=sk-...

# smoke — one conversation, deepest cutoff only
python -m benchmarks.locomo.run --project-name smoke --conversations 0-0 --cutoffs 200

# parity — upstream's exact configuration, directly comparable to their published number
python -m benchmarks.locomo.run --project-name parity --parity

# full — both judges, all five categories
python -m benchmarks.locomo.run --project-name full --judge-mode both
```

Cost scales with three multipliers, and a full run is not cheap:

- **Ingest**: 5,882 `add()` calls at chunk size 1 across all 10 conversations, each an
  extraction + reconciliation against existing facts.
- **Answering**: questions × cutoffs. All 1,986 questions at four cutoffs is 7,944 answerer
  calls.
- **Judging**: the same again per judge; `--judge-mode both` doubles it.

Use `--conversations 0-0` and `--cutoffs 200` while iterating, and `--skip-ingest` to re-run
evaluation against an already-populated database. Every result file records the full
`RunConfig` — extraction model, answerer, judge, cutoffs, dataset hash — because a score
without its configuration is not a result.

## Reading a result file

```
benchmarks/results/locomo/locomo_<project>_<timestamp>.json
  config           the RunConfig — everything needed to reproduce
  ingest           per-conversation: chunks, facts stored, facts current, facts retired
  metrics          parity judge: overall, by category, by cutoff
  strict_metrics   strict judge, same shape (judge-mode both)
  staleness        stale / ambiguous counts and rates
  evaluations      per question: retrieved memories, answer and verdict at each cutoff
```

`ingest.stored` vs `ingest.current` is worth looking at on its own. A reconciling store
retires facts as they are superseded, so the two diverge. For an additive store they are
equal by construction — every fact ever written is still live, which is the condition that
produces the ambiguity the strict judge measures.
