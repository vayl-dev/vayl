---
description: >-
  For agents where a wrong answer causes harm, memory must be correct under
  change, gated by a human, and provable after the fact. Vayl ships all three.
---

# Auditable, gated memory for high-stakes AI agents

**When an agent operates where a wrong answer causes harm — clinical, financial, legal — memory needs three things ordinary agent memory doesn't have: it must be correct under change, gated by a human on risky writes, and provable after the fact.** Vayl ships all three today.

## Correct under change

The reconciling core — one active value per fact, removal first-class, history preserved — so a superseded dose, plan, or clause never resurfaces as current.

## Gated by a human where it matters

* **Confirm-gated slots.** Declare fields (e.g. `active_medication`) so a change or removal is recorded as a _proposal_, not applied — a human `confirm_change` / `reject_change` decides, and _who decided_ is recorded.
* **Critical-fact channel.** Tag categories (allergies, key covenants) `critical` so they **bypass ranking** and always surface — an omission here is a safety failure, not a quality one.
* **Act-time gates.** `check_before_act` / `safe_recall` withhold or block when a fact is disputed, stale, or low-confidence, instead of proceeding on shaky data.

## Provable after the fact

* **Signed decision snapshots.** `record_decision` binds an action to the exact facts the agent saw; `explain_decision` reconstructs it later, verified unaltered.
* **Tamper-evident audit chain.** Hash-chained, Ed25519-signed; `verify_audit` catches any edit, reorder, or truncation; anyone can verify offline with the public key.
* **GDPR built in.** `delete` / `delete_all` hard-erase with signed receipts; `export_memory` for DSARs; retention purges keep the chain verifiable.

## Honest boundaries

Vayl is secure-by-default for on-prem / self-hosted use, but it is **not independently audited or penetration-tested**, and makes no "compliant" guarantee — it gives you the _controls_; your DPO and security review sign off. It runs in your environment; nothing phones home.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-hospital" style="color:$primary;">:hospital:</i> Tutorial: a hospital assistant</h4></td><td>A gated, auditable clinical assistant, end to end.</td><td><a href="../guides/tutorial-a-hospital-medication-assistant.md">tutorial-a-hospital-medication-assistant.md</a></td></tr><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>Confirm gates, critical facts, and act-time checks in depth.</td><td><a href="../guides/safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Signed decisions, attestations, and the audit chain.</td><td><a href="../mcp-tools/accountability.md">accountability.md</a></td></tr></tbody></table>
