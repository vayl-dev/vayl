---
description: Create, scope, rotate and revoke the keys your team and agents use.
icon: rectangle-terminal
---

# Managing API keys

Keys are created with the `create_principal` tool, which needs `admin`:

```
create_principal(name, role="member", kind="agent", scopes="", tenant="")
```

* The key (`vayl_sk_…`) is shown **once**. Vayl stores only its hash, so copy it then.
* `scopes` is a comma-separated list of `user_id` values the key may reach; empty means all.
* `tenant` defaults to your own tenant. Only the deployment operator can create keys in another tenant.
* The Community edition allows 3 principals; `license_status` shows the limit.

**The first admin.** A new database has no principals. Create one over local stdio (`vayl-mcp` runs as the local admin), or with Docker:

```bash
docker compose run --rm -e VAYL_AUTH_REQUIRED=0 vayl python -c "from vayl.api import mcp_server as s; print(s.create_principal('admin', role='admin'))"
```

**Rotate** a key by creating a new principal and revoking the old one. `revoke_principal(principal_id)` stops the key on its next request; `erase=True` also deletes the record. `list_principals` shows your tenant's principals, never their keys.

More in [Administration tools](https://vayl.gitbook.io/vayl-docs/documentation/mcp-tools/administration).
