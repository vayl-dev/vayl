---
description: Manage principals, policy, and operations.
icon: user-gear
---

# Administration

Tools for running a team deployment (most require the `admin` capability). A `?` marks an optional argument.

## create\_principal

```python
create_principal(name, role?, kind?, scopes?)
```

Create a user or agent and issue its API key (**shown once** — copy it now).

| Argument | Description                                                           |
| -------- | --------------------------------------------------------------------- |
| `name`   | display name (encrypted at rest — it's personal data)                 |
| `role`   | `admin`, `member`, `agent`, `viewer`, or `auditor` (default `member`) |
| `kind`   | `human`, `agent`, or `service`                                        |
| `scopes` | `user_id`s this principal may touch — **required for multi-tenant**   |

## list\_principals

```python
list_principals()
```

List the deployment's principals and their roles. Never returns keys.

## revoke\_principal

```python
revoke_principal(principal_id, erase?)
```

Disable a principal — its API key stops working **immediately** on the next request. `erase=True` hard-deletes the record (Art. 17 for a team member).

## license\_status

```python
license_status()
```

Show the edition (Community or licensed), seats used vs. allowed, expiry, and unlocked features.

## stats

```python
stats()
```

On-device KPIs — per-tool call counts, average latency, errors (with recent error detail), and the distribution of reconciliation actions. Nothing leaves the machine.

## health

```python
health()
```

Diagnose setup — checks the database, embedder, LLM, and graph (if enabled) are reachable. Run this first if something isn't working.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, capabilities, and scopes that these principals carry.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Stand up the authenticated team server, with Docker and Postgres.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>If <code>health</code> or <code>stats</code> surfaces a problem, start here.</td><td><a href="../reference/troubleshooting.md">troubleshooting.md</a></td></tr></tbody></table>
