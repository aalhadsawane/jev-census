# Decisions

Divergences from the design docs, and why. Newest first.

---

## 2026-09-20 — Direct TypeSafe API is the implementation path; Vercel Gateway is a historical fallback

**What changed.** TypeSafe granted API access. `api.typesafe.ai` is now reachable directly with the
official `typesafe-sdk` Python package and a `TYPESAFE_API_KEY`.

**Why it mattered.** From 2026-09-19 to 2026-09-20, TypeSafe's own endpoint was early-access-gated, so
Jev was reached through Vercel AI Gateway's evaluation modality (`experimental_evaluate()` from the
`ai` npm package) as a stopgap, under a free promo. That path is TypeScript-only — Vercel's own docs
state evaluation is "available through the AI SDK only" — which put real pressure on the project's
language choice, away from the Python stack the design docs specify.

**What was confirmed on each path**, both fully documented in `docs/00-JEV-API.md`:
- Gateway: 5 of 6 contract cases live, pricing independently confirmed to 5 decimal places, but an
  undocumented rate limit capped runs at roughly 5 calls per window regardless of account balance
  (Jev's own calls stayed on the $0 promo, not the paid balance).
- Direct API: all 6 cases live, no rate limit encountered, and token counts for equivalent calls
  matched the Gateway path exactly — independent confirmation both transports hit the same model.

**Decision.** Build on the direct API, in Python, per the original design docs. No rewrite of the
stack section was needed beyond correcting one detail (`typesafe-sdk` installs from plain PyPI, not a
custom index) — the Python-first design was right all along; only the transport was blocked.

**What was kept, not deleted.** The Vercel Gateway spike code lives on `worktree-ai-gateway-spike`
and is not merged into the implementation. It's real, working, committed history — useful if direct
access is ever revoked or rate-limited unexpectedly — but it is not the path being built on.

---

## 2026-09-19 — Rejected multi-document packing

Recorded in full in `docs/00-JEV-API.md` §6, not repeated here: packing several documents into one
`state` to amortise the question schema was the project's original headline optimization. The API
takes exactly one `state` per request with all questions answered against it, so per-document answers
need per-document questions — packing saves zero tokens and actively costs accuracy (documented
context rot). Replaced by call planning and schema economy as the project's core technical
contribution.
