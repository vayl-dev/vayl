# Building with Vayl — Use Cases & Integration Guide

Vayl is a **reconciling memory layer** for AI agents: instead of piling up every fact ever said, it
keeps memory **current** — a new value *supersedes* the old, "we dropped X" *retracts* it, ambiguous
inputs are *flagged* not guessed, and history is kept for audit. It runs **local, encrypted, and
on-device**.

Reach for Vayl whenever your agent stores **facts that change over time** and returning a **stale or
removed** fact would be wrong, embarrassing, or unsafe.

---

## The primitives (what your agent calls)

| Goal | Tool |
|---|---|
| Store fact(s) from natural language | `remember(text, user_id, agent_id?, run_id?, metadata?)` |
| Answer from **current** memory | `recall(question, user_id, …)` |
| Deep / relational question (graph) | `recall_related(question, …)` |
| Timeline of a subject ("what did we use before?") | `history(subject, …)` |
| Remove — keep in history (audit) | `forget(text, …)` |
| Remove — **hard delete** (GDPR) | `delete(subject, …)` / `delete_all(…)` |
| Correct a fact by id (audit-preserving) | `update_memory(memory_id, new_value, …)` |
| List / fetch / export | `list_memories`, `get_memory`, `export_memory` |
| Retention / accountability | `purge_expired(days, …)`, `audit_log(…)` |

### Scoping — the one concept to learn
Every call takes `user_id` and optional `agent_id` / `run_id`. Each **`(user_id, agent_id, run_id)`
combination is an isolated memory space**:

- `user_id` → **who the memory is about** (a customer, patient, end-user, project, tenant).
- `agent_id` → **which agent** owns it (e.g. `support-bot` vs `sales-bot`), if you run several.
- `run_id` → a **session/conversation**, if you want per-session scratch memory.

---

## How you integrate

**A. As an MCP server (agents — Claude Desktop, Cursor, custom MCP clients):**
```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": { "VAYL_DB": "/data/vayl.db", "VAYL_KEY": "your-passphrase" }
    }
  }
}
```
With no LLM env set, Vayl runs on a **local model (no data egress)**; set `OPENAI_API_KEY` +
`OPENAI_MODEL=gpt-5-mini` for production quality.

**B. Direct from Python (custom backends):**
```python
from vayl.api import mcp_server as vayl   # the tool functions are plain callables

vayl.remember("We switched from Redux to Zustand", user_id="team_acme")
print(vayl.recall("what state library do we use?", user_id="team_acme"))   # -> "Zustand"
```

Everything below uses these same primitives — only the **scoping** and **what you store** change.

---

## Use cases

### 1. AI coding assistant (team/project memory)
**Scenario:** an assistant in Cursor/Claude Code that knows a team's conventions and stack.
**Stale-memory pain:** conventions change constantly ("we moved off Redux", "we dropped Sentry").
An additive memory suggests code against the *old* stack.
**Build it:** scope by project — `user_id = "repo_acme"`. The agent calls `remember` as decisions are
made and `recall` before answering.
```python
vayl.remember("We deploy continuously now, not on Fridays", user_id="repo_acme")
vayl.recall("how do we deploy?", user_id="repo_acme")          # -> "continuously"
vayl.history("deploy_schedule", user_id="repo_acme")           # the full timeline
```

### 2. Customer support agent (CRM-aware chat)
**Scenario:** a support bot that remembers each customer's account context.
**Stale-memory pain:** "customer is on the Pro plan" after they downgraded; "still at Acme" after they
left. Wrong context → wrong answers.
**Build it:** `user_id = customer_id`. Reconcile status changes; erase on account deletion.
```python
vayl.remember("Customer moved from Pro to the Free plan", user_id="cust_5521")
vayl.recall("what plan is the customer on?", user_id="cust_5521")   # -> "Free"
vayl.delete_all(user_id="cust_5521")   # account deletion (GDPR erasure)
```

### 3. Personal AI assistant
**Scenario:** a personal assistant that tracks a user's preferences, people, and logistics.
**Stale-memory pain:** "you work at X", "your flight is Tuesday" — all change; stale = embarrassing.
**Build it:** `user_id = end_user_id`. Store preferences with `metadata` for provenance.
```python
vayl.remember("I changed teams — I'm on Platform now, not Growth",
              user_id="u_alex", metadata={"source": "chat"})
vayl.recall("what team am I on?", user_id="u_alex")   # -> "Platform"
```

### 4. Sales / deal assistant
**Scenario:** an assistant that tracks the state of each prospect and deal.
**Stale-memory pain:** "budget is $50k" after it changed; "champion is Dana" after they left.
**Build it:** `user_id = account_id`. Multi-hop questions via `recall_related` (with the graph).
```python
vayl.remember("Their new champion is Priya; budget moved to $120k", user_id="acct_88")
vayl.recall("who is the champion and what's the budget?", user_id="acct_88")
```

### 5. Multi-agent systems (shared, scoped memory)
**Scenario:** several specialized agents that should share *some* memory but not step on each other.
**Build it:** same `user_id`, different `agent_id` for private scratch; a shared `agent_id` for common
memory. Each space is isolated but under one user.
```python
vayl.remember("Prefers async standups", user_id="u_alex", agent_id="scheduler")
vayl.remember("On the Platform team",   user_id="u_alex", agent_id="org-bot")
# scheduler and org-bot keep separate spaces; query each with its agent_id
```

### 6. E-commerce / shopping assistant
**Scenario:** a shopping bot that remembers sizes, preferences, and past intent.
**Stale-memory pain:** "prefers size M" after they told you L; "wants a gift for their dad" from a
finished order still surfacing months later.
**Build it:** `user_id = shopper_id`; use `purge_expired` to age out stale session intent.
```python
vayl.remember("Actually I'm a size L now, not M", user_id="shopper_31")
vayl.purge_expired(older_than_days=90, user_id="shopper_31")   # retention
```

### 7. DevOps / incident assistant
**Scenario:** an ops copilot that knows the live infrastructure state.
**Stale-memory pain:** infra changes hourly; "the DB is on RDS" after you migrated to Cloud SQL is
actively dangerous during an incident.
**Build it:** `user_id = "infra_prod"`; `forget` decommissioned components; `recall_related` for
dependency questions ("what depends on the auth service?") via the graph.

### 8. HR / internal knowledge agent
**Scenario:** an internal assistant answering "who owns X", "who's on team Y".
**Stale-memory pain:** people move teams and leave; stale org facts mislead.
**Build it:** `user_id = "org"`, entity-scoped subjects; `audit_log` for accountability on sensitive
lookups. Keep it non-authoritative (point to the system of record).

### 9. Healthcare chat — **non-clinical only** (patient experience & coordination)
**Scenario:** appointment scheduling, wayfinding, general info, patient preferences, insurance/logistics.
**Stale-memory pain:** "your appointment is Monday" after it moved; "your address is …" after it changed.
**Build it:** `user_id = patient_id`; **on-prem + local LLM (no PHI egress)**, encryption on, `delete`
for erasure, `audit_log` for access accountability.
> ⚠️ **Clinical use is out of scope here.** Anything informing care (medication, triage, symptoms) is
> high-risk and heavily regulated (HIPAA / GDPR Art. 9 / EU AI Act / FDA SaMD) and requires a strong
> model, mandatory human oversight, clinical validation, and formal classification **before** any
> clinical role. See `COMPLIANCE.md`. Start non-clinical.

### 10. Education / tutoring agent
**Scenario:** a tutor that tracks what a learner has mastered and where they are now.
**Stale-memory pain:** "still struggling with fractions" after they've mastered them → wrong pacing.
**Build it:** `user_id = student_id`; supersede skill levels as they progress; `history` to show growth.

---

## Compliance-sensitive deployments (any of the above with personal data)

Vayl gives you the technical building blocks: **encryption at rest** (on by default), **erasure**
(`delete`/`delete_all`), **access/portability** (`export_memory`), **retention** (`purge_expired`),
**accountability** (`audit_log`), and a **local-LLM default** so data stays on the host. You remain
the data controller — see [`SECURITY.md`](../SECURITY.md) and [`COMPLIANCE.md`](../COMPLIANCE.md).

## Honest notes

- **Model strength is the quality dial.** Reconciliation reliability depends on the LLM. Use a strong
  model (e.g. `gpt-5-mini`, which scores 0% silently-wrong on `benchmarks/evaluations/messy_eval.py`) for production; the local default is great for dev/offline, weaker on the
  subjective edges. Measure yours with `benchmarks/evaluations/eval_reconcile.py`.
- **Vayl reconciles *facts/preferences*, not documents.** For document retrieval/RAG, use a vector DB;
  pair Vayl alongside it for the durable, changing facts.
- **High-stakes domains (clinical, legal, financial decisions)** need human oversight, validation, and
  the relevant regulatory classification — a memory layer alone does not clear that bar.
