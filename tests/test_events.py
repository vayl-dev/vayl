"""
State vs event.

The same-slot invariant — at most one ACTIVE value per (subject, scope) — is what makes "which
value is current?" answerable. Applied to *events* it is destructive: two charity races, two phone
calls, two paintings are all true at once, and retiring one because another arrived on the same
subject deletes the record that it happened.

So events are exempt, and the exemption is enforced in the engine rather than trusted to the
extractor: a mislabelled SUPERSEDE on an event cannot silently delete a previous event.
"""

from vayl.memory.llm_memory import LLMMemory, _is_event
from vayl.memory.reconcile import Action, Status


def ev(subject, value, action="ADD", target_id=None, conf=0.9):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": conf, "time_ref": "present", "kind": "event"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def state(subject, value, action="ADD", target_id=None, conf=0.9, time_ref="present"):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": conf, "time_ref": time_ref, "kind": "state"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


# ── events coexist ───────────────────────────────────────────────────────────

def test_two_events_on_one_subject_both_stay_active():
    """The regression this exists to prevent: a second race retiring the first."""
    m = LLMMemory()
    m._apply(ev("alice_charity_race", "2023-05-21"), "ran a charity race")
    m._apply(ev("alice_charity_race", "2023-09-10"), "ran another charity race")

    values = sorted(s.value for s in m.active())
    assert values == ["2023-05-21", "2023-09-10"]
    assert all(s.status is Status.ACTIVE for s in m.statements)


def test_an_event_mislabelled_as_supersede_still_coexists():
    """Enforced in the engine, not delegated to the extractor."""
    m = LLMMemory()
    act1, _, _ = m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    first = m.active()[0]
    act2, _, _ = m._apply(ev("alice_race", "2023-09-10", action="SUPERSEDE",
                             target_id=first.id), "ran another race")
    assert act2 is not Action.SUPERSEDE
    assert len(m.active()) == 2
    assert first.status is Status.ACTIVE          # the earlier event survived


def test_an_event_cannot_be_retracted_by_removal_language():
    """'we dropped X' must not delete the record that X happened."""
    m = LLMMemory()
    m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    m._apply(ev("alice_race_cancelled", "2023-09-10"),
             "we dropped the September race and no longer run it")
    assert len(m.active()) == 2


def test_a_state_fact_does_not_retire_an_event_sharing_its_subject():
    m = LLMMemory()
    m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    m._apply(state("alice_race", "training"), "alice's race status is training")
    assert any(s.value == "2023-05-21" and s.status is Status.ACTIVE for s in m.statements)


def test_repeating_the_exact_same_event_still_dedups():
    """Exemption from supersession is not permission to store duplicates."""
    m = LLMMemory()
    m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    act, _, _ = m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    assert act is Action.DEDUP
    assert len(m.active()) == 1


def test_events_are_marked_so_the_exemption_survives_a_reload():
    m = LLMMemory()
    m._apply(ev("alice_race", "2023-05-21"), "ran a race")
    s = m.active()[0]
    assert _is_event(s)
    assert (s.metadata or {}).get("kind") == "event"


# ── state keeps the invariant ────────────────────────────────────────────────

def test_state_facts_still_supersede():
    """The exemption must not leak into state, which is the whole product."""
    m = LLMMemory()
    m._apply(state("db", "Postgres"), "we use Postgres")
    m._apply(state("db", "Aurora"), "we moved to Aurora")
    assert [s.value for s in m.active()] == ["Aurora"]


def test_untagged_facts_default_to_state():
    """Every existing caller omits `kind`; they must keep reconciling exactly as before."""
    m = LLMMemory()
    m._apply({"action": "ADD", "subject": "db", "value": "Postgres", "scope": "global",
              "confidence": 0.9, "time_ref": "present"}, "we use Postgres")
    m._apply({"action": "ADD", "subject": "db", "value": "Aurora", "scope": "global",
              "confidence": 0.9, "time_ref": "present"}, "we moved to Aurora")
    assert [s.value for s in m.active()] == ["Aurora"]
    assert not _is_event(m.active()[0])


def test_state_retraction_is_unaffected():
    m = LLMMemory()
    m._apply(state("mon", "Sentry"), "we use Sentry")
    act, _, _ = m._apply(state("mon", "Sentry", action="RETRACT"), "we dropped Sentry")
    assert act is Action.RETRACT
    assert not [s for s in m.active() if s.value == "Sentry"]


def test_is_event_is_false_for_plain_statements():
    m = LLMMemory()
    m._apply(state("db", "Postgres"), "x")
    assert not _is_event(m.active()[0])


# ── prompt contract ──────────────────────────────────────────────────────────

def test_prompt_documents_the_distinction_and_concrete_values():
    from vayl.memory.llm_memory import SYS
    assert "STATE vs EVENT" in SYS
    assert "Events NEVER replace each other" in SYS
    assert "CONCRETE VALUES" in SYS
    # the specific failure that motivated it
    assert "attended_recently" in SYS


def test_prompt_narrows_when_to_flag():
    from vayl.memory.llm_memory import SYS
    assert "Do NOT flag" in SYS
    assert "events never conflict" in SYS


# ── prompt hygiene ───────────────────────────────────────────────────────────

def test_prompt_does_not_contradict_itself_about_events():
    """The regression this guards: 'EXTRACT each distinct DURABLE fact' sat in line 2 — the
    highest-attention position — while the STATE vs EVENT block forty lines later said not to
    discard events. An event is not durable, so the early instruction cancelled the later one and
    events kept getting dropped. Adding a section without reconciling it against the existing text
    is how a prompt rots."""
    from vayl.memory.llm_memory import SYS
    assert "durable" not in SYS.lower()
    assert "both STATE that holds and EVENTS that happened" in SYS


def test_prompt_forbids_inventing_date_precision():
    """A weak model turned 'painted a sunrise in 2022' into '2022-01-01'. A fabricated day looks
    like evidence."""
    from vayl.memory.llm_memory import SYS
    assert "NEVER INVENT PRECISION" in SYS
    assert '"2022"' in SYS and "the day is unknown" in SYS


def test_prompt_anchors_confidence_to_the_flag_threshold():
    """confidence gates FLAG at AUTO_THRESHOLD, so an unanchored guess creates false disputes on
    ordinary statements — 15 of them were measured on benign narrative."""
    from vayl.memory.llm_memory import AUTO_THRESHOLD, SYS
    assert "CONFIDENCE —" in SYS
    assert str(AUTO_THRESHOLD) in SYS          # the prompt names the actual threshold
    assert "0.95" in SYS and "0.30" in SYS     # both ends of the scale are anchored


def test_schema_field_descriptions_carry_no_echoable_examples():
    """A 3B model emitted `org_state_management` for a sentence about databases — lifted from the
    prompt's own Redux example. Values that look like plausible outputs get copied as outputs."""
    from vayl.memory.llm_memory import SYS
    schema = SYS[SYS.index('{"facts":['):SYS.index("MULTIPLE FACTS")]
    for echoed in ("redux_toolkit", "Company B", "Fitbit", "Carol"):
        assert echoed not in schema, f"{echoed!r} sits in a field description and will be echoed"


def test_flag_reason_is_retained_for_the_reviewer():
    """`reason` was generated on every fact and never read — the tokens were paid for and the value
    discarded. A flagged fact goes to a human, and 'why' is the first thing they need."""
    m = LLMMemory()
    m._apply(state("db", "Postgres"), "we use Postgres")
    m._apply({**state("db", "Aurora", action="FLAG"), "reason": "conflicting values, no change signal"},
             "someone said Aurora")
    flagged = [s for s in m.statements if s.status is Status.FLAGGED]
    assert flagged and (flagged[0].metadata or {}).get("reason") == "conflicting values, no change signal"


def test_prompt_forbids_catch_all_subjects():
    """Measured failure: a 3B extractor filed a painting, a self-care habit and a charity race all
    under `melanie_recent_work`. The same-slot invariant then dutifully retired each previous one —
    two true facts destroyed, and the audit log shows it as correct reconciliation.

    The invariant's ENFORCEMENT is deterministic, but its INPUT is a model-chosen subject. A subject
    broad enough to attract unrelated facts turns the guarantee into a data-destruction engine, so
    the prompt has to rule buckets out.
    """
    from vayl.memory.llm_memory import SYS
    assert "ONE SUBJECT = ONE ATTRIBUTE" in SYS
    assert "THE REPLACEMENT TEST" in SYS
    # the test must be phrased as a question the model can actually apply
    assert "would it be a CORRECTION of this fact?" in SYS
    # and name the grouping words that produced the failure
    for word in ("activity", "options", "details", "stuff"):
        assert word in SYS


def test_a_repeated_past_statement_archives_once():
    """The valid-time gate returns before the RESTATEMENT check, so it needed its own dedup guard.

    Without one, every repetition of a past-tense fact appended another HISTORICAL row — and
    history is never superseded or pruned, so it grows without bound. Measured on a real ingest:
    one subject holding five identical archived rows. That is precisely the additive behaviour the
    restatement rule exists to keep out of a reconciling store, leaking in through a path that
    returns early.
    """
    m = LLMMemory()
    past = {"action": "ADD", "subject": "old_stack", "value": "mysql", "scope": "global",
            "confidence": 0.9, "time_ref": "past"}
    a1, _, _ = m._apply(past, "we used mysql back then")
    a2, _, _ = m._apply(past, "we used mysql back then")
    a3, _, _ = m._apply(past, "historically it was mysql")

    assert a1 is Action.ARCHIVE
    assert a2 is Action.DEDUP and a3 is Action.DEDUP
    assert len([s for s in m.statements if s.status is Status.HISTORICAL]) == 1


def test_distinct_past_facts_still_archive_separately():
    """The guard must not collapse genuinely different history."""
    m = LLMMemory()
    for val in ("mysql", "oracle"):
        m._apply({"action": "ADD", "subject": "old_stack", "value": val, "scope": "global",
                  "confidence": 0.9, "time_ref": "past"}, f"we used {val}")
    assert len([s for s in m.statements if s.status is Status.HISTORICAL]) == 2


def test_extraction_retries_a_malformed_json_roll(monkeypatch):
    """A stochastic model returns invalid JSON a few percent of the time. _http_json retries
    transport errors, but a PARSE failure happens on the returned body and was not retried — one
    bad roll dropped the whole observation. A weak/local extractor made this a 7.5% chunk-failure
    rate on a benchmark ingest, enough to abort the run."""
    from vayl.memory import llm_memory as L
    calls = []

    def flaky(user):
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("model returned no JSON object")   # what _first_json raises
        return {"facts": [{"subject": "db", "value": "postgres", "action": "ADD"}]}

    monkeypatch.setattr(L, "_provider", lambda: "openai")
    monkeypatch.setattr(L, "_call_openai", flaky)
    out = L.llm_extract_classify("we use postgres", [])
    assert len(calls) == 3                       # two bad rolls, third succeeded
    assert out[0]["subject"] == "db"


def test_extraction_gives_up_after_the_retry_budget(monkeypatch):
    """Persistent failure still raises — a retry loop must not mask a genuinely broken endpoint."""
    from vayl.memory import llm_memory as L
    monkeypatch.setattr(L, "_provider", lambda: "openai")
    monkeypatch.setattr(L, "_EXTRACT_JSON_RETRIES", 2)
    monkeypatch.setattr(L, "_call_openai",
                        lambda user: (_ for _ in ()).throw(ValueError("always bad")))
    import pytest
    with pytest.raises(ValueError):
        L.llm_extract_classify("x", [])


# ── malformed-JSON salvage ───────────────────────────────────────────────────

def test_salvage_recovers_valid_facts_around_a_broken_one():
    """A 3B model drops a comma in a big facts array on complex input — and the SAME chunk fails
    every retry, so re-rolling cannot help. The other facts in the array are well-formed; dropping
    the whole observation over one broken element is a hole in memory."""
    from vayl.memory.llm_memory import _first_json
    missing_comma = ('{"facts":[{"subject":"db","value":"pg","action":"ADD"}'
                     '{"subject":"cache","value":"redis","action":"ADD"}]}')
    out = _first_json(missing_comma)
    assert [f["subject"] for f in out["facts"]] == ["db", "cache"]


def test_salvage_skips_only_the_unparseable_object():
    from vayl.memory.llm_memory import _first_json
    one_broken = ('{"facts":[{"subject":"a","value":"1","action":"ADD"},'
                  '{"subject":"b","value":,"action":"ADD"},'          # b is malformed
                  '{"subject":"c","value":"3","action":"ADD"}]}')
    out = _first_json(one_broken)
    assert [f["subject"] for f in out["facts"]] == ["a", "c"]        # b dropped, a and c kept


def test_a_clean_response_is_untouched_by_salvage():
    from vayl.memory.llm_memory import _first_json
    clean = '{"facts":[{"subject":"db","value":"pg","action":"ADD"}]}'
    assert _first_json(clean)["facts"][0]["subject"] == "db"


def test_unsalvageable_junk_still_raises():
    """Salvage must not turn a genuinely broken reply into a silent empty extraction — a reply with
    no JSON object at all still raises, so a dropped observation is loud, not silent."""
    import pytest

    from vayl.memory.llm_memory import _first_json
    with pytest.raises(ValueError):
        _first_json("the model refused and wrote prose with no objects at all")


def test_broken_wrapper_with_no_valid_inner_facts_raises():
    """If the array is broken AND every inner object is too, there is nothing to salvage — raise
    rather than store an empty extraction."""
    import pytest

    from vayl.memory.llm_memory import _first_json
    with pytest.raises(ValueError):
        _first_json('{"facts":[{"subject":,"value":}{"subject":}]}')
