# 04 — Guide for the Implementing Agent

> You are implementing `cartograph`. This says what to build, in what order, what the rules are, and
> which specific mistakes cost money or silently corrupt results.
>
> Read `00-API-NOTES.md` and `01-MOTIVATION.md` first. If you follow the rules without understanding
> the drivers, you will obey them right up until a driver actually matters, then break one because it
> looked locally convenient.

---

## Rule zero

**Verify the contract empirically before building on an inference about it.**

This project's first design was built on a plausible assumption about the API that turned out to be
wrong, and its headline claim — a 10x cost saving from packing documents into one state — did not
exist. The correction is in `00-API-NOTES.md` §6.

It was caught by reading the reference properly. It would have been caught even earlier by five
minutes of real API calls. Before any component depends on a behaviour you have not personally
observed, spend the few cents to observe it.

---

## Before the first line of code

1. Read `00-API-NOTES.md`, `01-MOTIVATION.md`, then `03-DESIGN.md` §13.
2. Re-fetch the live docs: `https://docs.typesafe.ai/llms.txt`, `/api.md`, `/concepts/state.md`,
   `/confidence.md`, `/model-jaggedness/jev-1.13.md`, `/cookbooks/parallel_questions.md`. The notes
   here are a **2026-09-19 snapshot of a fast-moving product**. Where the live API disagrees, the API
   wins and you update the document in the same commit.
3. Confirm `TYPESAFE_API_KEY` is in the environment. Never read it from a file, never log it, never
   write it into a manifest.
4. Do M0 before designing anything.

---

## Build order

Each milestone is independently demonstrable. Do not start one before its predecessor's definition of
done is met. The order puts **money-spending capability after the controls that contain it**.

### M0 — Contract spike *(budget: ~$1)*
Not a component. A notebook of real calls that answers, with evidence committed to
`docs/00-API-NOTES.md`:
- Open question **B**: is there a per-call question limit? Ramp up until something breaks.
- Open question **G**: are question ids billed? Compare `usage.input_tokens` for identical calls with
  short versus long ids.
- Confirm all three answer shapes, especially that `noul` carries no `confidence` and no
  `probabilities`.
- Confirm a structured-object state round-trips and that field names are usable in `instructions`.
- Reproduce the batching result in miniature: same question alone versus batched with others, same
  answer.

*Done when:* every claim in `00-API-NOTES.md` §1–§5 is either confirmed or corrected, with the
evidence committed.

### M1 — Walking skeleton
One document per call, all questions batched, all three types decoded, Parquet out with full
provenance.

*Done when:* an end-to-end run over 100 documents with at least one question of each type produces a
correct table with every column from `03-DESIGN.md` §6 populated, `confidence_source` correctly set,
and score `legend` preserved. Under $0.05.

### M2 — Durability
Cell-granularity cache, atomic checkpoints, append-only shards, resume, quarantine.

*Done when:* `kill -9` mid-run then resume yields output identical to an uninterrupted run, zero
duplicate and zero re-paid cells; adding a second question re-asks exactly one question per document.
**Actually kill the process; do not simulate it.**

### M3 — Estimation
`cartograph estimate`: stratified sampling, local token estimation, and the **state/schema split**.

*Done when:* the projection lands within 10% of an M1-style run's actual cost, and the split makes it
obvious whether documents or questions dominate.

*Why before the planner:* you cannot responsibly build the thing that spends unattended until you can
predict what it spends.

### M4 — Call planner and projections
Projection declarations, grouping, per-group state construction, group-count reporting.

*Done when:* a question set with two distinct projections produces two calls per document, each state
containing only its group's fields; grouping is deterministic and unit-tested with no network.

### M5 — Scheduler and budget control
Adaptive concurrency, retry classification per the failure table, budget governor, graceful shutdown.

*Done when:* a simulated `429` storm halves concurrency and recovers; the cap stops a run cleanly and
resumably; spend accounting matches reported usage to the micro-dollar; `SIGINT` drains, finalises and
checkpoints.

### M6 — Schema economy *(the core contribution)*
`cartograph schema-tune`: variant evaluation against a reference, per-type agreement metrics, cost
delta, the accuracy-versus-verbosity table.

*Done when:* it produces a real measured curve for a real question set, and that curve is committed.
This is criterion 3 in `01-MOTIVATION.md` and the most publishable thing in the repository.

### M7 — Validation and calibration
`label`, `validate`, per-question per-type thresholds, the Markdown report.

*Done when:* the report gives per-question accuracy with sample sizes and intervals, calibration
error, and recommended thresholds with coverage — never a single cross-type headline number (D6).

### M8 — Adoption polish
README with a verified headline number, a genuine one-command demo on a small public dataset,
installable package, DuckDB recipes, progress display.

*Done when:* a new user goes from install to labelled results on their own CSV in under five minutes
without reading source code.

### M9 — Flagship
See `05-FLAGSHIP.md`. Not before M7. Publishing corpus-scale claims without a validation report is
the one failure mode that damages the project rather than delaying it.

---

## Hard rules

**Contract**
1. Verify before depending. Rule zero.
2. **Never put more than one document in a state.** Zero token saving, documented accuracy cost. Put a
   comment where someone would be tempted, because it looks like an obvious win.
3. Never assume structural invariants: no `P(x) + P(not x) = 1`, no threshold carried from a noul to a
   choice, no arithmetic between separate questions.

**Money**
4. Every spending path is behind an explicit budget. No debug flag bypasses it.
5. Integer micro-dollars. Never floats for money.
6. Charge attempts, not successes.
7. Unit and integration tests never make live API calls. A suite that spends money gets run less
   often, which is the opposite of the point.

**Correctness**
8. Never collapse a distribution on the write path (D5).
9. Never write a partial call's results. All requested question ids present and type-matched, or
   discard and retry.
10. Never present a derived noul confidence as model-reported. `confidence_source` is mandatory.
11. Never silently truncate a document. Chunk or quarantine, and record which.
12. Never write a partially complete shard as complete. Atomic finalisation only.
13. Hash deterministically: stable key order, normalised whitespace, explicit UTF-8. A hash that
    varies across processes silently disables the cache.
14. Refuse to resume when `questionset_hash` changed; name what changed.

**Process**
15. A data error never aborts a long run — quarantine and continue.
16. A config error (`401`, `422`) always aborts immediately, before the money.
17. Record decisions — group count, calibration ratio, concurrency changes, chosen thresholds — into
    the manifest, not just stdout. Stdout is lost; the manifest is evidence (D9).
18. The engine never branches on a model answer (D10).

---

## Testing

**Unit, no network.** Planner, projection grouping, estimator, hashing, cache keys, budget
arithmetic, per-type decoding. These are pure by design — keep them that way.

**Fixture-based integration.** Record real responses once, replay forever. Required fixtures: a clean
multi-type call; a `429`; a `529`; a `422`; a response **missing a requested question id**; a response
with a **type mismatch**; a noul answer (to assert no confidence is invented silently); a score answer
(to assert legend and index-keyed probabilities survive).

**Property tests.** Every question appears in exactly one call group. No call exceeds the token limit.
Decoding a well-formed response yields exactly the requested question ids. Cache key stability across
processes.

**Chaos test.** `kill -9` at a random point in a multi-shard run, resume, compare to a clean run.
Automate it; it is what proves M2.

**One opt-in live smoke test**, a few cents, before release, never in CI by default.

---

## Traps

**The schema dominates on short documents.** Benchmark the cost model on *short* documents. On long
ones the document dominates and you will conclude the schema is irrelevant — true for that corpus,
badly wrong for the flagship one.

**Estimation drift.** Local token counts will not match the provider's. Low means context overflow and
failed calls; high costs a little efficiency. Be conservative and calibrate against
`usage.input_tokens` during the run.

**Noul has no confidence, and it is easy to paper over.** The moment someone adds a uniform
`confidence` column, the distinction vanishes and the validation report becomes misleading. The
`confidence_source` column and a fixture asserting it are the defence.

**Score probabilities are keyed by level index, not name.** Drop the `legend` and the results become
uninterpretable the moment the question set changes.

**Context rot is invisible in testing.** Sending the whole record to every question works fine on
small samples and quietly costs accuracy at scale. Projections are the defence; open question **I**
is how you would prove it.

**Retries are invisible in cost accounting until the bill arrives.** Instrument attempts from M5.

**Cache keys containing run-varying data** produce a cache that never hits while appearing to work.
Assert key stability across processes in a test.

**Uniform sampling for validation** yields easy cases and a flattering number that will not survive
publication. Stratify across the probability range.

**Progress that lies.** With caching, "documents processed" and "documents paid for" diverge sharply.
Show both.

**Questions that ask for counting, arithmetic or date comparison.** The model's best-documented
failure modes. The validator should reject them at authoring time rather than letting a user discover
it across five million rows.

---

## Stack

Recommendations; deviate with the reason in the commit message.

- **Python 3.11+** — where this project's users already are.
- **Async HTTP** with a bounded pool. Wrap the official SDK (`typesafe-sdk`, installed from
  `--extra-index-url https://pypi.typesafe.ai/`) if it exposes async and retries cleanly; drop to raw
  HTTP if it constrains concurrency control.
- **Pydantic** for the question-set schema and API models — its validation messages are part of the UX
  here.
- **PyArrow** for Parquet, **DuckDB** for analysis recipes, **SQLite** for the cache. Do not invent a
  file format.
- **Typer/Click** plus **Rich**.
- `uv`, `ruff`, `pytest` with `pytest-asyncio`.
- Type hints everywhere and strict checking in CI. `Call` and `Cell` are different granularities with
  similar names, and the type checker is what stops you conflating them.

---

## Scope discipline

Do not build without revisiting the anti-goals in `01-MOTIVATION.md`: a web UI, a hosted service,
multi-provider abstraction, LLM-generated questions, agentic workflows, a plugin system, distributed
execution, a custom query language, streaming or interactive modes, or multi-document packing.

When something seems obviously worth adding, check the anti-goals table first. The project's
coherence — and much of its value as a portfolio piece — comes from doing one thing at a scale where
the engineering genuinely matters.

---

## Commit and documentation discipline

- Small, focused commits. The history is part of what a reviewing employer reads.
- When the live API contradicts these documents, fix the document in the same commit as the code.
  Stale design docs are worse than none.
- Record each resolved item from `03-DESIGN.md` §13 there, with evidence.
- Every measured number in the README must be reproducible by a command in the README. If you cannot
  produce the command, delete the number.
- Keep `DECISIONS.md` for divergences from these documents, with reasoning.
