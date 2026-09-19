from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from jev_census.cell import Cell
from jev_census.shard_writer import ShardWriter

TS = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _cell(doc_id: str) -> Cell:
    return Cell(
        doc_id=doc_id,
        question_id="is_urgent",
        type="noul",
        noul=0.5,
        choice=None,
        score=None,
        probabilities=None,
        legend=None,
        confidence=0.0,
        confidence_source="derived",
        gate="strict",
        chunk_count=1,
        projection_id="p0",
        call_id="c1",
        run_id="r1",
        model="jev-1.13.0",
        questionset_hash="qh",
        question_body_hash="bh",
        input_tokens=100,
        ts=TS,
    )


def test_finalizes_full_shards_as_buffer_fills(tmp_path):
    writer = ShardWriter(tmp_path, shard_size=2)
    cells = [_cell(f"doc-{i}") for i in range(5)]
    finalized = writer.add(cells)

    assert len(finalized) == 2  # two full shards of 2; 1 left buffered
    assert writer.buffered_count == 1
    assert writer.shard_paths() == finalized


def test_flush_finalizes_remaining_buffer(tmp_path):
    writer = ShardWriter(tmp_path, shard_size=2)
    writer.add([_cell(f"doc-{i}") for i in range(5)])
    last = writer.flush()

    assert last is not None
    assert writer.buffered_count == 0
    assert len(writer.shard_paths()) == 3


def test_flush_on_empty_buffer_is_a_noop(tmp_path):
    writer = ShardWriter(tmp_path, shard_size=2)
    assert writer.flush() is None
    assert writer.shard_paths() == []


def test_finalized_shard_is_readable_and_correct(tmp_path):
    writer = ShardWriter(tmp_path, shard_size=10)
    writer.add([_cell("doc-a"), _cell("doc-b")])
    [path] = [writer.flush()]

    table = pq.read_table(path)
    assert table.num_rows == 2
    doc_ids = {row["doc_id"] for row in table.to_pylist()}
    assert doc_ids == {"doc-a", "doc-b"}


def test_resuming_continues_shard_numbering_without_overwrite(tmp_path):
    writer1 = ShardWriter(tmp_path, shard_size=2)
    writer1.add([_cell("doc-a"), _cell("doc-b")])  # -> 000000.parquet
    assert writer1.shard_paths() == [tmp_path / "000000.parquet"]

    # Simulate a fresh process resuming into the same shards directory.
    writer2 = ShardWriter(tmp_path, shard_size=2)
    writer2.add([_cell("doc-c"), _cell("doc-d")])  # must become 000001, not overwrite 000000

    paths = writer2.shard_paths()
    assert paths == [tmp_path / "000000.parquet", tmp_path / "000001.parquet"]
    # original shard untouched
    original = pq.read_table(tmp_path / "000000.parquet").to_pylist()
    assert {r["doc_id"] for r in original} == {"doc-a", "doc-b"}


def test_crash_during_finalize_leaves_no_partial_parquet_visible(tmp_path, monkeypatch):
    """A killed run leaves no partially visible shard (T2.3 done-when).
    Simulate a crash mid-write: pq.write_table raises after touching the .tmp
    file. shard_paths() must never see it."""
    import jev_census.shard_writer as shard_writer_module

    def crashing_write_table(table, path):
        Path(path).write_bytes(b"not a real parquet file, truncated mid-write")
        raise OSError("simulated crash")

    monkeypatch.setattr(shard_writer_module.pq, "write_table", crashing_write_table)

    writer = ShardWriter(tmp_path, shard_size=1)
    with pytest.raises(OSError, match="simulated crash"):
        writer.add([_cell("doc-a")])

    assert writer.shard_paths() == []  # the .tmp file must not be glob-visible
    assert (tmp_path / "000000.parquet.tmp").exists()  # it's still on disk, just not "finalized"
    assert not (tmp_path / "000000.parquet").exists()


def test_next_writer_after_crash_reuses_the_failed_index(tmp_path, monkeypatch):
    """After a crash leaves a stray .tmp, the next writer must not skip that
    index — nothing was actually finalized there, so it must be retried, not left as a gap."""
    import jev_census.shard_writer as shard_writer_module

    def crashing_write_table(table, path):
        Path(path).write_bytes(b"garbage")
        raise OSError("simulated crash")

    monkeypatch.setattr(shard_writer_module.pq, "write_table", crashing_write_table)
    writer = ShardWriter(tmp_path, shard_size=1)
    with pytest.raises(OSError):
        writer.add([_cell("doc-a")])
    monkeypatch.undo()

    resumed = ShardWriter(tmp_path, shard_size=1)
    resumed.add([_cell("doc-a")])
    assert resumed.shard_paths() == [tmp_path / "000000.parquet"]
