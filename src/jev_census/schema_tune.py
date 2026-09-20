"""`census schema-tune` (T6.7, `05-P6-QUALITY.md`): the accuracy-vs-verbosity
curve. `02-BUILD-PLAN.md` calls it "the most publishable artifact in the
repository — nobody in the Jev ecosystem has measured it," and it closes
open question **H** in `00-JEV-API.md`.

**Variants are authored by hand, not generated** (`01-DESIGN.md`: "question
wording is the author's accountability"; the anti-goals forbid LLM-generated
question sets). This module prices variants against a reference set; it
never writes them.

Implementation note: rather than a parallel call-execution path, a
schema-tune run is literally the Scheduler run twice — once for the
reference question set, once per variant — against the same sampled
sub-corpus and the same `--census-dir` cache, so an unchanged variant's
matching questions hit the cache for free and only genuinely different
questions cost anything.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .normalizer import normalize
from .planner import question_schema_tokens
from .question_set import Question, QuestionSet, load_question_set
from .sampling import modal_level
from .sources import read_source

# 01-DESIGN.md's floors table. `None` means "measured and reported, no gate".
FLOORS: dict[str, dict[str, float | None]] = {
    "noul": {"agreement_floor": 0.97, "drift_floor": 0.05},
    "choice": {"agreement_floor": 0.95, "drift_floor": None},
    "score": {"agreement_floor": 0.92, "drift_floor": 0.15},
}

CAVEAT = (
    "Agreement is not accuracy — it only means the terse variant answers like the verbose "
    "one, which matters only if the verbose one was validated."
)


@dataclass(frozen=True)
class Variant:
    name: str
    question_set: QuestionSet


def load_variants(variants_path: str | Path) -> tuple[Path, list[Variant]]:
    """Parses a `variants.yaml` (05-P6-QUALITY.md's format): a `reference`
    question set plus named `variants`, each a per-question partial
    override merged onto a copy of the reference. `criteria: null` in an
    override deliberately clears that field (e.g. dropping criteria
    entirely for a noul question) rather than being ignored as absent.
    Returns the reference path and the list of variants."""
    raw = yaml.safe_load(Path(variants_path).read_text(encoding="utf-8"))
    reference_path = Path(variants_path).parent / raw["reference"]
    reference = load_question_set(reference_path)

    variants = []
    for entry in raw["variants"]:
        overrides: dict[str, dict[str, Any]] = entry.get("overrides", {})
        new_questions = []
        for q in reference.questions:
            if q.id in overrides:
                merged = q.model_dump()
                merged.update(overrides[q.id])
                new_questions.append(Question(**merged))
            else:
                new_questions.append(q)
        variant_qs = reference.model_copy(update={"questions": new_questions})
        variants.append(Variant(name=entry["name"], question_set=variant_qs))
    return reference_path, variants


def write_question_set_yaml(question_set: QuestionSet, path: str | Path) -> None:
    """Serializes an in-memory `QuestionSet` (a variant, built by merging
    overrides onto the reference — `load_variants` never touches disk for
    these) back to a YAML file, so it can be run through the normal
    `SchedulerConfig(questions_path=...)` path like any other question set."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(question_set.model_dump(exclude_none=True), sort_keys=False), encoding="utf-8")


def reservoir_sample_corpus(input_path: str | Path, out_path: str | Path, *, n: int, seed: int = 0) -> int:
    """Writes an `n`-document reservoir sample of `input_path` to `out_path`
    as its own Parquet file — the fixed sub-corpus reference and every
    variant are compared against, so the comparison is apples to apples
    regardless of corpus ordering. Returns the number of rows written."""
    rng = random.Random(seed)
    sample: list[dict] = []
    for i, doc in enumerate(normalize(read_source(input_path))):
        row = dict(doc.fields)
        row["_doc_id"] = doc.id
        if i < n:
            sample.append(row)
        else:
            j = rng.randint(0, i)
            if j < n:
                sample[j] = row
    if not sample:
        return 0
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(sample), out_path)
    return len(sample)


def load_cells_for_question(cells_path: Path, question_id: str) -> list[dict]:
    table = pq.read_table(cells_path)
    rows = [r for r in table.to_pylist() if r["question_id"] == question_id]
    for r in rows:
        if r["probabilities"] is not None:
            r["probabilities"] = dict(r["probabilities"])
    return rows


def compare_noul(reference_cells: list[dict], variant_cells: list[dict]) -> tuple[float, float]:
    """(thresholded agreement, mean absolute probability drift)."""
    ref_by_doc = {c["doc_id"]: c for c in reference_cells}
    agreements, drifts = [], []
    for vc in variant_cells:
        rc = ref_by_doc.get(vc["doc_id"])
        if rc is None:
            continue
        agreements.append((rc["noul"] > 0.5) == (vc["noul"] > 0.5))
        drifts.append(abs(rc["noul"] - vc["noul"]))
    if not agreements:
        return 0.0, 0.0
    return sum(agreements) / len(agreements), sum(drifts) / len(drifts)


def compare_choice(reference_cells: list[dict], variant_cells: list[dict]) -> tuple[float, float]:
    """(argmax agreement, mean drift in the reference's chosen option's
    probability)."""
    ref_by_doc = {c["doc_id"]: c for c in reference_cells}
    agreements, drifts = [], []
    for vc in variant_cells:
        rc = ref_by_doc.get(vc["doc_id"])
        if rc is None:
            continue
        agreements.append(rc["choice"] == vc["choice"])
        chosen = rc["choice"]
        ref_p = rc["probabilities"].get(chosen, 0.0)
        var_p = vc["probabilities"].get(chosen, 0.0)
        drifts.append(abs(ref_p - var_p))
    if not agreements:
        return 0.0, 0.0
    return sum(agreements) / len(agreements), sum(drifts) / len(drifts)


def compare_score(reference_cells: list[dict], variant_cells: list[dict]) -> tuple[float, float]:
    """(exact-level agreement, mean shift in the weighted score)."""
    ref_by_doc = {c["doc_id"]: c for c in reference_cells}
    agreements, shifts = [], []
    for vc in variant_cells:
        rc = ref_by_doc.get(vc["doc_id"])
        if rc is None:
            continue
        agreements.append(modal_level(rc) == modal_level(vc))
        shifts.append(abs(rc["score"] - vc["score"]))
    if not agreements:
        return 0.0, 0.0
    return sum(agreements) / len(agreements), sum(shifts) / len(shifts)


_COMPARE_FNS = {"noul": compare_noul, "choice": compare_choice, "score": compare_score}


@dataclass(frozen=True)
class VariantResult:
    variant_name: str
    question_id: str
    type: str
    agreement: float
    drift: float
    avg_schema_tokens_reference: int
    avg_schema_tokens_variant: int
    passed: bool


def score_variant(
    reference_cells_path: Path,
    variant_cells_path: Path,
    variant_name: str,
    reference_question_set: QuestionSet,
    variant_question_set: QuestionSet,
) -> list[VariantResult]:
    reference_by_id = {q.id: q for q in reference_question_set.questions}
    variant_by_id = {q.id: q for q in variant_question_set.questions}

    results = []
    for question_id, variant_q in variant_by_id.items():
        reference_q = reference_by_id.get(question_id)
        if reference_q is None:
            continue
        qtype = variant_q.type
        reference_cells = load_cells_for_question(reference_cells_path, question_id)
        variant_cells = load_cells_for_question(variant_cells_path, question_id)
        agreement, drift = _COMPARE_FNS[qtype](reference_cells, variant_cells)

        floors = FLOORS[qtype]
        passed = agreement >= floors["agreement_floor"]
        if floors["drift_floor"] is not None:
            passed = passed and drift <= floors["drift_floor"]

        results.append(
            VariantResult(
                variant_name=variant_name,
                question_id=question_id,
                type=qtype,
                agreement=agreement,
                drift=drift,
                avg_schema_tokens_reference=question_schema_tokens(reference_q),
                avg_schema_tokens_variant=question_schema_tokens(variant_q),
                passed=passed,
            )
        )
    return results


def format_schema_tune_report(results: list[VariantResult]) -> str:
    headers = ["variant", "question", "type", "agreement", "drift", "schema tokens (ref -> variant)", "verdict"]
    rows = []
    for r in results:
        rows.append(
            [
                r.variant_name,
                r.question_id,
                r.type,
                f"{r.agreement:.3f}",
                f"{r.drift:.3f}",
                f"{r.avg_schema_tokens_reference} -> {r.avg_schema_tokens_variant}",
                "PASS" if r.passed else "FAIL",
            ]
        )
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    table = "\n".join([fmt(headers), *(fmt(row) for row in rows)])
    return f"{table}\n\n{CAVEAT}\n"


def write_schema_tune_csv(path: str | Path, results: list[VariantResult]) -> None:
    """The accuracy-vs-verbosity curve's source data: tokens-per-document
    against agreement, per variant per question."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "variant", "question_id", "type", "agreement", "drift",
                "schema_tokens_reference", "schema_tokens_variant", "passed",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "variant": r.variant_name,
                    "question_id": r.question_id,
                    "type": r.type,
                    "agreement": r.agreement,
                    "drift": r.drift,
                    "schema_tokens_reference": r.avg_schema_tokens_reference,
                    "schema_tokens_variant": r.avg_schema_tokens_variant,
                    "passed": r.passed,
                }
            )
