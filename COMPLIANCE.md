# Vayl — EU Compliance Deployment Guide

For a Data Protection Officer / privacy counsel evaluating Vayl for deployment that processes
**EU residents' personal data**, in the **on-premise / self-hosted** model (your organization runs
Vayl inside your own environment).

> **This is not legal advice.** It is a technical mapping of Vayl's features to the obligations you
> are likely to face, and an honest split of who is responsible for what. Your DPO / counsel makes
> the final determination for your specific processing. Vayl provides *building blocks*; it does not,
> and cannot, make your organization compliant.

## Roles (this is the foundation)

In the on-prem model:

| Party | Role under GDPR | Consequence |
|---|---|---|
| **Your organization** (the deployer) | **Data controller** | You carry the obligations below. |
| **Vayl** (the software vendor) | **Not a processor** — it runs on your infrastructure and the vendor never receives your data | No controller↔vendor DPA needed for the local deployment. |
| **Your chosen LLM/embedding provider** *(only if cloud)* | **Sub-processor** | You need a DPA + a transfer basis with them. **Avoided entirely if you use the local model** (Vayl's default). |

## The two decisions that dominate your compliance

1. **LLM choice = your international-transfer position.**
   - **Local model (Vayl's default): personal data never leaves the machine.** No sub-processor, no
     Art. 44 transfer, strong data residency. This is the compliant-by-default configuration.
   - **Cloud model (e.g. US-hosted): memory text is sent to that provider.** That provider becomes a
     sub-processor; you need a DPA and a **Chapter V transfer basis** (adequacy, EU-US Data Privacy
     Framework certification, or SCCs + a Transfer Impact Assessment). For EU data, prefer a local or
     EU-hosted model.
2. **EU AI Act risk class = your *use case*, not Vayl.** A memory layer isn't classified on its own;
   the *system it's part of* is. See "EU AI Act" below.

## GDPR — obligations and the responsibility split

| GDPR requirement | Owner | What Vayl provides |
|---|---|---|
| **Lawful basis** (Art. 6) — consent / legitimate interest / contract | **You** | — |
| **Transparency / privacy notice** (Art. 13–14) | **You** | — |
| **Data-subject access** (Art. 15) | **You** run the process | `list_memories`, `get_memory`, `history` |
| **Rectification** (Art. 16) | **You** | `update_memory` (audit-preserving) |
| **Erasure / right to be forgotten** (Art. 17) | **You** | `delete(subject)` / `delete_all()` — **hard delete**, history and graph included |
| **Restriction / objection** (Art. 18, 21) | **You** | scope isolation via `user_id`/`agent_id`/`run_id` |
| **Portability / access** (Art. 20, 15) | **You** | ✅ `export_memory` — machine-readable JSON: statements (active + history) **plus decision snapshots, audit entries, and receipts** about the subject |
| **Accountability** (Art. 5(2)) | **Shared** | ✅ append-only `audit_log` — who / what / when, for every data operation |
| **Storage limitation** (Art. 5(1)(e)) | **You** set the policy | ✅ `purge_expired` — hard-delete records older than N days; flags extend it to audit / decisions / receipts (audit chain stays verifiable via a signed anchor) |
| **Security of processing** (Art. 32) | **Shared** | **encryption at rest** (on by default), auth, localhost-only, no telemetry, parameterized queries |
| **Records of Processing** (Art. 30) | **You** | data-flow description (this doc + `SECURITY.md`) |
| **DPIA** (Art. 35) — likely required for AI profiling / large-scale | **You** | inputs: architecture, data-flow, security controls, this doc |
| **Processor agreement** (Art. 28) | **You**, *only if* using a cloud LLM | local default avoids it |
| **Breach notification** (Art. 33–34, 72h) | **You** | — |
| **DPO** (Art. 37), if required | **You** | — |
| **Privacy by design & default** (Art. 25) | **Shared** | local-first, erasure built in, encrypted, minimal egress |

### Storage limitation & retention (Art. 5(1)(e)) — read this carefully
Vayl is **event-sourced**: `forget` **retracts but retains history** (auditable), while
`delete`/`delete_all` **hard-erase** everything. These are different tools for different obligations:
- Map a **retention/minimization** policy to periodic `delete` of stale subjects.
- Map a **data-subject erasure request** to **`delete`/`delete_all`**, *not* `forget` — `forget`
  keeps the value in history, which does **not** satisfy Art. 17.

Vayl provides `purge_expired(older_than_days)` to enforce a retention window (and `audit_log` records
every erasure as evidence). You still set and document the *policy*; schedule the purge accordingly.

### Erasure vs. accountability — a tension to resolve explicitly
`delete`/`delete_all` hard-erase the **facts**, but the **audit log and erasure receipts deliberately
retain an (encrypted) reference to the subject** — that retention *is* the accountability feature
(Art. 5(2)), and it is in tension with erasure (Art. 17). Decide and document one of:
- **Encrypted retention** — the audit/receipt detail is Fernet ciphertext at rest (pseudonymized),
  retained under Art. 17(3)(b)/(e) (legal obligation / legal claims) for a defined period; **or**
- **Crypto-shredding** — destroy the encryption key to render the retained references unrecoverable
  (effective erasure of the audit detail too); **or**
- **Redaction** — purge/anonymize the audit entries for that subject.

The signed **erasure receipt** proves the facts were deleted; the retained (encrypted) audit reference
is your accountability trail — choose its fate per your retention policy, don't leave it implicit.

**Decision snapshots are redacted on erasure.** Decision records snapshot the beliefs behind an
action — including the personal values. `delete`/`delete_all` therefore also **redact the erased
values from those snapshots**: the decision rows and summaries remain (the accountability record of
*what* was decided), the value is replaced with an explicit redaction marker, the decision is
**re-signed** so verification still passes, and the redaction itself is recorded in the audit log.
Retention for the append-only tables exists too: `purge_expired(include_audit / include_decisions /
include_receipts)` — the audit chain stays verifiable across purges via a **signed retention anchor**.

### Special-category data (Art. 9)
If the agent may store health, biometric, racial, political, religious, sexual-orientation, or
trade-union data, that is special-category: you generally need **explicit consent** (or another Art. 9
condition) and heightened safeguards. Vayl's encryption-at-rest helps with Art. 32 but does **not**
provide the legal basis.

### Automated decision-making (Art. 22)
If Vayl's memory feeds **automated decisions with legal or similarly significant effects**, data
subjects have rights to human intervention and to contest. Vayl gives you concrete controls for this
(see "Trust-layer controls" below): the **safety gate** (`check_before_act` / `safe_recall`) is a
testable human-in-the-loop trigger — it refuses to act on disputed/low-confidence/stale memory and
routes to a human — while **belief provenance** and **`explain_decision`** supply the "meaningful
information about the logic" a data subject can be given, and the basis on which they contest.

## EU AI Act

Vayl is a component of an AI system. Your obligations depend on how you use it:

- **Roles:** you are typically the **deployer** (and possibly **provider** if you build the agent);
  the foundation-model vendor is the **GPAI provider**. Obligations differ per role.
- **Risk classification (your use case decides):**
  - **Prohibited** practices (e.g. social scoring, certain biometric categorization) — do not deploy.
  - **High-risk** (Annex III: employment, credit, education, essential services, biometric,
    law-enforcement, migration): triggers conformity assessment, risk management, logging, human
    oversight, technical documentation. If your agent operates here, the **system** is high-risk.
    Several of these duties map to Vayl's trust layer — **logging/traceability (Art. 12)** → the
    tamper-evident audit + decision records; **human oversight (Art. 14)** → the safety gate;
    **transparency (Art. 13)** → provenance/`explain_decision`; **accuracy (Art. 15)** →
    reconciliation. See "Trust-layer controls" below. These *support* the obligations; they don't
    discharge them.
  - **Limited-risk** (a chatbot / assistant interacting with people): **transparency** — you must
    inform users they are interacting with AI, and label AI-generated content where applicable.
  - **Minimal-risk:** most back-office memory use — no specific AI Act obligations.
- **GPAI obligations** sit mainly with the **model provider**. Using a **local open model** reduces
  your exposure to third-party GPAI dependencies.

The AI Act applies in phases; confirm the current applicable dates and your classification with counsel.

## Trust-layer controls — provenance, safety gate, verifiable memory

Beyond the data-subject-rights tooling above, Vayl implements a *trust layer* that maps directly to
GDPR accountability/accuracy and to EU AI Act high-risk duties. These are the controls to put in front
of a DPO evaluating **agent autonomy** — the "can we let it act, and can we prove why it did" question.

| Feature | What it does | Serves |
|---|---|---|
| **Belief provenance** (`recall(explain=True)`) | Returns the exact facts behind an answer — value, **who asserted it** (source), confidence, what it superseded | GDPR Art. 15/22 (meaningful info about the logic); AI Act Art. 13 (transparency) |
| **Decision audit** (`record_decision` / `explain_decision`) | Immutable, **signed** snapshot of the beliefs behind an agent action — answers "why did it do X?" even after the facts change | GDPR Art. 5(2) accountability, Art. 22 (contest a decision); AI Act Art. 12 (record-keeping) |
| **Safety gate** (`check_before_act` / `safe_recall`) | Refuses to *act* on disputed / low-confidence / stale / superseded memory; surfaces it for a human | GDPR Art. 22 (human intervention); AI Act Art. 14 (human oversight) |
| **Tamper-evident audit** (`verify_audit`) | Hash-chained + Ed25519-signed audit log; proves it wasn't edited, reordered, or truncated | GDPR Art. 5(1)(f) integrity, Art. 5(2); AI Act Art. 12 |
| **Signed erasure receipts** (`delete` → `verify_receipt`) | A third-party-verifiable proof that an erasure happened | GDPR Art. 17 (demonstrate erasure) |
| **Knowledge attestation** (`attest`) | Signed proof of what was held at a point in time | Accountability / evidentiary |
| **Source attribution** (`remember(source=…)`) | Records which agent/person/connector asserted each fact; drives source-aware reconciliation in shared spaces | Accuracy (5(1)(d)), accountability, data provenance |
| **Public-key verification** (`export_public_key`) | Anyone verifies receipts, attestations, and the audit chain **without the secret key or DB access** | Independent auditability |

**Human oversight (Art. 22 / AI Act Art. 14) — how to wire it.** Put `check_before_act` (or
`safe_recall`) on the path to any autonomous action with legal or significant effect. When it returns
BLOCKED/WITHHELD, route to a human. That is a concrete, testable oversight control — not a policy
promise — and provenance + `explain_decision` give the reviewer the "why" they need to act or contest.

**Key management (Art. 32).** The encryption (Fernet) and signing (Ed25519) keys underpin these
guarantees. Vayl supports **HashiCorp Vault Transit** envelope encryption out of the box
(`VAYL_KMS=vault`): the master key never leaves Vault, only a *wrapped* key blob sits on the Vayl host,
and the key is unwrapped into memory at startup — so custody moves **off the data host** (a key beside
the DB protects against file copy, not machine theft). Self-hosted Vault keeps that custody on your own
infrastructure, consistent with the sovereignty posture. Vayl **fails closed** if the KMS is unreachable
(refuses to start rather than run unencrypted). The tamper-evidence and receipts are only as strong as
the custody of the signing key — treat Vault access, and key rotation, accordingly.

## Sector overlays (if applicable)
- **Finance:** DORA (operational resilience, ICT risk).
- **Essential / important entities:** NIS2 (security measures, incident reporting).
- **Health:** national health-data law and the European Health Data Space.

These govern your environment, not Vayl specifically.

## Why Vayl fits an EU deployment
- **Data residency:** local/on-prem + **local-LLM default → personal data stays on the machine.**
- **Security:** **encrypted at rest** (Art. 32), authenticated, localhost-only, no telemetry.
- **Data-subject rights, built in:** erasure, access, rectification map to concrete tools.
- **Auditability:** the history/change-log evidences what was held and when it changed.

This equips you to comply; it does not replace your legal basis, notices, DPIA, or DPO sign-off.

## Evidence of implemented controls — verify each yourself

Vayl does **not** self-declare compliance (that is your deployment's property, validated by your
auditor). What it *can* prove is the **technical controls it implements** — and every one below is
**independently verifiable** by the referenced command or automated test. This is auditor-grade
evidence, not a claim. Run the full suite with `pytest` (151 automated tests).

| Control (requirement) | What Vayl does | How to prove it |
|---|---|---|
| **Encryption at rest** (Art. 32) | Content columns are Fernet ciphertext on disk | Store a fact, then `strings vayl.db \| grep <the value>` → **nothing**; the raw `value` column is `gAAAAA…`. Test: `test_crypto.py::test_data_is_ciphertext_at_rest_but_plaintext_on_read` |
| **Right to erasure** (Art. 17) | `delete` / `delete_all` hard-delete rows **incl. history and graph** | After erasure, `export_memory` returns `count: 0` and `history` is empty; `audit_log` shows a `delete(erasure)` entry. Test: `test_store.py::test_delete_hard_erases_a_subject_including_history` |
| **Access & portability** (Art. 15/20) | `export_memory` → machine-readable JSON (statements + decisions + audit + receipts) | Call `export_memory`; output is valid JSON with every record. Test: `test_compliance.py::test_export_is_machine_readable_and_includes_history` |
| **Rectification** (Art. 16) | `update_memory` retires the old value to history, sets the new one active | Test: `test_apply.py::test_update_is_audit_preserving` |
| **Accountability** (Art. 5(2)) | Append-only `audit_log` records who/what/when for every data op | Do any operation, then `audit_log` shows the timestamped entry. Test: `test_compliance.py::test_audit_records_who_what_when_newest_first` |
| **Storage limitation** (Art. 5(1)(e)) | `purge_expired(days)` hard-deletes rows older than N days | Test: `test_compliance.py::test_expire_deletes_only_old_rows` |
| **Data residency / no egress** | Defaults to a local LLM — no data leaves the host | Boot with no LLM env → it uses local Ollama. Test: `test_config.py::test_out_of_the_box_is_local_ollama_no_egress` |
| **Access control** | `vayl-server` requires a Bearer credential on every request; roles grant capabilities (RBAC), fail-closed | Request without / with an invalid key → `401`; a valid key binds its principal. Tests: `test_api.py::test_missing_key_is_401`, `test_invalid_or_malformed_key_is_401`, `test_valid_key_binds_the_principal_for_the_request` |
| **Space isolation** (data minimization) | `(user_id, agent_id, run_id)` scoping | Test: `test_store.py::test_agent_spaces_are_isolated_for_the_same_user` |
| **No telemetry** | Only outbound call is the LLM/embedder you configure | Code review — grep for network calls; there are no analytics/telemetry endpoints |
| **Injection resistance** (Art. 32) | All SQL parameterized | Code review of `store.py`; stress-tested with injection strings |
| **Tamper-evident audit** (Art. 5(1)(f), 5(2)) | Audit log is hash-chained + Ed25519-signed | `verify_audit` reports INTACT; edit any row → it names the broken seq. Tests: `test_audit.py::test_tampering_a_detail_breaks_the_chain`, `::test_signed_chain_verifies_and_signature_tamper_is_caught` |
| **Proof of erasure** (Art. 17) | `delete` issues a signed, third-party-verifiable receipt | `verify_receipt` → VALID; edit any field → INVALID. Tests: `test_receipts.py::test_erasure_receipt_verifies_with_public_key_only`, `::test_editing_any_payload_field_invalidates_the_receipt` |
| **Decision accountability** (Art. 5(2), 22) | Immutable **signed** snapshot of the beliefs behind an action | Snapshot survives later fact changes; receipt verifies. Tests: `test_decisions.py::test_snapshot_is_immutable_across_later_fact_changes`, `::test_signed_receipt_verifies_and_tamper_is_caught` |
| **Human-oversight hook** (Art. 22 / AI Act 14) | Safety gate refuses to act on unsafe memory | `check_before_act`/`safe_recall` BLOCK on flagged/low-conf/stale. Tests: `test_safety.py::test_check_blocks_a_flagged_conflict`, `::test_safe_recall_withholds_on_low_confidence` |
| **Belief provenance** (Art. 15/22, AI Act 13) | Recall returns the exact facts + source used to answer | Test: `test_apply.py::test_query_with_provenance_returns_exact_facts_used` |
| **Independent verifiability** | Public key verifies receipts/attestations/chain without the secret | `export_public_key`, then `receipts.verify(receipt, public_key)` off-box → True. Test: `test_receipts.py::test_persisted_receipt_roundtrips_and_stays_verifiable` |

**What this proves and what it doesn't.** It proves Vayl *implements* these controls, verifiably.
It does **not** prove your *deployment* is compliant — that additionally requires your legal basis,
notices, DPIA, RoPA, retention decisions, AI-Act classification, and your auditor's/DPO's sign-off
(and, for a certification, an accredited assessor). Vayl provides the evidence; you and your auditor
provide the compliance.

## DPO / controller checklist

- [ ] Establish and document a **lawful basis** for the processing.
- [ ] Publish a **privacy notice** covering the memory processing.
- [ ] Complete a **DPIA** (AI + personal-data processing is typically in scope).
- [ ] Maintain **Records of Processing** (Art. 30).
- [ ] Choose the **local/EU model** for data residency; if cloud, put a **DPA + transfer basis** in place.
- [ ] Define a **retention schedule**; map erasure requests to `delete`/`delete_all` (not `forget`).
- [ ] Implement the **data-subject-rights workflow** using Vayl's tools.
- [ ] Confirm **special-category** handling (Art. 9) and **Art. 22** human oversight if relevant.
- [ ] Put the **safety gate** (`check_before_act` / `safe_recall`) on autonomous-action paths and route BLOCKED/WITHHELD to a human (Art. 22 / AI Act Art. 14).
- [ ] Decide the **audit/receipt retention vs. crypto-shred** policy for erased subjects (see "Erasure vs. accountability").
- [ ] Use **`VAYL_KMS=vault`** (HashiCorp Vault Transit) for production key custody; enable rotation, keep the master key off the data host.
- [ ] Classify the system under the **AI Act** and meet the applicable transparency/high-risk duties.
- [ ] Keep OS **full-disk encryption** and access controls on the host (see `SECURITY.md`).
- [ ] Have counsel/DPO **sign off** before go-live; commission a **security/pen test**.

## Disclaimer
Vayl is early-stage software, is not certified, and makes no compliance guarantee. This guide is a
technical aid for your assessment, not a legal opinion. Determinations rest with your DPO and counsel.
