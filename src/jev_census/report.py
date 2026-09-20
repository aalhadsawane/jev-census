"""`validation_report.md` generator and verdict rules (T6.5,
`05-P6-QUALITY.md`). Reproduces the README's table exactly — columns,
order, and the "FAIL — exploratory only" phrasing.

`census validate` persists each question's `QuestionScore` as JSON
(`results/validation/<question_id>.json`) so `census report` can rebuild
the Markdown without re-reading the gold set — P8 needs to be able to
regenerate the artifact without the labelling CSVs at hand.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .scoring import MIN_USABLE_ROWS, QuestionScore, ThresholdResult

DEFAULT_GATE_FLOOR = 0.90


def compute_verdict(score: QuestionScore, *, gate: str, gate_floor: float = DEFAULT_GATE_FLOOR) -> str:
    """PASS / FAIL / INSUFFICIENT, per T6.5's rules:

    - INSUFFICIENT when fewer than MIN_USABLE_ROWS usable labels.
    - PASS when the reported weighted accuracy >= gate_floor.
    - FAIL otherwise — a `gate: exploratory` question still FAILs, but is
      marked "— exploratory only" and excluded from headline claims,
      never silently hidden (exactly the README's `frustration` row).
    """
    if score.n < MIN_USABLE_ROWS or score.weighted_accuracy is None:
        return "INSUFFICIENT"
    if score.weighted_accuracy >= gate_floor:
        return "PASS"
    if gate == "exploratory":
        return "FAIL — exploratory only"
    return "FAIL"


def any_strict_question_failed(
    scores: list[QuestionScore], gates: dict[str, str], *, gate_floor: float = DEFAULT_GATE_FLOOR
) -> bool:
    """The exit-code rule: non-zero only if a `gate: strict` question FAILs.
    An exploratory failure is an expected, disclosed outcome, not a broken
    build — this is what makes `census validate` usable as a CI gate."""
    for score in scores:
        gate = gates.get(score.question_id, "strict")
        if compute_verdict(score, gate=gate, gate_floor=gate_floor) == "FAIL":
            return True
    return False


def _format_accuracy(score: QuestionScore) -> str:
    return "—" if score.weighted_accuracy is None else f"{score.weighted_accuracy:.2f}"


def _format_ece(score: QuestionScore) -> str:
    # noul's ECE column always prints "—" (05-P6-QUALITY.md): a noul
    # carries no model-reported confidence, and our derived one is not the
    # model's claim, so it never shares a column with a choice/score ECE.
    if score.type == "noul" or score.ece_confidence is None:
        return "—"
    return f"{score.ece_confidence:.3f}"


def _format_threshold(score: QuestionScore) -> str:
    t = score.threshold.threshold
    if t is None:
        return "—"
    return f"|p-0.5| > {t:.2f}" if score.type == "noul" else f"conf > {t:.2f}"


def _format_coverage(score: QuestionScore) -> str:
    c = score.threshold.coverage
    return "—" if c is None else f"{c * 100:.0f}%"


_TABLE_HEADERS = ["question", "type", "accuracy", "n", "ECE", "threshold", "coverage", "verdict"]


def format_report_table(
    scores: list[QuestionScore], gates: dict[str, str], *, gate_floor: float = DEFAULT_GATE_FLOOR
) -> str:
    """The README's plain-text table — one row per question, in the order
    given, columns aligned to their own content's width."""
    rows = []
    for score in scores:
        gate = gates.get(score.question_id, "strict")
        rows.append(
            [
                score.question_id,
                score.type,
                _format_accuracy(score),
                str(score.n),
                _format_ece(score),
                _format_threshold(score),
                _format_coverage(score),
                compute_verdict(score, gate=gate, gate_floor=gate_floor),
            ]
        )

    widths = [
        max(len(_TABLE_HEADERS[i]), *(len(row[i]) for row in rows)) if rows else len(_TABLE_HEADERS[i])
        for i in range(len(_TABLE_HEADERS))
    ]

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    return "\n".join([fmt_row(_TABLE_HEADERS), *(fmt_row(row) for row in rows)])


def format_footnotes(
    scores: list[QuestionScore], *, excluded_chunked: dict[str, int] | None = None
) -> str:
    """Everything the table has no room for (T6.5): per-question n, unclear
    count, weighted vs unweighted accuracy, the Wilson interval, n_eff, the
    noul `ece_probability` footnote, score's adjacent agreement, and the
    exclusion note when chunked cells were skipped."""
    lines: list[str] = []
    for score in scores:
        lines.append(f"### {score.question_id}")
        lines.append(f"- n = {score.n}, unclear = {score.n_unclear}")
        if score.weighted_accuracy is not None:
            lines.append(
                f"- weighted accuracy {score.weighted_accuracy:.3f} "
                f"(unweighted {score.unweighted_accuracy:.3f}), "
                f"95% Wilson CI [{score.wilson_lo:.3f}, {score.wilson_hi:.3f}], "
                f"n_eff = {score.n_eff:.1f}"
            )
        if score.type == "noul" and score.ece_probability is not None:
            lines.append(f"- noul probability calibration (ece_probability): {score.ece_probability:.3f}")
        if score.type == "score" and score.adjacent_agreement is not None:
            lines.append(f"- adjacent agreement (|modal level - gold| <= 1): {score.adjacent_agreement:.3f}")
        excluded = (excluded_chunked or {}).get(score.question_id, 0)
        if excluded:
            lines.append(f"- {excluded} chunked (aggregated) cell(s) excluded from this sample")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_validation_report(
    scores: list[QuestionScore],
    gates: dict[str, str],
    *,
    gate_floor: float = DEFAULT_GATE_FLOOR,
    excluded_chunked: dict[str, int] | None = None,
) -> str:
    table = format_report_table(scores, gates, gate_floor=gate_floor)
    footnotes = format_footnotes(scores, excluded_chunked=excluded_chunked)
    return f"{table}\n\n{footnotes}"


# --- persistence: `census validate` writes, `census report` reads --------


def score_to_dict(score: QuestionScore) -> dict:
    return dataclasses.asdict(score)


def score_from_dict(data: dict) -> QuestionScore:
    data = dict(data)
    data["threshold"] = ThresholdResult(**data["threshold"])
    return QuestionScore(**data)


def write_validation_json(path: str | Path, score: QuestionScore) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(score_to_dict(score), indent=2), encoding="utf-8")


def read_validation_json(path: str | Path) -> QuestionScore:
    return score_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
