"""Retry policy (T4.2, T4.3): how many attempts each `FailureClass` gets and
how long to wait between them. Exponential backoff with full jitter,
honouring the server's `Retry-After` when it sends one (01-DESIGN.md
Concurrency: "exponential backoff with full jitter, honour `Retry-After`").
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .failure import FailureClass


@dataclass(frozen=True)
class RetryPolicy:
    # 01-DESIGN.md Failure handling, by class:
    #   RATE_LIMIT_OR_OVERLOAD -> "backoff, reduce concurrency, retry" (generous, not unbounded)
    #   SERVER_OR_NETWORK      -> "bounded retries, then quarantine"
    #   SUSPICIOUS_RESPONSE    -> "discard, retry once, then quarantine" (1 retry = 2 attempts)
    #   FATAL_CONFIG           -> "abort immediately. Never retry"
    max_attempts_rate_limit: int = 8
    max_attempts_server: int = 3
    max_attempts_suspicious: int = 2
    backoff_base_seconds: float = 0.5
    backoff_cap_seconds: float = 30.0

    def max_attempts(self, failure_class: FailureClass) -> int:
        return {
            FailureClass.RATE_LIMIT_OR_OVERLOAD: self.max_attempts_rate_limit,
            FailureClass.SERVER_OR_NETWORK: self.max_attempts_server,
            FailureClass.SUSPICIOUS_RESPONSE: self.max_attempts_suspicious,
            FailureClass.FATAL_CONFIG: 1,
        }[failure_class]

    def should_retry(self, failure_class: FailureClass, attempt: int) -> bool:
        """`attempt` is 1-based: the attempt that just failed."""
        return attempt < self.max_attempts(failure_class)

    def backoff_seconds(
        self, attempt: int, *, retry_after_ms: int | None = None, rng: random.Random | None = None
    ) -> float:
        """Full jitter: uniform(0, min(cap, base * 2**attempt)). A server's
        own Retry-After always wins when present — it knows better than our
        guess."""
        if retry_after_ms is not None:
            return retry_after_ms / 1000.0
        rng = rng or random
        ceiling = min(self.backoff_cap_seconds, self.backoff_base_seconds * (2**attempt))
        return rng.uniform(0, ceiling)
