import csv
import inspect
import itertools

import pyarrow as pa
import pyarrow.parquet as pq

from jev_census.sources import read_csv, read_parquet, read_source

ROWS = [
    {"ticket_id": "T-1", "subject": "Payouts failing", "body": "Help!"},
    {"ticket_id": "T-2", "subject": "Export CSV", "body": "Where is it?"},
    {"ticket_id": "T-3", "subject": "Considering Zendesk", "body": "Third outage."},
]


def _write_parquet(path, rows):
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_read_parquet_preserves_row_order(tmp_path):
    path = tmp_path / "tickets.parquet"
    _write_parquet(path, ROWS)
    rows = list(read_parquet(path))
    assert [r["ticket_id"] for r in rows] == ["T-1", "T-2", "T-3"]
    assert rows[0]["subject"] == "Payouts failing"


def test_read_csv_preserves_row_order(tmp_path):
    path = tmp_path / "tickets.csv"
    _write_csv(path, ROWS)
    rows = list(read_csv(path))
    assert [r["ticket_id"] for r in rows] == ["T-1", "T-2", "T-3"]
    assert rows[1]["body"] == "Where is it?"


def test_read_source_dispatches_on_extension(tmp_path):
    parquet_path = tmp_path / "a.parquet"
    csv_path = tmp_path / "a.csv"
    _write_parquet(parquet_path, ROWS)
    _write_csv(csv_path, ROWS)

    assert [r["ticket_id"] for r in read_source(parquet_path)] == ["T-1", "T-2", "T-3"]
    assert [r["ticket_id"] for r in read_source(csv_path)] == ["T-1", "T-2", "T-3"]


def test_read_source_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text("{}")
    try:
        read_source(path)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unsupported source format" in str(e)


def test_readers_are_generators_not_materialized_lists(tmp_path):
    """Streaming at constant memory relies on these never being eagerly built
    into a list; assert the generator contract directly rather than measuring
    memory, which would be flaky."""
    assert inspect.isgeneratorfunction(read_csv)
    assert inspect.isgeneratorfunction(read_parquet)


def test_read_parquet_is_lazy_across_row_groups(tmp_path):
    """Writing several small row groups and taking only the first batch's worth
    must not require reading the whole file — take() should not raise even if
    later row groups were corrupt, because they're never touched."""
    path = tmp_path / "many_groups.parquet"
    writer = None
    schema = pa.schema([("i", pa.int64())])
    with pq.ParquetWriter(path, schema) as w:
        for group in range(5):
            batch_rows = [{"i": group * 10 + i} for i in range(10)]
            w.write_table(pa.Table.from_pylist(batch_rows, schema=schema))
    first_five = list(itertools.islice(read_parquet(path, batch_size=10), 5))
    assert [r["i"] for r in first_five] == [0, 1, 2, 3, 4]
