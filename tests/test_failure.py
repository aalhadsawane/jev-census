"""One test per row of the Failure handling table in 01-DESIGN.md (T4.2)."""

import httpx2
import pytest
import typesafe_sdk as sdk

from jev_census.client import JevConfigError, JevTransientError
from jev_census.decoder import DecodeError
from jev_census.failure import FailureClass, classify

_HEADERS = httpx2.Headers({})


def _jev_transient(sdk_exc: Exception) -> JevTransientError:
    """Mirrors how client.py actually raises: `raise JevTransientError(...) from exc`,
    which sets __cause__ — exactly what classify() reads."""
    err = JevTransientError(str(sdk_exc))
    err.__cause__ = sdk_exc
    return err


def test_429_rate_limited_is_rate_limit_or_overload():
    sdk_exc = sdk.TypeSafeRateLimitError(status=429, body=None, headers=_HEADERS, message="rl")
    assert classify(_jev_transient(sdk_exc)) == FailureClass.RATE_LIMIT_OR_OVERLOAD


def test_529_overloaded_is_rate_limit_or_overload():
    sdk_exc = sdk.TypeSafeInternalServerError(status=529, body=None, headers=_HEADERS, message="overloaded")
    assert classify(_jev_transient(sdk_exc)) == FailureClass.RATE_LIMIT_OR_OVERLOAD


def test_5xx_other_is_server_or_network():
    sdk_exc = sdk.TypeSafeInternalServerError(status=500, body=None, headers=_HEADERS, message="boom")
    assert classify(_jev_transient(sdk_exc)) == FailureClass.SERVER_OR_NETWORK


def test_timeout_is_server_or_network():
    sdk_exc = sdk.TypeSafeAPITimeoutError(timeout=30.0)
    assert classify(_jev_transient(sdk_exc)) == FailureClass.SERVER_OR_NETWORK


def test_connection_reset_is_server_or_network():
    sdk_exc = sdk.TypeSafeAPIConnectionError("connection reset")
    assert classify(_jev_transient(sdk_exc)) == FailureClass.SERVER_OR_NETWORK


def test_401_is_fatal_config():
    exc = JevConfigError("unauthorized")
    assert classify(exc) == FailureClass.FATAL_CONFIG


def test_422_is_fatal_config():
    exc = JevConfigError("unprocessable")
    assert classify(exc) == FailureClass.FATAL_CONFIG


def test_missing_question_id_is_suspicious_response():
    exc = DecodeError(["missing answer for question id 'q1'"])
    assert classify(exc) == FailureClass.SUSPICIOUS_RESPONSE


def test_type_mismatch_is_suspicious_response():
    exc = DecodeError(["question 'q1': expected type 'choice', got 'noul'"])
    assert classify(exc) == FailureClass.SUSPICIOUS_RESPONSE


def test_unrecognized_exception_raises_type_error():
    with pytest.raises(TypeError):
        classify(ValueError("not a jev failure"))
