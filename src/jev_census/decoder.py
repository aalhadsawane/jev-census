"""Decoder (T1.7): turn a `SystemOneResponse` into typed, per-question answer
fragments. Doc/call-level provenance (doc_id, call_id, run_id, ts, ...) is not
this stage's job — it belongs to whoever plans and writes the `Cell` (T1.9 for
now, the Writer in general); this module only owns the three answer shapes.

Per the stage contract in 01-DESIGN.md: "Every requested question id present
and type-matched, or the call is discarded." A missing id or a type mismatch
raises `DecodeError` for the whole call rather than returning partial results.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from .question_set import Question


class DecodeError(Exception):
    """Raised with every mismatch found (missing id, wrong type). The whole
    call is discarded — never write a partial call's results (01-DESIGN.md
    Hard rules)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class DecodedAnswer:
    question_id: str
    type: str
    noul: float | None
    choice: str | None
    score: float | None
    probabilities: dict[str, float] | None
    legend: dict[str, str] | None
    confidence: float | None
    confidence_source: str | None  # "model" | "derived"


def _stringify_legend_value(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _decode_noul(question_id: str, answer: NoulAnswer) -> DecodedAnswer:
    # No `probabilities`, no model-reported `confidence` — the API never sends
    # one for noul (00-JEV-API.md). Derived here, and only ever presented as derived.
    derived_confidence = abs(answer.noul - 0.5) * 2
    return DecodedAnswer(
        question_id=question_id,
        type="noul",
        noul=answer.noul,
        choice=None,
        score=None,
        probabilities=None,
        legend=None,
        confidence=derived_confidence,
        confidence_source="derived",
    )


def _decode_choice(question_id: str, answer: ChoiceAnswer) -> DecodedAnswer:
    return DecodedAnswer(
        question_id=question_id,
        type="choice",
        noul=None,
        choice=answer.choice,
        score=None,
        probabilities=dict(answer.probabilities),
        legend=None,
        confidence=answer.confidence,
        confidence_source="model",
    )


def _decode_score(question_id: str, answer: ScoreAnswer) -> DecodedAnswer:
    # SDK keys legend/probabilities by int level index; output schema wants
    # string keys (01-DESIGN.md: "level index as string -> p").
    legend = {str(level): _stringify_legend_value(desc) for level, desc in answer.legend.items()}
    probabilities = {str(level): p for level, p in answer.probabilities.items()}
    return DecodedAnswer(
        question_id=question_id,
        type="score",
        noul=None,
        choice=None,
        score=answer.score,
        probabilities=probabilities,
        legend=legend,
        confidence=answer.confidence,
        confidence_source="model",
    )


_DECODERS = {
    NoulAnswer: _decode_noul,
    ChoiceAnswer: _decode_choice,
    ScoreAnswer: _decode_score,
}


def decode_answers(
    response: SystemOneResponse, questions: Mapping[str, Question]
) -> list[DecodedAnswer]:
    """Decode every requested question's answer. Raises `DecodeError` (listing
    every problem found) if any id is missing or type-mismatched; returns
    nothing partial."""
    errors: list[str] = []
    decoded: list[DecodedAnswer] = []

    for question_id, question in questions.items():
        answer = response.answers.get(question_id)
        if answer is None:
            errors.append(f"missing answer for question id '{question_id}'")
            continue
        if answer.type != question.type:
            errors.append(
                f"question '{question_id}': expected type '{question.type}', got '{answer.type}'"
            )
            continue
        decoder = _DECODERS[type(answer)]
        decoded.append(decoder(question_id, answer))

    if errors:
        raise DecodeError(errors)
    return decoded
