import random

from jev_census.failure import FailureClass
from jev_census.retry import RetryPolicy


def test_max_attempts_per_class():
    policy = RetryPolicy()
    assert policy.max_attempts(FailureClass.RATE_LIMIT_OR_OVERLOAD) == 8
    assert policy.max_attempts(FailureClass.SERVER_OR_NETWORK) == 3
    assert policy.max_attempts(FailureClass.SUSPICIOUS_RESPONSE) == 2
    assert policy.max_attempts(FailureClass.FATAL_CONFIG) == 1


def test_fatal_config_never_retries():
    policy = RetryPolicy()
    assert policy.should_retry(FailureClass.FATAL_CONFIG, attempt=1) is False


def test_suspicious_response_retries_exactly_once():
    policy = RetryPolicy()
    assert policy.should_retry(FailureClass.SUSPICIOUS_RESPONSE, attempt=1) is True
    assert policy.should_retry(FailureClass.SUSPICIOUS_RESPONSE, attempt=2) is False


def test_server_or_network_bounded_retries():
    policy = RetryPolicy()
    assert policy.should_retry(FailureClass.SERVER_OR_NETWORK, attempt=1) is True
    assert policy.should_retry(FailureClass.SERVER_OR_NETWORK, attempt=2) is True
    assert policy.should_retry(FailureClass.SERVER_OR_NETWORK, attempt=3) is False


def test_retry_after_wins_over_computed_backoff():
    policy = RetryPolicy()
    assert policy.backoff_seconds(1, retry_after_ms=2500) == 2.5


def test_backoff_is_full_jitter_within_bounds():
    policy = RetryPolicy(backoff_base_seconds=1.0, backoff_cap_seconds=10.0)
    rng = random.Random(0)
    for attempt in range(1, 6):
        value = policy.backoff_seconds(attempt, rng=rng)
        ceiling = min(10.0, 1.0 * 2**attempt)
        assert 0 <= value <= ceiling


def test_backoff_respects_cap_at_high_attempts():
    policy = RetryPolicy(backoff_base_seconds=1.0, backoff_cap_seconds=5.0)
    rng = random.Random(0)
    value = policy.backoff_seconds(20, rng=rng)
    assert 0 <= value <= 5.0


def test_backoff_deterministic_given_seeded_rng():
    policy = RetryPolicy()
    r1 = policy.backoff_seconds(3, rng=random.Random(42))
    r2 = policy.backoff_seconds(3, rng=random.Random(42))
    assert r1 == r2
