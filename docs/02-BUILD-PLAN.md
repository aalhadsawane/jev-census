# 02 — Build Plan

The task list for the implementing agent. Tasks are ordered by dependency, not preference.
Do not start a phase before its predecessor's exit criteria are met.

Read `README.md` (what we are building), `00-JEV-API.md` (the contract), `01-DESIGN.md` (how).

**Rule zero: verify the contract empirically before building on an inference about it.** The first
version of this design assumed documents could be packed into one state, projected a 10x saving, and
was wrong — the API takes one state per request. It cost a rewrite. Five minutes of real calls would
have caught it. Before any component depends on behaviour you have not personally observed, spend the
cents to observe it.

---

## Phase map

| Phase | Delivers | Exit criteria | Est. | Status |
|---|---|---|---|---|
| **P0 Verify** | Confirmed API facts | Every claim in `00-JEV-API.md` confirmed or corrected, with evidence committed | 0.5 day | ✅ done |
| **P1 Skeleton** | 100 rows in → correct Parquet out | End-to-end run, all three question types, full provenance | 2 days | ✅ done |
| **P2 Durability** | Survives interruption | `kill -9` + resume is identical to a clean run | 2 days | ✅ done |
| **P3 Cost** | Knows the price before spending | `estimate` within 10% of actual | 1.5 days | ✅ done |
| **P4 Scale** | Runs a real corpus | 100k+ rows unattended without intervention | 2 days | ✅ done |
| **P5 Planner** | Projections and grouping | Two projections produce two calls per document | 1 day | next |
| **P6 Quality** | Trustworthy columns | Validation report with per-question accuracy | 3 days | |
| **P7 Adoption** | Installable and demoable | Clean clone → labelled results in 5 min | 2 days | |
| **P8 Launch** | Public analysis | Published with methodology and raw data | 3 days | |

Roughly three weeks of evenings. P6 is the phase that makes this a portfolio project rather than a
script; P8 is the phase that makes it visible. Neither works without P1–P5.

**The task tables below stay the index.** P0–P4 were built directly from them and the tables were
enough, because those phases build machinery with obvious right answers. P5–P8 have open design
decisions inside almost every task — how to id a projection, how to weight a stratified sample, what
`confidence_source` means on an aggregated cell — so each has an elaborated spec that makes those
decisions rather than leaving them to be re-derived:

| Phase | Spec | Holds |
|---|---|---|
| P5 | [`04-P5-PLANNER.md`](04-P5-PLANNER.md) | Call groups, projection ids, context limit, chunking and per-type aggregation |
| P6 | [`05-P6-QUALITY.md`](05-P6-QUALITY.md) | Stratification and reweighting, accuracy/ECE/threshold statistics, report format, schema-tune |
| P7 | [`06-P7-ADOPTION.md`](06-P7-ADOPTION.md) | Packaging traps, the bundled demo, example sets, executable recipes, README provenance |
| P8 | [`07-P8-EXECUTION.md`](07-P8-EXECUTION.md) | Run order and publication gates for the launch analysis (the *why* stays in `03-LAUNCH.md`) |

Read a phase's spec before starting its tasks. Where a spec says "decisions already made", they are
made — reopen one only with a reason worth writing into `DECISIONS.md`.

---

## P0 — Verify the contract

No shipping code. A notebook of real calls, findings committed to `docs/00-JEV-API.md`.
**Budget ~$1.**

| ID | Task | Depends | Done when |
|---|---|---|---|
| T0.1 | Call the API with one question of each type; record exact request and response JSON | — | All three answer shapes confirmed, especially that `noul` carries no `confidence` and no `probabilities` |
| T0.2 | Ramp question count in one call until something breaks (open question **B**) | T0.1 | Per-call question limit known, or confirmed absent |
| T0.3 | Two identical calls, short vs long question ids; compare `usage.input_tokens` (**G**) | T0.1 | Known whether ids are billed |
| T0.4 | Structured object state; reference field names in `instructions` | T0.1 | Confirmed the model can address named fields |
| T0.5 | Push concurrency until `429`; record limits and whether request- or token-rate binds (**D**) | T0.1 | Starting AIMD parameters chosen from data |
| T0.6 | Ask one question alone vs batched with others; compare answers | T0.1 | Batching independence reproduced in miniature |
| T0.7 | Update `00-JEV-API.md` with every finding | T0.1–T0.6 | Open questions B, D, G closed; F decided |

**Exit:** no claim in the design rests on an unverified assumption.

---

## P1 — Walking skeleton

One document per call, all questions batched, correct table out.

| ID | Task | Depends | Done when |
|---|---|---|---|
| T1.1 | Question set YAML schema + Pydantic models | T0.7 | The README's `support-triage.yaml` parses |
| T1.2 | Validator: type rules, criteria requirements, proposition check, counting/math/date lint | T1.1 | Each rule in `01-DESIGN.md` has a failing-case test |
| T1.3 | Deterministic hashing: `body_hash`, `questionset_hash` | T1.1 | Same input → same hash across processes (test asserts this) |
| T1.4 | Source readers: Parquet + CSV | — | Streams a 1M-row file at constant memory |
| T1.5 | Normalizer → `Document`, stable ids | T1.4 | Ids stable across two runs; content-hash fallback recorded in manifest |
| T1.6 | Jev client: auth, request build, typed errors. Transport only | T0.1 | Returns parsed response or a typed error; no retry logic inside |
| T1.7 | Decoder for all three answer shapes, incl. derived noul confidence | T1.6 | Fixtures for each type decode correctly; `confidence_source` set right; score `legend` preserved |
| T1.8 | Parquet writer with the full provenance column set | T1.7 | Every column in `01-DESIGN.md` output schema populated |
| T1.9 | `census run` minimal: `--limit N --budget X`, no concurrency | T1.1–T1.8 | 100 docs × 3 question types → correct table, under $0.05 |

**Exit:** `census run --limit 100` reproduces a slice of the README example.

---

## P2 — Durability

Nothing after this phase is safe to run at scale without it.

| ID | Task | Depends | Done when |
|---|---|---|---|
| T2.1 | SQLite cell cache, keyed per `01-DESIGN.md` | T1.9 | Re-running identical config makes zero API calls |
| T2.2 | Cache granularity test: add a question to an existing set | T2.1 | Exactly one question re-asked per document |
| T2.3 | Append-only shard writer with atomic finalisation | T1.8 | A killed run leaves no partially visible shard |
| T2.4 | Checkpoint: cursor + shard ledger, written atomically | T2.3 | Checkpoint always readable, never half-written |
| T2.5 | `--resume`, refusing on `questionset_hash` mismatch and naming changed questions | T2.4 | Resume works; a modified question set is refused with a clear message |
| T2.6 | Quarantine file for unprocessable rows | T1.5 | One corrupt row does not abort the run; it is counted in the summary |
| T2.7 | Chaos test: `kill -9` at a random point, resume, diff against clean run | T2.5 | Automated; zero duplicate and zero re-paid cells. **Actually kill the process** |

**Exit:** T2.7 passes repeatedly.

---

## P3 — Cost control

Build the brakes before the engine.

| ID | Task | Depends | Done when |
|---|---|---|---|
| T3.1 | Token estimator — pure, network-free, shared by estimate and planner | T1.1 | Unit-tested offline against known strings |
| T3.2 | `census estimate`: stratified sample, projection, **state/schema split** | T3.1 | Output matches the README's estimate block |
| T3.3 | Cost accounting in integer micro-dollars, charging attempts | T1.6 | Accounting matches summed `usage.input_tokens` exactly |
| T3.4 | Budget governor with admission control | T3.3 | Cap stops a run cleanly and resumably; overshoot bounded by in-flight calls |
| T3.5 | Estimator calibration against `usage.input_tokens`, auto-widening margin | T3.3 | Running actual/estimated ratio exposed in the run summary |

**Exit:** `estimate` predicts a real run within 10%, and the budget cap has never been exceeded by
more than one in-flight call.

---

## P4 — Scale

| ID | Task | Depends | Done when |
|---|---|---|---|
| T4.1 | Async worker pool with bounded queues and backpressure | T3.4 | A slow writer throttles the planner rather than growing memory |
| T4.2 | Retry classification per the failure table | T1.6 | Each row of the table has a fixture-driven test |
| T4.3 | AIMD adaptive concurrency | T4.2, T0.5 | Simulated `429` storm halves concurrency and recovers |
| T4.4 | Graceful shutdown on `SIGINT` and on budget breach | T4.1, T3.4 | Drains, finalises, checkpoints, exits non-zero with resume command |
| T4.5 | Progress display: docs done, spend vs cap, throughput, concurrency, cache rate, ETA | T4.1 | Cached and paid documents counted separately |

**Exit:** a 100k+ row run completes unattended.

---

## P5 — Planner and projections

Spec: [`04-P5-PLANNER.md`](04-P5-PLANNER.md).

| ID | Task | Depends | Done when |
|---|---|---|---|
| T5.1 | `projection` in the question schema, with defaults | T1.1 | Per-question override parses; a typo'd field aborts on document 1 instead of sending `null` |
| T5.2 | Grouping by projection; `CallGroup` construction | T5.1 | Deterministic, unit-tested, no network |
| T5.2b | **Verify the real context limit against the live API** | T5.2 | `CONTEXT_LIMIT_TOKENS` set from observation; `00-JEV-API.md` updated in the same commit |
| T5.3 | Group-count reporting in `estimate` and `run` | T5.2, T3.2 | Prints `N questions · M call groups · state sent Mx per document`; single-group output unchanged |
| T5.4 | Long-document chunking and per-type aggregation | T1.7, T5.2b | `chunk_count` set; aggregated cells marked `confidence_source=derived` and excluded from validation samples |

**Exit:** a two-projection question set produces two calls per document, each state carrying only its
group's fields — asserted by the *absence* of the other group's fields, not by call count alone.

---

## P6 — Quality *(the differentiator)*

This phase is what separates the project from a script. Do not compress it.

Spec: [`05-P6-QUALITY.md`](05-P6-QUALITY.md). Read it before T6.1 — the sampling weights it defines
are load-bearing for every statistic in T6.2–T6.4, and a report built without them looks correct and
is not.

| ID | Task | Depends | Done when |
|---|---|---|---|
| T6.1 | `census label`: stratified sample across the probability range | T2.1 | Sample is not uniform; strata and per-stratum weights written into the labelling CSV |
| T6.2 | Accuracy scoring against a gold CSV, with sample size and interval | T6.1 | Per question, never aggregated across types; weighted by stratum, Wilson interval on Kish `n_eff` |
| T6.3 | Expected calibration error + reliability diagram data | T6.2 | Choice and score on model confidence; noul on its own probability scale, in a separate field |
| T6.4 | Per-question, per-type threshold recommendation with coverage | T6.3 | Noul thresholds on distance-from-0.5; never reused across types; minimum-surviving-sample guard |
| T6.5 | `validation_report.md` generator with pass/fail against `gate` | T6.4 | Reproduces the README's report block; non-zero exit only when a `strict` question fails |
| T6.6 | Review queue export + human-answer re-import as a separate override layer | T6.4 | Overrides never merged into model output columns — asserted by byte-identity of `cells.parquet` |
| T6.7 | `census schema-tune`: variant evaluation, per-type agreement, cost delta | T6.2, T3.2 | Produces a real accuracy-vs-verbosity curve, committed. Closes open question **H** |
| T6.8 | **Projection ablation: projected vs full-record state on the same question** | T6.2 | Closes open question **I**, which `00-JEV-API.md` already assigns to this phase |

**Exit:** every published column has a measured accuracy behind it. T6.7's curve is the most
publishable artifact in the repository — nobody in the Jev ecosystem has measured it.

---

## P7 — Adoption

Spec: [`06-P7-ADOPTION.md`](06-P7-ADOPTION.md).

| ID | Task | Depends | Done when |
|---|---|---|---|
| T7.1 | Package with `uv`; `pip install jev-census` works from a clean venv | P6 | Installs and runs on a machine that never had the source |
| T7.2 | One-command demo on a small public dataset, bundled question set | T7.1 | Clean clone → labelled results in under 5 minutes |
| T7.3 | Example question sets for 3 common corpora | T7.2 | Each has a committed validation report |
| T7.4 | DuckDB recipe docs: long→wide, thresholding, time series | T7.1 | Copy-pasteable against the demo output |
| T7.5 | README updated with verified numbers, each reproducible by a command in it | P6 | No number present that a command cannot regenerate |

**Exit:** a stranger can use it without reading source.

---

## P8 — Launch

Strategy in [`03-LAUNCH.md`](03-LAUNCH.md); run order and publication gates in
[`07-P8-EXECUTION.md`](07-P8-EXECUTION.md). Do not start before P6.

| ID | Task | Depends | Done when |
|---|---|---|---|
| T8.1 | Pilot 1,000 rows; read answers by hand; revise questions | P7 | Question set survives contact with real data |
| T8.2 | Gold set + validation for every question to be published | T8.1 | Failures identified and accepted as disclosures |
| T8.3 | Full run within budget | T8.2 | Actual spend and wall-clock recorded |
| T8.4 | Analysis page + raw data + methodology | T8.3 | Charts legible on a phone; accuracy stated beside each |
| T8.5 | Distribution: repo public, awesome-list PRs, Show HN, social post | T8.4 | Sequenced per `03-LAUNCH.md` |

---

## Hard rules

**Contract.** Verify before depending. Never put more than one document in a state — put a comment
where someone would be tempted. Never assume structural invariants: no `P(x) + P(not x) = 1`, no
threshold carried from a noul to a choice.

**Money.** Every spending path behind an explicit budget; no debug flag bypasses it. Integer
micro-dollars, never floats. Charge attempts, not successes. **Tests never make live API calls** — a
suite that spends money gets run less often, which defeats the point.

**Correctness.** Never collapse a distribution on the write path. Never write a partial call's
results. Never present a derived noul confidence as model-reported. Never truncate silently. Never
write a partial shard as complete. Hash deterministically — stable key order, normalised whitespace,
explicit UTF-8.

**Process.** Data errors quarantine and continue; config errors (`401`, `422`) abort immediately.
Record decisions — group count, calibration ratio, thresholds, concurrency changes — in the manifest,
not just stdout. The engine never branches on a model answer.

---

## Testing

- **Unit, no network:** planner, grouping, estimator, hashing, cache keys, budget arithmetic, per-type
  decoding. These are pure by design.
- **Fixtures:** clean multi-type call; `429`; `529`; `422`; response missing a requested question id;
  response with a type mismatch; a noul answer (assert no confidence is invented silently); a score
  answer (assert legend and index-keyed probabilities survive).
- **Property tests:** every question in exactly one group; no call over the token limit; decoding
  returns exactly the requested ids; cache key stable across processes.
- **Chaos:** T2.7, automated.
- **One opt-in live smoke test**, a few cents, before release, never in CI by default.

---

## Traps

| Trap | Defence |
|---|---|
| Benchmarking cost on long documents | Benchmark on **short** ones, where schema is 90%+ of spend |
| Local token estimates drifting from the provider | Conservative margin; calibrate against `usage.input_tokens` (T3.5) |
| A uniform `confidence` column quietly erasing the noul distinction | `confidence_source` column + a fixture asserting it |
| Dropping the score `legend` | Results become uninterpretable once the question set changes |
| Context rot — invisible on small samples, costly at scale | Projections (P5); open question **I** measures it |
| Retries invisible in cost accounting | Instrument attempts from T3.3 |
| Cache keys containing run-varying data | Cache that never hits while appearing to work — test key stability |
| Uniform validation sampling | Easy cases, flattering number — stratify (T6.1) |
| Progress implying spend when serving cache | Count cached and paid separately (T4.5) |

---

## Stack

Python 3.11+ · `typesafe-sdk` (`pip install typesafe-sdk`, plain PyPI, no custom index — confirmed
live 2026-09-20) wrapping the client: `AsyncTypeSafeClient` exists and is the one to build the
Scheduler on, with a `RetryPolicy` the SDK already exposes · Pydantic for schemas · PyArrow for
Parquet · DuckDB for recipes · SQLite for cache · Typer + Rich · `uv`, `ruff`, `pytest` +
`pytest-asyncio` · strict type checking in CI.

`Call` and `Cell` are different granularities with similar names; the type checker is what stops you
conflating them.

---

## Discipline

Small, focused commits — the history is part of what a reviewing employer reads. When the live API
contradicts a document, fix the document in the same commit as the code. Record resolved open
questions in `00-JEV-API.md` with evidence. Keep `DECISIONS.md` for divergences from these documents.
Every number in the README must be reproducible by a command in the README.

Do not build, without revisiting the anti-goals: a web UI, a hosted service, multi-provider
abstraction, LLM-generated questions, agentic workflows, a plugin system, distributed execution, a
query language, streaming modes, or multi-document packing.
