"""Client tests never make live API calls (02-BUILD-PLAN.md Testing rules):
`system_one` is monkeypatched to raise the exact SDK exceptions we classify."""

import httpx2  # typesafe_sdk's own vendored httpx package, used for its Headers type
import pytest
import typesafe_sdk as sdk

from jev_census.client import JevClient, JevConfigError, JevTransientError, to_sdk_question
from jev_census.question_set import Question

_HEADERS = httpx2.Headers({})


def _client() -> JevClient:
    return JevClient(api_key="test-key-not-real")


def _instantiate(exc_cls: type[Exception]) -> Exception:
    """Every SDK error class needs different constructor args (status/body/headers,
    a timeout, or a field_path). Build a minimal valid instance of whichever one."""
    if exc_cls is sdk.TypeSafeAPITimeoutError:
        return exc_cls(timeout=30.0)
    if exc_cls is sdk.TypeSafeAPIResponseValidationError:
        return exc_cls(status=200, body=None, headers=_HEADERS, field_path="answers.q")
    if exc_cls is sdk.TypeSafeAPIConnectionError:
        return exc_cls("connection failed")
    status = {
        sdk.TypeSafeAuthenticationError: 401,
        sdk.TypeSafeBadRequestError: 400,
        sdk.TypeSafeUnprocessableEntityError: 422,
        sdk.TypeSafeNotFoundError: 404,
        sdk.TypeSafePermissionDeniedError: 403,
        sdk.TypeSafeRateLimitError: 429,
        sdk.TypeSafeInternalServerError: 529,
    }[exc_cls]
    return exc_cls(status=status, body=None, headers=_HEADERS, message="boom")


@pytest.mark.parametrize(
    "exc_cls",
    [
        sdk.TypeSafeAuthenticationError,
        sdk.TypeSafeBadRequestError,
        sdk.TypeSafeUnprocessableEntityError,
        sdk.TypeSafeNotFoundError,
        sdk.TypeSafePermissionDeniedError,
    ],
)
def test_config_errors_classified_as_fatal(exc_cls, monkeypatch):
    client = _client()

    def raiser(*args, **kwargs):
        raise _instantiate(exc_cls)

    monkeypatch.setattr(client._client, "system_one", raiser)
    with pytest.raises(JevConfigError):
        client.ask("state", {"q": Question(id="q", type="noul", instructions="It is true.")})


@pytest.mark.parametrize(
    "exc_cls",
    [
        sdk.TypeSafeRateLimitError,
        sdk.TypeSafeInternalServerError,
        sdk.TypeSafeAPIConnectionError,
        sdk.TypeSafeAPITimeoutError,
        sdk.TypeSafeAPIResponseValidationError,
    ],
)
def test_transient_errors_classified_as_retryable(exc_cls, monkeypatch):
    client = _client()

    def raiser(*args, **kwargs):
        raise _instantiate(exc_cls)

    monkeypatch.setattr(client._client, "system_one", raiser)
    with pytest.raises(JevTransientError):
        client.ask("state", {"q": Question(id="q", type="noul", instructions="It is true.")})


def test_to_sdk_question_noul():
    q = Question(
        id="is_urgent",
        type="noul",
        instructions="The problem is time-sensitive.",
        criteria={"true": "Blocked work.", "false": "No pressure."},
    )
    sdk_q = to_sdk_question(q)
    assert isinstance(sdk_q, sdk.Noul)
    assert sdk_q.criteria == {"true": "Blocked work.", "false": "No pressure."}


def test_to_sdk_question_choice():
    q = Question(
        id="department",
        type="choice",
        instructions="The team that should handle this.",
        criteria={"billing": "Payments.", "technical": "Bugs."},
    )
    sdk_q = to_sdk_question(q)
    assert isinstance(sdk_q, sdk.Choice)
    assert sdk_q.criteria == {"billing": "Payments.", "technical": "Bugs."}


def test_to_sdk_question_score():
    q = Question(
        id="frustration",
        type="score",
        instructions="How frustrated the customer sounds.",
        criteria=["Calm", "Annoyed", "Angry"],
    )
    sdk_q = to_sdk_question(q)
    assert isinstance(sdk_q, sdk.Score)
    assert sdk_q.criteria == ["Calm", "Annoyed", "Angry"]


def test_client_construction_does_not_touch_network():
    """Constructing the client must not require a live connection — it only
    configures the SDK's HTTP client lazily."""
    _client()
