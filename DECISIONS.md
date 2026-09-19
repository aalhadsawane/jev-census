# Decisions

Divergences from the design docs, and why. Newest first.

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
