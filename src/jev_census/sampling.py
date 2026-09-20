"""Stratified sampling for `census label` (T6.1, `05-P6-QUALITY.md`).

Pure and network-free: stratifies a question's cells, samples equally per
stratum (never proportionally — the point is to cover the decision
boundary, where accuracy is actually in question, not to mirror the
corpus), and computes the reweighting every downstream statistic in
`scoring.py` depends on:

    stratum_weight = (cells in this stratum in the corpus) / (cells sampled from this stratum)

Because allocation is equal rather than proportional, raw accuracy over the
sample is NOT corpus accuracy. Skipping this weight produces a report that
looks right, passes naive tests, and is wrong — every statistic downstream
must use it.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .normalizer import normalize
from .sources import read_source

DEFAULT_STRATA = 5

LABEL_BASE_FIELDS = [
    "doc_id", "question_id", "type", "stratum", "stratum_weight",
    "model_noul", "model_choice", "model_score", "model_modal_level", "confidence",
]
LABEL_TAIL_FIELDS = ["gold_answer", "notes"]


def _noul_stratum(cell: dict, strata: int) -> str:
    value = cell["noul"]
    bin_width = 1.0 / strata
    index = min(int(value / bin_width), strata - 1)
    lo, hi = index * bin_width, (index + 1) * bin_width
    return f"{lo:.1f}-{hi:.1f}"


def _confidence_bin(confidence: float, strata: int) -> str:
    bin_width = 1.0 / strata
    index = min(int(confidence / bin_width), strata - 1)
    lo, hi = index * bin_width, (index + 1) * bin_width
    return f"{lo:.1f}-{hi:.1f}"


def _choice_stratum(cell: dict, strata: int) -> str:
    return f"{cell['choice']}:{_confidence_bin(cell['confidence'], strata)}"


def modal_level(cell: dict) -> str:
    """The level index (as a string, matching probabilities/legend's own
    keying) the model put the most probability mass on -- the score
    equivalent of a noul's value or a choice's argmax."""
    probabilities: dict[str, float] = cell["probabilities"]
    return max(probabilities, key=probabilities.get)


def _score_stratum(cell: dict, strata: int) -> str:
    return f"{modal_level(cell)}:{_confidence_bin(cell['confidence'], strata)}"


_STRATUM_FNS = {"noul": _noul_stratum, "choice": _choice_stratum, "score": _score_stratum}


def assign_stratum(cell: dict, strata: int = DEFAULT_STRATA) -> str:
    """The stratum key for one cell, per its type (05-P6-QUALITY.md's table):
    noul on its own probability, choice on (chosen option, confidence bin),
    score on (modal level, confidence bin)."""
    return _STRATUM_FNS[cell["type"]](cell, strata)


def _allocate(stratum_sizes: dict[str, int], n: int) -> dict[str, int]:
    """How many cells to sample from each stratum. Starts as-equal-as-possible
    across strata; a stratum with fewer cells than its share contributes all
    of it, and the shortfall is redistributed across strata that still have
    room, repeating until either `n` is satisfied or no stratum has any
    capacity left (05-P6-QUALITY.md: "the remainder is redistributed")."""
    allocation = {s: 0 for s in stratum_sizes}
    remaining_strata = {s for s, size in stratum_sizes.items() if size > 0}
    remaining_n = n

    while remaining_n > 0 and remaining_strata:
        share, extra = divmod(remaining_n, len(remaining_strata))
        progressed = False
        for i, stratum in enumerate(sorted(remaining_strata)):
            want = share + (1 if i < extra else 0)
            capacity = stratum_sizes[stratum] - allocation[stratum]
            take = min(want, capacity)
            if take > 0:
                allocation[stratum] += take
                remaining_n -= take
                progressed = True
            if allocation[stratum] >= stratum_sizes[stratum]:
                remaining_strata.discard(stratum)
        if not progressed:
            break  # no stratum has room for more; the corpus is simply smaller than n
    return allocation


@dataclass(frozen=True)
class SampledCell:
    cell: dict[str, Any]
    stratum: str
    stratum_weight: float


def stratified_sample(
    cells: list[dict], *, n: int, strata: int = DEFAULT_STRATA, seed: int = 0
) -> list[SampledCell]:
    """Equal-per-stratum sample of `cells` (already filtered to one question
    id, already excluding `chunk_count > 1` unless the caller wants those
    included). Deterministic given `seed`, so a labelling effort is
    reproducible and re-runnable."""
    grouped: dict[str, list[dict]] = {}
    for cell in cells:
        grouped.setdefault(assign_stratum(cell, strata), []).append(cell)

    stratum_sizes = {stratum: len(pool) for stratum, pool in grouped.items()}
    allocation = _allocate(stratum_sizes, n)

    rng = random.Random(seed)
    sampled: list[SampledCell] = []
    for stratum, take in allocation.items():
        if take == 0:
            continue
        pool = grouped[stratum]
        weight = len(pool) / take
        for cell in rng.sample(pool, take):
            sampled.append(SampledCell(cell=cell, stratum=stratum, stratum_weight=weight))
    return sampled


# --- run/corpus I/O ---------------------------------------------------------


def read_manifest(run_dir: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no run found at {run_dir} (missing manifest.json)")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def read_run_cells(run_dir: Path, question_id: str, *, include_chunked: bool = False) -> list[dict]:
    """Every finalized cell for one question, from the run's durable shards
    directly (`.census/runs/<run_id>/shards/`) — the same source of truth
    resume correctness relies on, not a `--out` path that may have moved.
    `chunk_count > 1` cells are excluded by default (01-DESIGN.md: an
    aggregated cell is our arithmetic, not a model answer)."""
    shards_dir = run_dir / "shards"
    cells: list[dict] = []
    for shard_path in sorted(shards_dir.glob("*.parquet")):
        table = pq.read_table(shard_path)
        for row in table.to_pylist():
            if row["question_id"] != question_id:
                continue
            # pyarrow's map_ column comes back as a list of (key, value)
            # pairs, not a dict -- normalize it once here rather than at
            # every call site that reads `probabilities`.
            if row["probabilities"] is not None:
                row["probabilities"] = dict(row["probabilities"])
            if row["legend"] is not None:
                row["legend"] = dict(row["legend"])
            if not include_chunked and row["chunk_count"] > 1:
                continue
            cells.append(row)
    return cells


def recover_doc_fields(manifest: dict, doc_ids: set[str]) -> dict[str, dict]:
    """Re-streams the original source corpus (its path and id_field are
    recorded in the manifest) to recover the projection fields' raw values
    for a set of doc_ids — cells.parquet never stores source text, only
    answers, so a labeller who needs to see the actual ticket has to go back
    to the corpus. Only keeps rows whose id is in `doc_ids`, so this stays
    one linear pass regardless of corpus size."""
    input_path = manifest["input_path"]
    id_field = manifest.get("id_field")
    found: dict[str, dict] = {}
    for doc in normalize(read_source(input_path), id_field=id_field):
        if doc.id in doc_ids:
            found[doc.id] = doc.fields
            if len(found) == len(doc_ids):
                break
    return found


def projection_fields_for_question(manifest: dict, cells: list[dict]) -> list[str]:
    """Which corpus columns a question's cells were projected from, from the
    manifest's `{projection_id: [fields]}` map (P5) keyed by the
    `projection_id` already stamped on every cell."""
    if not cells:
        return []
    projection_id = cells[0]["projection_id"]
    return list(manifest.get("projection_groups", {}).get(projection_id, []))


def label_csv_fieldnames(projection_fields: list[str]) -> list[str]:
    return LABEL_BASE_FIELDS + list(projection_fields) + LABEL_TAIL_FIELDS


def write_label_csv(
    path: str | Path,
    *,
    question_id: str,
    question_type: str,
    sampled: list[SampledCell],
    doc_fields_by_id: dict[str, dict],
    projection_fields: list[str],
    overwrite: bool = False,
) -> int:
    """Writes the label CSV. Refuses to clobber an existing file unless
    `overwrite=True` — losing a half-day of human labelling to a re-run is
    the single most expensive bug available in this phase
    (05-P6-QUALITY.md)."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{path} already exists — refusing to overwrite a label file that may already "
            "hold human answers. Pass overwrite=True (CLI: --overwrite) if you're sure."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = label_csv_fieldnames(projection_fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sc in sampled:
            cell = sc.cell
            row = {
                "doc_id": cell["doc_id"],
                "question_id": question_id,
                "type": question_type,
                "stratum": sc.stratum,
                "stratum_weight": sc.stratum_weight,
                "model_noul": cell.get("noul"),
                "model_choice": cell.get("choice"),
                "model_score": cell.get("score"),
                "model_modal_level": modal_level(cell) if question_type == "score" else "",
                "confidence": cell.get("confidence"),
                "gold_answer": "",
                "notes": "",
            }
            doc_fields = doc_fields_by_id.get(cell["doc_id"], {})
            for field in projection_fields:
                row[field] = doc_fields.get(field, "")
            writer.writerow(row)
    return len(sampled)


def read_label_csv(path: str | Path) -> list[dict]:
    """Reads a (possibly human-filled) label CSV back. Numeric columns are
    parsed; `gold_answer`/`notes`/projection fields stay strings. Empty
    numeric cells (a question type that doesn't apply, e.g. `model_choice`
    on a noul row) become `None`."""

    def _maybe_float(value: str) -> float | None:
        return float(value) if value not in ("", None) else None

    rows = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row = dict(raw)
            row["stratum_weight"] = _maybe_float(row.get("stratum_weight"))
            row["model_noul"] = _maybe_float(row.get("model_noul"))
            row["model_score"] = _maybe_float(row.get("model_score"))
            row["confidence"] = _maybe_float(row.get("confidence"))
            rows.append(row)
    return rows


def label_rows_from_cells(
    gold_rows: list[dict], cells_by_doc_id: dict[str, dict], *, default_weight: float = 1.0
) -> list[dict]:
    """Rebuilds label-CSV-shaped scoring rows from a set of
    `(doc_id, gold_answer[, stratum_weight])` rows and a run's cells, keyed
    by doc_id — used wherever existing gold labels need to score a
    DIFFERENT run's (or a fresh run's) answers for the same documents.
    `ablation.py`'s T6.8 comparison uses this to score two runs against one
    gold set; `census demo`'s bundled gold set (doc_id + gold_answer only,
    no `stratum_weight`) uses it to score whatever the current demo run
    just produced, so the bundled labels never go stale relative to model
    changes. `default_weight` fills in for gold rows that carry no
    `stratum_weight` of their own."""
    rows = []
    for gold_row in gold_rows:
        cell = cells_by_doc_id.get(gold_row["doc_id"])
        if cell is None:
            continue
        rows.append(
            {
                "type": cell["type"],
                "model_noul": cell.get("noul"),
                "model_choice": cell.get("choice"),
                "model_score": cell.get("score"),
                "model_modal_level": modal_level(cell) if cell["type"] == "score" else "",
                "confidence": cell.get("confidence"),
                "stratum_weight": gold_row.get("stratum_weight", default_weight),
                "gold_answer": gold_row["gold_answer"],
            }
        )
    return rows
