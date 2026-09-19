"""T4.4: graceful shutdown on SIGINT. Unlike T2.7's kill -9 (which allows no
cleanup at all), a SIGINT must drain in-flight work, finalize the shard,
checkpoint, print the resume command, and exit non-zero (130) — then a
`--resume` must complete correctly with no duplicate or re-paid cells.

Runs `census run` as a real OS subprocess with `CENSUS_FAKE_CLIENT=1` (the
deterministic offline fake), per the Testing rules in 02-BUILD-PLAN.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from jev_census.scheduler import SchedulerConfig, run_scheduled
from tests.fakes import FakeAsyncJevClient

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _write_corpus(path: Path, n: int) -> None:
    rows = [
        {"ticket_id": f"T-{i:04d}", "subject": f"subject {i}", "body": f"unique body text for row {i}"}
        for i in range(n)
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)


def _read_run_id(census_dir: Path) -> str:
    runs_dir = census_dir / "runs"
    [run_dir] = list(runs_dir.iterdir())
    return json.loads((run_dir / "manifest.json").read_text())["run_id"]


def _rows_without_provenance(path: Path) -> set[tuple]:
    table = pq.read_table(path)
    keep = [c for c in table.column_names if c not in {"call_id", "run_id", "ts", "input_tokens"}]
    rows = table.select(keep).to_pylist()
    return {
        tuple(sorted((k, json.dumps(v, sort_keys=True, default=str)) for k, v in row.items()))
        for row in rows
    }


def test_sigint_stops_cleanly_and_resume_matches_a_clean_run(tmp_path):
    n_docs = 40
    corpus = tmp_path / "tickets.parquet"
    _write_corpus(corpus, n_docs)
    questions_path = FIXTURES / "support-triage.yaml"

    interrupted_census_dir = tmp_path / "census-interrupted"
    interrupted_out = tmp_path / "results-interrupted"

    env = os.environ.copy()
    env["CENSUS_FAKE_CLIENT"] = "1"
    env["CENSUS_FAKE_CLIENT_DELAY"] = "0.1"  # slow enough for a reliable interrupt window

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "jev_census.cli", "run",
            "--input", str(corpus),
            "--questions", str(questions_path),
            "--budget", "1000",
            "--out", str(interrupted_out),
            "--census-dir", str(interrupted_census_dir),
            "--id-field", "ticket_id",
            "--shard-size", "5",
            "--concurrency-max", "4",
            "--quiet",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    time.sleep(1.0)
    assert proc.poll() is None, "process finished before we could interrupt it — test window too short"
    proc.send_signal(signal.SIGINT)
    stdout, stderr = proc.communicate(timeout=15)

    assert proc.returncode == 130, f"expected exit 130, got {proc.returncode}\n{stdout}\n{stderr}"
    assert "interrupted" in stdout.lower()
    assert "--resume" in stdout

    run_id = _read_run_id(interrupted_census_dir)
    shards_before_resume = list((interrupted_census_dir / "runs" / run_id / "shards").glob("*.parquet"))
    docs_before_resume = set()
    for shard in shards_before_resume:
        docs_before_resume.update(pq.read_table(shard, columns=["doc_id"]).column("doc_id").to_pylist())
    assert len(docs_before_resume) < n_docs, "the interrupt landed after the run already finished"

    # Resume, fast this time.
    env["CENSUS_FAKE_CLIENT_DELAY"] = "0"
    resumed = subprocess.run(
        [
            sys.executable, "-m", "jev_census.cli", "run",
            "--input", str(corpus),
            "--questions", str(questions_path),
            "--budget", "1000",
            "--out", str(interrupted_out),
            "--census-dir", str(interrupted_census_dir),
            "--id-field", "ticket_id",
            "--shard-size", "5",
            "--resume", run_id,
            "--quiet",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr

    resumed_table = pq.read_table(interrupted_out / "cells.parquet")
    assert resumed_table.num_rows == n_docs * 4

    rows = resumed_table.to_pylist()
    pairs = [(r["doc_id"], r["question_id"]) for r in rows]
    assert len(pairs) == len(set(pairs)), "duplicate cells after resuming from a SIGINT stop"
    assert {r["doc_id"] for r in rows} == {f"T-{i:04d}" for i in range(n_docs)}

    # Clean, uninterrupted run of the same corpus/questions, for comparison.
    clean_census_dir = tmp_path / "census-clean"
    clean_out = tmp_path / "results-clean"
    clean_config = SchedulerConfig(
        input_path=corpus,
        questions_path=questions_path,
        budget_usd=1000.0,
        out_dir=clean_out,
        census_dir=clean_census_dir,
        id_field="ticket_id",
        shard_size=5,
    )
    asyncio.run(run_scheduled(clean_config, FakeAsyncJevClient()))

    resumed_rows = _rows_without_provenance(interrupted_out / "cells.parquet")
    clean_rows = _rows_without_provenance(clean_out / "cells.parquet")
    assert resumed_rows == clean_rows, "resumed run's cells differ from a clean, uninterrupted run"
