"""T7.4 acceptance (06-P7-ADOPTION.md): every SQL block in docs/RECIPES.md
must execute against real `census demo` output. Extracts each ```sql fenced
block and runs it through DuckDB -- if the recipes page drifts from the
actual cells.parquet schema, this test catches it, not a reader.

Runs `census demo` with CENSUS_FAKE_CLIENT=1 (deterministic, network-free,
per the Testing rules) to produce a real, schema-correct cells.parquet --
the SQL doesn't care whether the answers are real, only that the columns
and types match what a live run would produce, which the fake client's
answers do.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from jev_census.cli import app

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_PATH = REPO_ROOT / "docs" / "RECIPES.md"

runner = CliRunner()


def _extract_sql_blocks(markdown: str) -> list[str]:
    return re.findall(r"```sql\n(.*?)```", markdown, re.DOTALL)


@pytest.fixture(scope="module")
def demo_output(tmp_path_factory):
    """Runs `census demo` once (fake client) and returns the directory it
    was run from, so every recipe query below can share the same output
    rather than re-running the demo per query."""
    work_dir = tmp_path_factory.mktemp("recipes_demo")
    import os

    old_cwd = Path.cwd()
    old_env = os.environ.get("CENSUS_FAKE_CLIENT")
    os.chdir(work_dir)
    os.environ["CENSUS_FAKE_CLIENT"] = "1"
    try:
        result = runner.invoke(app, ["demo", "--budget", "1000"])
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("CENSUS_FAKE_CLIENT", None)
        else:
            os.environ["CENSUS_FAKE_CLIENT"] = old_env

    # A small fixture overrides.parquet, matching overrides.py's
    # OVERRIDES_SCHEMA, so recipe 3's join has something real to join
    # against even though the demo itself never runs a review cycle.
    cells = pq.read_table(work_dir / "demo-results" / "cells.parquet")
    a_row = cells.to_pylist()[0]
    overrides_schema = pa.schema(
        [
            ("doc_id", pa.string()), ("question_id", pa.string()), ("human_answer", pa.string()),
            ("reviewer", pa.string()), ("ts", pa.timestamp("us", tz="UTC")), ("source_run_id", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "doc_id": a_row["doc_id"], "question_id": a_row["question_id"],
                    "human_answer": "overridden-value", "reviewer": "test", "ts": None,
                    "source_run_id": "test-run",
                }
            ],
            schema=overrides_schema,
        ),
        work_dir / "demo-results" / "overrides.parquet",
    )
    return work_dir


def test_recipes_file_has_four_sql_blocks():
    blocks = _extract_sql_blocks(RECIPES_PATH.read_text(encoding="utf-8"))
    assert len(blocks) == 4


@pytest.mark.parametrize("block_index", range(4))
def test_recipe_sql_block_executes(demo_output, block_index):
    blocks = _extract_sql_blocks(RECIPES_PATH.read_text(encoding="utf-8"))
    sql = blocks[block_index]

    demo_data_dir = (REPO_ROOT / "src" / "jev_census" / "demo_data").resolve()
    if not demo_data_dir.exists():
        # If this test somehow runs against an installed package rather
        # than the source tree, fall back to the installed demo_data.
        import jev_census

        demo_data_dir = Path(jev_census.__file__).resolve().parent / "demo_data"
    # The query executes after chdir(demo_output) below, so this must be
    # absolute -- a relative path here would resolve against the wrong cwd.
    sql = sql.replace("demo_data_path_placeholder", str(demo_data_dir))

    con = duckdb.connect()
    con.execute(f"SET file_search_path = '{demo_output}'")
    import os

    old_cwd = Path.cwd()
    os.chdir(demo_output)
    try:
        result = con.execute(sql).fetchall()
    finally:
        os.chdir(old_cwd)
    assert result is not None  # every recipe returns rows or an empty result set, never an error
