from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from jev_census.cell import Cell
from jev_census.shard_writer import ShardWriter

TS = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


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


def test_add_call_is_atomic_never_split_across_shards(tmp_path):
    """A single add() call (one document's cells, in real usage) always lands
    in one shard entirely, even if it alone exceeds shard_size."""
    writer = ShardWriter(tmp_path, shard_size=2)
    finalized = writer.add([_cell("doc-a"), _cell("doc-b"), _cell("doc-c")])  # 3 cells, threshold 2

    assert len(finalized) == 1
    assert writer.buffered_count == 0
    table = pq.read_table(finalized[0])
    assert table.num_rows == 3  # all 3 cells together, not split


def test_finalizes_once_threshold_crossed_across_several_calls(tmp_path):
    """Several small add() calls (one per document) accumulate in the buffer
    until the threshold is crossed, then finalize as one shard together."""
    writer = ShardWriter(tmp_path, shard_size=3)
    assert writer.add([_cell("doc-a")]) == []
    assert writer.add([_cell("doc-b")]) == []
    finalized = writer.add([_cell("doc-c")])  # buffer now at 3 -> finalizes

    assert len(finalized) == 1
    assert writer.buffered_count == 0
    assert pq.read_table(finalized[0]).num_rows == 3


def test_flush_finalizes_remaining_buffer(tmp_path):
    writer = ShardWriter(tmp_path, shard_size=10)
    writer.add([_cell(f"doc-{i}") for i in range(5)])
    last = writer.flush()

    assert last is not None
    assert writer.buffered_count == 0
    assert len(writer.shard_paths()) == 1


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
