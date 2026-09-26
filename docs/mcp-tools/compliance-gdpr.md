---
description: Erase, export, and expire personal data, with signed erasure receipts.
icon: scale-balanced
---

# Compliance (GDPR)

These four tools cover data-subject rights: erasure (Art. 17), access and portability (Art. 15/20), and storage limitation (Art. 5(1)(e)). Erasures are hard deletes, history included, and each one issues a signed receipt you can check with [`verify_receipt`](accountability.md#verify_receipt).

All four take the memory-space arguments `user_id="default"`, `agent_id=""`, `run_id=""`. Outputs below were captured by running the tools offline.

| Tool | Capability | Annotations |
| --- | --- | --- |
| `delete` | `delete` (admin, member) | destructive |
| `delete_all` | `admin` | destructive |
| `export_memory` | `read` | read-only |
| `purge_expired` | `admin` | destructive |

{% hint style="info" %}
**`forget` or `delete`?** [`forget`](memory.md#forget) retires a fact and keeps it in history: use it when something stopped being true. `delete` removes every trace: use it when the data must go.
{% endhint %}

## delete

```python
delete(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

Permanently erases every record of `subject` in the space, including history and retraction tombstones. The erased values are also redacted from any decision snapshots for that subject (the snapshots are re-signed), and a signed erasure receipt is issued.

```json
{"name": "delete", "arguments": {"subject": "refund_window", "user_id": "cust_5521"}}
```

```
Erased 1 record(s) for 'refund_window'; redacted its values from 1 decision snapshot(s).
  signed erasure receipt #3 issued — verify with verify_receipt #3 (or independently with the public key from export_public_key).
```

When nothing matches, it returns `No records found for 'refund_window'.` A receipt is still written in that case.

## delete\_all

```python
delete_all(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

Permanently erases memory, history included. With `agent_id` and `run_id` both empty, it erases **every space** of `user_id` (account deletion) and redacts values from all that user's decision snapshots. Pass `agent_id` or `run_id` to erase one space only.

```
Erased all memory for 'patient_42' (4 record(s)).
  signed erasure receipt #5 issued — verify with verify_receipt #5.
```

When snapshots were redacted, the count line adds `; values redacted from N decision snapshot(s)`.

{% hint style="warning" %}
**Erasure fails closed with the graph on.** With `VAYL_GRAPH` enabled, `delete` and `delete_all` purge the Neo4j graph first. If the graph can't be reached, the call stops before touching the store: nothing is erased, no receipt is issued, and you get the generic `Vayl couldn't complete that (ref …)` message. The server log records a `RuntimeError` under that ref; the full text (`erasure aborted: graph purge failed and nothing was erased`) is logged at DEBUG and kept in the encrypted metrics store. Retry once the graph is back.
{% endhint %}

## export\_memory

```python
export_memory(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

Returns everything held about a space as JSON, for a subject access or portability request: every statement (active and history), plus the decision snapshots, audit entries (up to 1,000 each) and receipts for that `user_id`. Needs only `read`, and is scope-checked like any read. The export itself is audited.

```json
{
  "space": {"user_id": "cust_5521", "agent_id": "", "run_id": ""},
  "count": 1,
  "records": [
    {"id": 1, "subject": "refund_window", "value": "30 days", "scope": "global",
     "status": "ACTIVE", "supersedes": null, "confidence": 0.95, "metadata": null}
  ],
  "decisions": [
    {"id": 1, "ts": "2026-09-26T12:20:13+00:00", "user_id": "cust_5521", "agent_id": "", "run_id": "",
     "summary": "Issued refund to order #91",
     "beliefs": [{"confidence": 0.95, "id": 1, "set_at": 1790425213.58, "source": "policy-bot",
                  "status": "ACTIVE", "subject": "refund_window", "supersedes": null, "value": "30 days"}],
     "entry_hash": "7d5bb151…", "anchor": "a1a125e0…", "signed": true, "verified": true}
  ],
  "audit": [
    {"ts": "2026-09-26T12:20:13+00:00", "user_id": "cust_5521", "agent_id": "", "run_id": "",
     "action": "record_decision", "detail": "Issued refund to order #91"},
    {"ts": "2026-09-26T12:20:13+00:00", "user_id": "cust_5521", "agent_id": "", "run_id": "",
     "action": "remember", "detail": "[policy-bot] ADD refund_window"}
  ],
  "receipts": []
}
```

The real output is indented JSON; it is condensed here, with hashes shortened.

## purge\_expired

```python
purge_expired(older_than_days: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
              include_audit: bool = False, include_decisions: bool = False,
              include_receipts: bool = False) -> str
```

Hard-deletes statements written more than `older_than_days` days ago for `user_id` (all of its spaces, or the one space named by `agent_id`/`run_id`).

```
Purged 0 record(s) older than 365 days.
```

{% hint style="danger" %}
**The `include_*` flags are deployment-wide.** They are not limited to `user_id`: `include_decisions` and `include_receipts` purge those tables for every user and tenant, and `include_audit` deletes old audit rows across the whole deployment. The return says so:

```
Purged 0 record(s) older than 3650 days. Also purged (deployment-wide): decisions=0, receipts=0.
```
{% endhint %}

`include_audit=True` deletes from the head of the audit chain and writes a signed retention anchor, so `verify_audit` keeps passing on the rows that remain. Deleted audit entries are gone for good; export them first if you must keep them.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Verify the signed erasure receipts these tools issue.</td><td><a href="accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>The <code>delete</code> and <code>admin</code> capabilities these tools require.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td><code>VAYL_GRAPH</code>, encryption, and the other settings erasure depends on.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
