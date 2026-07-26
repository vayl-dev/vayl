"""
Declared slots and the critical-fact channel — canonical vocabulary, fragment folding, and categories that bypass ranking.
"""

import json

import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import (
    CriticalOverflow,
    LLMMemory,
    _subject_similar,
    _subject_tokens,
    is_critical,
)
from vayl.memory.reconcile import Action, Statement
from vayl.memory.schema import SlotSchema, from_dict, load

# ══════════════════════════════════════════════════════════════════
# from test_slot_schema
# ══════════════════════════════════════════════════════════════════

CLINICAL = {"slots": [
    {"name": "allergy", "category": "critical", "verbatim": True,
     "description": "a substance the patient reacts to",
     "aliases": ["allergies", "patient_allergy", "drug_allergy"]},
    {"name": "active_medication", "category": "critical", "verbatim": True},
    {"name": "code_status", "category": "critical"},
]}


@pytest.fixture
def schema():
    return from_dict(CLINICAL)


@pytest.fixture
def clinical_engine(monkeypatch, schema):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", schema)
    return schema


def fact(subject, value, **kw):
    o = {"action": "ADD", "subject": subject, "value": value, "scope": "global",
         "confidence": 0.9, "time_ref": "present"}
    o.update(kw)
    return o


def test_a_declared_name_resolves_to_itself(schema):
    assert schema.canonical("allergy") == "allergy"


def test_aliases_fold_onto_the_canonical_slot(schema):
    for alias in ("allergies", "patient_allergy", "drug_allergy"):
        assert schema.canonical(alias) == "allergy"


def test_matching_tolerates_spelling_not_meaning(schema):
    """Case and separators fold; a semantically-similar name does NOT. Deciding two differently
    named things are the same slot must be declared, never inferred."""
    assert schema.canonical("Patient Allergy") == "allergy"
    assert schema.canonical("patient-allergy") == "allergy"
    assert schema.canonical("PATIENT_ALLERGY") == "allergy"
    assert schema.canonical("penicillin_reaction") == "penicillin_reaction"   # untouched


def test_undeclared_subjects_pass_through(schema):
    assert schema.canonical("melanie_self_care_practices") == "melanie_self_care_practices"
    assert schema.resolve("anything_else") is None


def test_an_empty_schema_is_falsey_and_changes_nothing():
    s = SlotSchema()
    assert not s and len(s) == 0
    assert s.canonical("whatever") == "whatever"
    assert s.prompt_fragment() == ""


def test_loads_from_a_file(tmp_path):
    p = tmp_path / "clinical.json"
    p.write_text(json.dumps(CLINICAL))
    s = load(str(p))
    assert len(s) == 3 and s.canonical("allergies") == "allergy"


def test_no_path_means_no_schema(monkeypatch):
    monkeypatch.delenv("VAYL_SLOT_SCHEMA", raising=False)
    assert not load()


def test_a_malformed_schema_raises_rather_than_falling_back(tmp_path):
    """Failing open would mean a clinical deployment believing allergies are canonicalised and
    always-injected when they are not — silence on exactly the guarantee the file provides."""
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(Exception):
        load(str(p))


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(str(tmp_path / "absent.json"))


def test_slots_without_a_name_are_dropped():
    s = from_dict({"slots": [{"name": ""}, {"description": "no name"}, {"name": "ok"}]})
    assert [x.name for x in s.slots] == ["ok"]


def test_fragment_lists_the_slots_and_marks_verbatim_ones(schema):
    frag = schema.prompt_fragment()
    assert '"allergy"' in frag and '"code_status"' in frag
    assert "VERBATIM" in frag
    assert "a substance the patient reacts to" in frag


def test_fragment_does_not_force_unrelated_facts_into_declared_slots(schema):
    """Forcing the nearest match would file a blood-pressure reading under `allergy`."""
    frag = schema.prompt_fragment()
    assert "name it freely as usual" in frag
    assert "Do NOT force" in frag


def test_every_provider_receives_the_vocabulary():
    """A schema that only applies on one provider silently stops applying when someone changes
    LLM_PROVIDER."""
    import inspect
    src = inspect.getsource(llm_memory)
    assert src.count("SLOT_SCHEMA.prompt_fragment()") >= 3


def test_a_declared_slot_canonicalizes_on_write(clinical_engine):
    m = LLMMemory()
    m._apply(fact("patient_allergy", "penicillin"), "allergic to penicillin")
    assert m.active()[0].subject == "allergy"


def test_canonicalization_lets_the_invariant_fire_across_different_namings(clinical_engine):
    """The point of the whole exercise: two namings of one slot now reconcile."""
    m = LLMMemory()
    m._apply(fact("patient_allergy", "penicillin"), "allergic to penicillin")
    m._apply(fact("allergies", "penicillin and latex"), "also allergic to latex")
    assert len(m.active()) == 1
    assert m.active()[0].value == "penicillin and latex"


def test_without_the_schema_the_same_writes_do_not_reconcile():
    """Establishes the test above is measuring the schema, not something else."""
    m = LLMMemory()
    m._apply(fact("patient_allergy", "penicillin"), "x")
    m._apply(fact("allergies", "penicillin and latex"), "y")
    assert len(m.active()) == 2


def test_declared_category_is_stamped_so_the_critical_channel_sees_it(clinical_engine):
    from vayl.memory.llm_memory import is_critical
    m = LLMMemory()
    m._apply(fact("patient_allergy", "penicillin"), "allergic to penicillin")
    s = m.active()[0]
    assert (s.metadata or {}).get("category") == "critical"
    assert is_critical(s, categories=["critical"])


def test_an_undeclared_fact_is_not_tagged(clinical_engine):
    m = LLMMemory()
    m._apply(fact("favourite_colour", "green"), "I like green")
    s = m.active()[0]
    assert (s.metadata or {}).get("category") is None
    assert s.subject == "favourite_colour"


def test_category_and_event_tags_coexist(clinical_engine):
    """Both are stamped into metadata; neither may clobber the other."""
    from vayl.memory.llm_memory import _is_event
    m = LLMMemory()
    m._apply(fact("patient_allergy", "penicillin", kind="event"), "reacted to penicillin")
    s = m.active()[0]
    assert _is_event(s)
    assert (s.metadata or {}).get("category") == "critical"


def test_no_schema_means_no_behaviour_change():
    m = LLMMemory()
    m._apply(fact("db", "Postgres"), "we use Postgres")
    m._apply(fact("db", "Aurora"), "we moved to Aurora")
    assert [s.value for s in m.active()] == ["Aurora"]
    assert m.active()[0].metadata is None


# ══════════════════════════════════════════════════════════════════
# from test_slot_resolution
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def resolve_on(monkeypatch):
    monkeypatch.setattr(llm_memory, "_SLOT_RESOLVE", True)


def _sr_fact(subject, value, kind="state", action="ADD"):
    return {"action": action, "subject": subject, "value": value, "scope": "global",
            "confidence": 0.9, "time_ref": "present", "kind": kind}


def test_date_and_ordinal_noise_is_stripped_from_subjects():
    """The observed pattern was a stable stem plus an appended date."""
    assert _subject_tokens("caroline_self_care_realization_8_may_2023") == \
        _subject_tokens("caroline_self_care_realization")
    assert _subject_tokens("event_25_december_2022") == {"event"}


def test_similarity_folds_the_observed_fragments():
    assert _subject_similar("caroline_support_group_attendance",
                            "caroline_support_group_attendance_8_may_2023")
    assert _subject_similar("caroline_self_care_realization_14_may_2023",
                            "caroline_self_care_realization_25_may_2023")


def test_distinct_attributes_do_not_match():
    """The protection that makes a loose threshold safe: different attributes share no tokens."""
    assert not _subject_similar("alice_city", "alice_birthplace")
    assert not _subject_similar("db_primary", "cache_layer")
    assert not _subject_similar("patient_allergy", "patient_medication")


def test_empty_or_all_noise_subjects_never_match():
    assert not _subject_similar("2023_05_08", "2023_06_09")   # both reduce to nothing
    assert not _subject_similar("", "anything")


def test_a_dated_fragment_folds_into_its_stem(resolve_on):
    m = LLMMemory()
    m._apply(_sr_fact("caroline_self_care_realization", "realized_importance_of_selfcare"),
             "she realised self-care matters")
    act, subj, _ = m._apply(
        _sr_fact("caroline_self_care_realization_14_may_2023", "realized_importance_of_selfcare"),
        "again, self-care matters")

    assert act is Action.DEDUP
    assert subj == "caroline_self_care_realization"          # folded onto the first name
    assert len(m.active()) == 1


def test_the_fold_never_overwrites_a_value(resolve_on):
    """The safety guarantee. A different value on a similar subject is left alone, as two facts —
    folding it would be a supersede, and this path must never supersede."""
    m = LLMMemory()
    m._apply(_sr_fact("caroline_support_group_attendance", "2023-05-08"), "went on the 8th")
    act, _, _ = m._apply(
        _sr_fact("caroline_support_group_attendance_9_june", "2023-06-09"), "went again in June")

    assert act is not Action.DEDUP
    assert len(m.active()) == 2                              # both dates survive


def test_events_never_fold(resolve_on):
    """Two events with the same value and similar subjects are still two events — folding one into
    the other is the exact data loss the event exemption exists to prevent."""
    m = LLMMemory()
    m._apply(_sr_fact("melanie_race", "charity_5k", kind="event"), "ran a 5k")
    act, _, _ = m._apply(_sr_fact("melanie_race_may", "charity_5k", kind="event"), "ran another 5k")

    assert act is not Action.DEDUP
    assert len(m.active()) == 2


def test_off_by_default_leaves_fragments_separate():
    """Without the flag, behaviour is exactly as before — the fragments stay as distinct slots."""
    m = LLMMemory()
    m._apply(_sr_fact("caroline_self_care_realization", "realized_importance"), "x")
    m._apply(_sr_fact("caroline_self_care_realization_8_may_2023", "realized_importance"), "y")
    assert len(m.active()) == 2


def test_a_declared_slot_is_not_touched_by_fragment_resolution(resolve_on, monkeypatch):
    """Declared slots already canonicalise deterministically; fragment resolution only handles the
    open-ended remainder (spec is None), so it must not second-guess the schema."""
    from vayl.memory.schema import from_dict
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA",
                        from_dict({"slots": [{"name": "allergy", "aliases": ["patient_allergy"]}]}))
    m = LLMMemory()
    m._apply(_sr_fact("allergy", "penicillin"), "allergic to penicillin")
    # a same-value near-similar subject that ALSO maps to the declared slot: the schema handles it,
    # via canonicalization, and the fold path is skipped (spec is not None)
    act, subj, _ = m._apply(_sr_fact("patient_allergy", "penicillin"), "allergic to penicillin")
    assert act is Action.DEDUP and subj == "allergy"


def test_an_exact_restatement_still_dedups_with_resolution_on(resolve_on):
    """The pre-existing exact-match dedup must keep working when the flag is on."""
    m = LLMMemory()
    m._apply(_sr_fact("db", "postgres"), "we use postgres")
    act, _, _ = m._apply(_sr_fact("db", "postgres"), "still postgres")
    assert act is Action.DEDUP and len(m.active()) == 1


def test_a_genuine_supersede_is_untouched(resolve_on):
    """Fragment resolution is ADD-only; a real supersede (new value, same slot) must still fire."""
    m = LLMMemory()
    m._apply(_sr_fact("db", "postgres"), "we use postgres")
    m._apply(_sr_fact("db", "aurora"), "we moved to aurora")
    assert [s.value for s in m.active()] == ["aurora"]


# ══════════════════════════════════════════════════════════════════
# from test_critical_facts
# ══════════════════════════════════════════════════════════════════

def crit(subject, value, category="allergy"):
    s = Statement(f"{subject}@global", subject, value, "global")
    s.metadata = {"category": category}
    s._emb = [0.0, 1.0]          # deliberately a poor match for the test queries
    return s


def noise(i):
    s = Statement(f"n{i}@global", f"noise_{i}", f"value_{i}", "global")
    s._emb = [1.0, 0.0]          # ranks above the critical facts on every query
    return s


@pytest.fixture
def echo_qa(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: [[1.0, 0.0] for _ in texts])


@pytest.fixture
def memory_with_buried_allergy(echo_qa):
    """One allergy among 60 better-matching facts — it cannot win on rank."""
    m = LLMMemory()
    m.statements = [noise(i) for i in range(60)] + [crit("patient_allergy", "penicillin")]
    return m


def test_is_critical_only_when_a_category_is_configured():
    s = crit("patient_allergy", "penicillin")
    assert not is_critical(s, categories=[])              # off by default
    assert is_critical(s, categories=["allergy"])
    assert not is_critical(s, categories=["medication"])


def test_is_critical_is_case_insensitive():
    s = crit("a", "b", category="Allergy")
    assert is_critical(s, categories=["ALLERGY"])


def test_untagged_facts_are_never_critical():
    assert not is_critical(noise(1), categories=["allergy"])


def test_a_critical_fact_reaches_context_despite_ranking_last(memory_with_buried_allergy):
    """The whole point: it loses on rank and is present anyway."""
    ctx = memory_with_buried_allergy.query("what noise values do we have?", k=10,
                                           critical_categories=["allergy"])
    assert "penicillin" in ctx


def test_without_the_category_the_same_fact_is_lost(memory_with_buried_allergy):
    """Establishes that the fixture really does bury it — otherwise the test above proves nothing."""
    ctx = memory_with_buried_allergy.query("what noise values do we have?", k=10)
    assert "penicillin" not in ctx


def test_critical_facts_do_not_consume_ranked_slots(echo_qa):
    m = LLMMemory()
    m.statements = [noise(i) for i in range(60)] + [crit("a1", "penicillin"),
                                                    crit("a2", "latex")]
    ctx = m.query("noise?", k=10, critical_categories=["allergy"])
    assert "penicillin" in ctx and "latex" in ctx
    # both criticals present, and ranked facts still filled the remaining budget
    assert ctx.count("noise_") >= 5


def test_a_critical_fact_is_not_duplicated_when_it_also_ranks(echo_qa):
    m = LLMMemory()
    s = crit("patient_allergy", "penicillin")
    s._emb = [1.0, 0.0]                       # now it WOULD rank well too
    m.statements = [noise(i) for i in range(20)] + [s]
    ctx = m.query("penicillin?", k=10, critical_categories=["allergy"])
    assert ctx.count("penicillin") == 1


def test_more_critical_facts_than_budget_raises_rather_than_truncating(echo_qa, monkeypatch):
    monkeypatch.setattr(llm_memory, "_CRITICAL_BUDGET", 3)
    m = LLMMemory()
    m.statements = [crit(f"a{i}", f"drug_{i}") for i in range(5)]
    with pytest.raises(CriticalOverflow) as e:
        m.query("allergies?", k=10, critical_categories=["allergy"])
    assert "truncating" in str(e.value)       # the message says why it refused


def test_overflow_does_not_fire_within_budget(echo_qa, monkeypatch):
    monkeypatch.setattr(llm_memory, "_CRITICAL_BUDGET", 5)
    m = LLMMemory()
    m.statements = [crit(f"a{i}", f"drug_{i}") for i in range(5)]
    assert "drug_0" in m.query("allergies?", k=10, critical_categories=["allergy"])


def test_safe_recall_passes_critical_categories_down(memory_with_buried_allergy):
    """A gate can only judge what it was handed. If a critical fact were ranked out, safe_recall
    would pass on an answer it never knew was incomplete."""
    res = memory_with_buried_allergy.safe_recall("what noise values?", k=10,
                                                 critical_categories=["allergy"])
    assert any(p["value"] == "penicillin" for p in res["used"])


def test_default_behaviour_is_unchanged(echo_qa):
    """A general-purpose deployment configures no critical categories and must behave exactly as
    before — inventing categories would be worse than having none."""
    m = LLMMemory()
    m.statements = [noise(i) for i in range(60)]
    ctx = m.query("noise?", k=10)
    assert "noise_" in ctx


def test_env_var_drives_the_default(monkeypatch):
    monkeypatch.setattr(llm_memory, "_CRITICAL_CATEGORIES", ("allergy",))
    assert is_critical(crit("a", "b"))
    assert not is_critical(noise(1))


def test_mcp_parses_a_category_list():
    from vayl.api.mcp_server import _critical
    assert _critical("allergy, active_medication") == ["allergy", "active_medication"]
    assert _critical("allergy") == ["allergy"]


def test_mcp_empty_means_use_the_deployment_default_not_none():
    """The security property of the parser. If empty parsed to [], a caller could silently opt out
    of always-injecting the categories an operator marked critical — turning a deployment-wide
    safety guarantee into something any caller can switch off by omitting an argument. None means
    'fall through to VAYL_CRITICAL_CATEGORIES'."""
    from vayl.api.mcp_server import _critical
    assert _critical("") is None
    assert _critical(None) is None
    assert _critical("   ,  ,") is None


def test_mcp_recall_and_safe_recall_accept_the_parameter():
    import inspect

    from vayl.api import mcp_server as srv
    for tool in ("recall", "safe_recall"):
        sig = inspect.signature(getattr(srv, tool).fn if hasattr(getattr(srv, tool), "fn")
                                else getattr(srv, tool))
        assert "critical_categories" in sig.parameters, f"{tool} is missing the parameter"
