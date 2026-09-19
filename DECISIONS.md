# Decisions

Divergences from the design docs, and why. Newest first.

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
