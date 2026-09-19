"""Integer micro-dollar money arithmetic (T3.3).

01-DESIGN.md Hard rules: "Integer micro-dollars, never floats." A float
accumulator drifts over hundreds of thousands of additions; an integer count
of micro-dollars (1e-6 USD) does not. Floats are only for the CLI-facing
boundary (a human typed `--budget 10.00`, a human reads `$0.0023`).
"""

from __future__ import annotations

import math

MICRO_USD_PER_USD = 1_000_000

# $0.042 per 1e6 input tokens (00-JEV-API.md Cost model), output free. That is
# exactly 0.042 micro-dollars per token: (0.042 / 1e6) USD/token * 1e6 micro-USD/USD.
MICRO_USD_PER_INPUT_TOKEN = 0.042


def usd_to_micro_usd(usd: float) -> int:
    return round(usd * MICRO_USD_PER_USD)


def micro_usd_to_usd(micro_usd: int) -> float:
    return micro_usd / MICRO_USD_PER_USD


def tokens_to_micro_usd(input_tokens: int) -> int:
    """Cost of `input_tokens` attempted input tokens, rounded up. Ceiling,
    never floor or round-to-nearest — undercounting spend is how a budget cap
    gets silently exceeded."""
    return math.ceil(input_tokens * MICRO_USD_PER_INPUT_TOKEN)
