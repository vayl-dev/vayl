"""Built-in slot-schema presets: loadable via `preset:<name>`, resolvable by alias, carrying the
safety flags (confirm / multi / critical) each domain needs — so a deployment gets them with one
env var instead of hand-authoring JSON."""
import pytest

from vayl.memory import schema


def test_list_presets_bundles_the_three_domains():
    assert set(schema.list_presets()) >= {"clinical", "finance", "support"}


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
