import pytest

from jev_census.aimd import AIMDConcurrency


def test_invalid_bounds_rejected():
    with pytest.raises(ValueError):
        AIMDConcurrency(initial=100, ceiling=10)


def test_starts_at_initial():
    aimd = AIMDConcurrency(initial=4, ceiling=64, floor=1)
    assert aimd.limit == 4


def test_rate_limit_halves_immediately():
    aimd = AIMDConcurrency(initial=16, ceiling=64, floor=1)
    aimd.on_rate_limited()
    assert aimd.limit == 8


def test_rate_limit_storm_halves_repeatedly_down_to_floor():
    """T4.3 done-when: a simulated 429 storm halves concurrency."""
    aimd = AIMDConcurrency(initial=32, ceiling=64, floor=1)
    seen = [aimd.limit]
    for _ in range(10):
        aimd.on_rate_limited()
        seen.append(aimd.limit)
    assert seen == [32, 16, 8, 4, 2, 1, 1, 1, 1, 1, 1]
    assert aimd.limit == aimd.floor


def test_additive_increase_after_clean_window():
    aimd = AIMDConcurrency(initial=4, ceiling=64, floor=1, clean_window=3)
    aimd.on_success()
    aimd.on_success()
    assert aimd.limit == 4  # not yet a full clean window
    aimd.on_success()
    assert aimd.limit == 5  # window completed -> +1


def test_recovers_after_storm():
    """T4.3 done-when: ... and recovers."""
    aimd = AIMDConcurrency(initial=16, ceiling=64, floor=1, clean_window=5)
    for _ in range(4):
        aimd.on_rate_limited()
    assert aimd.limit == 1

    for _ in range(5 * 10):  # 10 clean windows' worth of successes
        aimd.on_success()
    assert aimd.limit == 11  # 1 + 10 additive steps


def test_increase_never_exceeds_ceiling():
    aimd = AIMDConcurrency(initial=1, ceiling=3, floor=1, clean_window=1)
    for _ in range(10):
        aimd.on_success()
    assert aimd.limit == 3


def test_decrease_never_goes_below_floor():
    aimd = AIMDConcurrency(initial=2, ceiling=64, floor=1)
    for _ in range(10):
        aimd.on_rate_limited()
    assert aimd.limit == 1


def test_rate_limit_resets_clean_streak():
    aimd = AIMDConcurrency(initial=8, ceiling=64, floor=1, clean_window=3)
    aimd.on_success()
    aimd.on_success()
    aimd.on_rate_limited()
    aimd.on_success()  # only 1 clean success since the reset
    assert aimd.limit == 4  # halved from 8, not yet incremented again
