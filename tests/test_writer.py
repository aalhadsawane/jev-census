from datetime import UTC, datetime

import pyarrow.parquet as pq

from jev_census.cell import Cell
from jev_census.writer import CELLS_SCHEMA, write_cells_parquet

TS = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _base_kwargs(**overrides):
    kwargs = {
        "doc_id": "T-1041",
        "question_id": "is_urgent",
        "type": "noul",
        "noul": 0.97,
        "choice": None,
        "score": None,
        "probabilities": None,
        "legend": None,
        "confidence": 0.94,
        "confidence_source": "derived",
        "gate": "strict",
        "chunk_count": 1,
        "projection_id": "p0",
        "call_id": "call-1",
        "run_id": "run-1",
        "model": "jev-1.13.0",
        "questionset_hash": "qshash",
        "question_body_hash": "bodyhash",
        "input_tokens": 282,
        "ts": TS,
    }
    kwargs.update(overrides)
    return kwargs


def _cells():
    noul_cell = Cell(**_base_kwargs())
    choice_cell = Cell(
        **_base_kwargs(
            question_id="department",
            type="choice",
            noul=None,
            choice="billing",
            probabilities={"billing": 0.91, "technical": 0.06, "sales": 0.03},
            confidence=0.88,
            confidence_source="model",
        )
    )
    score_cell = Cell(
        **_base_kwargs(
            question_id="frustration",
            type="score",
            noul=None,
            score=2.7,
            probabilities={"0": 0.02, "1": 0.08, "2": 0.19, "3": 0.71},
            legend={"0": "Calm", "1": "Mildly annoyed", "2": "Frustrated", "3": "Angry"},
            confidence=0.74,
            confidence_source="model",
        )
    )
    return [noul_cell, choice_cell, score_cell]


def test_every_output_schema_column_present(tmp_path):
    out = tmp_path / "cells.parquet"
    write_cells_parquet(_cells(), out)

    table = pq.read_table(out)
    expected_columns = {f.name for f in CELLS_SCHEMA}
    assert set(table.column_names) == expected_columns
    assert table.num_rows == 3


def test_noul_row_has_no_probabilities_and_derived_confidence(tmp_path):
    out = tmp_path / "cells.parquet"
    write_cells_parquet(_cells(), out)
    rows = pq.read_table(out).to_pylist()
    noul_row = next(r for r in rows if r["question_id"] == "is_urgent")

    assert noul_row["noul"] == 0.97
    assert noul_row["choice"] is None
    assert noul_row["score"] is None
    assert noul_row["probabilities"] is None
    assert noul_row["confidence_source"] == "derived"


def test_choice_row_preserves_probabilities(tmp_path):
    out = tmp_path / "cells.parquet"
    write_cells_parquet(_cells(), out)
    rows = pq.read_table(out).to_pylist()
    choice_row = next(r for r in rows if r["question_id"] == "department")

    assert choice_row["choice"] == "billing"
    assert dict(choice_row["probabilities"]) == {"billing": 0.91, "technical": 0.06, "sales": 0.03}
    assert choice_row["legend"] is None


def test_score_row_preserves_legend_and_probabilities(tmp_path):
    out = tmp_path / "cells.parquet"
    write_cells_parquet(_cells(), out)
    rows = pq.read_table(out).to_pylist()
    score_row = next(r for r in rows if r["question_id"] == "frustration")

    assert score_row["score"] == 2.7
    assert dict(score_row["legend"]) == {
        "0": "Calm", "1": "Mildly annoyed", "2": "Frustrated", "3": "Angry"
    }
    assert dict(score_row["probabilities"]) == {"0": 0.02, "1": 0.08, "2": 0.19, "3": 0.71}


def test_provenance_columns_populated(tmp_path):
    out = tmp_path / "cells.parquet"
    write_cells_parquet(_cells(), out)
    rows = pq.read_table(out).to_pylist()
    for row in rows:
        assert row["doc_id"] == "T-1041"
        assert row["run_id"] == "run-1"
        assert row["call_id"] == "call-1"
        assert row["model"] == "jev-1.13.0"
        assert row["questionset_hash"] == "qshash"
        assert row["question_body_hash"] == "bodyhash"
        assert row["input_tokens"] == 282
        assert row["gate"] == "strict"
        assert row["chunk_count"] == 1
        assert row["ts"] == TS


def test_writer_creates_parent_directories(tmp_path):
    out = tmp_path / "nested" / "dir" / "cells.parquet"
    write_cells_parquet(_cells(), out)
    assert out.exists()
