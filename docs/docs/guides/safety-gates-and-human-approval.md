---
description: >-
  Block unsafe actions, require human approval for risky writes, guarantee
  critical facts are seen, and pin down domain fields.
icon: gear
---

# Safety gates & human approval

Reconciliation keeps memory current. For high-stakes agents — clinical, financial, legal — Vayl adds four guardrails on top.

## Gate an irreversible action

Before your agent acts on a fact, check it:

```python
check_before_act("active_medication", user_id="patient_42")
```

Returns **SAFE**, or **BLOCKED** with the reasons — any of:

* the value is **disputed** (flagged),
* **confidence** is below your bar,
* the fact is **stale** (older than a max age),
* the fact **just changed** and may not be settled.

Tune the policy per call (`min_confidence`, `max_staleness_days`, …).

`safe_recall` combines recall with the gate: it answers **only if** every current fact behind the answer is safe to act on, and otherwise withholds the answer and tells you why. Use it on the path to an action rather than plain `recall`.

## Human approval for risky writes

Some writes are themselves the hazard — _"we should probably stop the warfarin"_ is not an order to stop it. A slot declared `confirm: true` does **not** apply a replacement or removal. It records a **proposal**, leaves the current value standing, and waits:

```
pending_changes()
  -> #42 REMOVE active_medication: 'warfarin 5mg daily'
         said: "stop the warfarin"

confirm_change(42, decided_by="dr_smith")   # applies it, records who decided
reject_change(42, decided_by="dr_smith")    # discards it; current value unchanged
```

* A pending proposal is **flagged, not active**, so it can never present itself as current in a recall.
* A first write to a gated slot isn't blocked (nothing to lose yet).
* A proposal can't be confirmed once the value it would have replaced has itself changed — it was made against state that no longer holds.
* A rejected proposal is kept as history: that someone proposed it is itself worth auditing.

## Critical facts that bypass ranking

Recall is semantic top-k, which is probabilistic. For most memory a low-ranked fact is a quality issue; for an allergy it's a safety issue — it isn't ranked low, it's **invisible**, and the safety gates can't catch what retrieval never surfaced.

Mark categories critical so their facts skip ranking and are **always** included:

```bash
VAYL_CRITICAL_CATEGORIES=allergy,active_medication
```

```python
remember("Patient is allergic to penicillin", metadata={"category": "allergy"})
recall("summarise the chart", critical_categories="allergy")   # per-call override
```

If the critical set exceeds the context budget (`VAYL_CRITICAL_BUDGET`, default 200) the read **raises** rather than silently dropping the tail — dropping part of an always-include set would recreate the exact miss the mechanism exists to prevent.

## Declared slots

By default the extractor names its own slots, which suits open-ended memory. For a domain with known fields, declare them so reconciliation is deterministic (`VAYL_SLOT_SCHEMA=/path/slots.json`):

```json
{"slots": [
  {"name": "active_medication", "category": "critical", "verbatim": true, "confirm": true,
   "description": "a medication the patient is currently taking, with dose and frequency",
   "aliases": ["meds", "current_medication", "prescribed_medication"]}
]}
```

| Property   | Effect                                                                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `aliases`  | every spelling folds onto the canonical name (deterministic — case and separators only), so later statements land in the same slot and reconcile |
| `category` | tags every fact in the slot, feeding the critical-fact channel                                                                                   |
| `verbatim` | store the value exactly as stated (normalization is lossy — fine for a colour, unacceptable for a dose)                                          |
| `confirm`  | route changes through the approval queue above                                                                                                   |

A malformed or missing schema **raises** rather than silently falling back to empty. Empty by default — declare nothing and behaviour is unchanged.

## Next

* [Core concepts](../core-concepts/core-concepts.md) — flagging, state vs. event, and history.
* [Accountability](/broken/pages/U9ze39LzTuGVnuxHdWDY) — prove what was known and decided.
