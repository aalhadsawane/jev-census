# Decisions

Divergences from the design docs, and why. Newest first.

---

## 2026-09-21 — Phase P6 (quality) infrastructure complete and live-verified; gold-labelling is not

Built `sampling.py` (T6.1), `scoring.py` (T6.2-T6.4), `report.py` (T6.5), `overrides.py` (T6.6),
`schema_tune.py` (T6.7), and `ablation.py` (T6.8), plus the CLI surface: `census label`, `validate`,
`report`, `review export`/`import`, `schema-tune`, `ablation`. 297 tests pass (up from 219 at the end
of P5), including a full end-to-end integration test (`test_p6_integration.py`) driving the whole
`run → label → validate → report → review export → review import` cycle through Typer's `CliRunner`
against the fake client, plus live runs of `schema-tune` and `ablation` against the real API.

**Read this before trusting the phase-exit claim below.** `05-P6-QUALITY.md`'s literal exit criterion
is "every question in the demo question set has a gold set of ≥200 stratified labels." That number of
labels requires sustained, genuine human judgment against real ticket content — it is not something
this session produced, and nothing here should be read as claiming it did. What *is* done, and
verified: every module and CLI command works correctly end-to-end, proven against small live-API runs
where the "gold" labels were either self-consistent-with-the-model (explicitly for pipeline-mechanics
testing only, never presented as validation) or, for T6.8, genuinely authored by hand with real
judgment on deliberately simple synthetic tickets. Producing a real 200+-item gold set per question for
the actual demo corpus is the honest remaining gap before P6's literal exit criterion is met, and it is
a human task, not an engineering one — flagged explicitly rather than papered over with fabricated
labels.

**Real bugs found while building, not just new features:**

1. **A genuine shape mismatch, not a fixture bug.** `overrides.py`'s `export_review_queue` called
   `scoring.threshold_value`, which expects label-CSV row keys (`model_noul`), against raw cells from
   `read_run_cells`, which use cells.parquet's own key names (`noul`). These are two different row
   shapes that happen to share a similar purpose; forcing one function to silently accept both would
   have been the actual bug. Fixed with a small cell-shaped variant local to `overrides.py`, documented
   as a *shape* distinction, not a naming accident.
2. **A `sed`-driven rename corrupted a string literal.** Renaming the private helper `_modal_level` to
   the public `modal_level` (needed once `overrides.py` also required it) used a blind
   `sed 's/_modal_level/modal_level/g'`, which also matched the substring `_modal_level` inside the
   unrelated string literal `"model_modal_level"` (a label-CSV column name), corrupting it to
   `"modelmodal_level"` in two places. Caught immediately by the full test suite failing; fixed by hand
   and confirmed no other renames in the same session collided the same way. Worth remembering: a
   blind rename-by-substring is unsafe near any name that is itself a substring of another identifier.
3. **`reservoir_sample_corpus` didn't create its own output directory.** Surfaced immediately on the
   first live `schema-tune` run (`FileNotFoundError` writing the sampled sub-corpus) — the function
   assumed the caller had already created `results/schema_tune_runs/`; fixed to create it itself,
   matching every other writer in this codebase.
4. **Two test-fixture bugs, not code bugs, in the integration tests**, both caught by their own
   assertions rather than silently passing: the synthetic "wrong answer" branch in
   `test_p6_integration.py` used a modulus that fully overlapped the "unclear" branch's, so it never
   actually fired — every question scored a trivial 1.00 until a `not all(acc == "1.00")` assertion was
   added and the modulus separated. A second fixture used a placeholder `"OTHER_OPTION"` as a
   deliberately-wrong `choice` gold answer, which the gold-file hygiene check (T6.2) correctly rejected
   as not a real option — exactly the check working as designed, not a bug in it.

**Design decisions made while implementing, not fully specified in `05-P6-QUALITY.md`:**

- The manifest gained two fields — `id_field` and `projection_groups` (`{projection_id: [fields]}`) —
  in both `scheduler.py`'s and `runner.py`'s manifest writers. `census label` needs to re-stream the
  original source corpus to recover a question's projection-field values (cells.parquet only stores
  answers, never source text) and needs to know which fields belong to a given `projection_id` without
  re-reading the original question set YAML. `04-P5-PLANNER.md` had already named this as a P5 decision
  ("readability is recovered by writing the map into manifest.json") but P5's own implementation never
  actually added it — closed here since P6 is the first phase that needs it.
- `review_queue.csv`'s column shape deliberately diverges from the label CSV's: no `stratum`/
  `stratum_weight` (meaningless outside a validation sample) and `human_answer` instead of
  `gold_answer`/`notes` (the review queue is a human deciding what a production answer *should be*,
  not building a validation gold set — different vocabulary for a different task, even though
  `05-P6-QUALITY.md`'s prose reads as "the same shape plus one column").
- `census schema-tune` and `census ablation` are both implemented as the Scheduler run twice (or more)
  against a small sampled sub-corpus sharing one `--census-dir`, rather than a parallel call-execution
  path — an unchanged variant's matching questions hit the cache for free, for the same reason a
  resumed `census run` does.

**Live-verified, closing open questions H and I** in `00-JEV-API.md` with real measurements (full
detail there): criteria verbosity cost nothing for `department` (agreement 1.000) but real accuracy for
`is_urgent` when criteria were dropped entirely (agreement 0.900, below the 0.97 floor). The projection
ablation, run against a deliberately constructed 12-document sample where a question's needed signal
lived outside its declared projection, scored 0.50 (structurally blind) against 1.00 for full-record —
not a case against projections, but a real, measured demonstration of what happens when one is scoped
too narrowly for what its question actually needs.

---

## 2026-09-20 — Phase P5 (planner) complete, live-verified including real chunking

Built `planner.py` (T5.1, T5.2, T5.2b) and `chunking.py` (T5.4): `_process_one_document` in
`scheduler.py` now loops over real `CallGroup`s instead of one hardcoded `projection_id="p0"` union
of every question's fields. `runner.py` (the sequential reference implementation) is deliberately
untouched — it stays frozen at single-group planning, per `04-P5-PLANNER.md`.

**T5.2b, measured before anything depended on it (Rule Zero).** Ramped a single-field state against
a minimal `noul` question until the live API refused: 163,000 chars / 32,872 `usage.input_tokens`
succeeded; 164,000 chars failed with a plain `400 max_tokens_exceeded` — not a `422`. That matters:
`client.py` classifies `400` as fatal (same bucket as `401`/`422`), so a context overflow that
reaches the API aborts the whole run rather than being retried or quarantined. `01-DESIGN.md`'s
"chunk or quarantine" is therefore enforced entirely *proactively* by the planner, never reactively.
Recorded in `00-JEV-API.md`; `CONTEXT_LIMIT_TOKENS = 32_768` in `planner.py`.

**Real bug found while building T5.4, not caught by the unit tests first.** `chunk_document`'s
original chunk-sizing math left room for only the group's *cheapest* question, on the theory that
`split_for_context` would handle batching the rest. It doesn't work: once a chunk is sized that
tight, any question in the group *larger* than the cheapest one legitimately can't fit alongside it,
and `split_for_context` (correctly) raises `ContextOverflowError` for it — not because the document
is unchunkable, but because the chunk itself was undersized. Fixed by sizing chunks against the
group's *largest* question instead, which guarantees every question fits in at least one call per
chunk. Caught by an end-to-end scheduler test (`test_long_document_is_chunked_and_aggregated_end_to_end`)
that exercises the real pipeline rather than `chunking.py` in isolation — the unit tests alone, which
each picked a single question size, never exposed the interaction between the two functions.

A second, smaller issue surfaced by the same test: `chunk_document`'s field-budget arithmetic summed
each field's token estimate independently, but a merged JSON object's real token count isn't
perfectly additive (structural overhead: braces, keys, commas), so a chunk sized right at the
boundary could overshoot by a handful of tokens. Fixed by measuring the "other fields" overhead
against the actual combined-state estimate rather than assuming additivity, plus a flat 64-token
safety buffer on top of the already-applied 15% margin.

**A test-comparison artifact, not a runner bug, initially looked like real corruption.**
`test_chaos.py`'s "clean" comparison run used `runner.py`'s sequential `run_census` (still
`projection_id="p0"`), while the killed/resumed run goes through the Scheduler, which now assigns
real hash-based projection ids. Every cell's `projection_id` differed between the two, producing a
huge, alarming diff that briefly looked like the aggregation path was corrupting `choice` answers.
Root-caused by reproducing outside pytest and correlating with `chunk_count`/`confidence_source` in
the diff. Fixed by switching the "clean" comparison to `run_scheduled` (the same code path `census
run` actually uses, and the same one `test_sigint.py` already used) — comparing against the frozen
sequential reference implementation stopped being meaningful for a property (`projection_id` naming)
that legitimately differs between them now.

**Live-verified three ways**, not just against the fake client:
1. `census estimate` on a 50-row corpus with a real two-projection question set reproduced the
   worked example's exact output shape: `3 questions · 2 call groups · state sent 2x per document`,
   with per-group field/token breakdown lines.
2. The same corpus run live end to end: the two-projection set cost $0.0018 (42,584 input tokens)
   against $0.0012 (27,847 tokens) for the same corpus with the original single-projection set — a
   real 53% cost increase from sending the document twice, confirming the multiplier is not just a
   printed number.
3. A single real document (173,800 chars, well over the measured limit) run against the live API:
   split into 2 real chunks, all 4 questions correctly aggregated back to one cell each, every cell
   marked `chunk_count=2` and `confidence_source="derived"` — including the `choice` and `score`
   questions, whose per-chunk confidence was model-reported but whose aggregate, correctly, is not.

219 tests pass (up from 179 at the end of P4), including 19 new tests specific to P5 (planner
grouping/splitting properties, chunk aggregation per type against hand-computed values, and two
scheduler-level integration tests — real multi-chunk aggregation and the quarantine path — that the
unit-level tests alone would not have caught).

---

## 2026-09-20 — Phase P4 (scale) complete, 120k rows unattended

Built `client.py`'s `AsyncJevClient`, `failure.py`, `retry.py`, `aimd.py`, and `scheduler.py` (T4.1–T4.5):
the concurrent replacement for `runner.py`'s sequential loop. `runner.py` itself is untouched except for
one additive field (`RunResult.interrupted`, default `False`) — it remains the pure sequential reference
implementation, still fully tested, no longer the CLI's execution path.

**Concurrency model.** A bounded `asyncio.Queue` between the document producer and a pool of worker
coroutines gives backpressure: a full queue blocks the producer's `put()`, capping memory regardless of
corpus size, rather than a slow API throttling nothing while the queue (and memory) grows unbounded.
Each worker processes one document end to end — cache lookup, the API call (or a backoff sleep), decode,
cache write, shard write. Every one of those steps except the call itself is synchronous, and therefore
atomic with respect to every other worker under asyncio's cooperative single-threaded scheduling — no
lock was needed around the shared `CellCache` or `ShardWriter`. This wasn't the original plan (a
three-stage producer/worker/writer pipeline with a dedicated single writer task was); the simpler
one-worker-does-everything design turned out to be correct by construction once the "only one coroutine's
Python code ever runs at a time, and neither shared object ever awaits mid-operation" property was
recognized, and it avoided a whole extra queue and coordination layer.

**Admission control under real concurrency.** `01-DESIGN.md` already anticipated this: "overshoot is
bounded by in-flight concurrency," not by one call. Several workers can pass the budget check before any
of them updates the shared spend counter — intentional, not a race to fix.

**T4.4, tested with the same rigor as T2.7's `kill -9`:** a dedicated test sends a real `SIGINT` to a
subprocess mid-run, verifies exit code 130 and a printed resume command, then resumes and diffs the
result against a clean uninterrupted run. Stress-tested 10/10 clean, same as the chaos test.

**P4's exit criterion, verified directly, not inferred:** generated a 120,000-row synthetic corpus and
ran it unattended through the CLI (fake client, for speed — this validates pipeline mechanics, not live
cost) at concurrency ceiling 32. Completed in ~230 seconds, exit code 0, 480,000 cells written with zero
duplicates across 120,000 unique documents. Also live-verified against the real API on a small sample,
including the live progress line.

---

## 2026-09-20 — Phase P3 (cost control) complete, calibrated against the real API

Built `money.py`, `token_estimator.py`, `estimate.py` (`census estimate`), and calibration tracking in
`cache.py` (T3.1–T3.5). `runner.py`'s admission control now projects each call's cost with the
estimator before making it, rather than only checking spend-so-far.

Two real bugs found and fixed while building this phase, not just new features:

1. **Accounting didn't charge failed-decode attempts.** A call whose response failed to decode
   (`DecodeError`) was skipped without charging its tokens, even though the API had already consumed
   them. Fixed by charging `total_input_tokens_charged` immediately after a response comes back, before
   decoding is even attempted — matching `01-DESIGN.md`'s "charge attempts, not successes" literally, not
   just in spirit.
2. **`cache_hits`/`cache_misses` counted documents admission control then rejected.** They were
   incremented before the budget check, so a document rejected by the new projected-cost admission
   control still inflated "N newly asked" in the run summary. Moved the counters to only fire once a
   document is actually committed (decoded successfully, or a full cache hit).

**Calibration, live-verified:** ran `census run` against the real TypeSafe API and read back the
reported ratio — 2.27x. The char-based estimator meaningfully undercounts Jev's real tokenizer, which is
exactly the drift `calibration_ratio()` exists to surface; nothing here silently assumes the heuristic is
accurate. Admission control's overshoot bound holds regardless of estimator accuracy, since P1–P3 has no
concurrency yet: every call is checked individually before being admitted, so overshoot is bounded to at
most one call by construction, not by how good the estimate is.

**A chaos-test bug, not a runner bug**, surfaced while stress-testing this phase's changes: T2.7's
clean-vs-resumed comparison intermittently failed (~1 in 5–8 runs). Traced it — by comparing raw on-disk
Parquet output directly, bypassing the test's own comparison helper — to `input_tokens` not being
excluded from the diff, alongside `call_id`/`run_id`/`ts`. `input_tokens` is the *whole call's* token
count duplicated onto every cell that call answered; a resumed run legitimately batches a document's
still-missing questions differently than a clean run batches all of them together, so the same correct
decision can carry a different `input_tokens` value. The actual decision values never differed. Fixed the
comparison; stress-tested 15/15 clean afterward.

---

## 2026-09-20 — Phase P2 (durability) complete, chaos test passing

Built `cache.py`, `shard_writer.py`, `checkpoint.py`, `quarantine.py`, and `runner.py` (T2.1–T2.7),
which replaces T1.9's inline `cli.py` loop with a durable one. `cli.py` is now a thin Typer wrapper.

One divergence worth recording: `shard_writer.py`'s original slicing behaviour (finalize exactly
`shard_size` cells at a time, from T2.3's first commit) could split a single document's cells across
two shards if `shard_size` wasn't a multiple of the per-document cell count. That breaks resume's
correctness, because resume decides "already done" by reading which `doc_id`s are already durably
written to a finalized shard — a split document would look half-done. Changed to atomic-per-call
semantics: a single `add()` call's cells always land in one shard together, never split. The runner
calls `add()` once per document with that document's whole cell group, so this makes the invariant hold
by construction rather than by convention.

A second decision, not a divergence: resume correctness is decided by reading finalized shards'
`doc_id`s directly, not by trusting `checkpoint.json`'s `cursor` number. Finalizing a shard and writing
the checkpoint are two separate steps, and a crash between them must never produce a duplicate cell —
membership-in-an-actual-shard is the source of truth; the cursor is informational only. Every answer is
cached at decode time, before it is ever written to a shard, so a document reprocessed after a crash is
re-derived from cache rather than re-paid for.

T2.7 (the phase exit criterion) runs `census run` as a real OS subprocess, `kill -9`s it mid-run
(`CENSUS_FAKE_CLIENT=1` swaps in a deterministic offline fake client — no network, no cost, per the
Testing rules), resumes it, and diffs the result against a clean uninterrupted run of the same corpus.
Verified non-flaky across repeated runs: zero duplicate cells, zero re-paid cells, identical final
table. Also live-verified against the real API: a fresh run through the new runner, then an identical
re-run that made zero new API calls (cache hit on all 12 cells, $0.0000 spent).

---

## 2026-09-20 — Phase P1 (walking skeleton) complete, live-verified

Built `hashing.py`, `question_set.py`, `validator.py`, `sources.py`, `normalizer.py`, `client.py`,
`decoder.py`, `cell.py`, `writer.py`, and `census run` (T1.1–T1.9), packaged as `jev-census` with a
`pyproject.toml` (replacing `requirements.txt`). 67 unit tests, all network-free per the Testing rule in
`02-BUILD-PLAN.md`.

`census run --input tickets.parquet --questions support-triage.yaml --budget 0.05 --out results/
--limit 100 --id-field ticket_id` was run live against the direct API on a 100-row synthetic ticket
corpus: 400 cells (100 docs × 4 questions), 0 documents skipped, actual spend $0.0023 — the P1 exit
criterion ("`census run --limit 100` reproduces a slice of the README example") is met.

No deviation from the design docs. Open questions B, D, G (per-call question limit, real rate-limit
ceiling, whether question ids are billed) remain open and unblock nothing in P1 — deferred to their
assigned tasks in P2–P4.

---

## 2026-09-20 — Direct TypeSafe API confirmed as the implementation path

TypeSafe granted API access. `api.typesafe.ai` is reachable directly with the official `typesafe-sdk`
Python package and a `TYPESAFE_API_KEY`. All 6 planned contract cases (`noul` × 2, `choice`, `score`,
a mixed-type batch, structured object state) succeeded live, with no rate limit encountered — findings
folded into `docs/00-JEV-API.md`.

Build on this directly, in Python, per the original design docs. The only correction needed was one
detail in the stack section: `typesafe-sdk` installs from plain PyPI, no custom index.

---

## 2026-09-19 — Rejected multi-document packing

Recorded in full in `docs/00-JEV-API.md` §6, not repeated here: packing several documents into one
`state` to amortise the question schema was the project's original headline optimization. The API
takes exactly one `state` per request with all questions answered against it, so per-document answers
need per-document questions — packing saves zero tokens and actively costs accuracy (documented
context rot). Replaced by call planning and schema economy as the project's core technical
contribution.
