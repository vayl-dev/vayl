---
description: Record why an agent acted, prove what memory held, and verify the signed audit trail.
icon: file-signature
---

# Accountability

These seven tools produce and check signed evidence: decision snapshots bound to the facts an agent consulted, attestations of what memory held, erasure receipts, and a tamper-evident audit log. Signatures are Ed25519, and every audit entry is hash-chained to the one before it, with a signed head checkpoint. Anyone holding the public key can verify receipts and attestations without the database.

Outputs below were captured by running the tools offline.

## Capabilities at a glance

| Tool | Capability | Annotations |
| --- | --- | --- |
| `record_decision` | `write` | write, open-world (calls the LLM) |
| `explain_decision` | `verify` | read-only |
| `attest` | `write` | write, not open-world |
| `verify_receipt` | `verify`, plus ownership (below) | read-only |
| `audit_log` | `verify`; `admin` when `user_id` is empty | read-only |
| `verify_audit` | `verify` | read-only |
| `export_public_key` | `verify` | read-only |

## record\_decision

```python
record_decision(action_summary: str, question: str, user_id: str = "default",
                agent_id: str = "", run_id: str = "") -> str
```

Logs a decision the agent is about to take, bound to the exact facts it consulted. It runs `question` through recall with provenance and stores an immutable, signed snapshot of those beliefs (value, source, confidence, what each superseded) with `action_summary`. Later changes to the facts do not change the snapshot.

```json
{"name": "record_decision", "arguments": {"action_summary": "Issued refund to order #91", "question": "what is the refund window?", "user_id": "cust_5521"}}
```

```
Decision #1 recorded, bound to 1 belief(s).
  based on: The refund window is 30 days.
  receipt: 7d5bb151b51bd7f9…  (signed; verify with explain_decision #1)
```

The `based on:` line is the answering model's free text; the rest is deterministic.

## explain\_decision

```python
explain_decision(decision_id: int, user_id: str = "default") -> str
```

Reconstructs a past decision: the action and the beliefs held at that moment, even if those facts have since been superseded, retracted, or erased (erased values are redacted from the snapshot). It also re-verifies the decision's signature. Takes only `user_id`, and only finds decisions recorded under that `user_id`.

```
Decision #1  (2026-09-26T12:20:13+00:00)
  action: Issued refund to order #91
  believed at the time:
  • #1 refund_window = 30 days  [conf 0.95, from policy-bot, status ACTIVE]
  ✓ receipt verified (record intact)
```

The last line is `⚠ receipt FAILED verification (record altered)` if the record was changed, or `(unsigned)` when signing is off. An unknown id returns `No decision #99 in this scope.`

## attest

```python
attest(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

Issues a signed attestation of the active value of `subject` right now, anchored to the current audit-chain head. Use it to prove what was known at a point in time.

```
Attestation #1: 'state' = Zustand  (as of 2026-09-26T12:20:13+00:00).
  signed & anchored to audit head 994d80be94d5c476… — verify with verify_receipt #1.
```

Several active values are joined with ` · `. A subject with no active value is still attested, as `(nothing known)`.

## verify\_receipt

```python
verify_receipt(receipt_id: int) -> str
```

Verifies a stored erasure receipt or attestation: recomputes its canonical body and checks the Ed25519 signature.

```
Receipt #1 [knowledge_attestation] — 'state' = Zustand  (ts 2026-09-26T12:20:13+00:00)
  ✓ VALID — signature verified, payload intact
```

```
Receipt #3 [erasure_receipt] — erased 1 record(s) of 'refund_window'  (ts 2026-09-26T12:20:13+00:00)
  ✓ VALID — signature verified, payload intact
```

A bad signature shows `⚠ INVALID — signature does not match (altered or unsigned)`. An unknown id returns `No receipt #99.`

**Ownership.** Receipt ids are sequential, so the lookup is scoped: the owner is the `user_id` at the head of the receipt's scope (`user_id/agent_id/run_id`). A caller whose scope doesn't include it gets `Access denied: that receipt belongs to a memory space outside your scope.` A legacy receipt with no scope needs `admin`: `Access denied: verifying an unscoped receipt requires the 'admin' capability.`

## audit\_log

```python
audit_log(limit: int = 50, user_id: str = "") -> str
```

The accountability trail (GDPR Art. 5(2)): recent operations, newest first. Entries are encrypted at rest and are not removed by memory erasure. With `user_id` set, the call is scope-checked and shows that space's trail. With `user_id` empty, it shows the whole deployment and needs `admin`:

```
Access denied: the deployment-wide audit log requires the 'admin' capability. Pass a user_id within your scope to see that space's trail.
```

```
2026-09-26T12:20:13+00:00  attest             user=proj_7  subject=nope
2026-09-26T12:20:13+00:00  attest             user=proj_7  subject=state
2026-09-26T12:20:13+00:00  safe_recall        user=proj_7  what is the api timeout?: WITHHELD
2026-09-26T12:20:13+00:00  safe_recall        user=proj_7  what state library do we use?: WITHHELD
2026-09-26T12:20:13+00:00  check_before_act   user=proj_7  state: BLOCKED
```

An empty log returns `No audit entries.` The `detail` column can contain memory content (for example the first 80 characters of a recall question).

## verify\_audit

```python
verify_audit() -> str
```

Recomputes every audit row's hash and signature end to end, and checks the signed head checkpoint, so edited, reordered, or truncated rows are detected.

```
✓ Audit chain INTACT — 38 entries verified.
```

A broken chain returns `⚠ Audit chain BROKEN at seq <n>: <reason>.` Rows written before chaining existed are counted as `(<n> legacy/unchained)`.

`purge_expired(..., include_audit=True)` deletes old audit rows from the head of the chain and writes a signed retention anchor, so `verify_audit` still passes after a retention purge. See [`purge_expired`](compliance-gdpr.md#purge_expired).

## export\_public\_key

```python
export_public_key() -> str
```

Returns the deployment's Ed25519 public key, for third parties to verify receipts, attestations and the audit chain offline.

```
Ed25519 public key (hex):
6d6bbbbb0b4130b34d621dd20c207c51dc8d1290af299f56545b05ccc959286d
```

If the `cryptography` package is unavailable, it returns:

```
No signer configured (the `cryptography` package is unavailable).
```

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-scale-balanced" style="color:$primary;">:scale-balanced:</i> Compliance (GDPR)</h4></td><td>Erasure and export with signed receipts you can verify here.</td><td><a href="compliance-gdpr.md">compliance-gdpr.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety and gating</h4></td><td>Gate irreversible actions before the agent acts.</td><td><a href="safety-and-gating.md">safety-and-gating.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, scopes, and who may read the audit log.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
