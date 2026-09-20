#!/usr/bin/env python3
"""T7.5 (06-P7-ADOPTION.md): regenerates every README number that doesn't
require a live API call, printing each alongside the command that produces
it -- so a reader (or a reviewer) can confirm the README hasn't drifted
from what the tool actually does, without spending anything.

Numbers that need a real run (the demo's actual spend/timing, the
support-tickets example's run) are not regenerated here -- see
docs/PROVENANCE.md for exactly how those were produced and where the
committed output lives.

Usage: python scripts/readme_numbers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import pyarrow.parquet as pq

from jev_census.estimate import estimate, format_estimate


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    demo_corpus = REPO_ROOT / "src" / "jev_census" / "demo_data" / "stories.parquet"
    demo_questions = REPO_ROOT / "src" / "jev_census" / "demo_data" / "questions.yaml"
    tickets_corpus = REPO_ROOT / "examples" / "support-tickets" / "tickets.parquet"
    tickets_questions = REPO_ROOT / "examples" / "support-tickets" / "questions.yaml"

    _section("Demo corpus row count (README: '1,910 Hacker News story titles')")
    print("command: pq.ParquetFile('src/jev_census/demo_data/stories.parquet').metadata.num_rows")
    print("value:  ", pq.ParquetFile(demo_corpus).metadata.num_rows)

    _section("Demo estimate block (README: '797.8K tokens ~= $0.03')")
    print(
        "command: census estimate --input src/jev_census/demo_data/stories.parquet "
        "--questions src/jev_census/demo_data/questions.yaml --id-field id"
    )
    result = estimate(demo_corpus, demo_questions, id_field="id")
    print(format_estimate(result))

    _section("Support-tickets estimate block (README: '30 documents ... 7.3K tokens')")
    print(
        "command: census estimate --input examples/support-tickets/tickets.parquet "
        "--questions examples/support-tickets/questions.yaml --id-field ticket_id"
    )
    result = estimate(tickets_corpus, tickets_questions, id_field="ticket_id")
    print(format_estimate(result))

    _section("Test count (README/DECISIONS.md test counts)")
    print("command: pytest -q   (run separately -- not invoked here to keep this script fast)")

    _section("Numbers that need a live API call -- see docs/PROVENANCE.md")
    print("- census demo's real spend/timing/validation report")
    print("- examples/support-tickets/'s real run and validation report")
    print("- examples/long-form/'s real run, chunking, and validation report")
    print("- the frontier-LLM comparison (arithmetic only, but depends on the demo's real spend)")


if __name__ == "__main__":
    main()
