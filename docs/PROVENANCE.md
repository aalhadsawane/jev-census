# Provenance

Every number in the README, mapped to the exact command that produced it (T7.5, `06-P7-ADOPTION.md`:
"no number in the README that a command in the README cannot regenerate"). Run `scripts/readme_numbers.py`
to regenerate the ones that don't need a live API key; the ones that do are marked below.

| README number | Source | Live API key needed? |
|---|---|---|
| "1,910 Hacker News story titles" | `pq.ParquetFile('src/jev_census/demo_data/stories.parquet').metadata.num_rows` | No |
| Demo estimate block (`797.8K tokens ≈ $0.03`) | `census estimate --input src/jev_census/demo_data/stories.parquet --questions src/jev_census/demo_data/questions.yaml --id-field id` | No |
| Demo run (`$0.0569`, `45.3 docs/s`, `9,550 cells`) | `census demo` | Yes — real run, `DECISIONS.md`'s P7 entry records the exact run |
| Demo validation report (5 questions, 3 PASS, 1 exploratory) | `census demo`'s own `validation_report.md`, committed at `examples/hacker-news/README.md`'s result block | Yes (produced by the run above) |
| "43 seconds" (demo wall clock) | Timed end to end from a clean venv install through `census demo` completing; `DECISIONS.md`'s P7 entry has the full timed transcript (60s total including install) | Yes |
| Support-tickets estimate block (`30 documents`, `7.3K tokens`) | `census estimate --input examples/support-tickets/tickets.parquet --questions examples/support-tickets/questions.yaml --id-field ticket_id` | No |
| Support-tickets run (`spent $0.0007, 30 documents`) | `census run --input examples/support-tickets/tickets.parquet --questions examples/support-tickets/questions.yaml --budget 1 --out results --id-field ticket_id` | Yes |
| Support-tickets `cells.parquet` example rows (T-0001..T-0003) | `examples/support-tickets/results/cells.parquet`, the actual committed output of the run above | Yes (already run; file is committed) |
| Support-tickets validation report (`department` 0.87, `frustration` 0.80, etc.) | `examples/support-tickets/validation_report.md`, scored against `examples/support-tickets/gold/` | Yes (already run; file is committed) |
| Frontier-LLM comparison (`$8.37`, `147x`) | Backs out real input tokens from the demo's actual `$0.057` spend at Jev's published $0.042/MTok, then applies $3/MTok in + $15/MTok out at 150 assumed output tokens/document — full arithmetic shown inline in the README | No (arithmetic only, given the real spend figure above) |
| Long-form example (chunking, real bug) | `examples/long-form/README.md` and `DECISIONS.md`'s P7 entry | Yes (already run; files committed) |
| "12.2x cheaper... identical answers" (batching claim) | Cited from TypeSafe's own `/cookbooks/parallel_questions`, transcribed in `docs/00-JEV-API.md` | No — external citation, not this project's own measurement |
| Every phase's test count (e.g. "307 tests pass") | `pytest -q` from the repo root | No |

Numbers that are explicitly **not** claims — coverage estimates in the design docs (`docs/00-JEV-API.md`
labels these "design projections") are marked as such at the point of use and excluded from this table;
this table only covers what the README itself states as fact.
