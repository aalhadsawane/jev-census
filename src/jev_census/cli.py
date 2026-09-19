"""`census run` (T1.9): the P1 walking skeleton.

One document per call, every question batched into it, no concurrency, no
cache, no resume, no admission-control budget governor. This is deliberately
the simplest thing that reproduces a slice of the README's worked example —
P2 (durability), P3 (cost control) and P4 (scale) each replace a piece of
this file with something production-grade. Do not mistake this for T3.4's
governor: the spend check here is a simple running-total stop, not admission
control with overshoot bounds.
"""

from __future__ import annotations

import itertools
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .cell import build_cell
from .client import JevClient, JevConfigError, JevTransientError
from .decoder import DecodeError, decode_answers
from .normalizer import normalize
from .question_set import load_question_set
from .sources import read_source
from .writer import write_cells_parquet

app = typer.Typer(add_completion=False, help="census: ask the same questions of every row, get back a table.")
console = Console()

# Cost model per 00-JEV-API.md: $0.042/MTok input, output free.
INPUT_COST_PER_MTOK_USD = 0.042


@app.callback()
def main() -> None:
    """census: ask the same questions of every row, get back a table.

    A bare callback here (even a no-op) keeps `run` an explicit subcommand —
    Typer collapses a single `@app.command()` into the bare app otherwise,
    which would break the `census run ...` surface once T3.2/T6.1/etc. are
    still just names in 01-DESIGN.md's CLI section rather than implemented.
    """


@app.command()
def run(
    input: Path = typer.Option(..., "--input", exists=True, dir_okay=False, help="Parquet or CSV corpus"),
    questions: Path = typer.Option(..., "--questions", exists=True, dir_okay=False, help="Question set YAML"),
    budget: float = typer.Option(..., "--budget", help="Spending cap in USD"),
    out: Path = typer.Option(..., "--out", file_okay=False, help="Output directory"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Only process the first N documents"),
    id_field: Optional[str] = typer.Option(
        None, "--id-field", help="Source column to use as doc_id; content-hash fallback if omitted"
    ),
) -> None:
    load_dotenv(Path.cwd() / ".env.local")
    if not os.environ.get("TYPESAFE_API_KEY"):
        console.print("[red]TYPESAFE_API_KEY not set (checked environment and .env.local)[/red]")
        raise typer.Exit(code=1)

    question_set = load_question_set(questions)
    questions_by_id = {q.id: q for q in question_set.questions}

    # P1 has no call-group planner (P5): every question shares one projection,
    # the union of every question's resolved fields, and one call per document.
    projection_fields: set[str] = set()
    for q in question_set.questions:
        projection_fields.update(question_set.resolved_projection(q))

    docs = normalize(read_source(input), id_field=id_field)
    if limit is not None:
        docs = itertools.islice(docs, limit)
    docs = list(docs)

    client = JevClient()
    run_id = str(uuid.uuid4())
    cells = []
    spent_usd = 0.0
    skipped = 0

    progress_columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    )
    with Progress(*progress_columns, console=console) as progress:
        task = progress.add_task("census run", total=len(docs))
        for doc in docs:
            if spent_usd >= budget:
                console.print(f"[yellow]budget exhausted: ${spent_usd:.4f} / ${budget:.2f} — stopping[/yellow]")
                break

            state = (
                {field: doc.fields.get(field) for field in sorted(projection_fields)}
                if projection_fields
                else doc.fields
            )
            call_id = str(uuid.uuid4())

            try:
                response = client.ask(state, questions_by_id)
            except JevConfigError as exc:
                console.print(f"[red]config error, aborting run: {exc}[/red]")
                raise typer.Exit(code=1)
            except JevTransientError as exc:
                console.print(f"[yellow]transient error on '{doc.id}', skipping (P1 has no retry): {exc}[/yellow]")
                skipped += 1
                progress.advance(task)
                continue

            try:
                decoded = decode_answers(response, questions_by_id)
            except DecodeError as exc:
                console.print(f"[yellow]decode error on '{doc.id}', skipping: {exc}[/yellow]")
                skipped += 1
                progress.advance(task)
                continue

            ts = datetime.now(timezone.utc)
            input_tokens = response.usage.input_tokens or 0
            spent_usd += input_tokens / 1_000_000 * INPUT_COST_PER_MTOK_USD

            for decoded_answer in decoded:
                question = questions_by_id[decoded_answer.question_id]
                cells.append(
                    build_cell(
                        decoded_answer,
                        doc_id=doc.id,
                        gate=question_set.resolved_gate(question),
                        projection_id="p0",
                        call_id=call_id,
                        run_id=run_id,
                        model=response.model,
                        questionset_hash=question_set.questionset_hash,
                        question_body_hash=question.body_hash,
                        input_tokens=input_tokens,
                        ts=ts,
                    )
                )
            progress.advance(task)

    out_path = out / "cells.parquet"
    write_cells_parquet(cells, out_path)
    processed = len(docs) - skipped
    console.print(f"[green]wrote {len(cells)} cells for {processed} documents -> {out_path}[/green]")
    console.print(f"spent ${spent_usd:.4f} of ${budget:.2f} budget; {skipped} documents skipped")


if __name__ == "__main__":
    app()
