# 01 — Motivation and Design Drivers

> Read `00-API-NOTES.md` first. Every driver below is downstream of a verified fact about the API.
> Drivers are ordered by how expensive they are to retrofit, most expensive first.

---

## Revision note

This document was rewritten after reading the API reference properly. The first version was built on
an assumption — that many documents could be packed into one state and answered per index — which the
contract does not support and which the model's documented context rot actively punishes. The
correction is recorded in `00-API-NOTES.md` §6.

It is worth stating plainly why that matters beyond the fix itself: the original design's headline
claim was an order-of-magnitude cost saving that **did not exist**. Had it been implemented before
being checked, the project's most prominent number would have been wrong in public. The lesson is
encoded as a rule in `04-AGENT-GUIDE.md`: *verify the contract empirically before building on an
inference about it.*

---

## Why this project exists

**For users.** Corpus-scale semantic labelling has been either cheap and crude (regex, keyword
matching, embeddings plus a threshold) or accurate and unaffordable (a frontier LLM per row). Jev
collapses that tradeoff on price. It does not supply batching, resumption, cost control, calibration
or provenance — and without those, a multi-million-row run is a script that burns money and produces
numbers nobody should trust.

**For the ecosystem.** As of September 2026 the ~136 public Jev projects cluster into SDK wrappers,
agent guardrails, game demos, rerankers and one-off calibration studies. Offline corpus-scale batch
labelling is unclaimed. `jevql` does interactive SQL-shaped queries over Postgres; that is a
different job.

**For the author.** This is a portfolio artifact and must read as data-platform engineering — a
measured cost model, idempotent checkpointing, adaptive concurrency, calibration-aware output — not
as an API call in a loop. That constraint is a feature: what makes it impressive is what makes it
work at five million rows.

---

## The drivers

### D1 — One document per call, with every question batched

**The driver.** The API takes exactly one `state` per request and every question sees all of it.
Batching all questions about a document into a single call pays for the document once instead of
once per question. TypeSafe's cookbook measures 12.2x cheaper and 10.0x faster on a large document,
and verifies the answers are identical either way — each question is scored independently, so
batching costs no accuracy. It is a free win and the correct default in every case.

The corollary is the part that took a wrong turn first: **packing multiple documents into one state
saves nothing.** Per-document answers require per-document questions, so the token arithmetic comes
out identical, and the enlarged state invites context rot. See `00-API-NOTES.md` §6.

**Why it cannot be retrofitted.** The unit of work propagates into the cache key, the retry unit,
cost accounting, checkpoint granularity and the error surface. Choosing it late means changing all of
them at once.

**What it forces.** A `Call` is a first-class object: one document, one projected state, a set of
questions, and its projected cost. `Call` is the retry and cost unit; `Cell` is the cache and output
unit. Those two granularities must never be conflated.

---

### D2 — The question schema is paid on every row, so schema economy is the cost lever

**The driver.** Because the schema is re-sent with every document, a 650-token battery over 5.8M
documents is ~3.8B tokens of schema alone, dwarfing a corpus of short titles. On short-document
corpora the schema is upwards of 90% of the bill. This is the inverse of the usual intuition, where
the document dominates.

Therefore the central optimisation is not packing — it is finding the **minimum sufficient schema**:
the tersest instructions and criteria that preserve accuracy. That is an empirical question with a
measurable answer, and answering it well is this project's main technical contribution.

**Why it cannot be retrofitted.** Not structurally, but economically: if schema cost is not surfaced
from the first estimate, users write verbose question sets, run them over millions of rows, and
discover the cost afterwards. The tool must make the tradeoff visible at authoring time.

**What it forces.**
- `estimate` reports the state/schema split explicitly, not just a total.
- A `schema-tune` capability compares terse and verbose variants of the same question against a
  reference, reporting the accuracy delta alongside the cost delta.
- Question authoring guidance is part of the product, not a footnote: prefer `noul` where the
  judgement is genuinely binary, since it is the only primitive whose criteria are optional.
- Tension acknowledged: jaggedness #1 says Jev reads *literally* and boundary cases belong in the
  criteria. Terser is cheaper and, past a point, less accurate. The tool's job is to **measure** that
  curve, not to assume either end of it.

---

### D3 — State projection versus batching is a real tradeoff, and the planner owns it

**The driver.** Two documented facts pull in opposite directions. Batching is free and cheap, but
every question in a call shares one state. Context rot is real — *"accuracy falls as the state grows
with content unrelated to the decision"* — and the documented remedy is to send only the fields the
question needs.

If eighteen questions need eighteen different slices of a rich record, one batched call means every
question sees seventeen slices of distraction. Splitting into groups costs a repeated state but
protects accuracy.

**Why it cannot be retrofitted.** If questions cannot declare what state they need, there is no
information with which to group them later, and the question-set format is a public contract that is
painful to change once users have written files against it.

**What it forces.**
- Questions may declare a **projection**: which fields of a structured record they require.
- Questions sharing a projection are grouped into one call; different projections mean different
  calls. This is the **call planner**.
- The planner's objective is explicit and reportable: minimise total tokens subject to the grouping
  the user's projections imply, and show what the grouping costs.
- Default for a plain-text corpus is trivially one group — the common case stays simple.

---

### D4 — The three answer shapes are genuinely different; do not normalise them away

**The driver.** `noul` returns a bare float with **no `probabilities` map and no `confidence`**.
`choice` returns a label, a distribution over options, and confidence. `score` returns a weighted
value, a level-indexed distribution, a legend, and confidence. A schema that pretends these are one
shape will either invent a confidence for `noul` and present it as the model's, or drop real data
from the other two.

**Why it cannot be retrofitted.** It is the output schema. Everything downstream reads it, and it is
what gets published.

**What it forces.**
- The output table carries `confidence_source`: `model` for choice and score, `derived` for noul.
- Any derived noul confidence — distance from 0.5 is the natural choice — is labelled as ours and
  validated as ours. It is never presented as a model-reported quantity.
- `score` probabilities are keyed by level *index*; the `legend` is stored or resolved at write time,
  or the results become uninterpretable once the question set moves on.
- The weighted `score` is threshold material, never a measurement: the docs warn its levels are
  *"weak in numerical calibration."* Do not average it across a corpus and publish the mean.

---

### D5 — Probability distributions are never collapsed

**The driver.** Storing only the argmax discards the most valuable output and is irreversible: a
different threshold, a calibration analysis, or a list of marginal rows all then require re-running
and re-paying for the corpus.

**What it forces.** `probabilities` is written on every cell that has one, always, with no flag to
disable it. Thresholding is a **read-time** operation over stored results, never a write-time filter.
Any convenience accessor returning a bare label is visibly named as lossy.

**How we know it is working.** Changing a threshold and regenerating every downstream table costs
zero API calls.

---

### D6 — Thresholds are per question and per type; no structural invariants

**The driver.** The jaggedness documentation gives measured counterexamples: the same question as a
`Noul` and as a yes/no `Choice` returned 0.22 against 0.01. A question and its negation as two Nouls
summed to 1.19. The explicit guidance is *"don't carry a threshold tuned on a Noul over to a Choice,
and don't hold the model to arithmetic identities between separate questions."*

**Why it cannot be retrofitted.** A single global threshold is the kind of convenience that gets
baked into config, dashboards and published methodology before anyone notices it is unsound.

**What it forces.**
- Thresholds are stored per question id, never globally, and `noul` thresholds live on a different
  scale (distance from 0.5) from `choice`/`score` confidence.
- The engine never computes cross-question arithmetic and never assumes complementary probabilities.
- Choosing between "one `choice` over N options" and "N `nouls`" is a modelling decision the question
  author makes deliberately — the former is relative and settles *which*, the latter are absolute and
  may all be low. The docs say so; the authoring guide must too.

---

### D7 — Every run is resumable, and every cell is content-addressed

**The driver.** A multi-million-row run takes hours and will be interrupted — a crash, a 529, a
budget cap, a closed laptop. If interruption means paying twice, the tool fails at exactly the scale
it exists for.

**What it forces.**
- Append-only sharded output; no in-place mutation of results, ever.
- Atomic checkpoints; partial shards are completed or discarded on resume, never half-read.
- The cache is keyed at **cell** granularity — `hash(projected_state, question_id, question_body,
  model)`. Adding a fifteenth question to a fourteen-question set re-asks exactly one question per
  document. Editing one question invalidates only that question's cells. This is the single highest
  practical-value decision in the design, and it is only available if the cache is per-cell from the
  start.
- Resuming with a changed question set is **detected and refused**, with the changed questions named.

**How we know it is working.** `kill -9` mid-run, resume, and the output is identical to an
uninterrupted run with zero duplicate and zero re-paid cells.

---

### D8 — Spend is predictable before the run and enforced during it

**The driver.** This tool spends money on an API in a loop, unattended, for hours. Users ask what it
will cost and what stops it. A tool that cannot answer both is not trusted with a real corpus, and
trust is the adoption bottleneck.

**What it forces.**
- `estimate` is a real sampling-based command that never spends, and reports the state/schema split.
- `--budget` is **mandatory** on every spending command. There is no unbounded run mode.
- The governor stops cleanly and resumably; it never kills mid-write.
- Money is integer micro-dollars. Never floats.
- Retries, failures and discarded calls all charge budget, because the provider charges for them.
- Cached cells cost zero and are reported as savings.

---

### D9 — Provenance on every cell

**The driver.** The flagship analysis makes public claims about a public corpus, and those claims
will be checked. Credibility is the entire point of building this, and it requires answering "how
exactly did you get that number?" for any individual cell.

**What it forces.** Every row carries `run_id`, the model id **as returned by the API**,
`questionset_hash`, `question_body_hash`, `call_id`, `projection_id`, `timestamp` and `input_tokens`.
The manifest stores the question set verbatim, the source fingerprint, the resolved config and the
tool version. A sampled fraction of raw responses is retained under `--keep-raw` for audit.

---

### D10 — Deterministic control flow, probabilistic leaves

**The driver.** TypeSafe's own guidance is to keep code in control and give the model narrow,
structured decisions. Jev's per-question accuracy varies; a pipeline that lets an answer determine
*what happens next* compounds that error, while one that lets answers fill *cells of a table*
contains it.

**What it forces.** The plan is fixed at configuration time and never varies on an answer. No
question's result selects the next question. Accuracy is therefore a property of individual cells and
can be measured per question, which is what makes D11 possible at all.

---

### D11 — A question ships its accuracy, or it does not ship

**The driver.** The fastest way to destroy the flagship analysis is a confident chart built on a
question Jev answers at 68%. The defence is a gate.

**What it forces.**
- `label` produces a sample stratified across the *probability range*, not uniform — uniform sampling
  on a skewed corpus yields easy cases and a flattering, useless number.
- `validate` reports per-question accuracy with sample size and interval, calibration error, and the
  threshold achieving a target accuracy together with the coverage it implies.
- The validation report is a committed, publishable artifact.
- Questions below the gate are marked `exploratory` and excluded from headline claims.

---

### D12 — Declarative question sets, local-first execution

Two smaller drivers, decided now because both are contracts.

**Declarative.** Question sets are YAML: authorable by an analyst, hashable, diffable, committable and
shareable. Hashability is what D7's cache and D9's provenance depend on. A directory of good question
sets for common corpora is also the cheapest community-contribution surface the project has.

**Local-first.** One credential — `TYPESAFE_API_KEY` — and no server, daemon, account or telemetry.
Output is Parquet on local disk, readable by DuckDB, pandas and polars. Cache and checkpoints live in
one deletable project-local directory. The tool works fully offline against its cache.

---

## Anti-goals

| Not building | Why |
|---|---|
| Text generation | Jev cannot. Wrapping another model to fill the gap makes this an LLM framework and destroys the cost story. |
| Agents or agentic loops | Violates D10. The deliverable is a deterministic table. |
| A server or hosted product | Violates D12 and creates a support burden that kills a portfolio project. |
| A vector database | Different problem; Jev is complementary to embeddings by TypeSafe's own positioning. |
| Interactive or streaming query modes | A different project. Mixing offline batch with interactive ruins both. |
| Multi-provider abstraction | Every cost and calibration assumption here is Jev-specific. Abstracting erases what makes the design distinctive. |
| LLM-generated question sets | Violates D10 and D11. Question design is the user's judgement and the thing they are accountable for. |
| Multi-document packing | Zero token saving, real accuracy cost. `00-API-NOTES.md` §6. |

---

## Success criteria

1. A full run over a ≥1M-row public corpus completes within its stated budget, resumably, with a
   published validation report and downloadable raw results.
2. `estimate` predicts that run's cost within 10%, and reports the state/schema split.
3. `schema-tune` produces a real measured curve of accuracy against schema verbosity, for at least
   one question set, published.
4. A new user goes from install to labelled results on their own CSV in under five minutes.
5. The flagship analysis is published with its methodology, its gold-set accuracy table, and its
   failed questions disclosed alongside its findings.

Criterion 5 includes disclosing what did not work. That is not modesty. It is what makes the other
four believable — and, as the revision note at the top of this document shows, the project has
already had one such disclosure to make.
