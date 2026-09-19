"""`census estimate` (T3.2): cost projection before spending anything.

Samples documents with reservoir sampling (unbiased, single pass, no bias
toward the start of the file), projects the corpus-wide token/cost/runtime
total from the sample average, and reports the state/schema split
(01-DESIGN.md D2: the schema is the larger cost lever on short documents).
Never calls the API — `estimate` never spends (01-DESIGN.md § Budget).

Sampling still streams every row once to stay unbiased (count_rows() alone
can't tell us which rows to skip to without reading them); on a very large
corpus that is the dominant cost of running `estimate`, not network spend.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .money import micro_usd_to_usd, tokens_to_micro_usd
from .normalizer import normalize
from .question_set import load_question_set
from .sources import count_rows, read_source
from .token_estimator import estimate_call_tokens

DEFAULT_SAMPLE_SIZE = 200

# Matches the README's worked example. This is P4's Scheduler's *target*
# concurrent throughput — a sequential P1-P3 run is much slower — so runtime
# here is what the corpus will cost once P4 ships, not a claim about today.
ASSUMED_DOCS_PER_SECOND = 250


@dataclass(frozen=True)
class EstimateResult:
    document_count: int
    sample_size: int
    question_count: int
    call_group_count: int  # always 1 until P5's projections/grouping ships
    avg_state_tokens: float
    avg_schema_tokens: float
    total_tokens: int
    estimated_cost_usd: float
    estimated_runtime_seconds: float


def _reservoir_sample(items, k: int, rng: random.Random) -> list:
    sample: list = []
    for i, item in enumerate(items):
        if i < k:
            sample.append(item)
        else:
            j = rng.randint(0, i)
            if j < k:
                sample[j] = item
    return sample


def estimate(
    input_path: str | Path,
    questions_path: str | Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    id_field: str | None = None,
    seed: int = 0,
) -> EstimateResult:
    question_set = load_question_set(questions_path)
    questions_by_id = {q.id: q for q in question_set.questions}

    projection_fields: set[str] = set()
    for q in question_set.questions:
        projection_fields.update(question_set.resolved_projection(q))

    document_count = count_rows(input_path)
    docs = normalize(read_source(input_path), id_field=id_field)
    sample = _reservoir_sample(docs, sample_size, random.Random(seed))

    if not sample:
        return EstimateResult(
            document_count=document_count,
            sample_size=0,
            question_count=len(question_set.questions),
            call_group_count=1,
            avg_state_tokens=0.0,
            avg_schema_tokens=0.0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            estimated_runtime_seconds=0.0,
        )

    state_total = 0
    schema_total = 0
    for doc in sample:
        state = (
            {field: doc.fields.get(field) for field in sorted(projection_fields)}
            if projection_fields
            else doc.fields
        )
        state_tokens, schema_tokens = estimate_call_tokens(state, questions_by_id)
        state_total += state_tokens
        schema_total += schema_tokens

    avg_state = state_total / len(sample)
    avg_schema = schema_total / len(sample)
    total_tokens = round((avg_state + avg_schema) * document_count)
    cost_usd = micro_usd_to_usd(tokens_to_micro_usd(total_tokens))
    runtime_seconds = document_count / ASSUMED_DOCS_PER_SECOND

    return EstimateResult(
        document_count=document_count,
        sample_size=len(sample),
        question_count=len(question_set.questions),
        call_group_count=1,
        avg_state_tokens=avg_state,
        avg_schema_tokens=avg_schema,
        total_tokens=total_tokens,
        estimated_cost_usd=cost_usd,
        estimated_runtime_seconds=runtime_seconds,
    )


def _format_count(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


def _format_runtime(seconds: float) -> str:
    if seconds >= 3600:
        return f"~{seconds / 3600:.1f} hr"
    if seconds >= 60:
        return f"~{seconds / 60:.0f} min"
    return f"~{seconds:.0f} sec"


def format_estimate(result: EstimateResult) -> str:
    """Mirrors the README's `census estimate` block shape."""
    if result.sample_size == 0:
        return f"  {result.document_count:,} documents · nothing to sample (empty corpus)"

    total = result.avg_state_tokens + result.avg_schema_tokens
    state_pct = round(100 * result.avg_state_tokens / total) if total else 0
    schema_pct = 100 - state_pct

    lines = [
        (
            f"  {result.document_count:,} documents · {result.question_count} questions · "
            f"{result.call_group_count} call group"
        ),
        (
            f"  tokens/doc:  state {result.avg_state_tokens:.0f} ({state_pct}%)  ·  "
            f"schema {result.avg_schema_tokens:.0f} ({schema_pct}%)"
        ),
        f"  total:       {_format_count(result.total_tokens)} tokens  ≈  ${result.estimated_cost_usd:.2f}",
        f"  runtime:     {_format_runtime(result.estimated_runtime_seconds)} at {ASSUMED_DOCS_PER_SECOND} docs/s",
    ]
    if result.avg_schema_tokens > result.avg_state_tokens:
        lines.append("")
        lines.append(
            "  schema is the larger half — `census schema-tune` can price a terser question set"
        )
    return "\n".join(lines)
