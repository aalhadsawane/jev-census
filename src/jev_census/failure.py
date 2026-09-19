"""Failure classification (T4.2): every row of the Failure handling table in
`01-DESIGN.md` becomes one `FailureClass`, driving the Scheduler's retry
policy (T4.3, T4.4). `client.py` already collapses the SDK's exception zoo
into `JevConfigError`/`JevTransientError`, preserving the original SDK
exception as `__cause__` — `classify()` reads its `.status` from there to
tell 429/529 (reduce concurrency) apart from other 5xx/timeouts (bounded
retries only), which `JevTransientError` alone doesn't distinguish.
"""

from __future__ import annotations

from enum import Enum

from .client import JevConfigError, JevTransientError
from .decoder import DecodeError

# 529 isn't a registered HTTP status; TypeSafe's API uses it (and the SDK
# passes it through as the exception's .status) for "overloaded", same
# transient-backoff-and-shed-load class as 429 (00-JEV-API.md Errors table).
_RATE_LIMIT_OR_OVERLOAD_STATUSES = frozenset({429, 529})


class FailureClass(Enum):
    RATE_LIMIT_OR_OVERLOAD = "rate_limit_or_overload"  # 429, 529 -> backoff, reduce concurrency, retry
    SERVER_OR_NETWORK = "server_or_network"  # 5xx, timeout, reset -> bounded retries, then quarantine
    FATAL_CONFIG = "fatal_config"  # 401, 422 -> abort immediately, never retry
    SUSPICIOUS_RESPONSE = "suspicious_response"  # missing id / type mismatch -> discard, retry once, quarantine


def classify(exc: Exception) -> FailureClass:
    """Map a failure raised by the client (client.py) or decoder (decoder.py)
    to its class in the Failure handling table. Raises TypeError for
    anything else — an unclassified exception should never be silently
    treated as retryable."""
    if isinstance(exc, JevConfigError):
        return FailureClass.FATAL_CONFIG
    if isinstance(exc, DecodeError):
        return FailureClass.SUSPICIOUS_RESPONSE
    if isinstance(exc, JevTransientError):
        status = getattr(exc.__cause__, "status", None)
        if status in _RATE_LIMIT_OR_OVERLOAD_STATUSES:
            return FailureClass.RATE_LIMIT_OR_OVERLOAD
        return FailureClass.SERVER_OR_NETWORK
    raise TypeError(f"cannot classify {exc!r} as a Failure handling table row")
