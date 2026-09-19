"""Decoder fixtures mirror the live-confirmed response shapes recorded in
docs/00-JEV-API.md exactly (noul 0.99 refund example; the choice/score
batch example)."""

import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse, Usage

from jev_census.decoder import DecodeError, decode_answers
from jev_census.question_set import Question


def _response(**answers) -> SystemOneResponse:
    return SystemOneResponse(
        model="jev-1.13.0", usage=Usage(input_tokens=282, output_tokens=21), answers=answers
    )


def test_decode_noul_has_no_model_probabilities_and_derived_confidence():
    q = Question(id="refunded", type="noul", instructions="Was a refund issued?")
    response = _response(refunded=NoulAnswer(noul=0.99))

    [decoded] = decode_answers(response, {"refunded": q})

    assert decoded.type == "noul"
    assert decoded.noul == 0.99
    assert decoded.choice is None
    assert decoded.score is None
    assert decoded.probabilities is None
    assert decoded.legend is None
    assert decoded.confidence_source == "derived"
    assert decoded.confidence == pytest.approx(abs(0.99 - 0.5) * 2)


def test_decode_noul_near_half_has_low_derived_confidence():
    q = Question(id="ambiguous", type="noul", instructions="It is ambiguous.")
    response = _response(ambiguous=NoulAnswer(noul=0.5))
    [decoded] = decode_answers(response, {"ambiguous": q})
    assert decoded.confidence == 0.0


def test_decode_choice_preserves_probabilities_and_model_confidence():
    q = Question(
        id="department", type="choice", instructions="x", criteria={"billing": "a", "shipping": "b"}
    )
    response = _response(
        department=ChoiceAnswer(
            choice="billing", confidence=1.0, probabilities={"billing": 1.0, "shipping": 0.0}
        )
    )
    [decoded] = decode_answers(response, {"department": q})

    assert decoded.type == "choice"
    assert decoded.choice == "billing"
    assert decoded.confidence_source == "model"
    assert decoded.confidence == 1.0
    assert decoded.probabilities == {"billing": 1.0, "shipping": 0.0}
    assert decoded.legend is None


def test_decode_score_preserves_legend_as_string_keys():
    q = Question(id="coverage", type="score", instructions="x", criteria=["poor", "fair", "good", "excellent"])
    response = _response(
        coverage=ScoreAnswer(
            score=2.92,
            confidence=0.92,
            legend={0: "poor: no tests or docs", 1: "fair", 2: "good", 3: "excellent"},
            probabilities={0: 0.0, 1: 0.0, 2: 0.07, 3: 0.93},
        )
    )
    [decoded] = decode_answers(response, {"coverage": q})

    assert decoded.type == "score"
    assert decoded.score == 2.92
    assert decoded.confidence_source == "model"
    assert decoded.legend == {"0": "poor: no tests or docs", "1": "fair", "2": "good", "3": "excellent"}
    assert decoded.probabilities == {"0": 0.0, "1": 0.0, "2": 0.07, "3": 0.93}
    assert all(isinstance(k, str) for k in decoded.legend)
    assert all(isinstance(k, str) for k in decoded.probabilities)


def test_mixed_batch_decodes_all_three_types_one_state():
    """Batching independence, reproduced in miniature (T0.6 / T1.7 done-when)."""
    questions = {
        "is_urgent": Question(id="is_urgent", type="noul", instructions="x"),
        "churn_risk": Question(id="churn_risk", type="noul", instructions="y"),
        "frustration": Question(id="frustration", type="score", instructions="z", criteria=["a", "b"]),
    }
    response = _response(
        is_urgent=NoulAnswer(noul=0.97),
        churn_risk=NoulAnswer(noul=0.21),
        frustration=ScoreAnswer(
            score=2.7, confidence=0.74, legend={0: "a", 1: "b"}, probabilities={0: 0.3, 1: 0.7}
        ),
    )
    decoded = {d.question_id: d for d in decode_answers(response, questions)}

    assert decoded["is_urgent"].confidence_source == "derived"
    assert decoded["churn_risk"].confidence_source == "derived"
    assert decoded["frustration"].confidence_source == "model"
    # only the score question carries a model confidence; the two nouls don't
    # (00-JEV-API.md: "only the score question carried a confidence key")
    assert decoded["is_urgent"].noul == 0.97
    assert decoded["churn_risk"].noul == 0.21


def test_missing_answer_id_raises_and_discards_whole_call():
    q = Question(id="refunded", type="noul", instructions="x")
    response = _response()  # no answers at all
    with pytest.raises(DecodeError) as exc:
        decode_answers(response, {"refunded": q})
    assert any("missing answer" in e for e in exc.value.errors)


def test_type_mismatch_raises():
    q = Question(id="refunded", type="choice", instructions="x", criteria={"a": "1", "b": "2"})
    response = _response(refunded=NoulAnswer(noul=0.5))
    with pytest.raises(DecodeError) as exc:
        decode_answers(response, {"refunded": q})
    assert any("expected type 'choice', got 'noul'" in e for e in exc.value.errors)


def test_multiple_errors_all_reported_not_just_first():
    questions = {
        "a": Question(id="a", type="noul", instructions="x"),
        "b": Question(id="b", type="choice", instructions="y", criteria={"p": "1", "q": "2"}),
    }
    response = _response()  # both missing
    with pytest.raises(DecodeError) as exc:
        decode_answers(response, questions)
    assert len(exc.value.errors) == 2
