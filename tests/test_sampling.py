"""T6.1 stratified sampling tests (05-P6-QUALITY.md's done-when): the sample
is not uniform, weights reconstruct corpus proportions, and re-running
without --overwrite refuses.
"""

from __future__ import annotations

import pytest

from jev_census.sampling import (
    SampledCell,
    _allocate,
    assign_stratum,
    label_csv_fieldnames,
    read_label_csv,
    stratified_sample,
    write_label_csv,
)


def _noul_cell(value, doc_id="d"):
    return {"doc_id": doc_id, "type": "noul", "noul": value, "choice": None, "score": None,
            "probabilities": None, "confidence": abs(value - 0.5) * 2, "chunk_count": 1}


def _choice_cell(choice, confidence, doc_id="d"):
    return {"doc_id": doc_id, "type": "choice", "noul": None, "choice": choice, "score": None,
            "probabilities": {choice: confidence}, "confidence": confidence, "chunk_count": 1}


def _score_cell(modal_level, confidence, doc_id="d"):
    probs = {str(modal_level): confidence}
    return {"doc_id": doc_id, "type": "score", "noul": None, "choice": None, "score": float(modal_level),
            "probabilities": probs, "confidence": confidence, "chunk_count": 1}


# --- assign_stratum ----------------------------------------------------


def test_noul_stratum_bins():
    assert assign_stratum(_noul_cell(0.05)) == "0.0-0.2"
    assert assign_stratum(_noul_cell(0.35)) == "0.2-0.4"
    assert assign_stratum(_noul_cell(0.99)) == "0.8-1.0"
    assert assign_stratum(_noul_cell(1.0)) == "0.8-1.0"  # right edge clamped into last bin


def test_choice_stratum_combines_option_and_confidence_bin():
    assert assign_stratum(_choice_cell("billing", 0.85)) == "billing:0.8-1.0"
    assert assign_stratum(_choice_cell("technical", 0.05)) == "technical:0.0-0.2"


def test_score_stratum_uses_modal_level_and_confidence_bin():
    assert assign_stratum(_score_cell(2, 0.65)) == "2:0.6-0.8"


# --- _allocate -----------------------------------------------------------


def test_allocate_equal_when_plenty_of_capacity():
    allocation = _allocate({"a": 100, "b": 100}, n=20)
    assert allocation == {"a": 10, "b": 10}


def test_allocate_redistributes_shortfall():
    # "a" only has 3 available; the other 17 must go to "b" and "c".
    allocation = _allocate({"a": 3, "b": 100, "c": 100}, n=20)
    assert allocation["a"] == 3
    assert allocation["b"] + allocation["c"] == 17
    assert sum(allocation.values()) == 20


def test_allocate_caps_at_total_available():
    allocation = _allocate({"a": 5, "b": 5}, n=100)
    assert allocation == {"a": 5, "b": 5}


# --- stratified_sample ----------------------------------------------------


def test_sample_is_not_uniform_across_a_skewed_corpus():
    """A corpus skewed heavily toward one bin: equal allocation must still
    reach the sparse strata, unlike a uniform sample which would return
    mostly the dominant bin."""
    cells = [_noul_cell(0.95, doc_id=f"d{i}") for i in range(1000)]
    cells += [_noul_cell(0.15, doc_id=f"rare{i}") for i in range(10)]
    sampled = stratified_sample(cells, n=20, seed=0)
    strata_present = {sc.stratum for sc in sampled}
    assert "0.0-0.2" in strata_present
    assert "0.8-1.0" in strata_present
    # equal allocation: each populated stratum gets roughly n/num_strata,
    # not proportional to its 1000:10 corpus share.
    counts = {}
    for sc in sampled:
        counts[sc.stratum] = counts.get(sc.stratum, 0) + 1
    assert counts["0.0-0.2"] >= 5  # a proportional sample would give ~0


def test_stratum_weights_reconstruct_corpus_proportions():
    """Sum of stratum_weight over the WHOLE sample approximates the total
    corpus cell count for this question, per the reweighting formula in
    05-P6-QUALITY.md."""
    cells = [_noul_cell(0.95, doc_id=f"d{i}") for i in range(500)]
    cells += [_noul_cell(0.15, doc_id=f"rare{i}") for i in range(50)]
    sampled = stratified_sample(cells, n=40, seed=0)
    total_weight = sum(sc.stratum_weight for sc in sampled)
    assert total_weight == pytest.approx(len(cells), rel=0.05)


def test_stratified_sample_deterministic_given_seed():
    cells = [_noul_cell(v / 100, doc_id=f"d{v}") for v in range(100)]
    s1 = stratified_sample(cells, n=20, seed=42)
    s2 = stratified_sample(cells, n=20, seed=42)
    assert [sc.cell["doc_id"] for sc in s1] == [sc.cell["doc_id"] for sc in s2]


def test_stratified_sample_never_exceeds_corpus_size():
    cells = [_noul_cell(0.5, doc_id=f"d{i}") for i in range(5)]
    sampled = stratified_sample(cells, n=200, seed=0)
    assert len(sampled) == 5


# --- label CSV I/O ---------------------------------------------------------


def test_write_and_read_label_csv_round_trips(tmp_path):
    sampled = [
        SampledCell(cell=_noul_cell(0.9, doc_id="T-1"), stratum="0.8-1.0", stratum_weight=3.0),
        SampledCell(cell=_noul_cell(0.1, doc_id="T-2"), stratum="0.0-0.2", stratum_weight=2.0),
    ]
    path = tmp_path / "is_urgent.csv"
    n = write_label_csv(
        path, question_id="is_urgent", question_type="noul", sampled=sampled,
        doc_fields_by_id={"T-1": {"subject": "s1", "body": "b1"}, "T-2": {"subject": "s2", "body": "b2"}},
        projection_fields=["subject", "body"],
    )
    assert n == 2

    rows = read_label_csv(path)
    assert len(rows) == 2
    assert rows[0]["doc_id"] == "T-1"
    assert rows[0]["subject"] == "s1"
    assert rows[0]["stratum_weight"] == 3.0
    assert rows[0]["gold_answer"] == ""


def test_label_csv_fieldnames_include_projection_fields():
    fields = label_csv_fieldnames(["subject", "body", "thread"])
    assert "subject" in fields and "thread" in fields
    assert fields[0] == "doc_id"
    assert fields[-1] == "notes"


def test_write_label_csv_refuses_to_overwrite_by_default(tmp_path):
    sampled = [SampledCell(cell=_noul_cell(0.9, doc_id="T-1"), stratum="0.8-1.0", stratum_weight=1.0)]
    path = tmp_path / "is_urgent.csv"
    write_label_csv(
        path, question_id="is_urgent", question_type="noul", sampled=sampled,
        doc_fields_by_id={}, projection_fields=[],
    )
    with pytest.raises(FileExistsError):
        write_label_csv(
            path, question_id="is_urgent", question_type="noul", sampled=sampled,
            doc_fields_by_id={}, projection_fields=[],
        )
    # --overwrite explicitly allows it.
    write_label_csv(
        path, question_id="is_urgent", question_type="noul", sampled=sampled,
        doc_fields_by_id={}, projection_fields=[], overwrite=True,
    )
