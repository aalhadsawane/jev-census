# Decisions

Divergences from the design docs, and why. Newest first.

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
