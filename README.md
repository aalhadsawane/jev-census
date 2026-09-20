# jev-census

**Ask the same questions of every row in a dataset, and get back a table.**

You have a queue of support tickets, or a firehose of Hacker News posts, or a folder of long
documents. You want structured answers to the same questions on every one of them. You'd get there
with infinite interns; you have keyword rules that half work instead.

`census` asks those questions — all of them, of every row — and hands you a Parquet table with the
answers, the probability behind each one, and a report telling you how accurate each question actually
is. It runs on [TypeSafe's Jev](https://typesafe.ai), a model that returns typed decisions instead of
text, at $0.042 per million input tokens.

## Try it for real, right now

```
$ pip install jev-census
$ export TYPESAFE_API_KEY=...
$ census demo
```

1,910 real Hacker News story titles, 5 questions, a real validation report — **$0.057, 43 seconds**,
no clone required. Full transcript below.

---

## `census demo`, unedited

```
$ census demo

  demo: 1,910 Hacker News stories · 5 questions · 1 call group
  estimate: 797.8K tokens ≈ $0.03 · budget $0.25

  [====================] 100%  1,910/1,910 docs  $0.0569/$0.25  45.3 docs/s  cache 0%  eta 0s

  wrote demo-results/cells.parquet          9,550 cells
  wrote demo-results/validation_report.md   5 questions, 3 PASS, 1 exploratory

  next:  duckdb -c "SELECT * FROM 'demo-results/cells.parquet' LIMIT 5"
         docs/RECIPES.md has the long→wide pivot and thresholding queries
```

```
question               type    accuracy  n   ECE    threshold       coverage  verdict
is_question_post       noul    1.00      30  —      |p-0.5| > 0.12  100%      PASS
likely_controversial   noul    0.97      30  —      —               —         PASS
shows_something_built  noul    0.90      30  —      —               —         PASS
technical_depth        score   0.57      30  0.230  —               —         FAIL — exploratory only
topic_area              choice  0.83      30  0.094  —               —         FAIL
```

`topic_area` genuinely misses its gate (0.83, floor 0.90) — not smoothed over. The corpus, question
set, gold set, and full report all ship inside the package; see
[`examples/hacker-news/`](examples/hacker-news/) for the writeup and
[`src/jev_census/demo_data/SOURCE.md`](src/jev_census/demo_data/SOURCE.md) for exactly where the data
came from.

---

## The whole product, end to end

This walkthrough uses [`examples/support-tickets/`](examples/support-tickets/) — 30 synthetic support
tickets written for this repo (not real customer data), scored against 30 hand-labelled gold answers.
Every command below is copy-pasteable from that directory; every number is what it actually produced.

### 1. You have a corpus

`tickets.parquet`

| ticket_id | subject | body |
|---|---|---|
| T-0001 | Payouts failing | Help! My payouts have been failing for 3 days and nobody has replied. |
| T-0002 | How do I export? | Where did the CSV export go in the new dashboard? |
| T-0003 | Considering Zendesk | We're evaluating alternatives. This is the third outage this month. |

### 2. You write the questions once

`questions.yaml`

```yaml
version: 1
name: support-triage
defaults:
  projection: [subject, body]      # the only fields questions see

questions:
  - id: is_urgent
    type: noul                     # yes/no → a probability
    instructions: The customer states or implies the problem is time-sensitive.
    criteria:
      "true":  Blocked work, money at risk, or an explicit deadline.
      "false": A question or request with no stated time pressure.

  - id: department
    type: choice                   # pick one → a distribution over options
    instructions: The team that should handle this ticket.
    criteria:
      billing:   Payments, invoicing, refunds, payouts.
      technical: Bugs, outages, integrations, API errors.
      sales:     Pricing, upgrades, new accounts, renewals.

  - id: frustration
    type: score                    # ordered levels → a weighted value
    instructions: How frustrated the customer sounds.
    criteria:
      - Calm and neutral.
      - Mildly annoyed.
      - Clearly frustrated.
      - Angry, or threatening to escalate.

  - id: churn_risk
    type: noul
    instructions: The customer signals they may stop using the product.
    criteria:
      "true":  Mentions cancelling, competitors, or a failed evaluation.
      "false": No indication of leaving.
```

### 3. You check the price before you spend it

```
$ census estimate --input tickets.parquet --questions questions.yaml --id-field ticket_id

  30 documents · 4 questions · 1 call group
  tokens/doc:  state 35 (14%)  ·  schema 208 (86%)
  total:       7.3K tokens  ≈  $0.00
  runtime:     ~0 sec at 250 docs/s

  schema is the larger half — `census schema-tune` can price a terser question set
```

### 4. You run it

```
$ census run --input tickets.parquet --questions questions.yaml --budget 1 --out results --id-field ticket_id
  spent $0.0007, 30 documents
```

### 5. You get real artifacts

**`results/cells.parquet`** — one row per (document, question), real output from the run above. The
distributions are kept, always, because throwing them away means re-running the corpus to change your
mind about a threshold.

| doc_id | question_id | type | noul | choice | score | confidence | confidence_source |
|---|---|---|---|---|---|---|---|
| T-0001 | is_urgent | noul | **0.94** | – | – | 0.88 | derived |
| T-0001 | department | choice | – | **billing** | – | 1.00 | model |
| T-0001 | frustration | score | – | – | **2.04** | 0.96 | model |
| T-0001 | churn_risk | noul | **0.11** | – | – | 0.78 | derived |
| T-0002 | is_urgent | noul | **0.10** | – | – | 0.80 | derived |
| T-0002 | department | choice | – | **technical** | – | 1.00 | model |
| T-0003 | churn_risk | noul | **0.88** | – | – | 0.76 | derived |

**`validation_report.md`** — how much you may trust each column, measured against 30 hand-checked
labels, not asserted:

```
question     type    accuracy  n   ECE    threshold  coverage  verdict
churn_risk   noul    0.97      30  —      —          —         PASS
department   choice  0.87      30  0.110  —          —         FAIL
frustration  score   0.80      30  0.130  —          —         FAIL
is_urgent    noul    0.90      30  —      —          —         PASS
```

Two real, disclosed failures, at this sample size — full writeup in
[`examples/support-tickets/README.md`](examples/support-tickets/README.md).

### 6. You use it like any other table

```sql
-- docs/RECIPES.md has this and three more, tested against real output
PIVOT (
  SELECT doc_id, question_id, COALESCE(noul::VARCHAR, choice, score::VARCHAR) AS value
  FROM 'results/cells.parquet'
)
ON question_id USING FIRST(value)
ORDER BY doc_id;
```

Four columns that did not exist a minute ago. On the Hacker News demo above — 1,910 real documents,
5 questions each — that's real, at **$0.057**. A frontier LLM doing the same work costs roughly
**$8.37**: that run charged 1,357,143 input tokens (backed out from the real $0.057 spend at Jev's
published $0.042/MTok); at a representative frontier price of $3/MTok in, the same input costs $4.07,
and — since a frontier model has to *generate* a structured answer rather than pick from a predefined
set — a conservative 150 output tokens per document (a small JSON block per document) at $15/MTok adds
another $4.30. **$8.37 vs $0.057 is about 147x.** Recompute this yourself from current prices; the
methodology, not the multiplier, is the point.

---

## Why Jev, specifically

Jev is not an implementation detail here — the product only exists because of what this model does
differently, and every design decision downstream traces back to one of these properties.

| Jev property | What it makes possible |
|---|---|
| **Typed decisions, not text** — you predefine the classes and it picks among them | No JSON prompting, no parsing layer, nothing to validate. A malformed answer is not a failure mode that exists |
| **Calibrated probabilities on every answer** | The `validation_report.md` above. You can measure how much to trust a column and set a threshold from data instead of hope |
| **$0.042/MTok in, output free** | The comparison above. At frontier-LLM prices this table costs ~150x more and stops being worth making casually |
| **70–500ms** | Under a minute for 1,910 documents, not a weekend |
| **One state, many questions, scored independently** | Ask 20 questions for barely more than 1 — verified by TypeSafe's own cookbook at 12.2x cheaper with identical answers |

And the constraints matter just as much. Jev **cannot generate text**, cannot count, cannot do
arithmetic, reads instructions literally, and loses accuracy as irrelevant context grows. Those limits
are why this tool looks the way it does: fixed question sets instead of prompts, code holding all
control flow, narrow state projections, and a validation gate before anything gets published.

Full contract and documented limits: [`docs/00-JEV-API.md`](docs/00-JEV-API.md).

---

## What you provide, what you get

| You provide | You get |
|---|---|
| A corpus — Parquet or CSV | `cells.parquet` — every answer with its full probability distribution and provenance |
| A question set — one YAML file | `validation_report.md` — per-question accuracy, calibration, recommended thresholds |
| A budget ceiling in dollars | `review_queue.csv` — the uncertain rows, for a human |
| A Jev API key | `manifest.json` — exactly how every number was produced |

---

## Why this isn't a `for` loop around the API

The loop works for 500 rows. Here is what happens at scale, and each line is a component of this
product, not a hypothetical:

1. **You'd send the document once per question.** One state per request, and every question in a call
   sees it — batching pays for the document once instead of once per question.
2. **It will die partway through.** `kill -9` mid-run and resume: zero duplicate cells, zero re-paid
   cells — an automated chaos test proves this on every change, not just once.
3. **You'll add a fifth question next week.** Without per-cell caching, that re-runs all five;
   `census` re-asks only the new one.
4. **You'll want a different threshold.** Without stored distributions, that's a full re-run.
5. **You won't know if a question is any good.** `frustration` above isn't, at 0.80. Without a
   validation gate you'd have shipped a dashboard built on it.
6. **Nothing stops a bug at 3am.** Without a hard, enforced budget cap, a retry storm is a surprise
   invoice.

`census` is the difference between "I called a classifier a lot" and "here is a labelled dataset, and
here is how accurate each column is."

---

## What it is not

Not an agent, not a chat wrapper, not a server, not a vector database, not a text generator, and not a
general LLM framework. It turns a corpus and a question set into a calibrated table. Jev does the
classifying; `census` makes the result trustworthy, affordable and reproducible.

---

## Examples

Three corpus shapes, because the cost story differs by shape:

| Example | Shape | Shows |
|---|---|---|
| [`examples/hacker-news/`](examples/hacker-news/) | Very short documents | Extreme schema dominance — this is `census demo` |
| [`examples/support-tickets/`](examples/support-tickets/) | Short documents | The walkthrough above, with two disclosed gate failures |
| [`examples/long-form/`](examples/long-form/) | Long documents | State-dominated cost, the batching win, and real chunking on a document over the context limit — including a real bug that scale exposed |

Recipes for working with the output: [`docs/RECIPES.md`](docs/RECIPES.md) — every query tested against
real `census demo` output.

---

## Status

**Engine and quality tooling built and live-verified; a production-scale gold-labelling campaign is the
remaining gap.** Phases P0–P7 are complete: `census run` and `census estimate` work end to end against
the live API, survive `kill -9` and `SIGINT`, cache at cell granularity, cap spend in integer
micro-dollars, plan real call groups from projections, and chunk documents over the context limit —
all live-verified, including on real long-form data that found and fixed a real bug (see
`DECISIONS.md`). `census label`/`validate`/`report`/`review`/`schema-tune`/`ablation` are built and
exercised end to end, including live against the real API, with real (if example-scale, honestly
disclosed) gold sets. What has not happened is a real ≥150–250-item gold set per question for a
production corpus — that needs sustained human judgment, not more engineering, and is P8's job before
any launch-scale claim gets made. See `DECISIONS.md`'s dated entries for exactly what was verified and
what wasn't, phase by phase.

| Document | What it holds |
|---|---|
| [`docs/00-JEV-API.md`](docs/00-JEV-API.md) | The verified Jev contract, cost model and documented model limits. Facts, with citations. |
| [`docs/01-DESIGN.md`](docs/01-DESIGN.md) | Design drivers, architecture, data model, question format, output schema, CLI. |
| [`docs/02-BUILD-PLAN.md`](docs/02-BUILD-PLAN.md) | The sequenced task list: 8 phases, explicit dependencies, done-when for each task. |
| [`docs/03-LAUNCH.md`](docs/03-LAUNCH.md) | The flagship public analysis and how it gets published honestly. |
| [`docs/04-P5-PLANNER.md`](docs/04-P5-PLANNER.md) | P5 spec: call groups, projection ids, context limit, chunking and aggregation. |
| [`docs/05-P6-QUALITY.md`](docs/05-P6-QUALITY.md) | P6 spec: stratified sampling, accuracy and calibration statistics, thresholds, schema-tune. |
| [`docs/06-P7-ADOPTION.md`](docs/06-P7-ADOPTION.md) | P7 spec: packaging, the bundled demo, example question sets, executable recipes. |
| [`docs/07-P8-EXECUTION.md`](docs/07-P8-EXECUTION.md) | P8 spec: run order and the gates every published claim has to clear. |
| [`docs/RECIPES.md`](docs/RECIPES.md) | DuckDB recipes for the output, tested against real `census demo` output. |
| [`docs/PROVENANCE.md`](docs/PROVENANCE.md) | Every number in this README, mapped to the exact command that produced it. |
| [`DECISIONS.md`](DECISIONS.md) | Divergences from the documents above, real bugs found, and why. Newest first. |

## The name

A census asks a fixed questionnaire of every member of a population and publishes a table with its
sampling method and error bars attached. That is exactly this: a fixed question set, every row, a
table, and a validation report. It also gives the launch analysis its headline — *a census of
Hacker News posts.*

The `jev-` prefix is deliberate. Jev is what makes the product possible, and in an ecosystem this
young the model's name is also how people find the tool.

- Repository and package: **`jev-census`** — `pip install jev-census`
- Command: **`census`** — short enough to type in the examples above

Licence: Apache-2.0, matching the ecosystem norm.
