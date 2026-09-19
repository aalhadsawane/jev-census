from pathlib import Path

import pytest

from jev_census.question_set import QuestionSet, load_question_set
from jev_census.validator import ValidationError, validate_question_set

FIXTURES = Path(__file__).parent / "fixtures"


def _qs(**questions_kwargs) -> QuestionSet:
    """Build a minimal valid QuestionSet, then override one question's fields."""
    base_question = {
        "id": "q1",
        "type": "noul",
        "instructions": "The message is a complaint.",
    }
    base_question.update(questions_kwargs)
    return QuestionSet.model_validate({"version": 1, "name": "t", "questions": [base_question]})


def test_valid_readme_example_has_no_errors():
    qs = load_question_set(FIXTURES / "support-triage.yaml")
    warnings = validate_question_set(qs)
    assert warnings == []


def test_duplicate_ids_rejected():
    qs = QuestionSet.model_validate(
        {
            "version": 1,
            "name": "t",
            "questions": [
                {"id": "dup", "type": "noul", "instructions": "The message is urgent."},
                {"id": "dup", "type": "noul", "instructions": "The message is angry."},
            ],
        }
    )
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("not unique" in e for e in exc.value.errors)


def test_id_unsafe_as_column_name_rejected():
    qs = _qs(id="not a valid id!")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("safe as a column name" in e for e in exc.value.errors)


def test_choice_requires_at_least_two_options():
    qs = _qs(type="choice", criteria={"only_one": "desc"})
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("at least 2 criteria" in e for e in exc.value.errors)


def test_choice_null_rubric_warns_not_errors():
    qs = _qs(type="choice", criteria={"a": "desc", "b": None})
    warnings = validate_question_set(qs)
    assert any("null rubric" in w for w in warnings)


def test_score_requires_at_least_two_levels():
    qs = _qs(type="score", criteria=["only one level"])
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("at least 2 ordered levels" in e for e in exc.value.errors)


def test_score_criteria_must_be_a_list():
    qs = _qs(type="score", criteria={"not": "a list"})
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("ordered criteria array" in e for e in exc.value.errors)


def test_noul_criteria_must_have_true_and_false_keys():
    qs = _qs(type="noul", criteria={"yes": "a", "no": "b"})
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any('"true": ..., "false"' in e for e in exc.value.errors)


def test_noul_inverted_or_copy_pasted_criteria_rejected():
    qs = _qs(type="noul", criteria={"true": "Same text.", "false": "Same text."})
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("identical" in e for e in exc.value.errors)


def test_instructions_as_command_rejected():
    qs = _qs(instructions="Determine whether the customer is angry.")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("declarative proposition" in e for e in exc.value.errors)


def test_counting_language_rejected():
    qs = _qs(instructions="How many times did the customer mention a refund?")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("jaggedness #2" in e for e in exc.value.errors)


def test_average_language_rejected():
    qs = _qs(instructions="The average response time was acceptable.")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("jaggedness #2" in e for e in exc.value.errors)


def test_date_comparison_language_rejected():
    qs = _qs(instructions="The ticket was created before 2026-01-01.")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("jaggedness #3" in e for e in exc.value.errors)


def test_empty_instructions_rejected():
    qs = _qs(instructions="   ")
    with pytest.raises(ValidationError) as exc:
        validate_question_set(qs)
    assert any("must not be empty" in e for e in exc.value.errors)
