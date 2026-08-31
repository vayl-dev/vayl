---
description: GDPR erasure, export, and retention — with signed receipts.
icon: scale-balanced
---

# Compliance (GDPR)

Building blocks for data-subject rights. These tools need the `delete` or `admin` capability — see [Authentication & access](../core-concepts/authentication-and-access.md). A `?` marks an optional argument.

## delete

```python
delete(subject, user_id?, agent_id?, run_id?)
```

**Permanently erase** a subject, history included — the right to be forgotten (Art. 17). Unlike `forget`, nothing is retained: the erased values are also **redacted from decision snapshots** (re-signed, redaction audited), and Vayl issues a **signed erasure receipt**.

## delete\_all

```python
delete_all(user_id?, agent_id?, run_id?)
```

Permanently erase **all** of a user's memory (account deletion), with their decision snapshots redacted too. With no agent/run it erases the entire user across all spaces; with them, one space. Issues a signed erasure receipt.

## export\_memory

```python
export_memory(user_id?, agent_id?, run_id?)
```

**DSAR-complete export** (Art. 15/20): the subject's statements (active + history) plus their decision snapshots, audit entries, and receipts — as machine-readable JSON.

## purge\_expired

```python
purge_expired(older_than_days, user_id?, agent_id?, run_id?,
              include_audit?, include_decisions?, include_receipts?)
```

Retention (Art. 5(1)(e)): hard-delete records older than `older_than_days`. The `include_*` flags extend the purge to the audit log, decisions, and receipts. The audit chain stays verifiable across purges via a **signed retention anchor**.

{% hint style="info" %}
**`forget` vs `delete`:** `forget` retires a fact but keeps it in history (correctness); `delete` hard-erases it for compliance (privacy).
{% endhint %}

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Verify the signed erasure receipts these tools issue.</td><td><a href="accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>The <code>delete</code> and <code>admin</code> capabilities these tools require.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Run the authenticated team server these tools live behind.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>
