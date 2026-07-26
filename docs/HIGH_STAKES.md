# Deploying Vayl where a wrong answer causes harm

Written for clinical, financial, and legal deployments — anywhere a stale or missing fact does
damage rather than just annoying someone. It covers what Vayl gives you, how to configure it, and —
at least as importantly — **what it does not give you and would need before it belongs anywhere near
patient care.**

---

## The failure this is really about

Vayl's usual claim is that it won't return a superseded value as current. In a high-stakes domain
there is a second failure mode that matters more, and it is quieter:

> An answer can be **wrong by omission**. The allergy was recorded, it just didn't rank in the top-k,
> so the answer came back confident and incomplete.

The safety gates cannot catch this. `safe_recall` and `check_before_act` evaluate the facts they were
handed — staleness, confidence, unresolved conflicts. A fact lost during *retrieval* is invisible to
them too, so the gate passes on an answer it never knew was incomplete. **A miss is silent**, which is
what makes it dangerous.

Everything below exists to close that gap.

---

## Configuration

### 1. Declare your slots

```bash
export VAYL_SLOT_SCHEMA=/etc/vayl/clinical-slots.json
```

See [`examples/clinical-slots.json`](../examples/clinical-slots.json). Declaring a slot does four
things:

| Property | Why it matters here |
|---|---|
| `aliases` | Folds every spelling onto one canonical name, so `meds`, `current_medication` and `active_medication` reconcile instead of accumulating as three separate slots. Without this the same-slot invariant rarely fires on free text — measured at ~2% subject reuse on real conversational data. |
| `category` | Tags every fact in the slot for the critical channel below. Declared once by an operator rather than remembered by every caller. |
| `verbatim` | `"500mg twice daily"` is stored exactly. Normalization is lossy by design and would drop the dose. |
| `confirm` | Replacing or removing this value is proposed, not applied. |

Matching is **deterministic** — declared names and aliases, folded for case and separators only.
`penicillin_reaction` does *not* fold onto `allergy`. Inferring that two differently-named slots are
"the same" is how a reconciling store silently destroys a fact it should have kept, so that judgement
is declared, never guessed.

A malformed or missing schema raises at startup. Failing open would mean believing your critical
slots are canonicalised and always-injected when they are not — silence on exactly the guarantee the
file exists to provide.

### 2. Turn on the critical channel

```bash
export VAYL_CRITICAL_CATEGORIES=critical
export VAYL_CRITICAL_BUDGET=200        # default
```

Facts in those categories are partitioned out **before** ranking and rejoined after. They occupy no
ranked slots, so they cannot be crowded out by a better-matching but less important fact, and they
are not duplicated when they would have ranked anyway.

If the critical set exceeds the budget, the read **raises `CriticalOverflow`** rather than
truncating. Truncating an always-include set would recreate the exact silent miss it exists to
prevent, in the categories where a miss matters most. Handle it by raising the budget or narrowing
the categories — never by catching and continuing.

### 3. Gate the writes that are themselves hazardous

Slots declared `"confirm": true` don't auto-apply a replacement or removal:

```
pending_changes()                          # the queue, with the sentence that triggered each
confirm_change(id, decided_by="dr_smith")  # applies it
reject_change(id, decided_by="dr_smith")   # discards it; current value stands
```

Build a review surface on those three tools. `decided_by` is the point — an anonymous approval
records that *someone* approved, which is not accountability.

### 4. Ingest from FHIR, and trust the authorized feed

A hospital already emits its structured data as FHIR. Point the adapter at that feed:

```python
from vayl.clinical.fhir import to_facts
from vayl.api import mcp_server as vayl

for fact in to_facts(fhir_bundle):          # AllergyIntolerance, MedicationRequest, Condition, ...
    vayl.remember(fact["value"], metadata={"category": fact.get("category", "")},
                  source="fhir")
```

The mapping's correctness is in its STATUS handling, not its values: a `verificationStatus=refuted`
allergy RETRACTS rather than adds (a disproven allergy must leave the chart), a `status=stopped`
medication retracts, a `clinicalStatus=resolved` condition becomes history. Getting these backwards
is a safety bug, so they are pinned by tests.

Then declare the feed trusted:

```bash
export VAYL_TRUSTED_SOURCES=fhir
```

A FHIR order has already been authorized by a clinician in the EHR, so it applies directly rather
than sitting in the confirmation queue. The queue is reserved for changes inferred from *narrative*
— an LLM reading "we should stop the warfarin" in a note is not an order, and stays gated even when
FHIR is trusted.

**The boundary that matters:** Vayl is not the system of record. The coded allergy and medication
lists live in Epic/Oracle and stay there. Vayl ingests FHIR to build the *reconciling, change-tracking
view over an encounter* — one current truth per slot, with an audit trail — and to fold in the
unstructured clinical narrative (goals-of-care discussions, verbal changes) that never becomes a
coded resource. Write authoritative clinical data back to the EHR, not only to Vayl.

### 5. Scope every principal

```python
create_principal("ward-agent", role="agent", scopes="patient_1041")
```

`user_id` arrives from the caller. Roles answer "may this key read at all", not "whose record". An
unscoped principal reaches every space.

### 6. Use `safe_recall`, not `recall`, on any path to an action

It withholds the answer and returns reasons when a supporting fact is stale, low-confidence, or
flagged. Pass `critical_categories` so the gate judges the complete set.

---

## What Vayl does **not** give you

Stated plainly, because an evaluation shouldn't have to discover these.

**No clinical validation.** There is no measured sensitivity figure for critical-fact recall on a
labelled dataset. The always-inject channel makes a miss *structurally* impossible for tagged
categories — but only for facts that were correctly extracted and tagged in the first place, and that
end of the pipeline is unmeasured.

**Extraction is an LLM and can miss or mis-tag a fact.** If a fact is never extracted, no retrieval
guarantee helps: there is nothing to inject. The source sentence is retained on every fact, but only
for facts that were extracted at all.

**No regulatory posture.** No BAA, no ISO 14971 risk file, no quality system, no FDA/MDR
determination. Clinical decision support that informs treatment may be a regulated medical device.
Get counsel **before** a pilot — the answer shapes everything above.

**Not third-party audited.** The security model is documented in [`SECURITY.md`](../SECURITY.md) and
the compliance mapping in [`COMPLIANCE.md`](../COMPLIANCE.md), but neither has had an external review.

**Never the sole source for a decision.** Vayl is memory, not a clinician.

---

## Suggested posture

1. Start with a scope where a miss is **recoverable** — administrative, operational, scheduling —
   and keep the system out of the clinical decision path.
2. Measure critical-fact recall on your own labelled data before widening. Sensitivity is the metric;
   a false positive is recoverable, a miss is not.
3. Bring in regulatory counsel before the first patient-facing pilot.
4. Widen only after both a measurement and a sign-off, not after either alone.
