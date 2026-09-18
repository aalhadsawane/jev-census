# 02 — Architecture

> Assumes `00-API-NOTES.md` and `01-MOTIVATION.md`. Drivers referenced as **D1**–**D12** are defined
> there. This document describes system *shape*. Concrete formats live in `03-DESIGN.md`.

---

## Shape

```
                            ┌──────────────────────────┐
  questions.yaml ─────────► │  QuestionSet compiler    │  validate, hash,
                            │                          │  group by projection (D3)
                            └────────────┬─────────────┘
                                         │  QuestionSet + CallGroups
  corpus (parquet/csv/     ┌─────────────▼────────────┐
   jsonl/duckdb/hf) ──────►│  Source → Normalizer     │  → Document{id, fields, meta}
                           └─────────────┬────────────┘
                                         │  stream of Documents
                           ┌─────────────▼────────────┐
                           │  Cache filter (D7)       │  drop cells already answered
                           │  cell granularity        │ ── hits ──────┐
                           └─────────────┬────────────┘               │
                                         │  outstanding cells         │
                           ┌─────────────▼────────────┐               │
                           │  Call planner (D1,D2,D3) │  project state per group,
                           │                          │  emit one Call per group
                           └─────────────┬────────────┘               │
                                         │  Calls                     │
                           ┌─────────────▼────────────┐               │
                           │  Scheduler               │  concurrency (AIMD),          
                           │  + Budget governor (D8)  │  retry, spend cap             │
                           └─────────────┬────────────┘               │
                                         │                            │
                           ┌─────────────▼────────────┐               │
                           │  Jev client              │  POST /v1/systemone
                           │  (transport only)        │               │
                           └─────────────┬────────────┘               │
                                         │  answers keyed by question id
                           ┌─────────────▼────────────┐               │
                           │  Decoder (D4)            │  per-type decode:
                           │  noul │ choice │ score   │  shapes differ, deliberately
                           └─────────────┬────────────┘               │
                                         │  Cells                     │
                                         ├────────────────────────────┘
                                         │
                           ┌─────────────▼────────────┐
                           │  Writer + Checkpointer   │  append-only Parquet shards,
                           │  + Cache populate (D7,D9)│  atomic checkpoint, provenance
                           └─────────────┬────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            ▼                            ▼                            ▼
    results/cells.parquet        review_queue.csv            run_manifest.json
    (full distributions,         (below-threshold,           (question set verbatim,
     per-type shapes)             per question & type)        config, versions)
```

Everything left of the Scheduler is pure and network-free. Everything right of it touches money,
time and the network. Keep that seam sharp — it is what makes the system testable without spending.

---

## What changed from the first architecture

The **Packer** (assemble N documents into one state, ask indexed questions, unpack by index) is gone,
replaced by the **Call planner** (one document, project its state per question group, batch each
group's questions into one call). The reasons are in `00-API-NOTES.md` §6.

One consequence is a large de-risking. Under packing, the top architectural risk was *index
misalignment*: answers returned against the wrong document, silently, while remaining perfectly
well-typed. That risk is now structurally absent, because **answers come back keyed by the question
ids we chose** and each call concerns exactly one document. Decoding is a dictionary lookup, not a
positional reconstruction.

What replaces it as the top risk is cost estimation, since the schema is now paid per row.

---

## Stage contracts

| Stage | In | Out | Guarantees |
|---|---|---|---|
| **Source** | path or URI | raw records | Streams; never loads the corpus into memory; stable ordering |
| **Normalizer** | raw record | `Document` | `id` unique and stable across runs; fields UTF-8 and length-bounded |
| **QuestionSet compiler** | YAML | `QuestionSet` + `CallGroup[]` | Fully validated; deterministic hashes; grouping is a pure function of declared projections |
| **Cache filter** | `Document` + `QuestionSet` | outstanding cells | Never returns an already-answered cell; read-only |
| **Call planner** | document + group | `Call` | Projected state contains only the group's declared fields; total tokens under limit; records projection id |
| **Scheduler** | `Call` | admitted `Call` | Honours concurrency, backoff and remaining budget; never admits a call that would breach the cap |
| **Client** | `Call` | response or typed error | Transport only; no retries (Scheduler's job), no business logic |
| **Decoder** | response | `Cell[]` | Every requested question id present with matching type; per-type shape decoded faithfully (D4) |
| **Writer** | `Cell[]` | shards | Append-only; atomic finalisation; never partially visible |

---

## Components

**`sources/`** — Parquet, CSV, JSONL, DuckDB, HuggingFace readers. Separate because the format list
will grow and that growth must not touch the engine.

**`questions/`** — YAML schema, validator, compiler, projection grouping, hashing. A public contract
(D12), versioned independently of the engine.

**`planner/`** — state projection, call assembly, token estimation, schema-economy tooling. The
project's core IP (D2, D3). Must be fully testable **without a network**: it takes token counts as
input rather than calling out.

**`client/`** — HTTP, auth, error typing. Deliberately thin. Wrapping the official SDK is fine;
adding logic here is not.

**`scheduler/`** — concurrency, adaptive rate limiting, retry policy, budget governor. All
time- and money-dependent behaviour is quarantined here so it can be simulated.

**`decode/`** — the three answer shapes, and the derivation of a noul confidence (D4). Small,
isolated, heavily tested, because it is where a silent misinterpretation would live.

**`store/`** — cache, checkpoints, shard writer, manifest. The only module touching durable state;
enforces D7 and D9.

**`validate/`** — gold-set sampling, accuracy and calibration reporting, per-question threshold
recommendation (D6, D11). Depends on `store`; nothing depends on it, so it can be built last.

**`cli/`** — parsing, progress, confirmation gates. Presentation and safety only.

---

## Data model

Six objects.

**`Document`** — `id`, `fields` (a mapping; a plain-text corpus has a single field), `meta`
passthrough, per-field token estimates. `id` must be stable across runs and derived from the source,
not from row position; if the source has no natural key, hash the content and record that choice in
the manifest.

**`Question`** — `id`, `type` (`noul` | `choice` | `score`), `instructions`, optional/required
`criteria` per type, declared `projection` (D3), `gate`, and `body_hash` over everything that changes
its meaning.

**`QuestionSet`** — ordered `Question` list plus `questionset_hash`. Order is preserved from YAML for
reproducibility.

**`CallGroup`** — a set of questions sharing a projection. Computed by the compiler, not authored.
The number of groups is the number of API calls per document, and is therefore the single most
important number in the cost model after the schema size.

**`Call`** — `call_id`, the document, the group, the projected state, the assembled request, and the
projected token cost. A `Call` is the **retry unit and the cost unit**.

**`Cell`** — one document, one question, one answer, with per-type fields and the full provenance
block. A `Cell` is the **cache unit and the output unit**.

The `Call`/`Cell` asymmetry is the direct consequence of D1. Conflating them produces either
double-billing or a cache that never hits, so name them unambiguously everywhere and let the type
checker enforce the distinction.

---

## Concurrency

Async I/O, bounded worker pool, one process. No distributed coordination, no broker (D12).

- Bounded queues between planner → scheduler → writer, so a slow writer applies backpressure rather
  than accumulating unbounded in-flight work.
- Concurrency is **adaptive**: rate limits are not published, so capacity is discovered. Additive
  increase on sustained success, multiplicative decrease on `429`/`529`.
- The writer is single-threaded. Append-only writing is fast enough at these volumes, and concurrency
  in the durable layer is where corruption comes from.
- Request volume is now high — one call per document per call group, so a 5.8M-row corpus with two
  groups is ~11.6M requests. Request-rate limits, not token limits, may be the binding constraint.
  The scheduler must surface which one is biting.
- Graceful shutdown is first-class: on `SIGINT` or budget breach, stop admitting, drain in flight,
  finalise the shard, checkpoint, exit non-zero with a resume hint. Never exit from inside a worker.

---

## Failure and recovery

| Failure | Class | Response |
|---|---|---|
| `429` | transient, expected | Backoff with jitter, reduce concurrency, retry. Not an error. |
| `529` | transient, expected | As above, longer floor. |
| `5xx`, timeout, reset | transient | Retry with bounded attempts, then quarantine the call. |
| `401` | fatal, config | Abort immediately. Never retry. |
| `422` | fatal, config | Abort. A malformed question set is a bug the user must fix, not a condition to survive. |
| Missing question id in response | **suspicious** | Discard the call's results, retry once, then quarantine. Never write a partial call. |
| Answer type mismatch | **suspicious** | Same. Indicates a compiler or API change. |
| Budget exhausted | intentional | Clean stop, checkpoint, exit with the resume command. |
| Projected state exceeds context | data | Chunk per the long-document strategy, or quarantine. Never truncate silently. |
| Corrupt source row | data | Skip, quarantine with reason. Never abort a six-hour run over one bad row. |

Two principles. **Fatal configuration errors fail fast and loud** — the worst outcome is burning an
hour of budget on a question set that was malformed from the start, which is exactly what a `422`
means. **Data errors never abort the run** — they go to quarantine with a reason and are counted in
the run report.

---

## On-disk layout

```
.cartograph/                      # machine state, safe to delete
  cache.db                        # cell cache, content-addressed (D7)
  runs/<run_id>/
    manifest.json                 # question set verbatim, config, versions, source fingerprint (D9)
    checkpoint.json               # cursor + shard ledger, atomic
    shards/000000.parquet         # append-only, immutable once finalised
    quarantine.jsonl              # unprocessable rows + reasons
    raw/                          # sampled raw responses, if --keep-raw
results/                          # the deliverable
  cells.parquet
  documents.parquet               # source passthrough, joined by doc_id
  review_queue.csv
  validation_report.md
```

`.cartograph/` may be deleted at any time without losing published results.

---

## Extension points

Designed for, not built up front: new sources (implement the reader interface); alternative
projection strategies (the planner is strategy-shaped so groupings can be compared with the same
harness that measures schema economy); post-run aggregation as documented DuckDB recipes rather than
a query engine.

Explicitly not extension points: other model providers, output formats beyond Parquet/CSV, and
user-supplied code in the hot path.

---

## Risks, ranked

1. **Cost estimation error.** The schema is paid per row, so a mis-estimate is multiplied by corpus
   size. A 20% error on 5.8M rows is real money. Mitigation: sampling-based estimate, calibration
   against reported `usage.input_tokens` during the run, mandatory budget cap.
2. **Context rot from careless projection.** Sending a whole rich record to every question is the
   documented accuracy failure mode, and it is also the *easy* thing to do. Mitigation: projections
   are declarable, the planner reports what each group sends, and `schema-tune` can measure the
   accuracy difference.
3. **Undocumented rate limits, at very high request volume.** Millions of requests against unknown
   limits. Mitigation: adaptive concurrency from the first networked milestone; never hardcode
   parallelism; distinguish request-rate from token-rate throttling in the display.
4. **Retries silently inflating spend.** Mitigation: the governor counts attempts, not successes.
5. **Cache invalidation subtlety.** A question edited without a hash change returns stale answers.
   Mitigation: hash the full question body; refuse to resume on `questionset_hash` mismatch.
6. **Misreading a derived noul confidence as the model's.** We invent it; the docs are explicit that
   noul carries none. Mitigation: `confidence_source` column, and validation treats noul thresholds
   as a separate scale (D6).
