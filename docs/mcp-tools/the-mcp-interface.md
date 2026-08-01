---
description: >-
  Vayl's API is its MCP surface — the transports, discovery, call envelope,
  annotations, and errors.
icon: plug
---

# The MCP interface

Vayl has no REST API. Its API **is** the Model Context Protocol surface: a set of tools your client calls over MCP. This page documents the protocol-level details — how to connect, discover tools, call them, and read results. For each tool's arguments, see the group pages (Memory, Safety and gating, and so on). To call these from a script, see [Calling Vayl from code](../getting-started/calling-vayl-from-code.md).

## Transports

The same tools are reachable two ways:

| Transport           | Command       | Use                                                                              |
| ------------------- | ------------- | -------------------------------------------------------------------------------- |
| **stdio**           | `vayl-mcp`    | a single local user; the client launches the process and talks over stdin/stdout |
| **streamable-HTTP** | `vayl-server` | a shared team deployment; requests go to `POST /mcp` with a Bearer key           |

`vayl-server` is **stateless** — each request is handled independently, with no long-lived session carrying identity. That's why every HTTP request must present its own credential.

## Authenticate (HTTP only)

Every request to `/mcp` must include:

```
Authorization: Bearer vayl_sk_…
```

A missing or invalid key is rejected with **401** before any tool runs. Over stdio there's no key — the local caller is a trusted admin. (`/healthz`, `/readyz`, `/metrics` are the only unauthenticated routes.)

## Discover the tools — `tools/list`

The authoritative, always-current list of tools and their argument schemas:

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }
```

The response includes, for each tool, its `name`, `description`, `inputSchema` (JSON Schema of its arguments), and `annotations` (see below). Prefer this over any static list when you need exact argument names.

## Call a tool — `tools/call`

Arguments are passed as a JSON object under `params.arguments`:

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {
    "name": "remember",
    "arguments": { "text": "We switched to Zustand", "user_id": "proj_7" }
  }
}
```

Vayl tools return a **text** result. The MCP envelope wraps it in a `content` array:

```json
{
  "jsonrpc": "2.0", "id": 2,
  "result": { "content": [ { "type": "text", "text": "SUPERSEDE  state = \"Zustand\"  (was \"Redux\")" } ] }
}
```

In a client SDK that's `result.content[0].text`. Reads return the answer as text; writes return the reconciliation actions taken; erasures return a signed-receipt summary.

## Safety annotations

Every tool carries MCP annotations so a client can decide what to auto-run and what to confirm with the user:

| Annotation              | Meaning                                                              | Examples                                                                                                            |
| ----------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `readOnlyHint: true`    | doesn't change memory                                                | `recall`, `list_memories`, `history`, `get_memory`, `export_memory`, `audit_log`, `verify_audit`, `stats`, `health` |
| _(write)_               | changes memory, but nothing is lost (supersede/retract keep history) | `remember`, `forget`, `update_memory`, `record_decision`, `attest`, `confirm_change`, `set_reconcile_policy`        |
| `destructiveHint: true` | **irreversible** erasure                                             | `delete`, `delete_all`, `purge_expired`, `revoke_principal`                                                         |
| `openWorldHint: true`   | calls an external service (the LLM/embedder)                         | `remember`, `recall`, `health`                                                                                      |

A typical client auto-runs read-only tools and prompts the user before anything marked destructive.

## Capabilities and scoping (HTTP)

On `vayl-server`, before a tool runs its caller is checked twice, fail-closed: the **capability** its role grants (read / write / delete / verify / admin), and the **scope** — whether the caller may touch the `user_id` in the call. See [Authentication & access](../core-concepts/authentication-and-access.md).

## Errors

* **Unauthenticated (HTTP):** `401` before the tool runs.
* **Not permitted:** the tool returns a readable `Access denied: …` message (capability or scope), recorded to the audit log; it deliberately doesn't echo the requested `user_id`.
* **Tool failure:** a failing tool never crashes the client — the error becomes a readable message with an opaque reference id, and the full detail goes to the server log under that reference.
* **Protocol errors** (unknown method, malformed params) come back as standard JSON-RPC errors.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>Each tool's arguments, starting with the core store-and-recall set.</td><td><a href="memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>Runnable stdio and HTTP clients.</td><td><a href="../getting-started/calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Capabilities and scoping for the HTTP transport.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
