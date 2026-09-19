"""`census run` (T1.9, durable since P2): thin Typer wrapper around
`runner.run_census`. Progress/error reporting only lives here; every
correctness concern (caching, atomic shards, checkpointing, resume,
quarantine) lives in `runner.py`, which is framework-agnostic and tested
without ever touching this CLI layer.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

from .client import JevClient, JevConfigError
from .runner import RunConfig, RunnerError, run_census

app = typer.Typer(add_completion=False, help="census: ask the same questions of every row, get back a table.")
console = Console()


@app.callback()
def main() -> None:
    """census: ask the same questions of every row, get back a table.

    A bare callback here (even a no-op) keeps `run` an explicit subcommand —
    Typer collapses a single `@app.command()` into the bare app otherwise,
    which would break the `census run ...` surface once T3.2/T6.1/etc. are
    still just names in 01-DESIGN.md's CLI section rather than implemented.
    """


def _build_client():
    # Test-only seam: a subprocess-based chaos test (T2.7) needs `kill -9` to
    # mean something, so it runs this CLI as a real OS process — which means
    # it can't inject a fake client via Python call arguments. This env var is
    # never set in a normal invocation.
    if os.environ.get("CENSUS_FAKE_CLIENT") == "1":
        from tests.fakes import FakeJevClient

        delay = float(os.environ.get("CENSUS_FAKE_CLIENT_DELAY", "0"))
        return FakeJevClient(delay_seconds=delay)
    return JevClient()


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
) -> None:
    using_fake_client = os.environ.get("CENSUS_FAKE_CLIENT") == "1"
    if not using_fake_client:
        load_dotenv(Path.cwd() / ".env.local")
        if not os.environ.get("TYPESAFE_API_KEY"):
            console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
            raise typer.Exit(code=1)

    config = RunConfig(
        input_path=input,
        questions_path=questions,
        budget_usd=budget,
        out_dir=out,
        census_dir=census_dir,
        limit=limit,
        id_field=id_field,
        resume_run_id=resume,
        shard_size=shard_size,
    )
    client = _build_client()

    try:
        result = run_census(config, client)
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
    console.print(f"spent ${result.spent_usd:.4f} of ${budget:.2f} budget")
    if result.budget_exhausted:
        console.print(
            f"[yellow]budget exhausted before the corpus finished — resume with "
            f"--resume {result.run_id} after raising --budget[/yellow]"
        )


if __name__ == "__main__":
    app()
