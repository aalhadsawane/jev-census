"""AIMD adaptive concurrency (T4.3). 01-DESIGN.md Concurrency: "Additive
increase after a clean window, halve on 429/529. Floor 1, configurable
ceiling." At one call per document (no multi-document packing, ever),
request-rate may bind before token-rate — this only ever throttles how many
calls run at once, never anything about a call's own content.
"""

from __future__ import annotations


class AIMDConcurrency:
    def __init__(self, *, initial: int = 4, ceiling: int = 64, floor: int = 1, clean_window: int = 20):
        if not (floor <= initial <= ceiling):
            raise ValueError(f"require floor <= initial <= ceiling, got {floor} <= {initial} <= {ceiling}")
        self.floor = floor
        self.ceiling = ceiling
        self.clean_window = clean_window
        self._limit = initial
        self._clean_streak = 0

    @property
    def limit(self) -> int:
        return self._limit

    def on_success(self) -> None:
        """Additive increase: after `clean_window` consecutive successes,
        raise the limit by 1, up to the ceiling."""
        self._clean_streak += 1
        if self._clean_streak >= self.clean_window:
            self._clean_streak = 0
            self._limit = min(self.ceiling, self._limit + 1)

    def on_rate_limited(self) -> None:
        """Multiplicative decrease: a 429/529 halves the limit immediately
        and resets the clean streak, down to the floor."""
        self._clean_streak = 0
        self._limit = max(self.floor, self._limit // 2)
