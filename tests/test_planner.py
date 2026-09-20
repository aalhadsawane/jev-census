"""T5.2 planner tests: grouping, projection, and context-limit splitting.
Pure and network-free, per 04-P5-PLANNER.md's property-test list.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jev_census.planner import (
    CONTEXT_LIMIT_TOKENS,
    CallGroup,
    ContextOverflowError,
    plan_call_groups,
    project_state,
    question_schema_tokens,
    split_for_context,
    verify_projection_fields,
)
from jev_census.question_set import Question, QuestionSet, QuestionSetDefaults
from jev_census.token_estimator import estimate_tokens

REPO_ROOT = Path(__file__).resolve().parent.parent


def _q(id, projection=None, type="noul", instructions="The message is urgent."):
    return Question(id=id, type=type, instructions=instructions, projection=projection)


def _qs(*questions, default_projection=None):
    return QuestionSet(
        version=1,
        name="t",
        defaults=QuestionSetDefaults(projection=default_projection),
        questions=list(questions),
    )


def test_single_projection_produces_one_group():
    qs = _qs(_q("a"), _q("b"), _q("c"), default_projection=["subject", "body"])
    groups = plan_call_groups(qs)
    assert len(groups) == 1
    assert groups[0].fields == ("body", "subject")
    assert [q.id for q in groups[0].questions] == ["a", "b", "c"]


def test_two_projections_produce_two_groups():
    qs = _qs(
        _q("a", projection=["subject", "body"]),
        _q("b", projection=["subject", "body", "thread"]),
        _q("c", projection=["subject", "body"]),
    )
    groups = plan_call_groups(qs)
    assert len(groups) == 2
    assert [q.id for q in groups[0].questions] == ["a", "c"]
    assert [q.id for q in groups[1].questions] == ["b"]
    assert groups[1].fields == ("body", "subject", "thread")


def test_every_question_in_exactly_one_group():
    qs = _qs(
        _q("a", projection=["x"]),
        _q("b", projection=["y"]),
        _q("c", projection=["x"]),
        _q("d", projection=["z"]),
    )
    groups = plan_call_groups(qs)
    all_ids = [q.id for group in groups for q in group.questions]
    assert sorted(all_ids) == ["a", "b", "c", "d"]
    assert len(all_ids) == len(qs.questions)


def test_grouping_deterministic_across_processes():
    """Same question set -> same group ids, in a fresh interpreter, not just
    within this one (hashing.py's own stability guarantee, applied here)."""
    script = (
        "from jev_census.planner import plan_call_groups; "
        "from jev_census.question_set import load_question_set; "
        "qs = load_question_set('tests/fixtures/support-triage.yaml'); "
        "groups = plan_call_groups(qs); "
        "print([(g.projection_id, g.fields) for g in groups])"
    )
    results = set()
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        results.add(out.stdout)
    assert len(results) == 1, "plan_call_groups produced different ids across processes"


def test_reordered_yaml_same_fields_same_group_id():
    """Two questions declaring the same fields in a different order land in
    the same group with the same projection_id (04-P5-PLANNER.md)."""
    qs_a = _qs(_q("a", projection=["subject", "body"]))
    qs_b = _qs(_q("a", projection=["body", "subject"]))
    groups_a = plan_call_groups(qs_a)
    groups_b = plan_call_groups(qs_b)
    assert groups_a[0].projection_id == groups_b[0].projection_id


def test_projection_id_is_not_ordinal():
    """Reordering questions in the YAML must not change a group's id -- an
    ordinal ('p0', 'p1', ...) would; a hash of the sorted fields doesn't."""
    qs_1 = _qs(
        _q("a", projection=["subject", "body"]),
        _q("b", projection=["subject", "body", "thread"]),
    )
    qs_2 = _qs(
        _q("b", projection=["subject", "body", "thread"]),
        _q("a", projection=["subject", "body"]),
    )
    ids_1 = {g.fields: g.projection_id for g in plan_call_groups(qs_1)}
    ids_2 = {g.fields: g.projection_id for g in plan_call_groups(qs_2)}
    assert ids_1 == ids_2


def test_project_state_narrows_to_group_fields_only():
    group = CallGroup(projection_id="x", fields=("body", "subject"), questions=(_q("a"),))
    doc_fields = {"subject": "hi", "body": "text", "thread": "should not appear"}
    state = project_state(doc_fields, group)
    assert state == {"body": "text", "subject": "hi"}
    assert "thread" not in state


def test_project_state_missing_field_becomes_none():
    group = CallGroup(projection_id="x", fields=("missing_field",), questions=(_q("a"),))
    state = project_state({"other": "x"}, group)
    assert state == {"missing_field": None}


def test_verify_projection_fields_passes_when_all_present():
    groups = [CallGroup(projection_id="x", fields=("subject", "body"), questions=(_q("a"),))]
    verify_projection_fields(groups, {"subject", "body", "created_at"})  # should not raise


def test_verify_projection_fields_names_missing_field_and_available_columns():
    groups = [CallGroup(projection_id="x", fields=("subjct", "body"), questions=(_q("a"),))]
    with pytest.raises(ValueError) as exc_info:
        verify_projection_fields(groups, {"body", "created_at", "subject", "thread"})
    message = str(exc_info.value)
    assert "'subjct'" in message
    assert "not a column in the corpus" in message
    assert "body" in message and "subject" in message


def test_split_for_context_single_batch_common_case():
    group = CallGroup(
        projection_id="x",
        fields=("body",),
        questions=(_q("a"), _q("b"), _q("c")),
    )
    batches = split_for_context(group, {"body": "short text"})
    assert batches == [("a", "b", "c")]


def test_split_for_context_splits_when_schema_too_large():
    # Each question's instructions is a large block; state is tiny. Force a
    # tight context limit so several questions can't share one call.
    big_instructions = "x" * 2000
    questions = tuple(_q(f"q{i}", instructions=big_instructions) for i in range(10))
    group = CallGroup(projection_id="x", fields=("body",), questions=questions)
    batches = split_for_context(group, {"body": "short"}, context_limit=2000, margin=0.0)
    assert len(batches) > 1
    # Every question appears exactly once across all batches.
    all_ids = [qid for batch in batches for qid in batch]
    assert sorted(all_ids) == sorted(q.id for q in questions)


def test_split_for_context_no_batch_exceeds_the_limit():
    big_instructions = "word " * 500
    questions = tuple(_q(f"q{i}", instructions=big_instructions) for i in range(8))
    group = CallGroup(projection_id="x", fields=("body",), questions=questions)
    state = {"body": "some ticket text " * 20}
    context_limit = 2000
    margin = 0.15
    limit = int(context_limit * (1 - margin))
    batches = split_for_context(group, state, context_limit=context_limit, margin=margin)

    questions_by_id = {q.id: q for q in questions}
    state_tokens = estimate_tokens(state)
    for batch in batches:
        batch_tokens = state_tokens + sum(question_schema_tokens(questions_by_id[qid]) for qid in batch)
        assert batch_tokens <= limit


def test_split_for_context_raises_when_single_question_cannot_fit_alone():
    huge_instructions = "x" * 100_000
    group = CallGroup(projection_id="x", fields=("body",), questions=(_q("a", instructions=huge_instructions),))
    with pytest.raises(ContextOverflowError):
        split_for_context(group, {"body": "short"}, context_limit=1000, margin=0.0)


def test_context_limit_constant_is_a_measured_value_not_a_guess():
    # Sanity check on the measured constant itself (docs/00-JEV-API.md /
    # 04-P5-PLANNER.md T5.2b): well under the API's documented ballpark,
    # and clearly not an arbitrary round number picked without evidence.
    assert 30_000 < CONTEXT_LIMIT_TOKENS < 33_000
