---
description: Store, query, correct, and time-travel over facts.
icon: database
---

# Memory

Store facts in plain language, then ask what's true now — these are Vayl's core reconciling-memory tools. `user_id?`, `agent_id?`, and `run_id?` are the optional memory-space keys (a `?` marks an optional argument).

## remember

```python
remember(text, user_id?, agent_id?, run_id?, metadata?, source?)
```

Store fact(s) from a natural-language statement. Vayl extracts one or more structured facts and reconciles each — a new value **supersedes** the old, a removal **retracts**, and multiple facts in one message are all captured.

| Argument   | Description                                                                   |
| ---------- | ----------------------------------------------------------------------------- |
| `text`     | the statement (may contain several facts)                                     |
| `metadata` | tags applied to the facts stored in this call, e.g. `{"category": "allergy"}` |
| `source`   | who/what asserted it — belief provenance and source-aware reconciliation      |

**Returns** a summary of the reconciliation actions taken (supersede / retract / add / flag / dedup).

**Example**

```json
{"name": "remember", "arguments": {"text": "We switched from Redux to Zustand", "user_id": "proj_7"}}
```

```
Stored: [SUPERSEDE] state = Zustand
```

## recall

```python
recall(question, user_id?, agent_id?, run_id?, explain?, include_history?, critical_categories?)
```

Answer a question using hybrid semantic + lexical retrieval over the **active** set. Returns the current value, or **"I don't know"** rather than guessing.

| Argument              | Description                                                                                         |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| `question`            | natural-language question                                                                           |
| `explain`             | also return provenance — the exact facts used, each with source, confidence, and what it superseded |
| `include_history`     | reach retired facts, for questions about the past                                                   |
| `critical_categories` | comma-separated categories forced into the answer regardless of ranking                             |

**Example**

```json
{"name": "recall", "arguments": {"question": "what state library do we use?", "user_id": "proj_7"}}
```

```
Zustand
```

With `explain: true`, the answer is followed by the facts behind it:

```
Zustand

Based on these facts:
  • #7 state = Zustand  [conf 0.95, from alice, supersedes #3]
```

## recall\_related

```python
recall_related(question, user_id?, agent_id?, run_id?)
```

Answer deep / relational / multi-hop questions via the optional entity graph. Falls back to `recall` when the graph isn't enabled.

## history

```python
history(subject, user_id?, agent_id?, run_id?)
```

Return the full change-log for a subject — every value it has held, oldest to newest, with status (ACTIVE / SUPERSEDED / RETRACTED).

## get\_memory

```python
get_memory(memory_id, user_id?, agent_id?, run_id?)
```

Return one memory's structured detail: value, status, scope, confidence, `supersedes`, and metadata. Ids are shown as `#id` by `list_memories`.

## update\_memory

```python
update_memory(memory_id, new_value, user_id?, agent_id?, run_id?)
```

Correct a memory by id — **audit-preserving**. The old value is retired to history and `new_value` becomes active. The human-edit path, distinct from the LLM-driven `remember`.

## forget

```python
forget(text, user_id?, agent_id?, run_id?)
```

Retract a fact (_"we dropped Sentry"_). The fact is retired but **kept in history** for audit — a recall never returns it as current again.

## list\_memories

```python
list_memories(user_id?, agent_id?, run_id?)
```

List the current active facts (each with its `#id`) plus a history block of what was superseded or retracted.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-compass" style="color:$primary;">:compass:</i> Your first memory</h4></td><td>These tools in a hands-on walkthrough — store, correct, retract, recall.</td><td><a href="../getting-started/your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety and gating</h4></td><td>Gate irreversible actions before your agent acts on a memory.</td><td><a href="safety-and-gating.md">safety-and-gating.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Snapshot what the agent believed and did, on a signed audit chain.</td><td><a href="accountability.md">accountability.md</a></td></tr></tbody></table>
