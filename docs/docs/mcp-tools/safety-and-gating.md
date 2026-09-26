---
description: Gate irreversible actions and require human approval.
icon: shield-halved
---

# Safety and gating

Guardrails for high-stakes agents. See the [Safety gates guide](../guides/safety-gates-and-human-approval.md) for the workflow. A `?` marks an optional argument.

## check\_before\_act

```python
check_before_act(subject, user_id?, agent_id?, run_id?, min_confidence?,
                 require_active?, block_on_flagged?, max_staleness_days?,
                 block_on_recent_change_days?)
```

A safety gate to call **before** an irreversible action. Returns **SAFE**, or **BLOCKED** with reasons.

| Argument                      | Description                                   |
| ----------------------------- | --------------------------------------------- |
| `subject`                     | the slot you're about to act on               |
| `min_confidence`              | minimum confidence to be considered safe      |
| `require_active`              | require a current (non-retired) value         |
| `block_on_flagged`            | block if the value is disputed/flagged        |
| `max_staleness_days`          | reject facts older than this                  |
| `block_on_recent_change_days` | block if the value changed within this window |

**Example**

```json
{"name": "check_before_act", "arguments": {"subject": "active_medication", "user_id": "patient_42", "block_on_flagged": true}}
```

```
✅ SAFE to act on 'active_medication'.
  current: active_medication = warfarin 7 mg PO daily
```

When a value is disputed, stale, or below the confidence bar, the same call returns:

```
⛔ BLOCKED — do NOT act on 'active_medication':
  • value is flagged (unresolved conflict)
```

## safe\_recall

```python
safe_recall(question, user_id?, agent_id?, run_id?, min_confidence?,
            require_active?, block_on_flagged?, max_staleness_days?,
            block_on_recent_change_days?, critical_categories?)
```

Like `recall`, but answers **only if** every current fact behind the answer passes the same policy checks as `check_before_act`; otherwise it withholds the answer and returns why. Use it on the path to an action.

## pending\_changes

```python
pending_changes(user_id?, agent_id?, run_id?)
```

Show proposed changes to **confirm-required** slots that haven't been applied — the human-approval queue. Each entry shows old to new and the sentence that triggered it.

## confirm\_change

```python
confirm_change(memory_id, user_id?, agent_id?, run_id?, decided_by?)
```

Approve a proposed change, applying it. Records **who** decided (`decided_by`). A proposal can't be confirmed if the value it would replace has since changed.

## reject\_change

```python
reject_change(memory_id, user_id?, agent_id?, run_id?, decided_by?)
```

Discard a proposed change. The current value stands; the proposal is kept as history.

## set\_reconcile\_policy

```python
set_reconcile_policy(mode?, authority?, user_id?, agent_id?, run_id?)
```

Configure how a **shared** space resolves conflicts between contributors:

| `mode`      | Behaviour                                                          |
| ----------- | ------------------------------------------------------------------ |
| `RECENCY`   | newer wins                                                         |
| `AUTHORITY` | higher-ranked source wins; a lower one is flagged, not overwritten |
| `REVIEW`    | every cross-source conflict is flagged                             |

`authority` maps a source to a rank, used by `AUTHORITY` mode.

## get\_reconcile\_policy

```python
get_reconcile_policy(user_id?, agent_id?, run_id?)
```

Show the current reconciliation policy for the space.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>The full workflow for gating writes and requiring approval.</td><td><a href="../guides/safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Record what the agent decided, on a signed audit chain.</td><td><a href="accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>The store-and-recall tools these gates protect.</td><td><a href="memory.md">memory.md</a></td></tr></tbody></table>
