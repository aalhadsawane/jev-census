"""T6.6 tests: review queue export selects cells below threshold on the
question's own scale, overrides round-trip, and overrides never touch
cells.parquet (05-P6-QUALITY.md's done-when).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from jev_census.cell import Cell
from jev_census.overrides import export_review_queue, import_overrides, read_review_queue
from jev_census.report import write_validation_json
from jev_census.scoring import QuestionScore, ThresholdResult
from jev_census.shard_writer import ShardWriter

TS = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _cell(doc_id, noul, *, question_id="is_urgent", projection_id="p0"):
    return Cell(
        doc_id=doc_id, question_id=question_id, type="noul", noul=noul, choice=None, score=None,
        probabilities=None, legend=None, confidence=abs(noul - 0.5) * 2, confidence_source="derived",
        gate="strict", chunk_count=1, projection_id=projection_id, call_id="c1", run_id="r1",
        model="jev-1.13.0", questionset_hash="qh", question_body_hash="bh", input_tokens=100, ts=TS,
    )


def _make_run(tmp_path, cells, *, corpus_rows):
    run_dir = tmp_path / ".census" / "runs" / "r1"
    (run_dir / "shards").mkdir(parents=True)
    corpus_path = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(corpus_rows), corpus_path)

    manifest = {
        "run_id": "r1", "questionset_hash": "qh", "questions": {"is_urgent": "bh"},
        "input_path": str(corpus_path), "id_field": "ticket_id",
        "projection_groups": {"p0": ["subject", "body"]},
        "created_at": "2026-09-20T00:00:00Z",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    writer = ShardWriter(run_dir / "shards", shard_size=1000)
    for cell in cells:
        writer.add([cell])
    writer.flush()
    return run_dir


def test_export_review_queue_selects_only_below_threshold(tmp_path):
    cells = [
        _cell("T-1", 0.05),  # |p-0.5| = 0.45, above threshold 0.2 -> NOT reviewed
        _cell("T-2", 0.55),  # |p-0.5| = 0.05, below threshold 0.2 -> reviewed
        _cell("T-3", 0.45),  # |p-0.5| = 0.05, below threshold 0.2 -> reviewed
    ]
    corpus_rows = [
        {"ticket_id": "T-1", "subject": "s1", "body": "b1"},
        {"ticket_id": "T-2", "subject": "s2", "body": "b2"},
        {"ticket_id": "T-3", "subject": "s3", "body": "b3"},
    ]
    run_dir = _make_run(tmp_path, cells, corpus_rows=corpus_rows)

    validation_dir = tmp_path / "results" / "validation"
    score = QuestionScore(
        question_id="is_urgent", type="noul", n=100, n_unclear=0,
        weighted_accuracy=0.9, unweighted_accuracy=0.9, n_eff=100.0,
        wilson_lo=0.85, wilson_hi=0.95, ece_confidence=None, ece_probability=0.05,
        adjacent_agreement=None, threshold=ThresholdResult(threshold=0.2, coverage=0.7),
    )
    write_validation_json(validation_dir / "is_urgent.json", score)

    out_path = tmp_path / "results" / "review_queue.csv"
    count = export_review_queue(run_dir, validation_dir, out_path)
    assert count == 2

    rows = read_review_queue(out_path)
    doc_ids = {r["doc_id"] for r in rows}
    assert doc_ids == {"T-2", "T-3"}
    assert "T-1" not in doc_ids
    # projection fields included, human_answer blank
    assert rows[0]["subject"] in {"s2", "s3"}
    assert rows[0]["human_answer"] == ""


def test_export_review_queue_skips_questions_with_no_threshold(tmp_path):
    cells = [_cell("T-1", 0.5)]
    corpus_rows = [{"ticket_id": "T-1", "subject": "s1", "body": "b1"}]
    run_dir = _make_run(tmp_path, cells, corpus_rows=corpus_rows)

    validation_dir = tmp_path / "results" / "validation"
    score = QuestionScore(
        question_id="is_urgent", type="noul", n=100, n_unclear=0,
        weighted_accuracy=0.6, unweighted_accuracy=0.6, n_eff=100.0,
        wilson_lo=0.5, wilson_hi=0.7, ece_confidence=None, ece_probability=0.2,
        adjacent_agreement=None, threshold=ThresholdResult(threshold=None, coverage=None),
    )
    write_validation_json(validation_dir / "is_urgent.json", score)

    out_path = tmp_path / "results" / "review_queue.csv"
    count = export_review_queue(run_dir, validation_dir, out_path)
    assert count == 0


# --- overrides import --------------------------------------------------


def test_import_overrides_writes_only_answered_rows(tmp_path):
    rows = [
        {"doc_id": "T-1", "question_id": "is_urgent", "human_answer": "true"},
        {"doc_id": "T-2", "question_id": "is_urgent", "human_answer": ""},  # unreviewed, skipped
    ]
    path = tmp_path / "overrides.parquet"
    n = import_overrides(path, rows, reviewer="alice", source_run_id="r1")
    assert n == 1

    table = pq.read_table(path)
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["doc_id"] == "T-1"
    assert row["human_answer"] == "true"
    assert row["reviewer"] == "alice"
    assert row["source_run_id"] == "r1"


def test_import_overrides_appends_across_calls(tmp_path):
    path = tmp_path / "overrides.parquet"
    import_overrides(path, [{"doc_id": "T-1", "question_id": "q", "human_answer": "true"}],
                      reviewer="alice", source_run_id="r1")
    import_overrides(path, [{"doc_id": "T-2", "question_id": "q", "human_answer": "false"}],
                      reviewer="bob", source_run_id="r1")
    table = pq.read_table(path)
    assert table.num_rows == 2
    assert set(table.column("doc_id").to_pylist()) == {"T-1", "T-2"}


def test_import_overrides_never_touches_cells_parquet(tmp_path):
    """The rule that matters (T6.6): cells.parquet must be byte-identical
    before and after an override import."""
    cells_path = tmp_path / "cells.parquet"
    table = pa.Table.from_pylist([{"doc_id": "T-1", "question_id": "q", "choice": "billing"}])
    pq.write_table(table, cells_path)
    before = cells_path.read_bytes()

    overrides_path = tmp_path / "overrides.parquet"
    import_overrides(overrides_path, [{"doc_id": "T-1", "question_id": "q", "human_answer": "technical"}],
                      reviewer="alice", source_run_id="r1")

    after = cells_path.read_bytes()
    assert before == after
