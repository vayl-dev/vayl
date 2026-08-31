---
description: >-
  Build a clinical assistant that keeps a patient's meds and allergies current,
  never misses a critical fact, requires approval to stop a drug, and leaves an
  audit trail — a guided, end-to-end example.
icon: hospital
---

# Tutorial: a hospital medication assistant

Let's build something real. We'll wire Vayl into a **clinical assistant** that helps a care team track a patient's medications and allergies across a hospital stay. Along the way you'll use most of what makes Vayl different — reconciliation, declared slots, the critical-fact channel, the confirmation gate, safe recall, and the audit trail — and, more importantly, you'll see _why_ each one matters when a wrong answer can hurt someone.

{% hint style="warning" %}
This is a software tutorial, not medical guidance. Vayl **presents and safeguards** facts; a clinician decides. It is not a certified clinical system.
{% endhint %}

## What we're building

A patient, **Dorothy Vance**, is admitted. Our assistant will:

1. record her home medications and allergies on admission,
2. keep the medication list **current** as doses change and drugs are stopped,
3. make sure her **allergies are never missed**, even in a long chart,
4. require a **clinician to approve** any change or stop to a medication,
5. **withhold** an answer on the path to an action if a fact is unsafe or disputed,
6. leave a **signed, tamper-evident record** of what was known and decided.

Every patient is an isolated [memory space](../core-concepts/memory-spaces.md), so everything is scoped to `user_id="patient_dorothy"`.

## Step 0 — model the clinical fields with declared slots

By default Vayl lets the extractor name its own slots. For a clinical domain we want **deterministic** reconciliation, **verbatim** doses (never normalise `5 mg` away), medications and allergies handled as **lists**, and changes to medications **gated behind a human**. We get all of that by declaring the fields. Vayl ships an example at `examples/clinical-slots.json`; the important slots are:

```json
{
  "slots": [
    {
      "name": "allergy",
      "description": "a substance the patient reacts to, and the reaction",
      "category": "critical", "verbatim": true, "multi": true, "confirm": false,
      "aliases": ["allergies", "drug_allergy", "known_allergies"]
    },
    {
      "name": "active_medication",
      "description": "a medication the patient is currently taking, with dose and frequency",
      "category": "critical", "verbatim": true, "multi": true, "confirm": true,
      "aliases": ["current_medication", "medication", "meds"]
    }
  ]
}
```

Read those four flags carefully — each encodes a clinical decision:

| Flag                 | What it does                                   | Why it matters here                                                                                                                                        |
| -------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `verbatim`           | store the value exactly as stated              | normalising `warfarin 5 mg PO daily` could change the dose                                                                                                 |
| `category: critical` | tag every fact in the slot                     | feeds the critical-fact channel so meds/allergies bypass ranking                                                                                           |
| `multi: true`        | the slot holds a **list**                      | a patient has several meds and several allergies; a new one **adds**, it doesn't replace the others                                                        |
| `confirm`            | changes/removals are **proposed**, not applied | on `active_medication` a stop/change needs sign-off; on `allergy` it's `false`, because surfacing a _new_ allergy is safety-positive and must be immediate |

Point Vayl at the schema and mark the critical category:

```bash
VAYL_SLOT_SCHEMA=/abs/clinical-slots.json
VAYL_CRITICAL_CATEGORIES=critical
```

{% hint style="info" %}
A missing or malformed schema **raises** rather than silently falling back to open slots — so you never _believe_ a critical slot is canonicalised when it isn't.
{% endhint %}

## Step 1 — connect

We'll drive Vayl from a script with the Python MCP client (see Calling Vayl from code). A tiny helper keeps every call scoped to Dorothy and readable:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PATIENT = "patient_dorothy"
params = StdioServerParameters(
    command="vayl-mcp",
    env={
        "OPENAI_API_KEY": "sk-…",
        "VAYL_DB": "/abs/hospital.db",
        "VAYL_SLOT_SCHEMA": "/abs/clinical-slots.json",
        "VAYL_CRITICAL_CATEGORIES": "critical",
    },
)

async def main():
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        async def call(tool, **args):
            res = await s.call_tool(tool, {"user_id": PATIENT, **args})
            return res.content[0].text

        # … the steps below go here …

asyncio.run(main())
```

Everything from here is `await call("tool", …)`.

## Step 2 — admission: record home meds and allergies

On admission a nurse takes the Best Possible Medication History. Each line becomes a fact; because the slots are declared, Vayl files them in `active_medication` / `allergy` automatically and tags them `critical`:

```python
await call("remember", text="Home med: warfarin 5 mg PO daily",     source="bpmh")
await call("remember", text="Home med: metformin 500 mg PO twice daily", source="bpmh")
await call("remember", text="Allergic to penicillin (rash)",         source="bpmh")
```

Because `active_medication` is `multi`, warfarin and metformin **coexist** — the second med doesn't overwrite the first. `source="bpmh"` records _where_ each fact came from, which matters later when a pharmacy feed or a clinician disagrees.

{% hint style="info" %}
These are **first** writes to the gated slot — nothing to lose yet — so they apply directly. The confirm gate only kicks in when you later _change or remove_ an existing value (Step 4).
{% endhint %}

## Step 3 — the current picture

```python
print(await call("recall", question="what medications is the patient on?"))
# -> warfarin 5 mg PO daily; metformin 500 mg PO twice daily
```

This reads only the **active** set — the current list, not every med ever mentioned.

## Step 4 — a dose change needs approval (the confirm gate)

Day two, the team wants to increase the warfarin. Watch what happens:

```python
await call("remember", text="Increase warfarin to 7 mg PO daily", source="dr_smith")
print(await call("pending_changes"))
# -> #12 REPLACE active_medication: 'warfarin 5 mg PO daily' -> 'warfarin 7 mg PO daily'
#          said: "Increase warfarin to 7 mg PO daily"
```

The dose change was **not applied**. `active_medication` is `confirm`-gated, so Vayl recorded a **proposal** and left 5 mg standing. This is the point of the gate: an LLM reading _"increase the warfarin"_ in a note is not the same as a clinician ordering it. A clinician approves (or rejects) it explicitly:

```python
await call("confirm_change", memory_id=12, decided_by="dr_smith")
print(await call("recall", question="what is the warfarin dose?"))   # -> 7 mg PO daily
print(await call("history", subject="active_medication"))            # 5 mg -> 7 mg, with who decided
```

Note the change landed on **warfarin** specifically — metformin is untouched — because within a `multi` list Vayl reconciles by the item's identity (the drug), not the whole list.

## Step 5 — a newly discovered allergy (immediate, and never missed)

Mid-stay the patient reacts to a sulfa drug. Allergy is `multi` and **not** confirm-gated — surfacing a new allergy is safety-positive, so it applies at once and **adds** to the list:

```python
await call("remember", text="New allergy: sulfamethoxazole (hives)", source="dr_jones")
```

Now the safety-critical part. A chart can hold dozens of facts, and ordinary recall is semantic top-k — a fact that ranks low is _invisible_. For an allergy that's not a quality bug, it's a safety failure. Because allergies are tagged `critical`, they **bypass ranking** and are always injected:

```python
print(await call("recall", question="give me a one-line chart summary",
                 critical_categories="critical"))
# the penicillin AND sulfa allergies are guaranteed to appear, even in a long chart
```

## Step 6 — stopping a medication (gated again)

A stop is a removal — also gated on `active_medication`:

```python
await call("forget", text="Stop the metformin", source="dr_smith")
print(await call("pending_changes"))
# -> #21 REMOVE active_medication: 'metformin 500 mg PO twice daily'   said: "Stop the metformin"
```

The drug is still active until someone decides. If the team decides against it:

```python
await call("reject_change", memory_id=21, decided_by="dr_smith")   # metformin stays
```

Either way the proposal is kept in history — that someone proposed stopping a drug is itself worth auditing.

## Step 7 — don't act on an unsafe fact

Before the assistant acts — say, drafting a discharge script — gate the read. `safe_recall` answers **only if** every fact behind the answer is safe to act on; otherwise it withholds and says why:

```python
print(await call("safe_recall",
                 question="what is the current medication plan?",
                 block_on_flagged=True, max_staleness_days=1))
# answers with the plan, OR: "withheld — a value is disputed / stale"
```

Use `check_before_act(subject="active_medication", ...)` the same way before an irreversible step. A disputed (flagged) value, low confidence, or a just-changed fact all block the action instead of letting it proceed on shaky ground.

## Step 8 — accountability

When a clinician acts, capture _why_ — bound to the exact facts they saw:

```python
await call("record_decision",
           action_summary="Discharge on warfarin 7 mg daily; metformin continued",
           question="what medications is the patient on?")
```

That snapshot is signed and immutable, so months later `explain_decision` can show what was believed **at the time**, even after the meds change again. At discharge, issue a signed attestation of the med list and verify the trail:

```python
print(await call("attest", subject="active_medication"))   # signed "as of now" attestation
print(await call("verify_audit"))                          # -> INTACT
```

A third party can later check that attestation with `export_public_key()` alone — no database, no secret.

## What you leaned on

| Requirement                      | Vayl feature                                                            |
| -------------------------------- | ----------------------------------------------------------------------- |
| meds stay current as they change | reconciliation + `multi` per-drug lists                                 |
| allergies never missed           | the **critical-fact channel** (`category: critical`)                    |
| a human approves changes/stops   | **confirm-gated** declared slots + `pending_changes` / `confirm_change` |
| don't act on shaky data          | `safe_recall` / `check_before_act`                                      |
| prove what was known and decided | `record_decision`, `attest`, the signed audit chain                     |

That's a genuinely safety-aware assistant, and almost all of it is configuration plus the standard tools — the guarantees live in the engine, not in prompt wording.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>The gating mechanisms used here, in depth.</td><td><a href="safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Core concepts</h4></td><td>Why a superseded value can't come back.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> MCP tools</h4></td><td>Every tool used above, with its arguments.</td><td><a href="../mcp-tools/">mcp-tools</a></td></tr></tbody></table>
