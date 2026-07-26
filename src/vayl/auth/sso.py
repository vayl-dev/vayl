#!/usr/bin/env python3
"""
OIDC SSO (M5) — accept IdP-issued ID tokens as an alternative to API keys, for HUMAN single sign-on.

A human logs into your IdP (Okta / Entra ID / Google / Auth0), receives an OIDC ID token (a signed
JWT), and presents it to `vayl-server` as `Authorization: Bearer <jwt>`. The server verifies the JWT
(signature against the IdP's JWKS, plus issuer / audience / expiry) and maps its claims to a Vayl
Principal + roles — so SSO users get the same RBAC as API-key principals, without provisioning keys.

Enterprise feature: SSO is only honored when the license grants "sso" (see license.allows). API keys
(vayl_sk_…) always work regardless; SSO is additive.

Config via env (all required to enable SSO):
    VAYL_OIDC_ISSUER      e.g. https://acme.okta.com/oauth2/default
    VAYL_OIDC_AUDIENCE    the client/audience the token is issued for
    VAYL_OIDC_JWKS_URL    the IdP's JWKS endpoint (from its /.well-known/openid-configuration)
Optional role mapping:
    VAYL_OIDC_ROLE_CLAIM  claim holding the user's groups/roles (default "groups")
    VAYL_OIDC_ROLE_MAP    JSON {idp_group: vayl_role}, e.g. {"vayl-admins":"admin","eng":"member"}
    VAYL_OIDC_DEFAULT_ROLE role for users with no mapped group (default "viewer")
Optional tenant scoping (multi-tenant deployments):
    VAYL_OIDC_SCOPE_CLAIM claim holding the user_ids this SSO user may touch (a list or CSV string).
                          UNSET → SSO users are unrestricted (correct for single-tenant). Set it to
                          confine SSO users the same way an API key's `scopes` does — otherwise every
                          SSO user can reach every memory space in the deployment.
"""
import json
import os
from dataclasses import dataclass, field

import jwt  # PyJWT

from vayl.auth.auth import Principal, Role, _parse_scopes


@dataclass
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    role_claim: str = "groups"
    role_map: dict = field(default_factory=dict)     # {idp_group: vayl_role_name}
    default_role: str = "viewer"
    scope_claim: str = ""                             # claim → user_ids this user may touch ("" = off)


def configured():
    """Build OidcConfig from env, or None if SSO isn't configured."""
    issuer = os.environ.get("VAYL_OIDC_ISSUER")
    audience = os.environ.get("VAYL_OIDC_AUDIENCE")
    jwks_url = os.environ.get("VAYL_OIDC_JWKS_URL")
    if not (issuer and audience and jwks_url):
        return None
    try:
        role_map = json.loads(os.environ.get("VAYL_OIDC_ROLE_MAP") or "{}")
    except ValueError:
        role_map = {}
    return OidcConfig(
        issuer=issuer, audience=audience, jwks_url=jwks_url,
        role_claim=os.environ.get("VAYL_OIDC_ROLE_CLAIM", "groups"),
        role_map=role_map,
        default_role=os.environ.get("VAYL_OIDC_DEFAULT_ROLE", "viewer"),
        scope_claim=os.environ.get("VAYL_OIDC_SCOPE_CLAIM", ""))


def map_scopes(claims, cfg):
    """Map a token claim to the user_ids this SSO principal may touch. When no scope claim is
    configured, returns [] — unrestricted, matching an API key created without scopes. Pure."""
    if not cfg.scope_claim:
        return []
    return _parse_scopes(claims.get(cfg.scope_claim))


def map_roles(claims, cfg):
    """Map a token's group claim to Vayl roles. Unmapped/empty → the configured default role. Pure."""
    groups = claims.get(cfg.role_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    roles = []
    for g in groups:
        name = cfg.role_map.get(g)
        if name:
            try:
                r = Role(name)
                if r not in roles:
                    roles.append(r)
            except ValueError:
                continue
    if not roles:
        try:
            roles = [Role(cfg.default_role)]
        except ValueError:
            roles = [Role.VIEWER]
    return roles


def looks_like_jwt(token):
    """A compact JWS has exactly three dot-separated segments; API keys (vayl_sk_…) don't."""
    return isinstance(token, str) and token.count(".") == 2 and not token.startswith("vayl_sk_")


class OidcVerifier:
    def __init__(self, cfg, signing_key=None):
        # `signing_key` overrides JWKS lookup (used by tests). In production the key is fetched from
        # the IdP's JWKS by key id (kid), so rotation is handled without redeploying.
        self.cfg = cfg
        self._override_key = signing_key
        self._jwk_client = None

    def _key_for(self, token):
        if self._override_key is not None:
            return self._override_key
        if self._jwk_client is None:
            from jwt import PyJWKClient
            self._jwk_client = PyJWKClient(self.cfg.jwks_url)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token):
        """Verify an OIDC ID token → Principal, or None if it fails any check."""
        try:
            key = self._key_for(token)
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                audience=self.cfg.audience, issuer=self.cfg.issuer,
                options={"require": ["exp", "iss", "aud"]})
        except Exception:                       # noqa: BLE001 - any verification failure → no principal
            return None
        sub = claims.get("sub") or claims.get("email")
        if not sub:
            return None
        name = claims.get("email") or claims.get("name") or sub
        return Principal(id=f"sso:{sub}", name=name, roles=map_roles(claims, self.cfg),
                         kind="human", scopes=map_scopes(claims, self.cfg))
