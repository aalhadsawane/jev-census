"""P6 end-to-end integration test: the full CLI pipeline that
05-P6-QUALITY.md's worked example describes --

    census run -> census label -> (human fills gold_answer) -> census validate
    -> census report (no gold set needed) -> census review export ->
    (human fills human_answer) -> census review import

exercised through Typer's CliRunner (in-process — the fake client makes
this network-free per the Testing rules) rather than unit-testing each
module in isolation, which is the level at which cross-module wiring bugs
(mismatched dict shapes, wrong paths) actually show up.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pyarrow.parquet as pq
from typer.testing import CliRunner

from jev_census.cli import app

runner = CliRunner()


def _write_corpus(path: Path, n: int) -> None:
    import pyarrow as pa

    rows = [
        {
            "ticket_id": f"T-{i:04d}",
            "subject": f"subject {i}",
            "body": f"Help! My payouts have been failing for days. Row {i}."
            if i % 3 == 0
            else f"Where did the CSV export go in the dashboard? Row {i}.",
        }
        for i in range(n)
    ]
    pa.parquet.write_table(pa.Table.from_pylist(rows), path)


def _fill_gold_answers(csv_path: Path, *, wrong_fraction: float = 0.1) -> None:
    """Simulates a human labeller: mostly agrees with the model (so the
    validated accuracy is high), deliberately disagrees on a fixed fraction
    so the statistics aren't trivially 100%, and marks a couple 'unclear'.
    This is a synthetic stand-in for real human judgment, sufficient to
    prove the pipeline mechanics -- not a claim about real model accuracy.
    """
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = list(rows[0].keys()) if rows else []

    for i, row in enumerate(rows):
        qtype = row["type"]
        if i % 10 == 0:
            row["gold_answer"] = "unclear"
            continue
        # Distinct modulus from the "unclear" check above, so this actually
        # fires for some non-unclear rows rather than being permanently
        # shadowed by it.
        wrong = (i % 10) in range(1, 1 + round(wrong_fraction * 10))
        if qtype == "noul":
            model_says_true = row["model_noul"] not in ("", None) and float(row["model_noul"]) > 0.5
            row["gold_answer"] = str(not model_says_true if wrong else model_says_true).lower()
        elif qtype == "choice":
            options = {"billing", "technical", "sales"}
            other = next(iter(options - {row["model_choice"]}))
            row["gold_answer"] = other if wrong else row["model_choice"]
        elif qtype == "score":
            correct_level = int(row["model_modal_level"])
            row["gold_answer"] = str(correct_level + 2 if wrong else correct_level)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_full_p6_pipeline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_FAKE_CLIENT", "1")
    monkeypatch.chdir(tmp_path)

    corpus = tmp_path / "tickets.parquet"
    _write_corpus(corpus, n=60)
    questions_path = Path(__file__).parent / "fixtures" / "support-triage.yaml"

    # 1. census run
    result = runner.invoke(
        app,
        [
            "run", "--input", str(corpus), "--questions", str(questions_path),
            "--budget", "1000", "--out", "results", "--census-dir", ".census",
            "--id-field", "ticket_id", "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    run_dirs = list((tmp_path / ".census" / "runs").iterdir())
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name

    # 2. census label, for every question in the fixture.
    question_ids = ["is_urgent", "department", "frustration", "churn_risk"]
    for qid in question_ids:
        result = runner.invoke(
            app, ["label", "--run", run_id, "--question", qid, "--n", "40", "--seed", "0"]
        )
        assert result.exit_code == 0, result.output
        assert "sampled" in result.output

    # Re-running without --overwrite must refuse (T6.1 done-when).
    result = runner.invoke(
        app, ["label", "--run", run_id, "--question", "is_urgent", "--n", "40"]
    )
    assert result.exit_code != 0

    # 3. Simulate a human filling in gold_answer.
    label_dir = tmp_path / "results" / "label"
    for qid in question_ids:
        _fill_gold_answers(label_dir / f"{qid}.csv")

    # 4. census validate. Exit code isn't asserted here: with a deliberate
    # ~10% injected disagreement rate, accuracy can legitimately land on
    # either side of the default 0.90 gate floor depending on which strata
    # the sample happened to draw, and a strict-question FAIL is a correct
    # non-zero exit (report.py's own unit tests already cover the PASS/FAIL
    # exit-code rule precisely). What this step verifies is that the
    # command runs, reads the gold set, and writes a real report.
    result = runner.invoke(
        app, ["validate", "--run", run_id, "--gold", str(label_dir), "--census-dir", ".census", "--out", "results"]
    )
    assert "wrote results/validation_report.md" in result.output, result.output
    report_path = tmp_path / "results" / "validation_report.md"
    assert report_path.exists()
    report_text = report_path.read_text()
    for qid in question_ids:
        assert qid in report_text
    assert "question" in report_text and "verdict" in report_text  # header present
    # The synthetic gold set deliberately disagrees with the model some of
    # the time -- if every question's accuracy column reads 1.00, the
    # "wrong" branch in _fill_gold_answers silently never fired (a
    # test-fixture bug, not something real labelling would ever show).
    table_lines = report_text.splitlines()[1 : 1 + len(question_ids)]
    accuracies = [line.split()[2] for line in table_lines]
    assert any(acc != "1.00" for acc in accuracies), f"every question scored 1.00: {accuracies}"

    # Reliability CSVs were written per question.
    for qid in question_ids:
        assert (tmp_path / "results" / "reliability" / f"{qid}.csv").exists()

    # 5. census report regenerates identically WITHOUT the gold CSVs.
    original_report = report_path.read_text()
    for qid in question_ids:
        (label_dir / f"{qid}.csv").unlink()
    result = runner.invoke(app, ["report", "--run", run_id, "--census-dir", ".census", "--out", "results"])
    assert result.exit_code == 0, result.output
    assert report_path.read_text() == original_report

    # 6. census review export + import
    result = runner.invoke(app, ["review", "export", "--run", run_id, "--census-dir", ".census", "--out", "results"])
    assert result.exit_code == 0, result.output
    review_path = tmp_path / "results" / "review_queue.csv"
    assert review_path.exists()

    cells_path = tmp_path / "results" / "cells.parquet"
    cells_before = cells_path.read_bytes()

    with review_path.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.DictReader(handle))
    if review_rows:
        fieldnames = list(review_rows[0].keys())
        review_rows[0]["human_answer"] = "true"
        with review_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(review_rows)

        result = runner.invoke(
            app,
            ["review", "import", "--run", run_id, "--file", str(review_path), "--reviewer", "alice", "--out", "results"],
        )
        assert result.exit_code == 0, result.output
        overrides_path = tmp_path / "results" / "overrides.parquet"
        assert overrides_path.exists()
        table = pq.read_table(overrides_path)
        assert table.num_rows >= 1

    # cells.parquet is untouched by the whole review cycle (T6.6's rule).
    assert cells_path.read_bytes() == cells_before
