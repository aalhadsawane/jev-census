"""`census run`: thin Typer wrapper around `scheduler.run_scheduled` — the
concurrent, AIMD-throttled Scheduler (P4) that replaced T1.9/P2/P3's
sequential `runner.run_census` as the CLI's actual execution path.
`runner.run_census` is kept as-is (still fully tested, still importable) as
the pure sequential reference implementation; every scale/concurrency
concern here delegates straight to `scheduler.py`. Progress/error reporting
only lives in this file.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import typer
from dotenv import load_dotenv
from rich.console import Console

from . import report as report_module
from .ablation import format_ablation_report, full_record_variant, run_ablation_comparison
from .client import AsyncJevClient, JevConfigError
from .estimate import DEFAULT_SAMPLE_SIZE, format_count, format_estimate
from .estimate import estimate as run_estimate
from .overrides import export_review_queue, import_overrides, read_review_queue
from .planner import plan_call_groups
from .question_set import load_question_set
from .runner import RunnerError
from .sampling import (
    DEFAULT_STRATA,
    label_rows_from_cells,
    projection_fields_for_question,
    read_label_csv,
    read_manifest,
    read_run_cells,
    recover_doc_fields,
    stratified_sample,
    write_label_csv,
)
from .scheduler import SchedulerConfig, format_progress, run_scheduled
from .schema_tune import (
    format_schema_tune_report,
    load_variants,
    reservoir_sample_corpus,
    score_variant,
    write_question_set_yaml,
    write_schema_tune_csv,
)
from .scoring import (
    MIN_USABLE_ROWS,
    GoldFileError,
    question_reliability_bins,
    score_question,
    validate_gold_rows,
)

app = typer.Typer(add_completion=False, help="census: ask the same questions of every row, get back a table.")
review_app = typer.Typer(add_completion=False, help="Review queue export and human-answer import.")
app.add_typer(review_app, name="review")
console = Console()


@app.callback()
def main() -> None:
    """census: ask the same questions of every row, get back a table.

    A bare callback here (even a no-op) keeps `run` an explicit subcommand —
    Typer collapses a single `@app.command()` into the bare app otherwise,
    which would break the `census run ...` surface once T6.1/etc. are still
    just names in 01-DESIGN.md's CLI section rather than implemented.
    """


def _build_async_client():
    # Test-only seam: a subprocess-based chaos/SIGINT test needs a real OS
    # process, which means it can't inject a fake client via Python call
    # arguments. This env var is never set in a normal invocation.
    if os.environ.get("CENSUS_FAKE_CLIENT") == "1":
        from tests.fakes import FakeAsyncJevClient

        delay = float(os.environ.get("CENSUS_FAKE_CLIENT_DELAY", "0"))
        return FakeAsyncJevClient(delay_seconds=delay)
    return AsyncJevClient()


@app.command()
def estimate(
    input: Path = typer.Option(..., "--input", exists=True, dir_okay=False, help="Parquet or CSV corpus"),
    questions: Path = typer.Option(..., "--questions", exists=True, dir_okay=False, help="Question set YAML"),
    sample: int = typer.Option(DEFAULT_SAMPLE_SIZE, "--sample", help="Documents to sample for the projection"),
    id_field: str | None = typer.Option(
        None, "--id-field", help="Source column to use as doc_id; content-hash fallback if omitted"
    ),
) -> None:
    """Project cost and runtime before spending anything. Never calls the API."""
    result = run_estimate(input, questions, sample_size=sample, id_field=id_field)
    console.print()
    console.print(format_estimate(result))
    console.print()


@app.command()
def run(
    input: Path = typer.Option(..., "--input", exists=True, dir_okay=False, help="Parquet or CSV corpus"),
    questions: Path = typer.Option(..., "--questions", exists=True, dir_okay=False, help="Question set YAML"),
    budget: float = typer.Option(..., "--budget", help="Spending cap in USD"),
    out: Path = typer.Option(..., "--out", file_okay=False, help="Output directory"),
    limit: int | None = typer.Option(None, "--limit", help="Only process the first N documents"),
    id_field: str | None = typer.Option(
        None, "--id-field", help="Source column to use as doc_id; content-hash fallback if omitted"
    ),
    census_dir: Path = typer.Option(
        Path(".census"), "--census-dir", file_okay=False, help="Machine state directory (cache, checkpoints)"
    ),
    resume: str | None = typer.Option(
        None, "--resume", help="Run id to resume; refuses if the question set changed"
    ),
    shard_size: int = typer.Option(5000, "--shard-size", help="Cells buffered per shard before finalizing"),
    concurrency_max: int = typer.Option(64, "--concurrency-max", help="AIMD concurrency ceiling"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress periodic progress lines"),
) -> None:
    using_fake_client = os.environ.get("CENSUS_FAKE_CLIENT") == "1"
    if not using_fake_client:
        load_dotenv(Path.cwd() / ".env.local")
        if not os.environ.get("TYPESAFE_API_KEY"):
            console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
            raise typer.Exit(code=1)

    # T5.3: the call-group multiplier is real money, so it's visible before
    # any call goes out, not only in the summary afterwards.
    question_set = load_question_set(questions)
    call_groups = plan_call_groups(question_set)
    if len(call_groups) > 1:
        console.print(
            f"[cyan]{len(question_set.questions)} questions · {len(call_groups)} call groups · "
            f"state sent {len(call_groups)}x per document[/cyan]"
        )

    config = SchedulerConfig(
        input_path=input,
        questions_path=questions,
        budget_usd=budget,
        out_dir=out,
        census_dir=census_dir,
        limit=limit,
        id_field=id_field,
        resume_run_id=resume,
        shard_size=shard_size,
        concurrency_ceiling=concurrency_max,
        on_progress=(None if quiet else lambda snapshot: console.print(format_progress(snapshot))),
    )
    client = _build_async_client()

    try:
        result = asyncio.run(run_scheduled(config, client))
    except RunnerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except JevConfigError as exc:
        console.print(f"[red]config error, aborting run: {exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]run id: {result.run_id}[/cyan]")
    if result.documents_already_done:
        console.print(f"[cyan]resumed: {result.documents_already_done} documents already done, skipped[/cyan]")
    console.print(f"[green]wrote {result.out_path}[/green]")
    console.print(
        f"processed {result.documents_processed} documents "
        f"({result.cache_hits} cells from cache, {result.cache_misses} newly asked); "
        f"{result.documents_skipped} skipped, {result.documents_quarantined} quarantined"
    )
    console.print(
        f"spent ${result.spent_usd:.4f} of ${budget:.2f} budget "
        f"({result.total_input_tokens_charged:,} input tokens charged)"
    )
    if result.calibration_ratio is not None:
        console.print(f"estimator calibration: actual/estimated = {result.calibration_ratio:.2f}x")

    if result.interrupted:
        console.print("[yellow]interrupted (SIGINT) — stopped cleanly, nothing in flight was lost[/yellow]")
        console.print(f"[yellow]resume command: census run --resume {result.run_id} ...[/yellow]")
        # 130 = 128 + SIGINT's signal number, the conventional Unix exit code.
        raise typer.Exit(code=130)

    if result.budget_exhausted:
        console.print(
            f"[yellow]budget exhausted before the corpus finished — resume with "
            f"--resume {result.run_id} after raising --budget[/yellow]"
        )
        console.print(f"[yellow]resume command: census run --resume {result.run_id} ...[/yellow]")
        # Clean, resumable stop, not an error — but non-zero so scripts notice
        # the run is incomplete (01-DESIGN.md Governor: "exit non-zero, print
        # the resume command").
        raise typer.Exit(code=2)


@app.command()
def label(
    run: str = typer.Option(..., "--run", help="Run id to sample from"),
    question: str = typer.Option(..., "--question", help="Question id to sample"),
    n: int = typer.Option(200, "--n", help="Target sample size"),
    strata: int = typer.Option(DEFAULT_STRATA, "--strata", help="Bins per stratified dimension"),
    seed: int = typer.Option(0, "--seed", help="Sampling seed, for a reproducible labelling effort"),
    include_chunked: bool = typer.Option(
        False, "--include-chunked", help="Include chunk_count > 1 (aggregated) cells"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Allow overwriting an existing label CSV (may discard human answers)"
    ),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.1: stratified sample of one question's cells for hand-labelling.
    Never calls the API — reads the run's finalized shards directly."""
    run_dir = census_dir / "runs" / run
    manifest = read_manifest(run_dir)

    all_cells = read_run_cells(run_dir, question, include_chunked=True)
    if not all_cells:
        console.print(f"[red]no cells found for question '{question}' in run '{run}'[/red]")
        raise typer.Exit(code=1)
    if include_chunked:
        cells, excluded = all_cells, 0
    else:
        cells = [c for c in all_cells if c["chunk_count"] == 1]
        excluded = len(all_cells) - len(cells)

    sampled = stratified_sample(cells, n=n, strata=strata, seed=seed)
    projection_fields = projection_fields_for_question(manifest, cells)
    doc_fields_by_id = recover_doc_fields(manifest, {sc.cell["doc_id"] for sc in sampled})
    question_type = cells[0]["type"]

    out_path = out / "label" / f"{question}.csv"
    try:
        count = write_label_csv(
            out_path,
            question_id=question,
            question_type=question_type,
            sampled=sampled,
            doc_fields_by_id=doc_fields_by_id,
            projection_fields=projection_fields,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    strata_used = len({sc.stratum for sc in sampled})
    excl_note = f" ({excluded} excluded: chunk_count > 1)" if excluded else ""
    console.print(f"sampled {count} of {len(all_cells):,} cells across {strata_used} strata{excl_note}")
    console.print(f"wrote {out_path} — fill in `gold_answer`, then run `census validate`")


def _reliability_csv_path(out: Path, question_id: str) -> Path:
    return out / "reliability" / f"{question_id}.csv"


def _write_reliability_csv(path: Path, bins: list[dict]) -> None:
    import csv as csv_module

    path.parent.mkdir(parents=True, exist_ok=True)
    # Column names follow 05-P6-QUALITY.md's spec literally; for a noul
    # question "mean_confidence"/"weighted_accuracy" hold the mean
    # probability and the empirical true-frequency respectively, not a
    # model-reported confidence — same shape, different meaning, documented
    # here rather than forking the schema per type.
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv_module.DictWriter(
            handle, fieldnames=["bin_lower", "bin_upper", "n", "weighted_n", "mean_confidence", "weighted_accuracy"]
        )
        writer.writeheader()
        for b in bins:
            writer.writerow(
                {
                    "bin_lower": b["bin_lower"],
                    "bin_upper": b["bin_upper"],
                    "n": b["n"],
                    "weighted_n": b["weighted_n"],
                    "mean_confidence": b["mean_value"],
                    "weighted_accuracy": b["weighted_target"],
                }
            )


@app.command()
def validate(
    run: str = typer.Option(..., "--run"),
    gold: Path = typer.Option(..., "--gold", exists=True, help="A gold CSV, or a directory of per-question gold CSVs"),
    target_accuracy: float = typer.Option(0.99, "--target-accuracy"),
    gate_floor: float = typer.Option(report_module.DEFAULT_GATE_FLOOR, "--gate-floor"),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.2-T6.5: score every gold-labelled question, write
    `validation_report.md`, and exit non-zero only if a `gate: strict`
    question fails."""
    run_dir = census_dir / "runs" / run
    gold_paths = sorted(gold.glob("*.csv")) if gold.is_dir() else [gold]
    if not gold_paths:
        console.print(f"[red]no gold CSVs found at {gold}[/red]")
        raise typer.Exit(code=1)

    scores = []
    gates: dict[str, str] = {}
    for gold_path in gold_paths:
        rows = read_label_csv(gold_path)
        if not rows:
            continue
        question_id = rows[0]["question_id"]
        question_type = rows[0]["type"]

        cells = read_run_cells(run_dir, question_id, include_chunked=True)
        known_doc_ids = {c["doc_id"] for c in cells}
        gates[question_id] = cells[0]["gate"] if cells else "strict"

        valid_options = None
        if question_type == "choice":
            for cell in cells:
                if cell.get("probabilities"):
                    valid_options = set(cell["probabilities"].keys())
                    break

        try:
            validate_gold_rows(
                rows, question_type=question_type, known_doc_ids=known_doc_ids, valid_options=valid_options
            )
        except GoldFileError as exc:
            console.print(f"[red]{gold_path}: {exc}[/red]")
            raise typer.Exit(code=1)

        score = score_question(
            rows,
            question_id=question_id,
            question_type=question_type,
            target_accuracy=target_accuracy,
            min_n_eff=MIN_USABLE_ROWS,
        )
        scores.append(score)
        report_module.write_validation_json(out / "validation" / f"{question_id}.json", score)
        bins = question_reliability_bins(rows, question_type)
        _write_reliability_csv(_reliability_csv_path(out, question_id), bins)

    report_text = report_module.render_validation_report(scores, gates, gate_floor=gate_floor)
    report_path = out / "validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    passed = sum(
        1
        for s in scores
        if report_module.compute_verdict(s, gate=gates.get(s.question_id, "strict"), gate_floor=gate_floor)
        == "PASS"
    )
    console.print(f"wrote {report_path}")
    console.print(f"{passed} of {len(scores)} questions PASS")

    if report_module.any_strict_question_failed(scores, gates, gate_floor=gate_floor):
        raise typer.Exit(code=1)


@app.command()
def report(
    run: str = typer.Option(..., "--run"),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
    gate_floor: float = typer.Option(report_module.DEFAULT_GATE_FLOOR, "--gate-floor"),
) -> None:
    """T6.5: regenerate `validation_report.md` from stored validation JSON,
    without re-reading the gold set."""
    run_dir = census_dir / "runs" / run
    validation_dir = out / "validation"
    json_paths = sorted(validation_dir.glob("*.json"))
    if not json_paths:
        console.print(f"[red]no stored validation results at {validation_dir} — run `census validate` first[/red]")
        raise typer.Exit(code=1)

    scores = [report_module.read_validation_json(p) for p in json_paths]
    gates: dict[str, str] = {}
    for score in scores:
        cells = read_run_cells(run_dir, score.question_id, include_chunked=True)
        gates[score.question_id] = cells[0]["gate"] if cells else "strict"

    report_text = report_module.render_validation_report(scores, gates, gate_floor=gate_floor)
    report_path = out / "validation_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    console.print(f"wrote {report_path}")


@review_app.command("export")
def review_export(
    run: str = typer.Option(..., "--run"),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.6: export cells below their question's recommended threshold to
    `results/review_queue.csv`, ready for a human to fill in `human_answer`."""
    run_dir = census_dir / "runs" / run
    validation_dir = out / "validation"
    out_path = out / "review_queue.csv"
    count = export_review_queue(run_dir, validation_dir, out_path)
    console.print(f"wrote {out_path} — {count} cell(s) for review")


@review_app.command("import")
def review_import(
    run: str = typer.Option(..., "--run"),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False, help="A filled-in review_queue.csv"),
    reviewer: str = typer.Option(..., "--reviewer", help="Who reviewed these rows"),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.6: import human answers as a separate override layer
    (`results/overrides.parquet`) — never merged into `cells.parquet`."""
    rows = read_review_queue(file)
    overrides_path = out / "overrides.parquet"
    count = import_overrides(overrides_path, rows, reviewer=reviewer, source_run_id=run)
    console.print(f"wrote {count} override(s) to {overrides_path}")


@app.command("schema-tune")
def schema_tune_cmd(
    input: Path = typer.Option(..., "--input", exists=True, dir_okay=False, help="Parquet or CSV corpus"),
    questions: Path = typer.Option(..., "--questions", exists=True, dir_okay=False, help="Reference question set"),
    budget: float = typer.Option(..., "--budget", help="Spending cap in USD, per run (reference and each variant)"),
    variants: Path = typer.Option(..., "--variants", exists=True, dir_okay=False, help="variants.yaml"),
    sample: int = typer.Option(300, "--sample", help="Documents to sample once, shared by every variant"),
    seed: int = typer.Option(0, "--seed"),
    id_field: str | None = typer.Option(None, "--id-field"),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.7: prices each hand-authored variant in `--variants` against the
    reference question set on the same sampled sub-corpus, reusing the
    normal cell cache (an unchanged variant's matching questions cost
    nothing on a re-run). Never writes or rewrites a question set."""
    using_fake_client = os.environ.get("CENSUS_FAKE_CLIENT") == "1"
    if not using_fake_client:
        load_dotenv(Path.cwd() / ".env.local")
        if not os.environ.get("TYPESAFE_API_KEY"):
            console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
            raise typer.Exit(code=1)

    reference_path, variant_list = load_variants(variants)
    reference_qs = load_question_set(reference_path)

    tune_dir = out / "schema_tune_runs"
    sub_corpus = tune_dir / "sample.parquet"
    n_sampled = reservoir_sample_corpus(input, sub_corpus, n=sample, seed=seed)
    if n_sampled == 0:
        console.print("[red]corpus is empty[/red]")
        raise typer.Exit(code=1)
    console.print(f"sampled {n_sampled} documents for schema-tune (shared across reference and every variant)")

    # A dedicated cache subdirectory, shared by the reference and every
    # variant run below -- the cache is what makes an unchanged variant's
    # matching questions cost nothing on a re-run.
    tune_census_dir = census_dir / "schema-tune"

    def _run(name: str, questions_path: Path) -> Path:
        run_out = tune_dir / name
        config = SchedulerConfig(
            input_path=sub_corpus,
            questions_path=questions_path,
            budget_usd=budget,
            out_dir=run_out,
            census_dir=tune_census_dir,
            id_field=id_field,
            on_progress=None,
        )
        client = _build_async_client()
        result = asyncio.run(run_scheduled(config, client))
        console.print(f"  {name}: spent ${result.spent_usd:.4f}, {result.documents_processed} documents")
        return run_out / "cells.parquet"

    console.print("running reference...")
    reference_cells_path = _run("reference", reference_path)

    all_results = []
    for variant in variant_list:
        console.print(f"running variant '{variant.name}'...")
        variant_yaml = tune_dir / f"{variant.name}.yaml"
        write_question_set_yaml(variant.question_set, variant_yaml)
        variant_cells_path = _run(variant.name, variant_yaml)
        results = score_variant(
            reference_cells_path, variant_cells_path, variant.name, reference_qs, variant.question_set
        )
        all_results.extend(results)

    report_text = format_schema_tune_report(all_results)
    report_path = out / "schema_tune.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    write_schema_tune_csv(out / "schema_tune.csv", all_results)

    console.print()
    console.print(report_text)
    console.print(f"wrote {report_path} and {out / 'schema_tune.csv'}")


@app.command()
def ablation(
    run: str = typer.Option(..., "--run", help="An already-completed run (the 'projected' condition)"),
    gold: Path = typer.Option(..., "--gold", exists=True, help="Filled-in label CSV(s) for that run"),
    questions: Path = typer.Option(..., "--questions", exists=True, dir_okay=False, help="The question set used for --run"),
    budget: float = typer.Option(..., "--budget", help="Spending cap for the full-record comparison run"),
    census_dir: Path = typer.Option(Path(".census"), "--census-dir", file_okay=False),
    out: Path = typer.Option(Path("results"), "--out", file_okay=False),
) -> None:
    """T6.8: does a projected state beat a full-record state on the same
    question? Re-asks every question against the SAME gold-labelled
    documents with every projection widened to the whole record, and
    compares ACCURACY (not agreement) against the existing gold set."""
    using_fake_client = os.environ.get("CENSUS_FAKE_CLIENT") == "1"
    if not using_fake_client:
        load_dotenv(Path.cwd() / ".env.local")
        if not os.environ.get("TYPESAFE_API_KEY"):
            console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
            raise typer.Exit(code=1)

    run_dir = census_dir / "runs" / run
    manifest = read_manifest(run_dir)

    gold_paths = sorted(gold.glob("*.csv")) if gold.is_dir() else [gold]
    gold_csv_paths: dict[str, Path] = {}
    doc_ids_needed: set[str] = set()
    for gold_path in gold_paths:
        rows = read_label_csv(gold_path)
        if not rows:
            continue
        question_id = rows[0]["question_id"]
        gold_csv_paths[question_id] = gold_path
        doc_ids_needed.update(r["doc_id"] for r in rows)

    if not doc_ids_needed:
        console.print("[red]no gold rows found[/red]")
        raise typer.Exit(code=1)

    doc_fields_by_id = recover_doc_fields(manifest, doc_ids_needed)
    all_fields = sorted({field for fields in doc_fields_by_id.values() for field in fields})

    ablation_dir = out / "ablation_runs"
    sub_corpus = ablation_dir / "sample.parquet"
    sub_corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus_rows = [{"_doc_id": doc_id, **fields} for doc_id, fields in doc_fields_by_id.items()]
    pq.write_table(pa.Table.from_pylist(corpus_rows), sub_corpus)

    question_set = load_question_set(questions)
    full_record_qs = full_record_variant(question_set, all_fields)
    full_record_yaml = ablation_dir / "full_record.yaml"
    write_question_set_yaml(full_record_qs, full_record_yaml)

    full_record_out = ablation_dir / "full_record"
    config = SchedulerConfig(
        input_path=sub_corpus,
        questions_path=full_record_yaml,
        budget_usd=budget,
        out_dir=full_record_out,
        census_dir=census_dir / "ablation",
        id_field="_doc_id",
        on_progress=None,
    )
    client = _build_async_client()
    result = asyncio.run(run_scheduled(config, client))
    console.print(f"full-record run: spent ${result.spent_usd:.4f}, {result.documents_processed} documents")

    full_record_run_dir = (census_dir / "ablation") / "runs" / result.run_id
    projected_cells_by_question = {qid: read_run_cells(run_dir, qid) for qid in gold_csv_paths}
    full_record_cells_by_question = {
        qid: read_run_cells(full_record_run_dir, qid) for qid in gold_csv_paths
    }

    results = run_ablation_comparison(gold_csv_paths, projected_cells_by_question, full_record_cells_by_question)
    report_text = format_ablation_report(results)
    report_path = out / "ablation_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    console.print()
    console.print(report_text)
    console.print(f"wrote {report_path}")


DEMO_BUDGET_USD = 0.25


def _read_demo_gold(path: Path) -> list[dict]:
    """The bundled demo gold set's own, minimal shape: `doc_id,gold_answer`
    only -- no `stratum_weight` (it's a small fixed convenience sample, not
    a corpus-representative one) and no frozen model answer (those are
    read fresh from whatever the just-completed demo run produced, via
    `label_rows_from_cells`, so the bundled labels never go stale)."""
    import csv as csv_module

    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv_module.DictReader(handle)]


@app.command()
def demo(
    budget: float = typer.Option(DEMO_BUDGET_USD, "--budget", help="Spending cap in USD"),
    out: Path = typer.Option(Path("demo-results"), "--out", file_okay=False),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the estimate and stop -- never calls the API"
    ),
) -> None:
    """T7.2: one command, no clone, no config. Runs the bundled Hacker News
    demo corpus end to end -- estimate, run, validate against a bundled
    gold set -- against real data shipped inside the package."""
    demo_dir = Path(__file__).parent / "demo_data"
    corpus = demo_dir / "stories.parquet"
    questions = demo_dir / "questions.yaml"
    gold_dir = demo_dir / "gold"

    question_set = load_question_set(questions)
    est = run_estimate(corpus, questions, id_field="id")
    group_word = "call groups" if est.call_group_count != 1 else "call group"
    console.print()
    console.print(
        f"  demo: {est.document_count:,} Hacker News stories · {est.question_count} questions · "
        f"{est.call_group_count} {group_word}"
    )
    console.print(
        f"  estimate: {format_count(est.total_tokens)} tokens ≈ ${est.estimated_cost_usd:.2f} · "
        f"budget ${budget:.2f}"
    )
    console.print()

    if dry_run:
        console.print("  --dry-run: stopping before any API call")
        return

    using_fake_client = os.environ.get("CENSUS_FAKE_CLIENT") == "1"
    if not using_fake_client:
        load_dotenv(Path.cwd() / ".env.local")
        if not os.environ.get("TYPESAFE_API_KEY"):
            console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
            raise typer.Exit(code=1)

    census_dir = out / ".census"
    config = SchedulerConfig(
        input_path=corpus,
        questions_path=questions,
        budget_usd=budget,
        out_dir=out,
        census_dir=census_dir,
        id_field="id",
        on_progress=lambda snapshot: console.print(format_progress(snapshot)),
    )
    client = _build_async_client()
    result = asyncio.run(run_scheduled(config, client))

    table = pq.read_table(result.out_path)
    console.print(f"  wrote {result.out_path}          {table.num_rows:,} cells")

    run_dir = census_dir / "runs" / result.run_id
    gates = {q.id: question_set.resolved_gate(q) for q in question_set.questions}
    scores = []
    for gold_path in sorted(gold_dir.glob("*.csv")):
        question_id = gold_path.stem
        question = next((q for q in question_set.questions if q.id == question_id), None)
        if question is None:
            continue
        gold_rows = _read_demo_gold(gold_path)
        cells_by_doc_id = {c["doc_id"]: c for c in read_run_cells(run_dir, question_id, include_chunked=True)}
        rows = label_rows_from_cells(gold_rows, cells_by_doc_id, default_weight=1.0)
        scores.append(score_question(rows, question_id=question_id, question_type=question.type))

    report_text = report_module.render_validation_report(scores, gates)
    report_path = out / "validation_report.md"
    report_path.write_text(report_text, encoding="utf-8")

    verdicts = [
        report_module.compute_verdict(s, gate=gates.get(s.question_id, "strict")) for s in scores
    ]
    passed = sum(1 for v in verdicts if v == "PASS")
    exploratory = sum(1 for v in verdicts if "exploratory" in v)
    console.print(
        f"  wrote {report_path}   {len(scores)} questions, {passed} PASS, {exploratory} exploratory"
    )
    console.print()
    console.print(f'  next:  duckdb -c "SELECT * FROM \'{result.out_path}\' LIMIT 5"')
    console.print("         docs/RECIPES.md has the long→wide pivot and thresholding queries")


if __name__ == "__main__":
    app()
