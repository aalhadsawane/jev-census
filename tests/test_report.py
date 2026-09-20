"""T6.5 report tests: the table reproduces the README's exact worked
example, verdict rules, and JSON round-trip for `census report`.
"""

from __future__ import annotations

from jev_census.report import (
    any_strict_question_failed,
    compute_verdict,
    format_report_table,
    read_validation_json,
    render_validation_report,
    score_from_dict,
    score_to_dict,
    write_validation_json,
)
from jev_census.scoring import QuestionScore, ThresholdResult


def _score(question_id, type, *, accuracy, n, ece=None, threshold=None, coverage=None, n_unclear=0):
    return QuestionScore(
        question_id=question_id,
        type=type,
        n=n,
        n_unclear=n_unclear,
        weighted_accuracy=accuracy,
        unweighted_accuracy=accuracy,
        n_eff=float(n),
        wilson_lo=max(0.0, accuracy - 0.05) if accuracy is not None else None,
        wilson_hi=min(1.0, accuracy + 0.05) if accuracy is not None else None,
        ece_confidence=ece if type != "noul" else None,
        ece_probability=ece if type == "noul" else None,
        adjacent_agreement=0.9 if type == "score" else None,
        threshold=ThresholdResult(threshold=threshold, coverage=coverage),
    )


# The README's worked example, reproduced as QuestionScore inputs.
_README_SCORES = [
    _score("is_urgent", "noul", accuracy=0.96, n=240, ece=0.04, threshold=0.31, coverage=0.94),
    _score("department", "choice", accuracy=0.93, n=300, ece=0.031, threshold=0.74, coverage=0.86),
    _score("churn_risk", "noul", accuracy=0.91, n=240, ece=0.06, threshold=0.40, coverage=0.71),
    _score("frustration", "score", accuracy=0.68, n=200, ece=0.190, threshold=None, coverage=None),
]
_README_GATES = {"is_urgent": "strict", "department": "strict", "churn_risk": "strict", "frustration": "exploratory"}


def test_table_reproduces_readme_worked_example():
    table = format_report_table(_README_SCORES, _README_GATES)
    lines = table.splitlines()
    assert lines[0].split() == ["question", "type", "accuracy", "n", "ECE", "threshold", "coverage", "verdict"]

    assert "is_urgent" in lines[1] and "noul" in lines[1] and "0.96" in lines[1]
    assert "|p-0.5| > 0.31" in lines[1] and "94%" in lines[1] and lines[1].rstrip().endswith("PASS")

    assert "department" in lines[2] and "0.93" in lines[2] and "0.031" in lines[2]
    assert "conf > 0.74" in lines[2] and "86%" in lines[2] and lines[2].rstrip().endswith("PASS")

    assert "churn_risk" in lines[3] and "0.91" in lines[3]
    assert "|p-0.5| > 0.40" in lines[3] and "71%" in lines[3] and lines[3].rstrip().endswith("PASS")

    assert "frustration" in lines[4] and "score" in lines[4] and "0.68" in lines[4]
    assert "0.190" in lines[4]
    assert lines[4].rstrip().endswith("FAIL — exploratory only")
    # noul rows print — for ECE; the threshold/coverage columns print — when
    # no threshold was found (frustration).
    assert lines[1].split("  ")[4].strip() == "—" or "—" in lines[1]


def test_noul_ece_column_always_dash():
    score = _score("q", "noul", accuracy=0.9, n=40, ece=0.5)  # ece ignored for noul in the table
    table = format_report_table([score], {"q": "strict"})
    row = table.splitlines()[1]
    cells = [c for c in row.split("  ") if c.strip()]
    # ECE is the 5th column (index 4)
    assert cells[4].strip() == "—"


# --- verdicts ----------------------------------------------------------


def test_verdict_pass_above_gate_floor():
    score = _score("q", "noul", accuracy=0.95, n=100)
    assert compute_verdict(score, gate="strict") == "PASS"


def test_verdict_fail_below_gate_floor_strict():
    score = _score("q", "noul", accuracy=0.5, n=100)
    assert compute_verdict(score, gate="strict") == "FAIL"


def test_verdict_fail_exploratory_marked_distinctly():
    score = _score("q", "score", accuracy=0.68, n=200)
    assert compute_verdict(score, gate="exploratory") == "FAIL — exploratory only"


def test_verdict_insufficient_below_min_rows():
    score = _score("q", "noul", accuracy=1.0, n=5)
    assert compute_verdict(score, gate="strict") == "INSUFFICIENT"


def test_verdict_gate_floor_is_configurable():
    score = _score("q", "noul", accuracy=0.85, n=100)
    assert compute_verdict(score, gate="strict", gate_floor=0.90) == "FAIL"
    assert compute_verdict(score, gate="strict", gate_floor=0.80) == "PASS"


def test_exit_code_only_non_zero_for_strict_failure():
    exploratory_fail = [_score("frustration", "score", accuracy=0.68, n=200)]
    assert any_strict_question_failed(exploratory_fail, {"frustration": "exploratory"}) is False

    strict_fail = [_score("q", "noul", accuracy=0.5, n=100)]
    assert any_strict_question_failed(strict_fail, {"q": "strict"}) is True

    all_pass = _README_SCORES
    assert any_strict_question_failed(all_pass, _README_GATES) is False


def test_full_report_renders_table_and_footnotes():
    report = render_validation_report(_README_SCORES, _README_GATES)
    assert "is_urgent" in report
    assert "### is_urgent" in report
    assert "n_eff" in report
    assert "ece_probability" in report  # noul footnote present
    assert "adjacent agreement" in report  # score footnote present


def test_footnotes_report_excluded_chunked_count():
    report = render_validation_report(
        _README_SCORES, _README_GATES, excluded_chunked={"is_urgent": 12}
    )
    assert "12 chunked" in report


# --- JSON round-trip for `census report` ------------------------------


def test_validation_json_round_trips(tmp_path):
    score = _README_SCORES[1]  # department: has a threshold and ece_confidence
    path = tmp_path / "department.json"
    write_validation_json(path, score)
    loaded = read_validation_json(path)
    assert loaded == score


def test_score_to_dict_and_back_preserves_nested_threshold():
    score = _README_SCORES[0]
    d = score_to_dict(score)
    assert isinstance(d["threshold"], dict)
    restored = score_from_dict(d)
    assert restored == score
