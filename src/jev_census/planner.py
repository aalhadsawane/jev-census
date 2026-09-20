"""Call planner (T5.1-T5.2, T5.2b): partitions a `QuestionSet`'s questions into
`CallGroup`s by declared projection, and projects one document's fields down
to one group's state. Per the stage contract in `01-DESIGN.md`: "Call planner
... project state, one Call per group ... records projection_id." Pure and
network-free — no I/O anywhere in this module.

`CONTEXT_LIMIT_TOKENS` is a measured fact, not an assumption (docs/02-BUILD-PLAN.md
Rule Zero). Verified live 2026-09-20 by ramping a single-field state against a
minimal `noul` question until the API refused: 163,000 chars / 32,872
`usage.input_tokens` succeeded; 164,000 chars failed with
`400 max_tokens_exceeded`. See `docs/00-JEV-API.md` § "Other constraints" for
the full measurement. The limit binds on total input tokens (state + schema),
confirmed by `usage.input_tokens` tracking the boundary rather than a round
character count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hashing import stable_hash
from .question_set import Question, QuestionSet
from .token_estimator import estimate_tokens

# A round number safely under the confirmed-working 32,872 measured live
# (see module docstring). split_for_context's default 15% margin absorbs the
# gap between this and the measured boundary, and then some.
CONTEXT_LIMIT_TOKENS = 32_768


class ContextOverflowError(Exception):
    """Raised by `split_for_context` when a single question cannot fit
    alongside the given (already-projected) state even alone — the state
    itself is too large for even a one-question call. The caller (the
    Scheduler) quarantines the document with reason `state_exceeds_context`
    rather than sending a call that the live API would reject with `400
    max_tokens_exceeded` (01-DESIGN.md failure table: "Chunk or quarantine.
    Never truncate silently"). `chunking.py`'s `chunk_document` exists to
    avoid this by splitting the state before it ever reaches here; this
    exception is the signal that chunking is also insufficient (an
    unsplittable field is itself oversized)."""

    def __init__(self, question_id: str, state_tokens: int, question_tokens: int, limit: int):
        self.question_id = question_id
        self.state_tokens = state_tokens
        self.question_tokens = question_tokens
        self.limit = limit
        super().__init__(
            f"question '{question_id}' cannot fit alongside its projected state even alone: "
            f"state ~{state_tokens} tokens + question ~{question_tokens} tokens > "
            f"limit {limit} tokens"
        )


@dataclass(frozen=True)
class CallGroup:
    """Questions sharing a projection — `01-DESIGN.md`'s "group count = calls
    per document = the cost multiplier". `projection_id` is a hash of the
    sorted field list, not an ordinal ("p0", "p1", ...): an ordinal changes
    when someone reorders questions in the YAML, which would make two
    semantically identical runs produce diffing Parquet. A hash of the sorted
    fields is stable under reordering and comparable across question sets."""

    projection_id: str
    fields: tuple[str, ...]  # sorted — the id is a hash of exactly this
    questions: tuple[Question, ...]  # YAML declaration order preserved


def plan_call_groups(question_set: QuestionSet) -> list[CallGroup]:
    """Partition every question by its resolved projection. Deterministic:
    same question set -> same group ids, same question order within each
    group, across processes. Groups are returned in order of first
    appearance in the YAML, so a one-projection set's single group is always
    first (and only) — the common case stays trivial to reason about."""
    grouped: dict[tuple[str, ...], list[Question]] = {}
    order: list[tuple[str, ...]] = []
    for question in question_set.questions:
        fields = tuple(sorted(question_set.resolved_projection(question)))
        if fields not in grouped:
            grouped[fields] = []
            order.append(fields)
        grouped[fields].append(question)

    return [
        CallGroup(
            projection_id=stable_hash(list(fields))[:8],
            fields=fields,
            questions=tuple(grouped[fields]),
        )
        for fields in order
    ]


def verify_projection_fields(call_groups: list[CallGroup], available_fields: set[str]) -> None:
    """Runtime companion to the validator's structural checks (T5.1): confirms
    every field a call group projects actually exists as a column in the
    corpus. Checked once, against the first document's fields only — a
    corpus with ragged columns is a source problem, and per-document
    checking would cost a dict scan per call for a guarantee the first row
    already gives.

    Raises `ValueError` naming the first missing field and the available
    columns. The caller (the Scheduler) treats this as fatal: every document
    would fail identically, so aborting immediately is the kind thing to do
    (04-P5-PLANNER.md T5.1) — the same family as a `422`, not a per-row
    quarantine."""
    all_fields = sorted({field for group in call_groups for field in group.fields})
    missing = [field for field in all_fields if field not in available_fields]
    if missing:
        raise ValueError(
            f"projection field '{missing[0]}' is not a column in the corpus; "
            f"available: {sorted(available_fields)}"
        )


def project_state(doc_fields: dict[str, Any], group: CallGroup) -> dict[str, Any]:
    """One document's fields, narrowed to exactly this group's fields —
    sorted key order (from `group.fields`), which is what makes the cache
    key (`cache.py`'s `cache_key`) stable across a harmless YAML reorder.
    Never let a question in this group see a field outside `group.fields`."""
    return {field: doc_fields.get(field) for field in group.fields}


def question_schema_tokens(question: Question) -> int:
    return estimate_tokens({"instructions": question.instructions, "criteria": question.criteria})


def split_for_context(
    group: CallGroup,
    state: dict[str, Any],
    *,
    context_limit: int = CONTEXT_LIMIT_TOKENS,
    margin: float = 0.15,
) -> list[tuple[str, ...]]:
    """Question-id batches that each fit within `context_limit` given `state`
    already fits by itself (if it doesn't, that's `chunking.py`'s job, which
    runs before this). Greedy in `group.questions` declaration order. Returns
    one batch in the common case — this only produces more than one when the
    schema is genuinely too large to ask in a single call.

    Splits by question, never by field: dropping a field to fit would be a
    silent accuracy change (fewer questions get the context they need);
    moving a question to a later call is not.

    Raises `ContextOverflowError` if a single question cannot fit alongside
    the state even alone — the caller quarantines the document.
    """
    limit = int(context_limit * (1 - margin))
    state_tokens = estimate_tokens(state)

    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = state_tokens
    for question in group.questions:
        q_tokens = question_schema_tokens(question)
        if state_tokens + q_tokens > limit:
            raise ContextOverflowError(question.id, state_tokens, q_tokens, limit)
        if current and current_tokens + q_tokens > limit:
            batches.append(current)
            current = []
            current_tokens = state_tokens
        current.append(question.id)
        current_tokens += q_tokens
    if current:
        batches.append(current)
    return [tuple(batch) for batch in batches]
