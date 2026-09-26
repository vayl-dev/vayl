---
description: Let people sign in to vayl-server with your identity provider over OIDC.
icon: key
---

# SSO configuration guide

SSO needs an Enterprise license that grants `sso`, and the extra: `pip install "vayl-mcp[sso]"`. API keys keep working alongside it.

| Variable | Purpose | Default |
| --- | --- | --- |
| `VAYL_OIDC_ISSUER` | Your IdP's issuer URL | required |
| `VAYL_OIDC_AUDIENCE` | The audience your tokens carry | required |
| `VAYL_OIDC_JWKS_URL` | Where to fetch the IdP's signing keys | required |
| `VAYL_OIDC_ROLE_CLAIM` | Claim holding the user's groups | `groups` |
| `VAYL_OIDC_ROLE_MAP` | JSON object mapping groups to roles, such as `{"vayl-admins": "admin", "eng": "member"}`. A malformed value stops startup | `{}` |
| `VAYL_OIDC_DEFAULT_ROLE` | Role for users in no mapped group | `viewer` |
| `VAYL_OIDC_SCOPE_CLAIM` | Claim listing the `user_id` values a user may reach | unset (unrestricted) |

Tokens must be RS256 with valid `iss`, `aud` and `exp`. Clients send them as `Authorization: Bearer <token>`.

SSO users always belong to the `default` tenant, so an SSO user mapped to `admin` is the deployment operator. Use `VAYL_OIDC_SCOPE_CLAIM` to confine SSO users to their spaces.

More in [Authentication and access](https://vayl.gitbook.io/vayl-docs/documentation/core-concepts/authentication-and-access) and [Configuration](https://vayl.gitbook.io/vayl-docs/documentation/reference/configuration).
