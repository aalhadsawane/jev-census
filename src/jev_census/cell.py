"""`Cell` (T1.8): the output/cache unit. One row per (document, question), with
the full provenance column set from `01-DESIGN.md` § Output schema. A `Cell`
is a `DecodedAnswer` (decoder.py) plus everything the call/document context
knows that the decoder itself doesn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .decoder import DecodedAnswer


@dataclass(frozen=True)
class Cell:
    doc_id: str
    question_id: str
    type: str
    noul: float | None
    choice: str | None
    score: float | None
    probabilities: dict[str, float] | None
    legend: dict[str, str] | None
    confidence: float | None
    confidence_source: str | None
    gate: str
    chunk_count: int
    projection_id: str
    call_id: str
    run_id: str
    model: str
    questionset_hash: str
    question_body_hash: str
    input_tokens: int
    ts: datetime


def build_cell(
    decoded: DecodedAnswer,
    *,
    doc_id: str,
    gate: str,
    projection_id: str,
    call_id: str,
    run_id: str,
    model: str,
    questionset_hash: str,
    question_body_hash: str,
    input_tokens: int,
    ts: datetime,
    chunk_count: int = 1,
) -> Cell:
    return Cell(
        doc_id=doc_id,
        question_id=decoded.question_id,
        type=decoded.type,
        noul=decoded.noul,
        choice=decoded.choice,
        score=decoded.score,
        probabilities=decoded.probabilities,
        legend=decoded.legend,
        confidence=decoded.confidence,
        confidence_source=decoded.confidence_source,
        gate=gate,
        chunk_count=chunk_count,
        projection_id=projection_id,
        call_id=call_id,
        run_id=run_id,
        model=model,
        questionset_hash=questionset_hash,
        question_body_hash=question_body_hash,
        input_tokens=input_tokens,
        ts=ts,
    )
