# 01 — Design

Read the README worked example first. This document specifies how that example is produced.
Assumes `00-JEV-API.md` for the API contract.

Specifications only — YAML, JSON, CLI, tables. No implementation.

---

## Design drivers

Seven decisions that shape everything downstream. Each is expensive or impossible to retrofit.

**D1 — One document per call, every question batched.**
The API takes one state per request and all questions see it. Batching pays for the document once
instead of once per question; TypeSafe measured 12.2x cheaper with identical answers. Packing several
documents into one state saves zero tokens and triggers context rot (`00-JEV-API.md`).
→ `Call` is a first-class object: one document, one projected state, one question group. `Call` is the
retry and cost unit; `Cell` is the cache and output unit. Never conflate them.

**D2 — The schema is paid on every row.**
A 120-token ticket with a 230-token battery is 66% schema; a 20-token title with a 650-token battery
is 97%. Minimising the schema is the main cost lever on short corpora, and it trades against accuracy
because Jev reads literally.
→ `estimate` reports the state/schema split. `schema-tune` measures the accuracy cost of terser
criteria rather than guessing it.

**D3 — Answer shapes differ, and distributions are never collapsed.**
`noul` returns a bare float with no `probabilities` and no `confidence`. `choice` and `score` return
distributions and confidence. Storing only the argmax is irreversible: a new threshold then costs a
full re-run.
→ Nullable per-type columns, `probabilities` always written where they exist, and a
`confidence_source` column separating model-reported from derived.

**D4 — Thresholds are per question and per type.**
Structural invariants do not hold: the same question returned 0.22 as a noul and 0.01 as a choice
probability; a question and its negation summed to 1.19.
→ No global threshold. Noul thresholds live on their own scale (distance from 0.5). No cross-question
arithmetic anywhere in the engine.

**D5 — Resumable runs, cell-level cache, provenance on every row.**
A 480k-row run takes half an hour and will be interrupted. Published numbers get checked.
→ Append-only shards, atomic checkpoints, a cache keyed per `(state, question, model)`, and
run/model/hash/token columns on every cell.

**D6 — Spend is predicted before and capped during.**
The tool spends money unattended for hours. Users ask what it costs and what stops it.
→ `--budget` mandatory on every spending command. Integer micro-dollars. Attempts charged, not
successes. Clean resumable stop on breach.

**D7 — A question ships its accuracy or it does not ship.**
`frustration` scored 0.68 in the README example. Without a gate it would have become a dashboard.
→ `validate` produces per-question accuracy and thresholds; questions below their gate are marked
`exploratory` and excluded from headline claims.

---

## Architecture

```
questions.yaml ──► QuestionSet compiler ──► QuestionSet + CallGroups
                    validate, hash, group by projection

corpus ──► Source ──► Normalizer ──► Document{id, fields, meta}
                                          │
                                          ▼
                                   Cache filter ────── hits ─────┐
                                          │ misses              │
                                          ▼                     │
                                   Call planner                 │
                              project state, one Call per group │
                                          │                     │
                                          ▼                     │
                                   Scheduler + Budget governor  │
                              AIMD concurrency, retry, spend cap│
                                          │                     │
                                          ▼                     │
                                   Jev client (transport only)  │
                                          │                     │
                                          ▼                     │
                                   Decoder (per type) ──► Cells │
                                          │                     │
                                          ├─────────────────────┘
                                          ▼
                                   Writer + Checkpointer + Cache populate
                                          │
                 ┌────────────────────────┼────────────────────────┐
                 ▼                        ▼                        ▼
          cells.parquet            review_queue.csv          manifest.json
```

Everything above the Scheduler is pure and network-free. Everything below touches money and time.
That seam is what makes the system testable without spending.

### Stage contracts

| Stage | Guarantees |
|---|---|
| Source | Streams; never loads the corpus into memory; stable ordering |
| Normalizer | `id` unique and stable across runs; fields UTF-8, length-bounded |
| Compiler | Fully validated; deterministic hashes; grouping is a pure function of projections |
| Cache filter | Never returns an already-answered cell; read-only |
| Call planner | State contains only the group's fields; tokens under limit; records `projection_id` |
| Scheduler | Never admits a call that would breach the budget |
| Client | Transport only — no retries, no business logic |
| Decoder | Every requested question id present and type-matched, or the call is discarded |
| Writer | Append-only; atomic shard finalisation; never partially visible |

---

## Data model

| Object | Fields | Role |
|---|---|---|
| `Document` | `id`, `fields{}`, `meta{}`, token estimates | `id` stable across runs, from the source not row position |
| `Question` | `id`, `type`, `instructions`, `criteria`, `projection`, `gate`, `body_hash` | `body_hash` covers everything that changes meaning |
| `QuestionSet` | ordered `Question[]`, `questionset_hash` | Order preserved from YAML |
| `CallGroup` | questions sharing a projection | Group count = calls per document = the cost multiplier |
| `Call` | `call_id`, document, group, projected state, projected cost | **Retry and cost unit** |
| `Cell` | document × question × answer + provenance | **Cache and output unit** |

---

## Question set format

Field names mirror the API exactly. See the README for a complete example.

```yaml
version: 1
name: support-triage
defaults:
  gate: strict                # strict | exploratory
  projection: [subject, body] # fields questions see unless they override

questions:
  - id: is_urgent
    type: noul                # criteria optional
    instructions: ...
    criteria: { "true": ..., "false": ... }

  - id: department
    type: choice              # criteria required, map of option → rubric
    instructions: ...
    criteria: { billing: ..., technical: ..., sales: ... }

  - id: frustration
    type: score               # criteria required, ordered array, ≥2 levels
    instructions: ...
    criteria: [ ..., ..., ... ]
    gate: exploratory

  - id: comments_hostile
    projection: [subject, body, thread]   # different projection → different call group
```

### Validator rules

- `id` unique, stable, safe as a column name. Renaming an id is a new question and drops its cache.
- `choice`: ≥2 criteria entries. A `null` rubric is legal but warns — boundary cases belong in criteria.
- `score`: ≥2 ordered levels; order is semantic, level 0 upward.
- `noul`: if criteria are given, `"true"` must not be inverted relative to the instruction.
- `instructions` must be a declarative proposition, not a command.
- **Reject questions requiring counting, arithmetic or date comparison**, citing jaggedness #2/#3.
  Lint for "how many", "count", "average", "before/after \<date\>".
- `body_hash` = hash over `type + instructions + criteria + projection`, whitespace-normalised.
  `questionset_hash` = ordered hash of all body hashes + `version`.

---

## Call planning

The compiler partitions questions by declared `projection`. Same projection → same call.

- A plain-text corpus has one projection, so one call per document. The common case stays trivial.
- Group count multiplies cost per document, so the compiler reports it:
  `14 questions · 2 call groups · state sent 2x per document`.
- Grouping is deterministic and unit-testable with no network.
- State is a JSON **object** with named fields (per `/concepts/state`); a bare string is fine for
  single-field corpora.
- Constraint: `tokens(state) + tokens(schema) + margin ≤ context_limit`, margin 15% by default.
  Underestimating causes hard failures; overestimating costs a little efficiency.

**Never put more than one document in a state.** Put a comment where someone would be tempted.

### Long documents

Documents exceeding the limit alone are chunked with overlap, answered per chunk, aggregated:
`noul` → max probability (mean configurable); `choice` → probability-weighted vote; `score` → mean of
weighted scores, treated as threshold material only. Aggregated cells carry `chunk_count > 1` and are
excluded from validation samples by default. Truncation is never silent.

---

## Schema economy

`census schema-tune` samples documents, evaluates the verbose question set as a reference, evaluates
terser variants, and reports agreement, cost saved, and projected corpus-wide saving.

| Type | Metric | Starting floor |
|---|---|---|
| `noul` | thresholded agreement + mean absolute probability drift | ≥0.97, drift ≤0.05 |
| `choice` | argmax agreement + drift in chosen option's probability | ≥0.95 |
| `score` | exact-level agreement + mean shift in weighted score | ≥0.92, shift ≤0.15 levels |

Output is a table the author acts on, not an automatic rewrite — question wording is the author's
accountability. Note that agreement is not accuracy: it only means the terse variant answers like the
verbose one, which matters only if the verbose one was validated.

**Authoring guidance the tool should surface:** prefer `noul` where the judgement is genuinely binary
(cheapest — criteria optional); one `choice` over N options is cheaper than N `nouls` but is a
*relative* judgement settling which, while nouls are *absolute* and may all be low; keep projections
narrow.

---

## Output schema

Long format, one row per (document, question). Long beats wide because adding a question appends rows
instead of migrating a schema.

| Column | Type | Notes |
|---|---|---|
| `doc_id` | string | |
| `question_id` | string | |
| `type` | string | `noul` \| `choice` \| `score` |
| `noul` | double | noul only; P(yes). Null otherwise |
| `choice` | string | choice only |
| `score` | double | score only; weighted, may be fractional |
| `probabilities` | map<string,double> | choice: option→p. score: level-index→p. **Null for noul** |
| `legend` | map<string,string> | score only; index → description |
| `confidence` | double | model-reported for choice/score; derived for noul |
| `confidence_source` | string | `model` \| `derived` |
| `gate` | string | `strict` \| `exploratory` |
| `chunk_count` | int32 | >1 if aggregated |
| `projection_id`, `call_id`, `run_id`, `model`, `questionset_hash`, `question_body_hash`, `input_tokens`, `ts` | | provenance |

- Derived noul confidence is `|noul − 0.5| × 2`. It is ours, marked `derived`, validated on its own
  scale, and never compared against a choice/score confidence.
- Source passthrough columns live in a separate `documents.parquet`, joined on `doc_id`.
- Finalised shards are never rewritten.

---

## Cache

```
cache_key = hash(projected_state, question_id, question_body_hash, model_id_returned_by_api)
```

- Adding a fifth question to a four-question set re-asks one question per document.
- Editing one question invalidates only that question's cells.
- Re-running an identical configuration costs nothing.
- The model id must be the one the API **returned**, so a `jev-latest` version bump never silently
  mixes two models.

Savings are reported: `1.2M cells cached · $31.40 saved`.

---

## Budget and concurrency

**Governor.** Integer micro-dollars. Charge attempts, not successes. Admit a call only if
`spent + projected ≤ budget`; overshoot is bounded by in-flight concurrency. On breach: stop
admitting, drain, finalise, checkpoint, exit non-zero, print the resume command. `schema-tune` and
`validate` charge the same budget. `estimate` never spends.

**Concurrency.** AIMD — additive increase after a clean window, halve on `429`/`529`. Floor 1,
configurable ceiling, exponential backoff with full jitter, honour `Retry-After`. At one call per
document, request-rate may bind before token-rate; surface which one is throttling. Bounded queues
give backpressure. The writer is single-threaded.

**Estimator.** One implementation shared by `estimate` and the planner. Pure and network-free.
Calibrated during the run against `usage.input_tokens`; margin widens automatically on drift. Context
overflow is a planner bug, not a transient error.

---

## Thresholds, review queue, validation

Thresholding happens at **read time** over stored distributions, per question and per type (D4).
`choice`/`score` threshold on `confidence`; `noul` on distance from 0.5.

`census label` produces a gold-set sample **stratified across the probability range** — uniform
sampling on a skewed corpus yields easy cases and a flattering, useless number.

`census validate` reports per question: accuracy with sample size and interval, expected calibration
error, the threshold achieving a target accuracy (default 0.99), the coverage that implies, and a
pass/fail verdict against `gate`. It never aggregates accuracy across question types into one
headline number.

Cells below threshold export to `review_queue.csv`; re-imported human answers are stored as a
**separate override layer**, never merged into model output.

---

## Failure handling

| Failure | Class | Response |
|---|---|---|
| `429`, `529` | transient, expected | Backoff with jitter, reduce concurrency, retry |
| `5xx`, timeout, reset | transient | Bounded retries, then quarantine the call |
| `401`, `422` | fatal, config | Abort immediately. Never retry |
| Missing question id / type mismatch | suspicious | Discard the whole call, retry once, then quarantine |
| Budget exhausted | intentional | Clean stop, checkpoint, resume command |
| State exceeds context | data | Chunk or quarantine. Never truncate silently |
| Corrupt source row | data | Skip, quarantine with reason |

Config errors fail fast and loud — a `422` means burning budget on a malformed question set. Data
errors never abort a long run.

---

## CLI

```
census estimate     --input <src> --questions <yaml> [--sample N]
census schema-tune  --input <src> --questions <yaml> --budget <usd>
census run          --input <src> --questions <yaml> --budget <usd> --out <dir>
                    [--resume <run_id>] [--limit N] [--keep-raw <frac>] [--concurrency-max N]
census label        --run <run_id> --question <id> [--n 200]
census validate     --run <run_id> --gold <csv>
census report       --run <run_id>
census cache        stats | prune | clear
```

- `--budget` required by `run` and `schema-tune`. No unbounded mode exists.
- `run` prints the estimate and requires confirmation unless `--yes`.
- `--resume` refuses on `questionset_hash` mismatch and names what changed.
- Progress shows documents done, spend vs cap, throughput, concurrency, cache hit rate, ETA, with
  cached and paid documents counted **separately**.

---

## On-disk layout

```
.census/                          # machine state, safe to delete
  cache.db
  runs/<run_id>/
    manifest.json                 # question set verbatim, config, versions, source fingerprint
    checkpoint.json               # cursor + shard ledger, atomic
    shards/000000.parquet         # immutable once finalised
    quarantine.jsonl
    raw/                          # sampled responses, if --keep-raw
results/                          # the deliverable
  cells.parquet
  documents.parquet
  review_queue.csv
  validation_report.md
```

---

## Anti-goals

| Not building | Why |
|---|---|
| Text generation | Jev cannot; wrapping another model destroys the cost story |
| Agents or agentic loops | Control flow stays deterministic; answers fill cells, they never choose the next step |
| A server or hosted product | One credential, no daemon, no telemetry, no support burden |
| A vector database | Different problem; complementary to embeddings |
| Streaming or interactive modes | A different project; mixing ruins both |
| Multi-provider abstraction | Every cost and calibration assumption here is Jev-specific |
| LLM-generated question sets | Question design is the user's accountability |
| Multi-document packing | Zero saving, real accuracy cost |
