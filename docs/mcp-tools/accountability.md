---
description: Signed, tamper-evident record of what the agent believed and did.
icon: file-signature
---

# Accountability

Prove what was known, decided, and erased. Signatures use Ed25519; the audit log is a tamper-evident hash chain. A `?` marks an optional argument.

## record\_decision

```python
record_decision(action_summary, question, user_id?, agent_id?, run_id?)
```

Log a decision bound to the **exact facts the agent consulted** — an immutable, signed snapshot of what it believed at decision time. Returns a decision id and a signed receipt digest.

## explain\_decision

```python
explain_decision(decision_id, user_id?)
```

Reconstruct a past decision: the action plus the beliefs held **at that moment**, with the signed receipt verified so you know the record wasn't altered.

## attest

```python
attest(subject, user_id?, agent_id?, run_id?)
```

Issue a signed, third-party-verifiable **attestation** of the current value — "as of now, X is the value" — anchored to the tamper-evident audit head.

## audit\_log

```python
audit_log(limit?, user_id?)
```

The accountability trail — who did what, when. Detail is encrypted at rest and never wiped by erasure. Without a `user_id`, the deployment-wide log requires the `admin` capability; with one, it's scoped to that space.

## verify\_audit

```python
verify_audit()
```

Verify the tamper-evident audit chain end to end. Reports **INTACT**, or the exact row where it breaks (edited, reordered, truncated). A signed head checkpoint also detects deletion of the newest rows.

## verify\_receipt

```python
verify_receipt(receipt_id)
```

Verify a signed erasure receipt or attestation — recompute the body and check the Ed25519 signature. Returns VALID or INVALID. Scoped to the receipt's owning space.

## export\_public\_key

```python
export_public_key()
```

Return Vayl's Ed25519 public key, so anyone can verify receipts, attestations, and the audit chain **offline** — without the secret key or the database.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-scale-balanced" style="color:$primary;">:scale-balanced:</i> Compliance (GDPR)</h4></td><td>Erasure and export with signed receipts you can verify here.</td><td><a href="compliance-gdpr.md">compliance-gdpr.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety and gating</h4></td><td>Gate irreversible actions before the agent acts.</td><td><a href="safety-and-gating.md">safety-and-gating.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Who may read the audit log and issue attestations.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
