"""T6.2-T6.4 scoring tests (05-P6-QUALITY.md's done-when): per-type
correctness rules and the Wilson/Kish arithmetic against hand-computed
expected values, ECE against a deliberately miscalibrated toy example, and
threshold search including the minimum-sample guard.
"""

from __future__ import annotations

import pytest

from jev_census.scoring import (
    MIN_USABLE_ROWS,
    GoldFileError,
    adjacent_agreement,
    ece_confidence,
    ece_probability,
    is_correct,
    kish_n_eff,
    recommend_threshold,
    reliability_bins,
    score_question,
    validate_gold_rows,
    weighted_accuracy,
    weighted_adjacent_agreement,
    wilson_interval,
)


def _row(type, gold, *, model_noul=None, model_choice=None, model_modal_level=None,
         confidence=None, weight=1.0):
    return {
        "type": type, "gold_answer": gold, "model_noul": model_noul,
        "model_choice": model_choice, "model_modal_level": model_modal_level,
        "confidence": confidence, "stratum_weight": weight,
    }


# --- is_correct, per type ---------------------------------------------------


def test_noul_correctness_rule():
    assert is_correct(_row("noul", "true", model_noul=0.9)) is True
    assert is_correct(_row("noul", "true", model_noul=0.4)) is False
    assert is_correct(_row("noul", "false", model_noul=0.1)) is True
    assert is_correct(_row("noul", "false", model_noul=0.6)) is False


def test_choice_correctness_rule():
    assert is_correct(_row("choice", "billing", model_choice="billing")) is True
    assert is_correct(_row("choice", "billing", model_choice="technical")) is False


def test_score_correctness_uses_modal_level_not_rounded_score():
    # model_modal_level is a string index, matching how it's written to the
    # label CSV (sampling.py's _modal_level).
    assert is_correct(_row("score", "2", model_modal_level="2")) is True
    assert is_correct(_row("score", "2", model_modal_level="1")) is False


def test_unclear_and_missing_gold_excluded_not_wrong():
    assert is_correct(_row("noul", "unclear", model_noul=0.9)) is None
    assert is_correct(_row("noul", "", model_noul=0.9)) is None
    assert is_correct(_row("noul", "UNCLEAR", model_noul=0.9)) is None


def test_adjacent_agreement_score_only():
    assert adjacent_agreement(_row("score", "2", model_modal_level="1")) is True
    assert adjacent_agreement(_row("score", "2", model_modal_level="0")) is False
    assert adjacent_agreement(_row("noul", "true", model_noul=0.9)) is None
    assert adjacent_agreement(_row("score", "unclear", model_modal_level="1")) is None


# --- kish_n_eff / wilson_interval, hand-computed ----------------------------


def test_kish_n_eff_equal_weights_equals_raw_n():
    assert kish_n_eff([1.0, 1.0, 1.0, 1.0]) == pytest.approx(4.0)


def test_kish_n_eff_unequal_weights_hand_computed():
    # (1+1+1+9)^2 / (1+1+1+81) = 144/84
    assert kish_n_eff([1.0, 1.0, 1.0, 9.0]) == pytest.approx(144 / 84)


def test_wilson_interval_matches_hand_computed_textbook_example():
    # n=20, x=5 (p=0.25) -- computed independently with the closed-form
    # Wilson formula (z=1.959963984540054), a commonly cited worked example.
    lo, hi = wilson_interval(5 / 20, 20)
    assert lo == pytest.approx(0.11186170140766569, abs=1e-9)
    assert hi == pytest.approx(0.468700877618744, abs=1e-9)


def test_wilson_interval_n100_x90_hand_computed():
    lo, hi = wilson_interval(0.9, 100)
    assert lo == pytest.approx(0.8256343384950865, abs=1e-9)
    assert hi == pytest.approx(0.9447708629393249, abs=1e-9)


def test_wilson_interval_contains_point_estimate():
    lo, hi = wilson_interval(0.93, 240)
    assert lo < 0.93 < hi


def test_wilson_interval_edge_cases_stay_in_bounds():
    lo, hi = wilson_interval(1.0, 10)
    assert 0.0 <= lo <= hi <= 1.0
    lo, hi = wilson_interval(0.0, 10)
    assert 0.0 <= lo <= hi <= 1.0


# --- weighted_accuracy -----------------------------------------------------


def test_weighted_accuracy_hand_computed():
    rows = [
        _row("noul", "true", model_noul=0.9, weight=1.0),   # correct
        _row("noul", "true", model_noul=0.9, weight=1.0),   # correct
        _row("noul", "false", model_noul=0.9, weight=8.0),  # wrong, heavy weight
    ]
    result = weighted_accuracy(rows)
    # weighted: (1+1)/(1+1+8) = 2/10 = 0.2
    assert result.weighted_accuracy == pytest.approx(0.2)
    # unweighted: 2/3 correct
    assert result.unweighted_accuracy == pytest.approx(2 / 3)
    assert result.n == 3


def test_weighted_accuracy_excludes_unclear_rows():
    rows = [
        _row("noul", "true", model_noul=0.9),
        _row("noul", "unclear", model_noul=0.9),
        _row("noul", "", model_noul=0.9),
    ]
    result = weighted_accuracy(rows)
    assert result.n == 1


def test_weighted_accuracy_none_when_all_unclear():
    rows = [_row("noul", "unclear", model_noul=0.9)]
    assert weighted_accuracy(rows) is None


def test_weighted_adjacent_agreement_hand_computed():
    rows = [
        _row("score", "2", model_modal_level="2", weight=1.0),  # exact, agrees
        _row("score", "2", model_modal_level="1", weight=1.0),  # adjacent, agrees
        _row("score", "2", model_modal_level="0", weight=2.0),  # not adjacent
    ]
    result = weighted_adjacent_agreement(rows)
    assert result == pytest.approx(2 / 4)  # (1+1)/(1+1+2)


# --- ECE, hand-computed toy example -----------------------------------------


def test_ece_confidence_deliberately_miscalibrated_toy_example():
    # 5 rows, all confidence 0.9 (same bin), 3 correct / 2 wrong.
    # weighted accuracy in bin = 0.6, weighted confidence in bin = 0.9.
    # single populated bin -> ECE = |0.6 - 0.9| = 0.3
    rows = [
        _row("choice", "billing", model_choice="billing", confidence=0.9),
        _row("choice", "billing", model_choice="billing", confidence=0.9),
        _row("choice", "billing", model_choice="billing", confidence=0.9),
        _row("choice", "billing", model_choice="technical", confidence=0.9),
        _row("choice", "billing", model_choice="technical", confidence=0.9),
    ]
    assert ece_confidence(rows) == pytest.approx(0.3)


def test_ece_confidence_perfectly_calibrated_is_zero():
    rows = [
        _row("choice", "billing", model_choice="billing", confidence=1.0),
        _row("choice", "billing", model_choice="billing", confidence=1.0),
    ]
    assert ece_confidence(rows) == pytest.approx(0.0)


def test_ece_probability_bins_on_model_noul_not_confidence():
    # 3 rows all with model_noul=0.9 (same bin); gold true twice, false once.
    # empirical frequency of true = 2/3; mean probability = 0.9.
    # ECE = |2/3 - 0.9|
    rows = [
        _row("noul", "true", model_noul=0.9, confidence=0.4),
        _row("noul", "true", model_noul=0.9, confidence=0.4),
        _row("noul", "false", model_noul=0.9, confidence=0.4),
    ]
    assert ece_probability(rows) == pytest.approx(abs(2 / 3 - 0.9))


def test_reliability_bins_shape():
    rows = [
        _row("noul", "true", model_noul=0.85),
        _row("noul", "false", model_noul=0.15),
    ]
    bins = reliability_bins(
        rows,
        value_fn=lambda r: r["model_noul"],
        target_fn=lambda r: (1.0 if r["gold_answer"] == "true" else 0.0) if r["gold_answer"] != "" else None,
    )
    assert len(bins) == 2
    for b in bins:
        assert set(b) == {"bin_lower", "bin_upper", "n", "weighted_n", "mean_value", "weighted_target"}


# --- gold file hygiene -------------------------------------------------------


def test_validate_gold_rows_rejects_unknown_doc_id():
    rows = [{"doc_id": "T-999", "gold_answer": "true"}]
    with pytest.raises(GoldFileError) as exc:
        validate_gold_rows(rows, question_type="noul", known_doc_ids={"T-1", "T-2"})
    assert "T-999" in str(exc.value)
    assert "does not belong to this run" in str(exc.value)


def test_validate_gold_rows_rejects_invalid_noul_answer():
    rows = [{"doc_id": "T-1", "gold_answer": "maybe"}]
    with pytest.raises(GoldFileError) as exc:
        validate_gold_rows(rows, question_type="noul")
    assert "maybe" in str(exc.value)


def test_validate_gold_rows_rejects_unknown_choice_option():
    rows = [{"doc_id": "T-1", "gold_answer": "shipping"}]
    with pytest.raises(GoldFileError):
        validate_gold_rows(rows, question_type="choice", valid_options={"billing", "technical"})


def test_validate_gold_rows_accepts_unclear_for_every_type():
    rows = [{"doc_id": "T-1", "gold_answer": "unclear"}]
    validate_gold_rows(rows, question_type="noul")
    validate_gold_rows(rows, question_type="choice", valid_options={"billing"})
    validate_gold_rows(rows, question_type="score")


def test_validate_gold_rows_rejects_non_integer_score():
    rows = [{"doc_id": "T-1", "gold_answer": "high"}]
    with pytest.raises(GoldFileError):
        validate_gold_rows(rows, question_type="score")


# --- recommend_threshold -----------------------------------------------------


def test_recommend_threshold_recovers_known_ideal_threshold():
    # Rows with confidence 0.5..0.95; only confidence >= 0.8 are reliably
    # correct (90%+), below that ~50/50. With enough rows per level to
    # clear min_n_eff, the search should land at or above 0.8.
    rows = []
    for conf in [0.5, 0.6, 0.7, 0.8, 0.9]:
        for i in range(40):
            correct = conf >= 0.8 or i % 2 == 0  # tight accuracy above 0.8, noisy below
            rows.append(
                _row("choice", "billing", model_choice="billing" if correct else "technical", confidence=conf)
            )
    result = recommend_threshold(rows, target_accuracy=0.95, min_n_eff=30)
    assert result.threshold is not None
    assert result.threshold >= 0.8
    assert result.coverage is not None
    assert 0.0 < result.coverage <= 1.0


def test_recommend_threshold_returns_none_when_nothing_qualifies():
    rows = [_row("choice", "billing", model_choice="technical", confidence=c) for c in
            [0.5] * 50]
    result = recommend_threshold(rows, target_accuracy=0.99, min_n_eff=30)
    assert result.threshold is None
    assert result.coverage is None


def test_recommend_threshold_rejects_high_threshold_with_too_few_surviving_rows():
    # Only 5 rows at confidence 0.99, all correct -- would show 100% but
    # fails the min_n_eff=30 guard and must be skipped, not returned.
    rows = [_row("choice", "billing", model_choice="billing", confidence=0.99) for _ in range(5)]
    rows += [_row("choice", "billing", model_choice="technical", confidence=0.5) for _ in range(50)]
    result = recommend_threshold(rows, target_accuracy=0.9, min_n_eff=30)
    assert result.threshold is None  # the 0.99 candidate alone would satisfy accuracy but not n_eff


def test_recommend_threshold_lowest_qualifying_not_highest():
    # Confidence 0.6 and above are both perfectly accurate with plenty of
    # rows; the search must return 0.6 (more coverage), not 0.9.
    rows = [_row("choice", "billing", model_choice="billing", confidence=0.6) for _ in range(40)]
    rows += [_row("choice", "billing", model_choice="billing", confidence=0.9) for _ in range(40)]
    result = recommend_threshold(rows, target_accuracy=0.99, min_n_eff=30)
    assert result.threshold == pytest.approx(0.6)


# --- score_question (integration of the above) ------------------------------


def test_score_question_insufficient_flagged_by_low_n():
    rows = [_row("noul", "true", model_noul=0.9) for _ in range(5)]
    result = score_question(rows, question_id="q", question_type="noul")
    assert result.n < MIN_USABLE_ROWS


def test_score_question_noul_has_ece_probability_not_ece_confidence():
    rows = [_row("noul", "true", model_noul=0.9) for _ in range(40)]
    result = score_question(rows, question_id="q", question_type="noul")
    assert result.ece_confidence is None
    assert result.ece_probability is not None


def test_score_question_choice_has_ece_confidence_not_ece_probability():
    rows = [_row("choice", "billing", model_choice="billing", confidence=0.9) for _ in range(40)]
    result = score_question(rows, question_id="q", question_type="choice")
    assert result.ece_confidence is not None
    assert result.ece_probability is None


def test_score_question_only_score_type_has_adjacent_agreement():
    noul_rows = [_row("noul", "true", model_noul=0.9) for _ in range(40)]
    score_rows = [_row("score", "1", model_modal_level="1", confidence=0.9) for _ in range(40)]
    assert score_question(noul_rows, question_id="a", question_type="noul").adjacent_agreement is None
    assert score_question(score_rows, question_id="b", question_type="score").adjacent_agreement is not None


def test_no_scoring_function_aggregates_across_questions():
    """Structural guard for the 'never aggregate across questions' rule:
    every public scoring function takes rows for exactly one question and
    returns per-question output, never a dict keyed by multiple
    question_ids or a single number meant to span types."""
    import inspect

    import jev_census.scoring as scoring_module

    for name in ("weighted_accuracy", "ece_confidence", "ece_probability", "recommend_threshold", "score_question"):
        func = getattr(scoring_module, name)
        params = inspect.signature(func).parameters
        assert "question_ids" not in params
        assert "questions" not in params
