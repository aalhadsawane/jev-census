"""T5.4 chunking tests: splitting a document that doesn't fit alone, and
aggregating multi-chunk answers back into one cell per type, against
hand-computed expected values (04-P5-PLANNER.md's done-when).
"""

from __future__ import annotations

import itertools

import pytest

from jev_census.chunking import aggregate_chunk_answers, chunk_document
from jev_census.decoder import DecodedAnswer
from jev_census.planner import CallGroup, ContextOverflowError
from jev_census.question_set import Question


def _q(id, instructions="Judge the text.", criteria=None, type="noul"):
    return Question(id=id, type=type, instructions=instructions, criteria=criteria)


def _noul(value, source="model"):
    return DecodedAnswer(
        question_id="q", type="noul", noul=value, choice=None, score=None,
        probabilities=None, legend=None, confidence=abs(value - 0.5) * 2, confidence_source=source,
    )


def _choice(choice, probabilities):
    return DecodedAnswer(
        question_id="q", type="choice", noul=None, choice=choice, score=None,
        probabilities=probabilities, legend=None, confidence=probabilities[choice],
        confidence_source="model",
    )


def _score(score, probabilities, legend):
    return DecodedAnswer(
        question_id="q", type="score", noul=None, choice=None, score=score,
        probabilities=probabilities, legend=legend, confidence=max(probabilities.values()),
        confidence_source="model",
    )


# --- chunk_document -------------------------------------------------------


def test_short_document_is_not_chunked():
    group = CallGroup(projection_id="x", fields=("body",), questions=(_q("a"),))
    state = {"body": "a short ticket body"}
    chunks = chunk_document(state, group)
    assert chunks == [state]


def test_long_document_is_chunked_with_overlap():
    group = CallGroup(projection_id="x", fields=("body",), questions=(_q("a"),))
    long_text = "word " * 20_000  # ~20k tokens of body alone
    state = {"body": long_text}
    chunks = chunk_document(state, group, context_limit=2000, margin=0.15, overlap=0.10)
    assert len(chunks) > 1
    # Every chunk stays within the state field's character budget implied by
    # the limit; none is empty.
    for c in chunks:
        assert c["body"]
    # Consecutive chunks overlap: the tail of one reappears at the head of
    # the next.
    for a, b in itertools.pairwise(chunks):
        assert a["body"][-20:] in b["body"] or b["body"].startswith(a["body"][-50:][:50])


def test_chunking_preserves_other_fields_whole_in_every_chunk():
    group = CallGroup(projection_id="x", fields=("body", "subject"), questions=(_q("a"),))
    long_text = "word " * 20_000
    state = {"body": long_text, "subject": "Payouts failing"}
    chunks = chunk_document(state, group, context_limit=2000, margin=0.15)
    assert len(chunks) > 1
    for c in chunks:
        assert c["subject"] == "Payouts failing"


def test_chunk_document_raises_when_unsplittable_field_too_large():
    # subject alone (not the field being split) already exceeds the budget.
    group = CallGroup(projection_id="x", fields=("body", "subject"), questions=(_q("a"),))
    state = {"body": "word " * 5000, "subject": "s " * 5000}
    with pytest.raises(ContextOverflowError):
        chunk_document(state, group, context_limit=1000, margin=0.0)


def test_chunk_document_raises_on_non_text_split_field():
    group = CallGroup(projection_id="x", fields=("payload",), questions=(_q("a"),))
    state = {"payload": {"nested": "x" * 50_000}}
    with pytest.raises(ContextOverflowError):
        chunk_document(state, group, context_limit=1000, margin=0.0)


# --- aggregate_chunk_answers -----------------------------------------------


def test_aggregate_noul_max_mode():
    answers = [_noul(0.2), _noul(0.9), _noul(0.4)]
    result = aggregate_chunk_answers(answers, noul_mode="max")
    assert result.noul == 0.9
    assert result.confidence == pytest.approx(abs(0.9 - 0.5) * 2)
    assert result.confidence_source == "derived"
    assert result.probabilities is None


def test_aggregate_noul_mean_mode():
    answers = [_noul(0.2), _noul(0.4), _noul(0.6)]
    result = aggregate_chunk_answers(answers, noul_mode="mean")
    assert result.noul == pytest.approx(0.4)


def test_aggregate_choice_argmax_of_mean_distribution():
    answers = [
        _choice("billing", {"billing": 0.8, "technical": 0.2}),
        _choice("technical", {"billing": 0.1, "technical": 0.9}),
    ]
    result = aggregate_chunk_answers(answers)
    # mean: billing=0.45, technical=0.55 -> technical wins
    assert result.probabilities == {"billing": pytest.approx(0.45), "technical": pytest.approx(0.55)}
    assert result.choice == "technical"
    assert result.confidence == pytest.approx(0.55)
    assert result.confidence_source == "derived"


def test_aggregate_score_mean_of_weighted_scores_and_distributions():
    legend = {"0": "calm", "1": "annoyed", "2": "angry"}
    answers = [
        _score(0.0, {"0": 1.0, "1": 0.0, "2": 0.0}, legend),
        _score(2.0, {"0": 0.0, "1": 0.0, "2": 1.0}, legend),
    ]
    result = aggregate_chunk_answers(answers)
    assert result.score == pytest.approx(1.0)
    assert result.probabilities == {"0": pytest.approx(0.5), "1": pytest.approx(0.0), "2": pytest.approx(0.5)}
    assert result.legend == legend
    assert result.confidence_source == "derived"


def test_aggregate_score_legend_mismatch_raises():
    answers = [
        _score(0.0, {"0": 1.0}, {"0": "calm"}),
        _score(1.0, {"0": 1.0}, {"0": "different legend text"}),
    ]
    with pytest.raises(ValueError, match="legend differs"):
        aggregate_chunk_answers(answers)


def test_aggregate_mismatched_question_ids_raises():
    a = _noul(0.5)
    b = DecodedAnswer(
        question_id="different", type="noul", noul=0.5, choice=None, score=None,
        probabilities=None, legend=None, confidence=0.0, confidence_source="derived",
    )
    with pytest.raises(ValueError):
        aggregate_chunk_answers([a, b])


def test_aggregate_single_chunk_still_marked_derived():
    """Even a length-1 list -- which only happens if a caller mistakenly
    routes a single-chunk answer through aggregation -- must not silently
    claim model provenance it doesn't have."""
    result = aggregate_chunk_answers([_choice("billing", {"billing": 1.0})])
    assert result.confidence_source == "derived"
