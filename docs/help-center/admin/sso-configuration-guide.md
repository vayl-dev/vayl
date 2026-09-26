---
description: Log in with your identity provider using OIDC.
icon: key
---

# SSO configuration guide

With an Enterprise license, `vayl-server` accepts OIDC ID tokens (JWT) as an alternative to API keys. Configure it with environment variables:

| Variable                                      | Purpose                              |
| --------------------------------------------- | ------------------------------------ |
| `VAYL_OIDC_ISSUER`                            | your IdP's issuer URL                |
| `VAYL_OIDC_AUDIENCE`                          | the audience the token is issued for |
| `VAYL_OIDC_JWKS_URL`                          | the IdP's JWKS endpoint              |
| `VAYL_OIDC_ROLE_CLAIM` / `VAYL_OIDC_ROLE_MAP` | map an IdP group claim to Vayl roles |
| `VAYL_OIDC_SCOPE_CLAIM`                       | map a claim to tenant scopes         |

Tokens are verified against the IdP's JWKS with `iss`, `aud`, and `exp` enforced and the algorithm pinned to RS256. API keys keep working alongside SSO.
