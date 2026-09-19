import math

from jev_census.question_set import Question
from jev_census.token_estimator import estimate_call_tokens, estimate_schema_tokens, estimate_tokens


def test_estimate_tokens_known_string():
    text = "a" * 40  # 40 chars / 4 chars-per-token = exactly 10
    assert estimate_tokens(text) == 10


def test_estimate_tokens_rounds_up():
    text = "a" * 41  # 10.25 -> ceil to 11
    assert estimate_tokens(text) == 11


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_object_uses_json_length():
    value = {"a": 1, "b": 2}
    import json

    expected = math.ceil(len(json.dumps(value, ensure_ascii=False, sort_keys=True)) / 4.0)
    assert estimate_tokens(value) == expected


def test_estimate_tokens_respects_custom_chars_per_token():
    text = "a" * 20
    assert estimate_tokens(text, chars_per_token=2.0) == 10
    assert estimate_tokens(text, chars_per_token=4.0) == 5


def test_estimate_schema_tokens_sums_every_question():
    q1 = Question(id="a", type="noul", instructions="a" * 40)
    q2 = Question(id="b", type="noul", instructions="a" * 40)
    total = estimate_schema_tokens({"a": q1, "b": q2})
    single = estimate_schema_tokens({"a": q1})
    assert total == 2 * single


def test_estimate_call_tokens_splits_state_and_schema():
    state = "a" * 40  # 10 tokens
    q = Question(id="a", type="noul", instructions="a" * 80)  # instructions alone ~20 tokens
    state_tokens, schema_tokens = estimate_call_tokens(state, {"a": q})
    assert state_tokens == 10
    assert schema_tokens > 0
    assert schema_tokens == estimate_schema_tokens({"a": q})
