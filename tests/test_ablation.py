"""T6.8 projection ablation tests: full-record variant construction, and
accuracy comparison (not agreement) against a shared gold set.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jev_census.ablation import format_ablation_report, full_record_variant, run_ablation_comparison
from jev_census.question_set import load_question_set

FIXTURES = Path(__file__).parent / "fixtures"


def test_full_record_variant_widens_every_question_projection():
    question_set = load_question_set(FIXTURES / "support-triage.yaml")
    all_fields = ["subject", "body", "thread", "created_at"]
    widened = full_record_variant(question_set, all_fields)
    for q in widened.questions:
        assert q.projection == all_fields
    # Nothing else changed.
    assert [q.id for q in widened.questions] == [q.id for q in question_set.questions]
    assert [q.type for q in widened.questions] == [q.type for q in question_set.questions]
    assert [q.instructions for q in widened.questions] == [q.instructions for q in question_set.questions]


def _gold_csv(tmp_path, question_id, question_type, rows):
    path = tmp_path / f"{question_id}.csv"
    fieldnames = ["doc_id", "question_id", "type", "stratum", "stratum_weight", "gold_answer"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "doc_id": r["doc_id"], "question_id": question_id, "type": question_type,
                    "stratum": "s", "stratum_weight": r.get("weight", 1.0), "gold_answer": r["gold"],
                }
            )
    return path


def _cell(doc_id, noul):
    return {"doc_id": doc_id, "type": "noul", "noul": noul, "choice": None, "score": None,
            "probabilities": None, "confidence": abs(noul - 0.5) * 2}


def test_ablation_compares_accuracy_not_agreement(tmp_path):
    # 4 documents; gold is true for all. Projected condition gets 3/4
    # right; full-record gets all 4 right -- a genuine accuracy difference,
    # which agreement (schema_tune.py's metric) would not surface the same
    # way since it only measures whether the two conditions agree with
    # EACH OTHER, not with the truth.
    gold_rows = [{"doc_id": f"d{i}", "gold": "true"} for i in range(4)]
    gold_path = _gold_csv(tmp_path, "is_urgent", "noul", gold_rows)

    projected_cells = {
        "is_urgent": [
            _cell("d0", 0.9), _cell("d1", 0.9), _cell("d2", 0.9), _cell("d3", 0.2),  # 1 wrong
        ]
    }
    full_record_cells = {
        "is_urgent": [
            _cell("d0", 0.9), _cell("d1", 0.9), _cell("d2", 0.9), _cell("d3", 0.9),  # all right
        ]
    }

    results = run_ablation_comparison({"is_urgent": gold_path}, projected_cells, full_record_cells)
    assert len(results) == 1
    r = results[0]
    assert r.projected.weighted_accuracy == pytest.approx(0.75)
    assert r.full_record.weighted_accuracy == pytest.approx(1.0)


def test_format_ablation_report_names_the_winner(tmp_path):
    gold_rows = [{"doc_id": f"d{i}", "gold": "true"} for i in range(4)]
    gold_path = _gold_csv(tmp_path, "is_urgent", "noul", gold_rows)
    projected_cells = {"is_urgent": [_cell("d0", 0.9), _cell("d1", 0.9), _cell("d2", 0.9), _cell("d3", 0.2)]}
    full_record_cells = {"is_urgent": [_cell("d0", 0.9), _cell("d1", 0.9), _cell("d2", 0.9), _cell("d3", 0.9)]}

    results = run_ablation_comparison({"is_urgent": gold_path}, projected_cells, full_record_cells)
    report = format_ablation_report(results)
    assert "full-record" in report
    lines = report.splitlines()
    assert "is_urgent" in lines[1]
    assert lines[1].rstrip().endswith("full-record")


def test_ablation_never_aggregates_across_questions(tmp_path):
    """Structural check mirroring scoring.py's own: AblationResult is
    per-question, and format_ablation_report prints one row per question,
    never a combined number."""
    gold_a = _gold_csv(tmp_path, "a", "noul", [{"doc_id": "d0", "gold": "true"}])
    gold_b = _gold_csv(tmp_path, "b", "noul", [{"doc_id": "d1", "gold": "false"}])
    cells = {"a": [_cell("d0", 0.9)], "b": [_cell("d1", 0.1)]}
    results = run_ablation_comparison({"a": gold_a, "b": gold_b}, cells, cells)
    assert {r.question_id for r in results} == {"a", "b"}
    assert len(format_ablation_report(results).splitlines()) == 1 + len(results)  # header + one row each
