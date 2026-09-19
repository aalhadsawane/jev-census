"""Parquet writer (T1.8): `Cell`s -> `cells.parquet` with the full provenance
schema. This is P1's minimal single-shot writer; the append-only shard writer
with atomic finalisation (01-DESIGN.md D5) is T2.3 in Phase P2 — do not treat
this module as durable against interruption yet.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .cell import Cell

CELLS_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string()),
        pa.field("question_id", pa.string()),
        pa.field("type", pa.string()),
        pa.field("noul", pa.float64()),
        pa.field("choice", pa.string()),
        pa.field("score", pa.float64()),
        pa.field("probabilities", pa.map_(pa.string(), pa.float64())),
        pa.field("legend", pa.map_(pa.string(), pa.string())),
        pa.field("confidence", pa.float64()),
        pa.field("confidence_source", pa.string()),
        pa.field("gate", pa.string()),
        pa.field("chunk_count", pa.int32()),
        pa.field("projection_id", pa.string()),
        pa.field("call_id", pa.string()),
        pa.field("run_id", pa.string()),
        pa.field("model", pa.string()),
        pa.field("questionset_hash", pa.string()),
        pa.field("question_body_hash", pa.string()),
        pa.field("input_tokens", pa.int64()),
        pa.field("ts", pa.timestamp("us", tz="UTC")),
    ]
)


def _row(cell: Cell) -> dict:
    row = {field.name: getattr(cell, field.name) for field in CELLS_SCHEMA}
    # pyarrow's map_ type wants a list of (key, value) pairs, not a bare dict.
    row["probabilities"] = list(cell.probabilities.items()) if cell.probabilities is not None else None
    row["legend"] = list(cell.legend.items()) if cell.legend is not None else None
    return row


def cells_to_table(cells: list[Cell]) -> pa.Table:
    rows = [_row(cell) for cell in cells]
    return pa.Table.from_pylist(rows, schema=CELLS_SCHEMA)


def write_cells_parquet(cells: list[Cell], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = cells_to_table(cells)
    pq.write_table(table, path)
