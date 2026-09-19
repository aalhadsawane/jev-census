"""Jev client (T1.6): transport only. Wraps `typesafe_sdk.TypeSafeClient` (and,
for the async Scheduler in P4, `AsyncTypeSafeClient`), builds the request from
our `Question` model, and classifies every SDK exception into one of two
buckets. No retry logic lives here — that is the Scheduler's job (P4).
"""

from __future__ import annotations

from contextlib import contextmanager

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    Score,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeBadRequestError,
    TypeSafeClient,
    TypeSafeInternalServerError,
    TypeSafeNotFoundError,
    TypeSafePermissionDeniedError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
)

from .question_set import Question

_CONFIG_ERRORS = (
    TypeSafeAuthenticationError,
    TypeSafeBadRequestError,
    TypeSafeUnprocessableEntityError,
    TypeSafeNotFoundError,
    TypeSafePermissionDeniedError,
)
_TRANSIENT_ERRORS = (
    TypeSafeRateLimitError,
    TypeSafeInternalServerError,
    TypeSafeAPIConnectionError,
    TypeSafeAPITimeoutError,
    TypeSafeAPIResponseValidationError,
)

SdkQuestion = Noul | Choice | Score

DEFAULT_MODEL = "jev-latest"


class JevError(Exception):
    """Base for classified Jev client errors."""


class JevConfigError(JevError):
    """Fatal, config: 401/422/400/403/404. Abort immediately, never retry
    (00-JEV-API.md Errors table; 01-DESIGN.md Failure handling)."""


class JevTransientError(JevError):
    """Transient: 429/529/5xx, timeouts, connection failures, or a response
    that failed the SDK's own schema validation. The Scheduler backs off and
    retries; it is never raised as a reason to abort a run."""


@contextmanager
def _classify_sdk_errors():
    """Shared by the sync and async clients: the exact same status-code
    buckets, so `failure.py`'s classification is identical regardless of
    which transport made the call."""
    try:
        yield
    except _CONFIG_ERRORS as exc:
        raise JevConfigError(str(exc)) from exc
    except _TRANSIENT_ERRORS as exc:
        raise JevTransientError(str(exc)) from exc


def to_sdk_question(question: Question) -> SdkQuestion:
    """Translate our `Question` (question_set.py) into the SDK's request type."""
    if question.type == "noul":
        return Noul(instructions=question.instructions, criteria=question.criteria)
    if question.type == "choice":
        return Choice(instructions=question.instructions, criteria=question.criteria)
    if question.type == "score":
        return Score(instructions=question.instructions, criteria=question.criteria)
    raise ValueError(f"unknown question type: {question.type!r}")


class JevClient:
    """Synchronous transport wrapper. P1 runs without concurrency (T1.9); an
    async counterpart on `AsyncTypeSafeClient` is P4's Scheduler concern."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = TypeSafeClient(api_key=api_key, model=model)

    def ask(
        self,
        state: object,
        questions: dict[str, Question],
        model: str | None = None,
    ) -> SystemOneResponse:
        """One state, one or more questions, one call. Raises `JevConfigError`
        (fatal) or `JevTransientError` (retryable) on failure; never retries
        itself."""
        sdk_questions = {qid: to_sdk_question(q) for qid, q in questions.items()}
        with _classify_sdk_errors():
            return self._client.system_one(state, questions=sdk_questions, model=model or self.model)


class AsyncJevClient:
    """Async transport wrapper on `AsyncTypeSafeClient`, for the Scheduler's
    concurrent worker pool (P4). Same contract as `JevClient`: transport
    only, no retries, identical error classification."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = AsyncTypeSafeClient(api_key=api_key, model=model)

    async def ask(
        self,
        state: object,
        questions: dict[str, Question],
        model: str | None = None,
    ) -> SystemOneResponse:
        sdk_questions = {qid: to_sdk_question(q) for qid, q in questions.items()}
        with _classify_sdk_errors():
            return await self._client.system_one(state, questions=sdk_questions, model=model or self.model)

    async def close(self) -> None:
        await self._client.aclose()
