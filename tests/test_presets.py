"""Built-in slot-schema presets: loadable via `preset:<name>`, resolvable by alias, carrying the
safety flags (confirm / multi / critical) each domain needs — so a deployment gets them with one
env var instead of hand-authoring JSON."""
import pytest

from vayl.memory import schema


def test_list_presets_bundles_all_domains():
    assert set(schema.list_presets()) >= {"clinical", "finance", "support", "coding", "assistant", "sales"}


def test_every_preset_loads_and_has_no_alias_collisions():
    """Integrity guard for all bundled presets: each loads, is non-empty, and no two slots claim the
    same (normalized) name/alias — a collision would let one slot silently shadow another."""
    for name in schema.list_presets():
        sc = schema.load(path=f"preset:{name}")
        assert len(sc) > 0, name
        keys = [k for spec in sc.slots for k in spec.keys]
        assert len(keys) == len(set(keys)), f"alias collision in preset:{name}"


def test_coding_supersedes_stack_choices_and_lists_conventions():
    sc = schema.load(path="preset:coding")
    # 'Redux' and 'Zustand' both resolve to the SAME single-valued slot → a switch supersedes.
    state = sc.resolve("state_management")              # alias of state_library
    assert state.name == "state_library" and not state.multi and state.confirm is False
    # conventions are a verbatim guardrail list, surfaced via the critical-fact channel
    conv = sc.resolve("coding_convention")
    assert conv.name == "convention" and conv.multi and conv.verbatim and conv.category == "guardrail"
    assert sc.resolve("node_version").name == "runtime_version"   # alias resolves


def test_assistant_flags_dietary_restrictions_as_critical():
    sc = schema.load(path="preset:assistant")
    diet = sc.resolve("food_allergy")                   # alias of dietary_restriction
    assert diet.name == "dietary_restriction" and diet.category == "critical" and diet.multi
    assert sc.resolve("likes").name == "preference"     # alias resolves; ungated
    assert all(s.confirm is False for s in sc.slots)


def test_sales_deal_state_is_single_valued_and_ungated():
    sc = schema.load(path="preset:sales")
    assert sc.resolve("stage").name == "deal_stage" and not sc.resolve("stage").multi
    assert sc.resolve("action_item").name == "next_step" and sc.resolve("action_item").multi
    assert all(s.confirm is False for s in sc.slots)    # deal facts apply immediately


def test_clinical_meds_are_gated_allergies_are_not():
    sc = schema.load(path="preset:clinical")
    meds = sc.resolve("meds")                       # alias of active_medication
    assert meds.name == "active_medication"
    assert meds.confirm and meds.multi and meds.verbatim and meds.category == "critical"
    allergy = sc.resolve("allergies")
    # a list and critical, but NOT confirm-gated — surfacing a new allergy must be immediate
    assert allergy.name == "allergy" and allergy.multi and allergy.category == "critical"
    assert allergy.confirm is False


def test_finance_gates_kyc_and_risk():
    sc = schema.load(path="preset:finance")
    for name, alias in [("kyc_status", "kyc"), ("risk_tolerance", "risk_profile")]:
        spec = sc.resolve(alias)
        assert spec.name == name and spec.confirm and spec.category == "critical"
    assert sc.resolve("holding").name == "investment_holding"


def test_support_is_ungated_and_aliased():
    sc = schema.load(path="preset:support")
    assert sc.resolve("subscription").name == "plan"
    issue = sc.resolve("ticket")
    assert issue.name == "open_issue" and issue.multi and issue.confirm is False


def test_unknown_preset_raises_with_available_list():
    with pytest.raises(ValueError, match="unknown slot preset"):
        schema.load(path="preset:does-not-exist")


def test_preset_selected_via_env(monkeypatch):
    monkeypatch.setenv("VAYL_SLOT_SCHEMA", "preset:clinical")
    assert schema.load().resolve("allergy") is not None
