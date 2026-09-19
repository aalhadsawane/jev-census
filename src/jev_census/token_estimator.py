"""Token estimator (T3.1): pure, network-free, shared by `census estimate`
(T3.2), the budget governor's admission control (T3.4), and eventually the
call planner (P5). Jev's own tokenizer isn't public, so this is necessarily
an approximation — ~4 characters per token, the common ballpark for English
text — calibrated against real `usage.input_tokens` as a run progresses
(T3.5, see `cache.py`'s calibration table). Never spends; never touches the
network.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

from .question_set import Question

CHARS_PER_TOKEN = 4.0


def _text_for(value: object) -> str:
    """Canonical text form of a value being sent as state, or as a question's
    instructions/criteria, for length estimation. Strings pass through as-is;
    anything else (object, array) is the JSON that actually goes over the
    wire."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def estimate_tokens(value: object, *, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Rough, unmargined token count for one value. The caller decides
    whether and how much margin to add — a cost estimate wants the raw
    number; an admission-control check (T3.4) wants it padded."""
    text = _text_for(value)
    return math.ceil(len(text) / chars_per_token)


def estimate_schema_tokens(questions: Mapping[str, Question], *, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """The token cost paid on every document regardless of its own size —
    the sum of every question's instructions + criteria (01-DESIGN.md D2:
    "the schema is paid on every row")."""
    total = 0
    for question in questions.values():
        total += estimate_tokens(
            {"instructions": question.instructions, "criteria": question.criteria},
            chars_per_token=chars_per_token,
        )
    return total


def estimate_call_tokens(
    state: object, questions: Mapping[str, Question], *, chars_per_token: float = CHARS_PER_TOKEN
) -> tuple[int, int]:
    """`(state_tokens, schema_tokens)` for one call — one document's
    projected state against one batch of questions."""
    state_tokens = estimate_tokens(state, chars_per_token=chars_per_token)
    schema_tokens = estimate_schema_tokens(questions, chars_per_token=chars_per_token)
    return state_tokens, schema_tokens
