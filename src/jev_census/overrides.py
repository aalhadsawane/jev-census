"""Review queue export and the override layer (T6.6, `05-P6-QUALITY.md`).

**The one rule that matters**: overrides are NEVER merged into
`cells.parquet`. They live in a separate file (`results/overrides.parquet`),
joined at read time — the DuckDB `COALESCE` recipe belongs in T7.4, not
here. A model output column must stay a model output column or the
validation numbers already attached to it become fiction (`01-DESIGN.md`).
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .report import read_validation_json
from .sampling import modal_level, read_manifest, read_run_cells, recover_doc_fields

# Deliberately not the label CSV's shape: `stratum`/`stratum_weight` are a
# sampling artifact meaningless outside a validation run, and `gold_answer`/
# `notes` are the wrong vocabulary for "a human looked at this uncertain
# production answer and decided what it should be" -- that's `human_answer`.
REVIEW_BASE_FIELDS = [
    "doc_id", "question_id", "type",
    "model_noul", "model_choice", "model_score", "model_modal_level", "confidence",
]

def _cell_threshold_value(cell: dict) -> float:
    """Same scale as `scoring.threshold_value` (01-DESIGN.md D4: noul on
    distance from the coin flip, choice/score on model confidence) — but
    reading a raw cell's own field names (`noul`, `confidence`) rather than
    a label CSV row's (`model_noul`). `read_run_cells` returns raw cells,
    not label rows, so the two functions cannot be the same one without
    silently papering over a real shape mismatch."""
    if cell["type"] == "noul":
        return abs(cell["noul"] - 0.5)
    return cell["confidence"]


OVERRIDES_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string()),
        pa.field("question_id", pa.string()),
        pa.field("human_answer", pa.string()),
        pa.field("reviewer", pa.string()),
        pa.field("ts", pa.timestamp("us", tz="UTC")),
        pa.field("source_run_id", pa.string()),
    ]
)


def export_review_queue(run_dir: str | Path, validation_dir: str | Path, out_path: str | Path) -> int:
    """Every cell below its question's recommended threshold — on that
    question's own scale (noul: distance from 0.5; choice/score:
    confidence) — across every question with a stored validation result in
    `validation_dir` (written by `census validate`, T6.5). A question with
    no recommended threshold (the search never qualified — the
    `frustration` case) contributes nothing: there is no principled cutoff
    to review against. Returns the number of rows written."""
    run_dir = Path(run_dir)
    validation_dir = Path(validation_dir)
    manifest = read_manifest(run_dir)

    per_question: list[tuple[str, str, list[dict], list[str]]] = []
    all_projection_fields: list[str] = []
    doc_ids_needed: set[str] = set()

    for json_path in sorted(validation_dir.glob("*.json")):
        question_id = json_path.stem
        score = read_validation_json(json_path)
        threshold = score.threshold.threshold
        if threshold is None:
            continue  # no principled cutoff for this question -- nothing to export
        cells = read_run_cells(run_dir, question_id)
        below = [c for c in cells if _cell_threshold_value(c) < threshold]
        if not below:
            continue
        projection_id = cells[0]["projection_id"] if cells else None
        projection_fields = list(manifest.get("projection_groups", {}).get(projection_id, []))
        for field in projection_fields:
            if field not in all_projection_fields:
                all_projection_fields.append(field)
        doc_ids_needed.update(c["doc_id"] for c in below)
        per_question.append((question_id, score.type, below, projection_fields))

    doc_fields_by_id = recover_doc_fields(manifest, doc_ids_needed) if doc_ids_needed else {}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = REVIEW_BASE_FIELDS + all_projection_fields + ["human_answer"]

    count = 0
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for question_id, qtype, below, projection_fields in per_question:
            for cell in below:
                row = {
                    "doc_id": cell["doc_id"],
                    "question_id": question_id,
                    "type": qtype,
                    "model_noul": cell.get("noul"),
                    "model_choice": cell.get("choice"),
                    "model_score": cell.get("score"),
                    "model_modal_level": modal_level(cell) if qtype == "score" else "",
                    "confidence": cell.get("confidence"),
                    "human_answer": "",
                }
                doc_fields = doc_fields_by_id.get(cell["doc_id"], {})
                for field in all_projection_fields:
                    row[field] = doc_fields.get(field, "") if field in projection_fields else ""
                writer.writerow(row)
                count += 1
    return count


def read_review_queue(path: str | Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def import_overrides(
    path: str | Path, rows: list[dict], *, reviewer: str, source_run_id: str
) -> int:
    """Appends every row with a non-blank `human_answer` (from a filled-in
    `review_queue.csv`) to `results/overrides.parquet`. Never touches
    `cells.parquet` — see the module docstring. Existing overrides are
    preserved; this only ever appends. Returns the number of rows written
    (rows with an empty `human_answer` — still unreviewed — are skipped)."""
    path = Path(path)
    ts = datetime.now(UTC)
    new_rows = []
    for row in rows:
        human_answer = (row.get("human_answer") or "").strip()
        if not human_answer:
            continue
        new_rows.append(
            {
                "doc_id": row["doc_id"],
                "question_id": row["question_id"],
                "human_answer": human_answer,
                "reviewer": reviewer,
                "ts": ts,
                "source_run_id": source_run_id,
            }
        )
    if not new_rows:
        return 0

    new_table = pa.Table.from_pylist(new_rows, schema=OVERRIDES_SCHEMA)
    if path.exists():
        combined = pa.concat_tables([pq.read_table(path), new_table])
    else:
        combined = new_table
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(combined, path)
    return len(new_rows)
