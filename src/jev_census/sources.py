"""Source readers (T1.4): Parquet and CSV, streamed row by row at constant
memory. Ordering is whatever the file's own row order is — stable across reads
of the same file, which is what the normalizer's id-stability guarantee relies on.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def read_parquet(path: str | Path, batch_size: int = 1024) -> Iterator[dict[str, Any]]:
    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=batch_size):
        yield from batch.to_pylist()


def read_csv(path: str | Path) -> Iterator[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        yield from reader


def read_source(path: str | Path) -> Iterator[dict[str, Any]]:
    """Dispatch on file extension. `.parquet` or `.csv` only for P1 — JSONL and
    DuckDB sources are on the README's roadmap but not yet implemented."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return read_parquet(path)
    if suffix == ".csv":
        return read_csv(path)
    raise ValueError(f"unsupported source format '{suffix}' (expected .parquet or .csv): {path}")


def count_rows(path: str | Path) -> int:
    """Total document count, for `census estimate` (T3.2) to scale a sample's
    average up to the whole corpus. Parquet metadata carries the row count
    without reading any data; CSV needs one lightweight pass over the file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pq.ParquetFile(path).metadata.num_rows
    if suffix == ".csv":
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader, None)  # header
            return sum(1 for _ in reader)
    raise ValueError(f"unsupported source format '{suffix}' (expected .parquet or .csv): {path}")
