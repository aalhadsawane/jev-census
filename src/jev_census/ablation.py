"""Projection ablation (T6.8, `05-P6-QUALITY.md`): does a projected state
beat a full-record state on the same question? `00-JEV-API.md` assigns
open question **I** to this phase; `02-BUILD-PLAN.md`'s P6 task table had
no task for it — this is that gap closed, not new scope.

Unlike `schema_tune.py` (which compares two runs' answers against each
other — *agreement*), this module compares two runs' answers against the
SAME gold-labelled sample from T6.1/T6.2 — *accuracy*. That's why it
reuses an existing gold CSV rather than sampling its own: the point is
"which condition was actually right," not "did they answer alike."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .question_set import Question, QuestionSet
from .sampling import label_rows_from_cells, read_label_csv
from .scoring import QuestionScore, score_question


def full_record_variant(question_set: QuestionSet, all_fields: list[str]) -> QuestionSet:
    """Every question's projection widened to every field the corpus has —
    the "full record" condition this module compares against each
    question's normal, narrower projection. Nothing else about the
    question (instructions, criteria, type) changes."""
    new_questions = [
        Question(**{**q.model_dump(), "projection": list(all_fields)}) for q in question_set.questions
    ]
    return question_set.model_copy(update={"questions": new_questions})


@dataclass(frozen=True)
class AblationResult:
    question_id: str
    type: str
    projected: QuestionScore
    full_record: QuestionScore


def run_ablation_comparison(
    gold_csv_paths: dict[str, Path],
    projected_cells_by_question: dict[str, list[dict]],
    full_record_cells_by_question: dict[str, list[dict]],
) -> list[AblationResult]:
    """For each question with a gold CSV, scores both the projected
    condition's and the full-record condition's cells against the SAME
    gold labels. Cells are passed in pre-loaded (the projected condition's
    come from an already-completed run's durable shards via
    `sampling.read_run_cells`; the full-record condition's from a fresh
    comparison run) rather than a file path, since the projected run's
    cells don't live at any single well-known path. Returns one
    `AblationResult` per question — never a single number across
    questions, per the same structural rule `scoring.py` enforces."""
    results = []
    for question_id, gold_path in gold_csv_paths.items():
        gold_rows = read_label_csv(gold_path)
        if not gold_rows:
            continue
        question_type = gold_rows[0]["type"]

        projected_cells = {c["doc_id"]: c for c in projected_cells_by_question.get(question_id, [])}
        full_record_cells = {c["doc_id"]: c for c in full_record_cells_by_question.get(question_id, [])}

        projected_rows = label_rows_from_cells(gold_rows, projected_cells)
        full_record_rows = label_rows_from_cells(gold_rows, full_record_cells)

        results.append(
            AblationResult(
                question_id=question_id,
                type=question_type,
                projected=score_question(projected_rows, question_id=question_id, question_type=question_type),
                full_record=score_question(
                    full_record_rows, question_id=question_id, question_type=question_type
                ),
            )
        )
    return results


def format_ablation_report(results: list[AblationResult]) -> str:
    headers = ["question", "type", "accuracy (projected)", "accuracy (full-record)", "n", "winner"]
    rows = []
    for r in results:
        proj_acc = r.projected.weighted_accuracy
        full_acc = r.full_record.weighted_accuracy
        if proj_acc is None or full_acc is None:
            winner = "insufficient data"
        elif proj_acc > full_acc:
            winner = "projected"
        elif full_acc > proj_acc:
            winner = "full-record"
        else:
            winner = "tie"
        rows.append(
            [
                r.question_id,
                r.type,
                "—" if proj_acc is None else f"{proj_acc:.2f}",
                "—" if full_acc is None else f"{full_acc:.2f}",
                str(r.projected.n),
                winner,
            ]
        )
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    return "\n".join([fmt(headers), *(fmt(row) for row in rows)])
