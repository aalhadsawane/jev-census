"""Long-document chunking and per-type aggregation (T5.4, `01-DESIGN.md`
§ Long documents). Triggers only when a document's projected state does not
fit within the context limit even alone -- the common case (a short ticket,
a projected state well under 32k tokens) never touches this module at all.

Pure and network-free, like `planner.py`. `chunk_document` produces N states
to ask the same question group about; `aggregate_chunk_answers` turns N
per-chunk `DecodedAnswer`s for one question back into a single cell.
"""

from __future__ import annotations

from typing import Any, Literal

from .decoder import DecodedAnswer
from .planner import CONTEXT_LIMIT_TOKENS, CallGroup, ContextOverflowError, question_schema_tokens
from .token_estimator import CHARS_PER_TOKEN, estimate_tokens

NoulMode = Literal["max", "mean"]


def chunk_document(
    state: dict[str, Any],
    group: CallGroup,
    *,
    context_limit: int = CONTEXT_LIMIT_TOKENS,
    margin: float = 0.15,
    overlap: float = 0.10,
) -> list[dict[str, Any]]:
    """Split `state` into N chunks that each fit within `context_limit`
    alongside `group`'s full schema. Returns `[state]` unchanged when it
    already fits -- the common case pays nothing extra for this module
    existing.

    Whether chunking is worth attempting at all is decided against the
    group's CHEAPEST question -- "the document exceeds the limit alone"
    (01-DESIGN.md): if even the cheapest question can't fit alongside the
    state, no amount of chunking helps, so this returns `[state]` unchanged
    and leaves the "doesn't fit" outcome to `split_for_context`. But once
    chunking IS triggered, each chunk is sized to leave room for the group's
    MOST EXPENSIVE question, not the cheapest one -- sizing against the
    cheapest would produce chunks so tight that `split_for_context`, asked
    to place a merely-average-sized question against one of them, would see
    a spurious "doesn't fit" for a question that fits fine once the chunk
    itself is reasonably sized. Sizing against the largest question
    guarantees every question in the group fits in at least one call per
    chunk, which is what makes `split_for_context`'s per-chunk batching
    (T5.2) safe to run afterward.

    Splits only the LARGEST field by estimated size; every other field is
    carried whole into every chunk. This matches the shape a question set
    actually hits in practice: one long body alongside a handful of short
    metadata fields (subject, id, ...) that need to stay intact for each
    chunk to make sense standing alone.

    Raises `ContextOverflowError` if even a single chunk cannot be made to
    fit -- e.g. a field that isn't the one being split is itself too large,
    the split field isn't text, or the group's largest question is itself
    too large to ever fit alongside any state. The caller quarantines the
    document with reason `state_exceeds_context`; truncation is never
    silent (01-DESIGN.md Hard rules).
    """
    limit = int(context_limit * (1 - margin))
    smallest_schema = min(question_schema_tokens(q) for q in group.questions)
    largest_schema = max(question_schema_tokens(q) for q in group.questions)

    if estimate_tokens(state) + smallest_schema <= limit:
        return [state]

    if not state:
        raise ContextOverflowError("(all questions)", 0, smallest_schema, limit)

    split_field = max(state, key=lambda f: estimate_tokens(state[f]))
    other_fields = {f: v for f, v in state.items() if f != split_field}
    # The "overhead" of everything except the split field, measured against
    # the ACTUAL combined-state estimate rather than summed from each field
    # separately: JSON structure (braces, keys, commas) doesn't estimate
    # perfectly additively, and the field being split is by construction the
    # single biggest term, so this ratio captures the other fields' true
    # share of the combined estimate rather than assuming independence.
    other_tokens = estimate_tokens(state) - estimate_tokens({split_field: state[split_field]})
    other_tokens = max(other_tokens, 0)
    # A fixed slack on top of the already-applied `margin`, absorbing the
    # remaining rounding drift between this per-field arithmetic and the one
    # `split_for_context` performs on the real merged chunk state downstream
    # -- observed live to matter by single-digit tokens right at the
    # boundary, which a flat buffer this size makes irrelevant.
    safety_tokens = 64

    budget_for_split_field = limit - largest_schema - other_tokens - safety_tokens
    text = state[split_field]
    if budget_for_split_field <= 0 or not isinstance(text, str):
        # Either the non-splittable remainder alone already exceeds the
        # limit, or the largest field isn't text and so has nothing
        # meaningful to chunk on -- either way, no chunking scheme fixes it.
        raise ContextOverflowError("(all questions)", other_tokens, largest_schema, limit)

    chars_per_chunk = max(1, int(budget_for_split_field * CHARS_PER_TOKEN))
    stride = max(1, int(chars_per_chunk * (1 - overlap)))

    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chars_per_chunk)
        chunk_state = dict(other_fields)
        chunk_state[split_field] = text[start:end]
        chunks.append(chunk_state)
        if end >= len(text):
            break
        start += stride
    return chunks


def _mean_distribution(dists: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for dist in dists for key in dist})
    n = len(dists)
    return {key: sum(dist.get(key, 0.0) for dist in dists) / n for key in keys}


def aggregate_chunk_answers(
    answers: list[DecodedAnswer], *, noul_mode: NoulMode = "max"
) -> DecodedAnswer:
    """One question's answers across N (>=1) chunks of one document -> one
    cell. Every aggregated cell's `confidence` is OURS and marked
    `confidence_source="derived"` for every type, including `choice` and
    `score` -- the per-chunk confidence was model-reported, but the
    aggregate is our arithmetic over several of them, and calling that
    `model` would be the exact lie `01-DESIGN.md` D3 exists to prevent.

    Aggregation per type (01-DESIGN.md § Long documents):
      noul   -> max across chunks (mean via `noul_mode="mean"`); one chunk
                finding the condition is treated as the document having it.
      choice -> argmax of the mean per-option distribution.
      score  -> mean of the per-chunk weighted scores; mean per-level
                distribution; `legend` preserved (must be identical across
                chunks -- asserted, never merged).
    """
    if not answers:
        raise ValueError("aggregate_chunk_answers requires at least one answer")

    question_id = answers[0].question_id
    qtype = answers[0].type
    if any(a.question_id != question_id or a.type != qtype for a in answers):
        raise ValueError("aggregate_chunk_answers: all answers must share one question_id and type")

    legends = [a.legend for a in answers if a.legend is not None]
    legend = legends[0] if legends else None
    if any(candidate != legend for candidate in legends[1:]):
        raise ValueError(
            f"question '{question_id}': legend differs across chunks of the same document -- "
            "same question, same criteria, so this is a bug, not something to merge"
        )

    if qtype == "noul":
        values = [a.noul for a in answers if a.noul is not None]
        agg_value = max(values) if noul_mode == "max" else sum(values) / len(values)
        confidence = abs(agg_value - 0.5) * 2
        return DecodedAnswer(
            question_id=question_id,
            type="noul",
            noul=agg_value,
            choice=None,
            score=None,
            probabilities=None,
            legend=None,
            confidence=confidence,
            confidence_source="derived",
        )

    if qtype == "choice":
        dists = [a.probabilities for a in answers if a.probabilities is not None]
        mean_dist = _mean_distribution(dists) if dists else {}
        chosen = max(mean_dist, key=mean_dist.get) if mean_dist else None
        confidence = mean_dist.get(chosen, 0.0) if chosen is not None else 0.0
        return DecodedAnswer(
            question_id=question_id,
            type="choice",
            noul=None,
            choice=chosen,
            score=None,
            probabilities=mean_dist or None,
            legend=None,
            confidence=confidence,
            confidence_source="derived",
        )

    if qtype == "score":
        scores = [a.score for a in answers if a.score is not None]
        agg_score = sum(scores) / len(scores) if scores else None
        dists = [a.probabilities for a in answers if a.probabilities is not None]
        mean_dist = _mean_distribution(dists) if dists else {}
        confidence = max(mean_dist.values()) if mean_dist else 0.0
        return DecodedAnswer(
            question_id=question_id,
            type="score",
            noul=None,
            choice=None,
            score=agg_score,
            probabilities=mean_dist or None,
            legend=legend,
            confidence=confidence,
            confidence_source="derived",
        )

    raise ValueError(f"unknown question type '{qtype}'")
