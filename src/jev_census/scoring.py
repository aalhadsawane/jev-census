"""Accuracy, calibration, and threshold statistics (T6.2-T6.4,
`05-P6-QUALITY.md`). Pure and network-free — the part most worth
unit-testing against hand-computed numbers, since a subtly wrong formula
here produces a report that looks right and is wrong.

Every statistic here is WEIGHTED by each row's `stratum_weight`
(`sampling.py`'s equal-allocation reweighting) unless explicitly noted
otherwise — the raw sample is not proportional to the corpus, so an
unweighted number here would be measuring the sample's shape, not the
corpus's accuracy.

**Structural rule, enforced by the module's own shape**: no function here
takes more than one question's rows and returns a single number.
Aggregating accuracy across question types or across questions is
meaningless (`00-JEV-API.md` #8: structural invariants don't hold between
questions), so there is nowhere in this module's API to even attempt it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_USABLE_ROWS = 30  # below this, report INSUFFICIENT rather than an accuracy (T6.2)
_Z_95 = 1.959963984540054  # two-sided 95% normal quantile


def _clean_gold(row: dict) -> str:
    return (row.get("gold_answer") or "").strip()


def is_unclear_or_missing(row: dict) -> bool:
    gold = _clean_gold(row)
    return gold == "" or gold.lower() == "unclear"


class GoldFileError(Exception):
    """Raised with every problem found in a gold CSV, each naming the
    offending row — refuse rather than guess (05-P6-QUALITY.md)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_gold_rows(
    rows: list[dict],
    *,
    question_type: str,
    known_doc_ids: set[str] | None = None,
    valid_options: set[str] | None = None,
) -> None:
    """Gold-file hygiene checks (T6.2): an unknown `doc_id`, or one that
    doesn't belong to this run, is an error naming the row; a
    `gold_answer` outside the type's accepted set is an error naming the
    row and the accepted values. `unclear` (any case) and empty are always
    accepted, for every type. Raises `GoldFileError` listing every problem
    found, not just the first."""
    errors: list[str] = []
    for i, row in enumerate(rows):
        doc_id = row.get("doc_id")
        if known_doc_ids is not None and doc_id not in known_doc_ids:
            errors.append(f"row {i} (doc_id={doc_id!r}): doc_id does not belong to this run")

        if is_unclear_or_missing(row):
            continue
        gold = _clean_gold(row)

        if question_type == "noul":
            if gold.lower() not in ("true", "false"):
                errors.append(
                    f"row {i} (doc_id={doc_id!r}): gold_answer {gold!r} is not one of "
                    "true / false / unclear"
                )
        elif question_type == "choice":
            if valid_options is not None and gold not in valid_options:
                errors.append(
                    f"row {i} (doc_id={doc_id!r}): gold_answer {gold!r} is not one of "
                    f"{sorted(valid_options)} (or unclear)"
                )
        elif question_type == "score":
            try:
                int(gold)
            except ValueError:
                errors.append(
                    f"row {i} (doc_id={doc_id!r}): gold_answer {gold!r} is not an integer "
                    "level index (or unclear)"
                )
        else:
            errors.append(f"row {i} (doc_id={doc_id!r}): unknown question type {question_type!r}")

    if errors:
        raise GoldFileError(errors)


def is_correct(row: dict) -> bool | None:
    """The per-type correctness rule (05-P6-QUALITY.md). `None` for a
    missing or `unclear` gold answer — excluded from accuracy, not counted
    as wrong.

    noul:   (model_noul > 0.5) == (gold == "true")            -- the default
            decision rule; recommend_threshold() then searches for a better one.
    choice: model_choice == gold                               -- argmax agreement.
    score:  model_modal_level == gold                           -- NOT round(score).
            The weighted score is threshold material, not a measurement
            (00-JEV-API.md: "score levels are weak in numerical calibration");
            the modal level is the answer the model's probability mass
            actually picked.
    """
    if is_unclear_or_missing(row):
        return None
    gold = _clean_gold(row)
    qtype = row["type"]
    if qtype == "noul":
        return (row["model_noul"] > 0.5) == (gold.lower() == "true")
    if qtype == "choice":
        return row["model_choice"] == gold
    if qtype == "score":
        return row["model_modal_level"] == gold
    raise ValueError(f"unknown question type {qtype!r}")


def adjacent_agreement(row: dict) -> bool | None:
    """score only: |modal_level - gold| <= 1. An ordered scale that is never
    more than one level out is useful evidence even when exact agreement is
    mediocre — distinguishes "noisy but ordered" from "random"
    (05-P6-QUALITY.md). `None` for non-score rows or unclear/missing gold."""
    if row["type"] != "score" or is_unclear_or_missing(row):
        return None
    try:
        return abs(int(row["model_modal_level"]) - int(_clean_gold(row))) <= 1
    except (TypeError, ValueError):
        return None


def _usable_pairs(rows: list[dict]) -> list[tuple[dict, bool]]:
    pairs = []
    for row in rows:
        correct = is_correct(row)
        if correct is not None:
            pairs.append((row, correct))
    return pairs


def kish_n_eff(weights: list[float]) -> float:
    """Kish's effective sample size: (Σw)^2 / Σ(w^2). Weighting reduces a
    sample's information content; an interval computed on the raw row count
    overstates precision (05-P6-QUALITY.md)."""
    total = sum(weights)
    total_sq = sum(w * w for w in weights)
    if total_sq == 0:
        return 0.0
    return (total * total) / total_sq


def wilson_interval(p_hat: float, n_eff: float, *, z: float = _Z_95) -> tuple[float, float]:
    """95% Wilson score interval — not Wald: n is small (200-300) and
    accuracies sit near 0.95-0.99, exactly where a Wald interval runs past
    1.0 (05-P6-QUALITY.md). Computed on `n_eff` (Kish), not the raw row
    count."""
    if n_eff <= 0:
        return (0.0, 1.0)
    z2 = z * z
    denom = 1 + z2 / n_eff
    center = (p_hat + z2 / (2 * n_eff)) / denom
    margin = (z / denom) * math.sqrt((p_hat * (1 - p_hat) / n_eff) + (z2 / (4 * n_eff * n_eff)))
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True)
class AccuracyResult:
    weighted_accuracy: float
    unweighted_accuracy: float
    n: int  # raw usable row count
    n_eff: float
    wilson_lo: float
    wilson_hi: float


def weighted_accuracy(rows: list[dict]) -> AccuracyResult | None:
    """The point estimate the report prints (`Σ wᵢ·correctᵢ / Σ wᵢ`), plus
    the unweighted sample accuracy alongside it — when the two diverge a
    lot, the strata disagree with each other, which is information the
    reader deserves (05-P6-QUALITY.md). `None` if there are zero usable
    (non-unclear) rows."""
    pairs = _usable_pairs(rows)
    if not pairs:
        return None
    weights = [row["stratum_weight"] for row, _ in pairs]
    total_weight = sum(weights)
    correct_weight = sum(w for (_, correct), w in zip(pairs, weights) if correct)
    weighted_acc = correct_weight / total_weight
    unweighted_acc = sum(1 for _, correct in pairs if correct) / len(pairs)
    n_eff = kish_n_eff(weights)
    lo, hi = wilson_interval(weighted_acc, n_eff)
    return AccuracyResult(
        weighted_accuracy=weighted_acc,
        unweighted_accuracy=unweighted_acc,
        n=len(pairs),
        n_eff=n_eff,
        wilson_lo=lo,
        wilson_hi=hi,
    )


def weighted_adjacent_agreement(rows: list[dict]) -> float | None:
    """score only: the weighted fraction of usable rows within one level of
    gold. `None` for non-score question sets or zero usable rows."""
    pairs = [(row, adj) for row in rows if (adj := adjacent_agreement(row)) is not None]
    if not pairs:
        return None
    weights = [row["stratum_weight"] for row, _ in pairs]
    total = sum(weights)
    agree = sum(w for (_, adj), w in zip(pairs, weights) if adj)
    return agree / total


def _weighted_ece(pairs: list[tuple[dict, float]], *, value_fn, n_bins: int) -> float | None:
    """Shared machinery for `ece_confidence`/`ece_probability`: `pairs` is
    (row, target) with target in {0.0, 1.0}; bins on `value_fn(row)` over
    `n_bins` equal-width [0, 1] bins, weighted throughout by
    `stratum_weight` (05-P6-QUALITY.md: an ECE over the unweighted sample
    measures the sample's shape, not the corpus's calibration)."""
    if not pairs:
        return None
    bins: dict[int, list[tuple[dict, float]]] = {i: [] for i in range(n_bins)}
    for row, target in pairs:
        idx = min(max(int(value_fn(row) * n_bins), 0), n_bins - 1)
        bins[idx].append((row, target))

    total_weight = sum(row["stratum_weight"] for row, _ in pairs)
    if total_weight == 0:
        return None

    ece = 0.0
    for bin_rows in bins.values():
        if not bin_rows:
            continue
        bin_weight = sum(row["stratum_weight"] for row, _ in bin_rows)
        weighted_target = sum(row["stratum_weight"] * target for row, target in bin_rows) / bin_weight
        weighted_value = sum(row["stratum_weight"] * value_fn(row) for row, _ in bin_rows) / bin_weight
        ece += (bin_weight / total_weight) * abs(weighted_target - weighted_value)
    return ece


def ece_confidence(rows: list[dict], *, n_bins: int = 10) -> float | None:
    """Expected calibration error on the MODEL-REPORTED `confidence` —
    choice and score only (the report's `ECE` column). Bins on confidence,
    compares bin-weighted accuracy (the `is_correct` decision rule) against
    bin-weighted mean confidence. Callers must not call this for `noul`
    rows and print the result in the same column as a choice/score ECE —
    `ece_probability` exists precisely so that can never happen silently."""
    pairs = [(row, 1.0 if correct else 0.0) for row, correct in _usable_pairs(rows)]
    return _weighted_ece(pairs, value_fn=lambda row: row["confidence"], n_bins=n_bins)


def ece_probability(rows: list[dict], *, n_bins: int = 10) -> float | None:
    """`noul` only: bins on the model's own P(yes) (`model_noul`) and
    compares against the empirical frequency of `gold_answer == "true"` —
    a genuine reliability curve, since a noul value IS a probability,
    unlike its derived confidence. Never printed in the report's `ECE`
    column (05-P6-QUALITY.md); surfaced as a separate footnote line."""
    pairs = []
    for row in rows:
        if is_unclear_or_missing(row):
            continue
        pairs.append((row, 1.0 if _clean_gold(row).lower() == "true" else 0.0))
    return _weighted_ece(pairs, value_fn=lambda row: row["model_noul"], n_bins=n_bins)


def reliability_bins(
    rows: list[dict], *, value_fn, target_fn, n_bins: int = 10
) -> list[dict]:
    """The chart source data behind ece_confidence/ece_probability
    (`results/reliability/<question_id>.csv`, T6.3): one row per non-empty
    bin with `bin_lower, bin_upper, n, weighted_n, mean_value,
    weighted_target`. The analysis page (P8) should never recompute this
    from raw cells — it reads this file."""
    bins: dict[int, list[dict]] = {i: [] for i in range(n_bins)}
    for row in rows:
        target = target_fn(row)
        if target is None:
            continue
        idx = min(max(int(value_fn(row) * n_bins), 0), n_bins - 1)
        bins[idx].append(row)

    out = []
    bin_width = 1.0 / n_bins
    for idx in range(n_bins):
        bin_rows = bins[idx]
        if not bin_rows:
            continue
        weights = [row["stratum_weight"] for row in bin_rows]
        weighted_n = sum(weights)
        mean_value = sum(w * value_fn(row) for row, w in zip(bin_rows, weights)) / weighted_n
        weighted_target = (
            sum(w * target_fn(row) for row, w in zip(bin_rows, weights)) / weighted_n
        )
        out.append(
            {
                "bin_lower": idx * bin_width,
                "bin_upper": (idx + 1) * bin_width,
                "n": len(bin_rows),
                "weighted_n": weighted_n,
                "mean_value": mean_value,
                "weighted_target": weighted_target,
            }
        )
    return out


def threshold_value(row: dict) -> float:
    """The scale a threshold is compared against, per type (`01-DESIGN.md`
    D4, `00-JEV-API.md` #8): noul on distance from the coin flip,
    choice/score on model confidence. Never the same scale, never compared
    across types."""
    if row["type"] == "noul":
        return abs(row["model_noul"] - 0.5)
    return row["confidence"]


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float | None
    coverage: float | None  # weighted fraction of the corpus passing, or None


def recommend_threshold(
    rows: list[dict], *, target_accuracy: float = 0.99, min_n_eff: float = MIN_USABLE_ROWS
) -> ThresholdResult:
    """Sweeps every distinct observed threshold value (ascending) and
    returns the LOWEST one whose weighted accuracy at-or-above it reaches
    `target_accuracy` — lowest, because the goal is the most coverage that
    buys the required accuracy, not the safest-looking number
    (05-P6-QUALITY.md). A candidate is skipped, not disqualifying the
    search, if its surviving weighted `n_eff` is below `min_n_eff`: without
    that guard a high threshold reliably returns a garbage 1.00 on a
    handful of rows. Returns `(None, None)` if nothing qualifies — the
    honest answer when no threshold works."""
    pairs = _usable_pairs(rows)
    if not pairs:
        return ThresholdResult(None, None)

    total_weight = sum(row["stratum_weight"] for row, _ in pairs)
    candidates = sorted({threshold_value(row) for row, _ in pairs})

    for t in candidates:
        passing = [(row, correct) for row, correct in pairs if threshold_value(row) >= t]
        weights = [row["stratum_weight"] for row, _ in passing]
        n_eff = kish_n_eff(weights)
        if n_eff < min_n_eff:
            continue
        passing_weight = sum(weights)
        correct_weight = sum(w for (_, correct), w in zip(passing, weights) if correct)
        acc = correct_weight / passing_weight
        if acc >= target_accuracy:
            return ThresholdResult(threshold=t, coverage=passing_weight / total_weight)
    return ThresholdResult(None, None)


@dataclass(frozen=True)
class QuestionScore:
    """Everything `report.py` needs to render one question's row and
    footnotes. Deliberately scoped to one question — see the module
    docstring's structural rule."""

    question_id: str
    type: str
    n: int
    n_unclear: int
    weighted_accuracy: float | None
    unweighted_accuracy: float | None
    n_eff: float
    wilson_lo: float | None
    wilson_hi: float | None
    ece_confidence: float | None  # choice/score
    ece_probability: float | None  # noul
    adjacent_agreement: float | None  # score
    threshold: ThresholdResult


def score_question(
    rows: list[dict],
    *,
    question_id: str,
    question_type: str,
    target_accuracy: float = 0.99,
    min_n_eff: float = MIN_USABLE_ROWS,
) -> QuestionScore:
    n_unclear = sum(1 for row in rows if is_unclear_or_missing(row))
    acc = weighted_accuracy(rows)
    threshold = recommend_threshold(rows, target_accuracy=target_accuracy, min_n_eff=min_n_eff)

    return QuestionScore(
        question_id=question_id,
        type=question_type,
        n=acc.n if acc else 0,
        n_unclear=n_unclear,
        weighted_accuracy=acc.weighted_accuracy if acc else None,
        unweighted_accuracy=acc.unweighted_accuracy if acc else None,
        n_eff=acc.n_eff if acc else 0.0,
        wilson_lo=acc.wilson_lo if acc else None,
        wilson_hi=acc.wilson_hi if acc else None,
        ece_confidence=ece_confidence(rows) if question_type in ("choice", "score") else None,
        ece_probability=ece_probability(rows) if question_type == "noul" else None,
        adjacent_agreement=weighted_adjacent_agreement(rows) if question_type == "score" else None,
        threshold=threshold,
    )


def question_reliability_bins(rows: list[dict], question_type: str, *, n_bins: int = 10) -> list[dict]:
    """The right `value_fn`/`target_fn` pairing per type, wired for
    `reliability_bins()` (T6.3's chart source data): noul bins on its own
    probability against `gold == 'true'`; choice/score bin on model
    confidence against `is_correct`."""
    if question_type == "noul":

        def value_fn(row: dict) -> float:
            return row["model_noul"]

        def target_fn(row: dict) -> float | None:
            if is_unclear_or_missing(row):
                return None
            return 1.0 if _clean_gold(row).lower() == "true" else 0.0

    else:

        def value_fn(row: dict) -> float:
            return row["confidence"]

        def target_fn(row: dict) -> float | None:
            correct = is_correct(row)
            return None if correct is None else (1.0 if correct else 0.0)

    return reliability_bins(rows, value_fn=value_fn, target_fn=target_fn, n_bins=n_bins)
