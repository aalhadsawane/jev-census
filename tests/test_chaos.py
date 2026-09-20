"""T2.7 chaos test: kill -9 mid-run, resume, diff against a clean run.

Runs `census run` as a real OS subprocess (so `kill -9` means something) with
`CENSUS_FAKE_CLIENT=1` — the deterministic in-process fake from fakes.py, so
this never touches the network or costs anything, per the Testing rules in
02-BUILD-PLAN.md ("Tests never make live API calls").

The "clean" comparison run goes through `run_scheduled` (the Scheduler) —
the same code path `census run` actually uses, and (since P5) the same code
path that plans real call groups and assigns hash-based `projection_id`s.
`runner.py`'s sequential `run_census` is kept only as a pure reference
implementation frozen at single-group planning (always `projection_id="p0"`)
and is deliberately not used here: comparing against it would fail on
`projection_id` alone for a reason that has nothing to do with resume
correctness, the property this test actually checks.
"""

from __future__ import annotations

import asyncio
import json
import os
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
    # Row index embedded in the body guarantees unique state per document, so
    # cache hits in this test only ever come from a genuine prior answer to
    # that same document, never from two documents accidentally sharing text.
    rows = [
        {"ticket_id": f"T-{i:04d}", "subject": f"subject {i}", "body": f"unique body text for row {i}"}
        for i in range(n)
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)


def _read_run_id(census_dir: Path) -> str:
    runs_dir = census_dir / "runs"
    [run_dir] = list(runs_dir.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return manifest["run_id"]


def _rows_without_provenance(path: Path) -> set[tuple]:
    """Compare on everything except call/run-batch provenance, which
    legitimately differs between two separate runs of the same corpus:
    call_id/run_id/ts obviously, but also input_tokens — it's the *whole
    call's* token count, duplicated onto every cell that call answered
    (writer.py), and a resumed run's calls are batched differently than a
    clean run's (partial cache hits split a document's remaining questions
    into a smaller follow-up call). Two runs computing identical decisions
    via differently-sized batches will legitimately report different
    input_tokens per cell without the underlying answer differing at all.
    probabilities/legend come back as lists of (key, value) pairs (map
    columns) rather than plain dicts, so they're JSON-canonicalized to stay
    hashable for set comparison."""
    table = pq.read_table(path)
    keep = [c for c in table.column_names if c not in {"call_id", "run_id", "ts", "input_tokens"}]
    rows = table.select(keep).to_pylist()
    return {
        tuple(sorted((k, json.dumps(v, sort_keys=True, default=str)) for k, v in row.items()))
        for row in rows
    }


def test_kill_nine_then_resume_matches_a_clean_run(tmp_path):
    n_docs = 40
    corpus = tmp_path / "tickets.parquet"
    _write_corpus(corpus, n_docs)
    questions_path = FIXTURES / "support-triage.yaml"

    killed_census_dir = tmp_path / "census-killed"
    killed_out = tmp_path / "results-killed"

    env = os.environ.copy()
    env["CENSUS_FAKE_CLIENT"] = "1"
    env["CENSUS_FAKE_CLIENT_DELAY"] = "0.15"  # slow enough for a reliable kill window

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "jev_census.cli", "run",
            "--input", str(corpus),
            "--questions", str(questions_path),
            "--budget", "1000",
            "--out", str(killed_out),
            "--census-dir", str(killed_census_dir),
            "--id-field", "ticket_id",
            "--shard-size", "5",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    time.sleep(1.2)  # ~8 calls' worth at 0.15s each — well short of all 40
    assert proc.poll() is None, "process finished before we could kill it — test window too short"
    proc.kill()  # SIGKILL, i.e. kill -9
    proc.wait(timeout=10)
    assert proc.returncode != 0

    run_id = _read_run_id(killed_census_dir)
    shards_before_resume = list((killed_census_dir / "runs" / run_id / "shards").glob("*.parquet"))
    assert len(shards_before_resume) >= 1, "nothing was durably flushed before the kill"

    docs_before_resume = set()
    for shard in shards_before_resume:
        docs_before_resume.update(pq.read_table(shard, columns=["doc_id"]).column("doc_id").to_pylist())
    assert len(docs_before_resume) < n_docs, "the kill landed after the run already finished"

    # Resume, fast this time (no artificial delay), until it completes.
    env["CENSUS_FAKE_CLIENT_DELAY"] = "0"
    resumed = subprocess.run(
        [
            sys.executable, "-m", "jev_census.cli", "run",
            "--input", str(corpus),
            "--questions", str(questions_path),
            "--budget", "1000",
            "--out", str(killed_out),
            "--census-dir", str(killed_census_dir),
            "--id-field", "ticket_id",
            "--shard-size", "5",
            "--resume", run_id,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr

    resumed_table = pq.read_table(killed_out / "cells.parquet")
    assert resumed_table.num_rows == n_docs * 4  # 4 questions in support-triage.yaml

    rows = resumed_table.to_pylist()
    pairs = [(r["doc_id"], r["question_id"]) for r in rows]
    assert len(pairs) == len(set(pairs)), "duplicate (doc_id, question_id) cells after resume"
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

    resumed_rows = _rows_without_provenance(killed_out / "cells.parquet")
    clean_rows = _rows_without_provenance(clean_out / "cells.parquet")
    assert resumed_rows == clean_rows, "resumed run's cells differ from a clean, uninterrupted run"
