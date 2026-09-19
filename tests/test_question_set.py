from pathlib import Path

import pytest

from jev_census.question_set import load_question_set

FIXTURES = Path(__file__).parent / "fixtures"


def test_readme_support_triage_parses():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    assert qs.name == "support-triage"
    assert qs.version == 1
    assert [q.id for q in qs.questions] == ["is_urgent", "department", "frustration", "churn_risk"]
    assert qs.defaults.projection == ["subject", "body"]


def test_resolved_projection_falls_back_to_default():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    is_urgent = next(q for q in qs.questions if q.id == "is_urgent")
    assert qs.resolved_projection(is_urgent) == ["subject", "body"]


def test_question_projection_override_wins():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    overridden = qs.questions[0].model_copy(update={"projection": ["subject", "body", "thread"]})
    assert qs.resolved_projection(overridden) == ["subject", "body", "thread"]


def test_resolved_gate_defaults_to_strict():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    for q in qs.questions:
        assert qs.resolved_gate(q) == "strict"


def test_questionset_hash_stable_and_order_sensitive():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    h1 = qs.questionset_hash
    h2 = load_question_set(FIXTURES / "support-triage.yaml").questionset_hash
    assert h1 == h2

    reordered = qs.model_copy(update={"questions": list(reversed(qs.questions))})
    assert reordered.questionset_hash != h1


def test_body_hash_changes_when_criteria_changes():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    q = qs.questions[0]
    changed = q.model_copy(update={"criteria": {"true": "different", "false": "still different"}})
    assert q.body_hash != changed.body_hash


def test_unknown_top_level_field_rejected():
    from pydantic import ValidationError as PydanticValidationError

    from jev_census.question_set import QuestionSet

    with pytest.raises(PydanticValidationError):
        QuestionSet.model_validate(
            {
                "version": 1,
                "name": "x",
                "questions": [{"id": "a", "type": "noul", "instructions": "A thing is true."}],
                "unexpected_field": True,
            }
        )
