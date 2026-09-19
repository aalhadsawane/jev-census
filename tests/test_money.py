from jev_census.money import micro_usd_to_usd, tokens_to_micro_usd, usd_to_micro_usd


def test_usd_to_micro_usd_round_trip():
    assert usd_to_micro_usd(10.0) == 10_000_000
    assert micro_usd_to_usd(10_000_000) == 10.0


def test_usd_to_micro_usd_rounds():
    assert usd_to_micro_usd(0.01) == 10_000
    assert usd_to_micro_usd(7.0) == 7_000_000


def test_tokens_to_micro_usd_matches_cost_model():
    # $0.042 per 1e6 input tokens (00-JEV-API.md).
    assert tokens_to_micro_usd(1_000_000) == 42_000
    assert tokens_to_micro_usd(0) == 0


def test_tokens_to_micro_usd_rounds_up_never_down():
    # 1 token * 0.042 = 0.042 micro-usd -> must round up to 1, never truncate to 0.
    assert tokens_to_micro_usd(1) == 1


def test_tokens_to_micro_usd_is_integer():
    result = tokens_to_micro_usd(282)
    assert isinstance(result, int)
