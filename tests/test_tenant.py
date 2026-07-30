"""Tenant hard-partitioning (Level 2 multi-tenancy): a principal's tenant filters every store query,
so one org never sees another's memory even under the SAME user_id. Covers the store partition, the
default fallback, the Principal round-trip, and the request-path binding via set_principal."""
import pytest

from vayl.memory.reconcile import Statement, Status
from vayl.storage.store import Store, bind_tenant, reset_tenant


@pytest.fixture(autouse=True)
def _plaintext(monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "off")
    monkeypatch.setenv("VAYL_SIGN", "off")
    monkeypatch.delenv("VAYL_DATABASE_URL", raising=False)


def _put(st, tenant, user, subj, val):
    tok = bind_tenant(tenant)
    try:
        m = st.load(user)
        m.statements.append(Statement(f"{subj}@g", subj, val, "global",
                                      status=Status.ACTIVE, id=len(m.statements) + 1, raw=val))
        st.save(user, m)
    finally:
        reset_tenant(tok)


def _active(st, tenant, user):
    tok = bind_tenant(tenant)
    try:
        return sorted(s.value for s in st.load(user).active())
    finally:
        reset_tenant(tok)


def test_tenants_are_isolated_under_the_same_user_id(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    _put(st, "orgA", "u", "plan", "Enterprise")
    _put(st, "orgB", "u", "plan", "Free")            # same user_id, different org
    assert _active(st, "orgA", "u") == ["Enterprise"]
    assert _active(st, "orgB", "u") == ["Free"]       # never sees orgA


def test_unbound_request_uses_the_default_tenant(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    _put(st, "default", "u", "x", "v")
    assert sorted(s.value for s in st.load("u").active()) == ["v"]   # no bind → default tenant
    assert _active(st, "orgA", "u") == []                            # a bound org sees nothing


def test_principal_tenant_round_trips_through_create_and_verify(tmp_path):
    from vayl.auth.auth import Auth, Role
    from vayl.storage.db import Database
    a = Auth(Database(str(tmp_path / "a.db")))
    p, key = a.create("bot", roles=Role.AGENT, tenant="orgX")
    assert p.tenant == "orgX"
    assert a.verify(key).tenant == "orgX"
    _, k2 = a.create("bot2", roles=Role.AGENT)          # unset -> default
    assert a.verify(k2).tenant == "default"


def test_set_principal_binds_and_resets_the_store_tenant():
    import vayl.api.mcp_server as srv
    from vayl.auth.auth import Principal
    from vayl.storage import store as sm
    tok = srv.set_principal(Principal(id="p", name="x", tenant="orgA"))
    try:
        assert sm._TENANT.get() == "orgA"
    finally:
        srv.reset_principal(tok)
    assert sm._TENANT.get() is None
