---
description: Common problems and how to fix them.
icon: wrench
---

# Troubleshooting

Run the `health()` tool first — it checks the database, embedder, LLM, and graph and reports which piece is failing.

## The client doesn't show Vayl's tools

* Confirm the `command` is `vayl-mcp` and on your PATH: `pip show vayl-mcp`, then `vayl-mcp --help`.
* Restart the MCP client fully after editing its config.
* Check the client's MCP logs for a startup error — usually a bad value in the `env` block.

## Connection refused / LLM errors

`remember` and `recall` call your LLM and embedder. On a connection error:

* **Local model:** is the server running? A refused connection to `:11434` means Ollama isn't up. Start it, or point `OPENAI_BASE_URL` at a reachable endpoint.
* **Cloud:** check `OPENAI_API_KEY` is set and valid, and that `OPENAI_BASE_URL` / `OPENAI_MODEL` are correct.
* With **no** LLM environment set, Vayl expects a local Ollama at `localhost:11434`.

## `401 Unauthorized` from vayl-server

Every request to `/mcp` needs `Authorization: Bearer vayl_sk_…`.

* No principals yet? Bootstrap the first admin over stdio (`vayl-mcp` → `create_principal("you", role="admin")`), then use that key.
* Key revoked or mistyped → verify with `list_principals()` or issue a new one.
* `/healthz`, `/readyz`, and `/metrics` are the only unauthenticated routes.

## "Access denied … outside your assigned scope"

The key is **scoped** and you passed a `user_id` it isn't allowed to touch. Use a `user_id` within the key's scopes, or an admin key for cross-tenant tools. Scope denials are intentional and recorded to the audit log (and deliberately don't echo the requested id).

## `recall` says "I don't know" but I stored the fact

* **Wrong space.** Recall must use the same `user_id` / `agent_id` / `run_id` you stored under.
* **It's history.** If the fact was superseded or retracted, a normal recall won't return it — that's by design. Use `include_history=True` or `history(subject)`.
* **Retrieval miss.** For a fact that must always surface (an allergy), mark its category critical (`VAYL_CRITICAL_CATEGORIES`) so it bypasses ranking.

## Vayl refuses to start ("encryption unavailable")

Encryption and signing are **fail-closed**. If they're on (the default) but key material or the `cryptography` package is unavailable, Vayl won't start rather than run unprotected. Install `cryptography`, fix the key or Vault config, or explicitly set `VAYL_ENCRYPT=off` / `VAYL_SIGN=off`.

## A change wasn't applied

If the slot is `confirm`-required, the change is recorded as a **proposal**, not applied. Check `pending_changes()` and approve with `confirm_change(...)`. See [Safety gates & human approval](../guides/safety-gates-and-human-approval.md).

## Verifying integrity

* `verify_audit()` — is the audit chain intact?
* `verify_receipt(id)` — is an erasure receipt or attestation valid?
* `export_public_key()` — verify signatures offline, without the database.

## Still stuck?

Open an issue at [github.com/vayl-dev/vayl](https://github.com/vayl-dev/vayl/issues).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, with defaults — most fixes start here.</td><td><a href="configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Auth, TLS, and host settings for the team server.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Diagnose 401s and scope-denied errors.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
