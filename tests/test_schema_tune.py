"""T6.7 schema-tune tests: variant loading/merging, agreement/drift metrics
per type against hand-computed values, and the floors table enforcement.
"""

from __future__ import annotations

from pathlib import Path

from jev_census.question_set import load_question_set
from jev_census.schema_tune import (
    CAVEAT,
    FLOORS,
    compare_choice,
    compare_noul,
    compare_score,
    format_schema_tune_report,
    load_variants,
    score_variant,
    write_question_set_yaml,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_variants_merges_overrides_onto_reference():
    reference_path, variants = load_variants(FIXTURES / "schema-tune-variants.yaml")
    assert reference_path == FIXTURES / "support-triage.yaml"
    names = {v.name for v in variants}
    assert names == {"terse-criteria", "no-criteria"}

    terse = next(v for v in variants if v.name == "terse-criteria")
    department = next(q for q in terse.question_set.questions if q.id == "department")
    assert department.criteria == {"billing": "Payments.", "technical": "Bugs.", "sales": "Pricing."}
    # Unrelated question is untouched.
    is_urgent = next(q for q in terse.question_set.questions if q.id == "is_urgent")
    reference = load_question_set(FIXTURES / "support-triage.yaml")
    reference_is_urgent = next(q for q in reference.questions if q.id == "is_urgent")
    assert is_urgent.criteria == reference_is_urgent.criteria


def test_load_variants_null_override_clears_criteria():
    _, variants = load_variants(FIXTURES / "schema-tune-variants.yaml")
    no_criteria = next(v for v in variants if v.name == "no-criteria")
    is_urgent = next(q for q in no_criteria.question_set.questions if q.id == "is_urgent")
    assert is_urgent.criteria is None


def test_write_question_set_yaml_round_trips(tmp_path):
    _, variants = load_variants(FIXTURES / "schema-tune-variants.yaml")
    variant = variants[0]
    out_path = tmp_path / "variant.yaml"
    write_question_set_yaml(variant.question_set, out_path)
    reloaded = load_question_set(out_path)
    assert reloaded.questionset_hash != load_question_set(FIXTURES / "support-triage.yaml").questionset_hash
    assert {q.id for q in reloaded.questions} == {q.id for q in variant.question_set.questions}


# --- comparison metrics, hand-computed --------------------------------


def _noul_cell(doc_id, noul):
    return {"doc_id": doc_id, "noul": noul}


def test_compare_noul_hand_computed():
    reference = [_noul_cell("a", 0.9), _noul_cell("b", 0.1), _noul_cell("c", 0.6)]
    variant = [_noul_cell("a", 0.8), _noul_cell("b", 0.4), _noul_cell("c", 0.3)]
    # thresholded: a: true/true agree, b: false/false agree, c: true/false disagree -> 2/3
    # drift: |0.9-0.8|=0.1, |0.1-0.4|=0.3, |0.6-0.3|=0.3 -> mean 0.2333...
    agreement, drift = compare_noul(reference, variant)
    assert agreement == 2 / 3
    assert abs(drift - (0.1 + 0.3 + 0.3) / 3) < 1e-9


def _choice_cell(doc_id, choice, probs):
    return {"doc_id": doc_id, "choice": choice, "probabilities": probs}


def test_compare_choice_hand_computed():
    reference = [_choice_cell("a", "billing", {"billing": 0.9, "technical": 0.1})]
    variant = [_choice_cell("a", "technical", {"billing": 0.3, "technical": 0.7})]
    agreement, drift = compare_choice(reference, variant)
    assert agreement == 0.0
    assert drift == abs(0.9 - 0.3)  # drift in reference's chosen option's probability


def _score_cell(doc_id, score, probs):
    return {"doc_id": doc_id, "score": score, "probabilities": probs}


def test_compare_score_hand_computed():
    reference = [_score_cell("a", 2.0, {"0": 0.0, "1": 0.0, "2": 1.0})]
    variant = [_score_cell("a", 1.5, {"0": 0.0, "1": 1.0, "2": 0.0})]
    agreement, shift = compare_score(reference, variant)
    assert agreement == 0.0  # modal level 2 vs 1
    assert shift == 0.5


def test_compare_functions_ignore_unmatched_doc_ids():
    reference = [_noul_cell("a", 0.9)]
    variant = [_noul_cell("b", 0.9)]  # no overlap
    agreement, drift = compare_noul(reference, variant)
    assert agreement == 0.0 and drift == 0.0


# --- floors and report formatting --------------------------------------


def test_floors_table_matches_design_doc():
    assert FLOORS["noul"]["agreement_floor"] == 0.97
    assert FLOORS["noul"]["drift_floor"] == 0.05
    assert FLOORS["choice"]["agreement_floor"] == 0.95
    assert FLOORS["choice"]["drift_floor"] is None
    assert FLOORS["score"]["agreement_floor"] == 0.92
    assert FLOORS["score"]["drift_floor"] == 0.15


def test_report_includes_mandated_caveat_sentence_verbatim():
    report = format_schema_tune_report([])
    assert CAVEAT in report


def test_score_variant_pass_fail_against_floors(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    reference = load_question_set(FIXTURES / "support-triage.yaml")
    variant_qs = reference.model_copy()

    ref_cells = [
        {"doc_id": f"d{i}", "question_id": "is_urgent", "noul": 0.9, "choice": None, "score": None,
         "probabilities": None}
        for i in range(10)
    ]
    # Perfect agreement -> should PASS noul's floor (0.97 agreement, 0.05 drift).
    var_cells = ref_cells

    ref_path = tmp_path / "ref.parquet"
    var_path = tmp_path / "var.parquet"
    pq.write_table(pa.Table.from_pylist(ref_cells), ref_path)
    pq.write_table(pa.Table.from_pylist(var_cells), var_path)

    results = score_variant(ref_path, var_path, "v1", reference, variant_qs)
    is_urgent_result = next(r for r in results if r.question_id == "is_urgent")
    assert is_urgent_result.passed is True
    assert is_urgent_result.agreement == 1.0
