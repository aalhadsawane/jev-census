# 03 — Detailed Design

> Specifications, not implementations. Formats, algorithms and interfaces are described in YAML,
> JSON, CLI signatures and prose. Writing the code is the implementer's job.
>
> Where this document conflicts with `00-API-NOTES.md` or `01-MOTIVATION.md`, those win.

---

## 1. Question set format

The public contract (D12). Field names mirror the API exactly — `type`, `instructions`, `criteria` —
so that what a user writes is recognisably what gets sent.

```yaml
version: 1
name: hn-stories
description: Typed questions asked of every Hacker News story.

defaults:
  gate: strict                  # strict | exploratory
  projection: [title]           # fields each question sees unless it overrides

questions:

  # noul — criteria OPTIONAL, therefore the cheapest primitive per question
  - id: is_show_hn
    type: noul
    instructions: >
      The post announces something the author personally built or made,
      rather than linking to someone else's work.
    criteria:
      "true":  The author is presenting their own project, tool or writing.
      "false": The post links to work by someone else.

  # choice — criteria REQUIRED, a map of option to rubric
  - id: topic
    type: choice
    instructions: The primary subject of the post.
    criteria:
      ai_ml:    Artificial intelligence, machine learning or statistics.
      systems:  Operating systems, databases, networking, compilers, hardware.
      web:      Frontend, backend, browsers, web standards.
      business: Startups, funding, hiring, economics, management.
      science:  Science that is not computing.
      other:    None of the above apply cleanly.

  # score — criteria REQUIRED, an ORDERED array, at least two levels
  - id: technical_depth
    type: score
    instructions: How much domain expertise a reader needs to follow the post.
    criteria:
      - Understandable by any general reader.
      - Assumes familiarity with software but no specialism.
      - Assumes working knowledge of a specific technical field.
      - Assumes active practice in a narrow subfield.
    gate: exploratory

  # a question needing different state joins a different call group (D3)
  - id: comments_hostile
    type: noul
    instructions: The discussion contains personal hostility between commenters.
    projection: [title, top_comments]
```

### Validator rules

- `id` unique, stable, safe as a column name. Renaming an id is a **new question** and invalidates its
  cache entries — document this prominently, because it surprises people.
- `choice` requires ≥2 criteria entries. A `null` rubric is legal per the API but should warn: the
  jaggedness docs say boundary cases belong in the criteria.
- `score` requires ≥2 ordered levels. **Order is semantic** — level 0 upward.
- `noul` criteria are optional. If present, `"true"` and `"false"` must not be inverted relative to
  the instruction; the docs warn that a Noul whose true maps to "no" performs worse.
- `instructions` must be a declarative proposition, not a command. Jev evaluates a statement; it does
  not follow orders.
- **Reject questions that require counting, arithmetic or date comparison** at validation time, with
  a pointer to jaggedness #2 and #3. A linter that catches "how many", "count", "before/after
  [date]", "average" saves users from the model's best-documented failure modes.
- `body_hash` covers `type + instructions + criteria + projection`, whitespace-normalised.
  `questionset_hash` is the ordered hash of all body hashes plus `version`.

### `gate`

`strict` questions may appear in published claims only after passing validation (D11). `exploratory`
questions run, are stored, and are flagged — excluded from headline artifacts. The honest path is the
default path.

---

## 2. Call planning

Replaces the packer. Per document, per call group: build one request.

### 2.1 Grouping

The compiler partitions questions by their declared `projection`. Questions with identical
projections share a call; different projections mean different calls.

- A plain-text corpus has one projection and therefore one call per document. The common case stays
  trivial.
- Group count is the multiplier on cost-per-document, so the compiler **reports it**: `14 questions ·
  2 call groups · state sent 2x per document`.
- Grouping is a pure function of declared projections — deterministic, reproducible, and
  independently testable with no network.

### 2.2 Assembly

```
state     = the document projected to the group's declared fields
questions = every question in the group, keyed by its id
model     = the pinned model id resolved at run start
```

State should be a JSON **object** with named fields, per `/concepts/state`: *"Use an object for most
requests so each part of the state has a descriptive name."* A bare string is acceptable for
single-field corpora.

Constraint: `tokens(state) + tokens(schema) + margin ≤ context_limit`. Margin defaults to 15%.
Underestimating causes hard failures; overestimating costs a little efficiency. The asymmetry is
severe, so be conservative.

### 2.3 What the planner must never do

Put more than one document in a state. `00-API-NOTES.md` §6. This is worth a comment in the code,
because it looks like an obvious optimisation and is not one.

---

## 3. Schema economy

The core technical contribution (D2). The question schema is re-sent with every document, so on a
short-document corpus it is most of the bill. The question is empirical: **how terse can criteria be
before accuracy degrades?**

### 3.1 `cartograph schema-tune`

1. Sample `n` documents (default 300), stratified by length.
2. Evaluate them with the **verbose** question set. This is the reference.
3. Generate or accept author-supplied terser variants of each question — shorter rubrics, dropped
   optional noul criteria, merged options.
4. Evaluate the same documents with each variant.
5. Report per question: agreement with the reference, mean probability drift, schema tokens saved,
   and projected corpus-wide saving.

Output is a table the author acts on — not an automatic rewrite. Question wording is the author's
accountability (D10, D11), and the tool's job is to price the choice, not make it.

### 3.2 Agreement metrics, per type

| Type | Metric | Suggested floor |
|---|---|---|
| `noul` | agreement on the thresholded answer, plus mean absolute drift in the probability | ≥0.97, drift ≤0.05 |
| `choice` | argmax agreement, plus mean absolute drift in the chosen option's probability | ≥0.95 |
| `score` | exact-level agreement, plus mean absolute shift in weighted score | ≥0.92, shift ≤0.15 levels |

These floors are starting points to be replaced by measured values. Note that **agreement is not
accuracy** — it measures whether the terse variant answers like the verbose one, which is only
meaningful if the verbose one was validated first (D11).

### 3.3 The tension, stated honestly

Jaggedness #1: Jev reads literally, and boundary cases belong in the criteria. So terser is cheaper
and, past some point, less accurate. The design does not assume either end of that curve. It
measures it and shows the author the price of each point. Publishing that curve for a real question
set is criterion 3 in `01-MOTIVATION.md`.

### 3.4 Authoring guidance the tool should surface

- Prefer `noul` where the judgement is genuinely binary; it is the only type whose criteria are
  optional, and therefore the cheapest.
- One `choice` over N options is cheaper than N `nouls` and yields a normalised distribution — but it
  is a **relative** judgement settling *which*, while nouls are **absolute** and may all be low. The
  docs are explicit about this. It is a modelling decision, not a cost decision.
- Keep projections narrow. Context rot costs accuracy and unused fields cost tokens.

---

## 4. Token estimation

- One estimator, used identically by `estimate` and by the planner, so projections and reality agree.
- Pure and network-free, so planning is unit-testable offline.
- **Calibrate during the run** against `usage.input_tokens`; maintain a running actual/estimated ratio
  and widen the margin automatically if it drifts above 1.0.
- `estimate` must report the **state/schema split**, not just a total. That split is what tells a user
  whether to shorten their documents or their questions, and they are completely different actions.
- Context overflow is a **planner bug**, not a transient error: log it, count it as a run defect.

---

## 5. Cache design

Cell granularity, content-addressed (D7).

```
cache_key = hash(
    projected_state_for_this_question_group,
    question_id,
    question_body_hash,
    model_id_as_returned_by_the_api
)
```

Consequences, which are the point:

- Adding a fifteenth question to a fourteen-question set re-asks **one** question per document.
- Editing one question's wording invalidates **only that question's** cells.
- Re-running an identical configuration costs **nothing**.
- Changing a projection invalidates only the questions using it.

Cached cells are reported as explicit savings: `1.2M cells cached · $31.40 saved`. Small feature,
outsized effect on whether people keep using the tool.

**The model id must be the one the API returned**, not the one requested, so a run spanning a
`jev-latest` version bump never silently mixes two models.

---

## 6. Output schema

Long format: one row per `(document, question)` cell. Long beats wide because adding a question later
appends rows instead of migrating a schema — exactly the workflow the cache enables.

| Column | Type | Notes |
|---|---|---|
| `doc_id` | string | stable source key |
| `question_id` | string | |
| `type` | string | `noul` \| `choice` \| `score` |
| `noul` | double | noul only; the probability of yes. Null otherwise |
| `choice` | string | choice only; the selected option. Null otherwise |
| `score` | double | score only; probability-weighted, may be fractional. Null otherwise |
| `probabilities` | map<string,double> | choice: option→p. score: level-index-as-string→p. **Null for noul — the API returns none** |
| `legend` | map<string,string> | score only; level index → description |
| `confidence` | double | choice/score: as returned. noul: **derived by us** |
| `confidence_source` | string | `model` \| `derived` — never blur these (D4) |
| `gate` | string | `strict` \| `exploratory` |
| `projection_id` | string | which state slice this question saw |
| `call_id` | string | |
| `run_id`, `model`, `questionset_hash`, `question_body_hash`, `input_tokens`, `ts` | | provenance (D9) |
| `chunk_count` | int32 | >1 if aggregated from a chunked document |

Notes:

- Nullable per-type value columns beat a single stringly-typed `answer`. The three shapes are
  genuinely different (D4) and flattening them is how information gets lost.
- **Derived noul confidence**: `|noul − 0.5| × 2` is the natural definition. It is ours, it is marked
  `derived`, and it is validated on its own scale. It must never be compared against a
  choice/score confidence (D6).
- Source passthrough columns go in a separate `documents.parquet` keyed by `doc_id` and joined at read
  time. Denormalising them into every cell multiplies the corpus by the question count on disk for no
  benefit.
- Shards partitioned by `run_id` and sequence. Finalised shards are never rewritten.

---

## 7. Budget governor

Mandatory on every spending command (D8).

- **Integer micro-dollars throughout.** Float drift accumulated over millions of calls is a bug that
  costs money.
- Charge **attempts**, not successes. Retries and discarded calls cost real money at the provider.
- Admission control: admit a call only if `spent + projected ≤ budget`. Overshoot is bounded by
  in-flight concurrency, which is itself bounded.
- On breach: stop admitting, drain, finalise, checkpoint, exit non-zero, print the exact resume
  command.
- `schema-tune` and `validate` sampling charge against the same budget.
- `estimate` never spends.

---

## 8. Adaptive concurrency

Rate limits are unpublished and request volume is high (one call per document per group).

- AIMD: additive increase after a clean window, halve on any `429`/`529`.
- Floor 1, configurable ceiling, exponential backoff with full jitter. Honour `Retry-After` when
  present.
- **Distinguish request-rate throttling from token-rate throttling** in the display where the API
  makes it possible. At millions of small requests, request rate is the likely binding constraint,
  and a user watching a slow run should be able to see which limit is biting rather than guess.
- Surface current concurrency in the progress line.

---

## 9. Long documents

- Documents fitting within `context − schema − margin` are sent whole.
- Documents exceeding it are chunked with overlap, each chunk answered separately, then aggregated
  per question:
  - `noul`: max probability by default, mean configurable.
  - `choice`: probability-weighted vote across chunks.
  - `score`: mean of weighted scores — and remember the docs warn score levels are weak numerically,
    so treat the aggregate as threshold material only.
- Aggregated cells carry `chunk_count > 1` and are excluded from validation samples by default, where
  they would otherwise distort accuracy figures.
- Truncation is never silent. If opted into, it is recorded per cell.
- This is the one place the indexed-state idiom from `00-API-NOTES.md` §6 is legitimate: chunks of a
  single document are genuinely related content, which is the case the pattern was documented for.

---

## 10. Thresholds and the review queue

Thresholding is a **read-time** operation over stored distributions (D5), and thresholds are **per
question and per type** (D6).

- `choice` / `score`: threshold on the returned `confidence`.
- `noul`: threshold on distance from 0.5, on its own scale. **Never reuse a noul threshold on a
  choice or vice versa** — the docs give a measured counterexample where the same question returned
  0.22 as a noul and 0.01 as a choice probability.
- Cells below threshold export to `review_queue.csv` with document text, answer, distribution and a
  blank `human_answer` column.
- Re-import applies human answers as an **override layer stored separately** from model output, so the
  two are never conflated in analysis.
- Default thresholds in YAML are placeholders. `validate` replaces them with measured values.

---

## 11. Validation and calibration

`cartograph label` produces a gold-set sample, **stratified across the probability range**, not
uniform. Uniform sampling on a skewed corpus yields easy cases and a flattering, useless accuracy
number.

`cartograph validate` reports per question:

- accuracy against gold labels, with sample size and a confidence interval;
- expected calibration error, plus a reliability diagram as data;
- the threshold achieving a target accuracy (default 0.99) and the **coverage** at that threshold;
- a pass/fail verdict against the question's `gate`.

Because structural invariants do not hold (D6), the report never aggregates accuracy across question
types into a single headline number. Per-question or nothing.

Output is a committed Markdown report plus machine-readable data. It is the project's single most
important credibility artifact.

---

## 12. CLI surface

```
cartograph estimate     --input <src> --questions <yaml> [--sample N]
cartograph schema-tune  --input <src> --questions <yaml> --budget <usd>
cartograph run          --input <src> --questions <yaml> --budget <usd> --out <dir>
                        [--resume <run_id>] [--keep-raw <frac>] [--concurrency-max N]
cartograph label        --run <run_id> --question <id> [--n 200]
cartograph validate     --run <run_id> --gold <csv>
cartograph report       --run <run_id>
cartograph cache        stats | prune | clear
```

Safety rules built into the surface:

- `--budget` required by `run` and `schema-tune`. No unbounded run mode exists.
- `run` prints the estimate and requires confirmation unless `--yes`.
- `--resume` refuses on `questionset_hash` mismatch and names the changed questions.
- Progress always shows: documents done, spend vs cap, throughput, current concurrency, cache hit
  rate, ETA. Cached and paid documents are shown **separately** — a progress bar implying spend when
  nothing is being spent erodes the trust D8 exists to build.

---

## 13. Open questions

Resolved: *how does one question address N packed documents?* — it cannot, and should not.
See `00-API-NOTES.md` §6.

Outstanding, to be settled empirically and recorded here:

- **B.** Per-call limit on question count? Undocumented. Discover; encode as a planner constraint.
- **D.** Actual rate limits, and whether request-rate or token-rate binds first at this volume.
- **F.** Pin an explicit model version for long runs instead of `jev-latest`? Cookbooks pin. Leaning
  pin-and-record.
- **G.** Are question ids billed, given they are not sent to the model? Measure with a controlled pair
  of calls; if free, ids can be fully descriptive at no cost.
- **H.** The accuracy-versus-verbosity curve (§3). The central empirical question of this design.
- **I.** Does a projected state measurably outperform a full-record state on the same question? This
  quantifies context rot and justifies the planner's grouping. Worth publishing on its own.
