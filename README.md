# jev-census

**Ask the same questions of every row in a dataset, and get back a table.**

You have 480,000 support tickets. You want to know which are urgent, which team each belongs to,
how angry the customer is, and which ones smell like churn. You would know all of that if you had
infinite interns. You don't, so you have keyword rules that half-work.

`census` asks those questions — all of them, of every ticket — and hands you a Parquet table with the
answers, the probability behind each one, and a report telling you how accurate each question
actually is. It runs on [TypeSafe's Jev](https://typesafe.ai), a model that returns typed decisions
instead of text at $0.042 per million input tokens.

The 480,000 tickets above cost **about $7** and **half an hour**.

---

## The whole product, end to end

### 1. You have a corpus

`tickets.parquet` — 480,000 rows.

| ticket_id | created_at | subject | body |
|---|---|---|---|
| T-1041 | 2026-03-02 | Payouts failing | Help! My payouts have been failing for 3 days and nobody has replied. |
| T-1042 | 2026-03-02 | How do I export? | Where did the CSV export go in the new dashboard? |
| T-1043 | 2026-03-02 | Considering Zendesk | We're evaluating alternatives. This is the third outage this month. |

### 2. You write the questions once

`support-triage.yaml`

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
$ census estimate --input tickets.parquet --questions support-triage.yaml

  480,000 documents · 4 questions · 1 call group
  tokens/doc:  state 120 (34%)  ·  schema 230 (66%)
  total:       168M tokens  ≈  $7.06
  runtime:     ~32 min at 250 docs/s

  schema is the larger half — `census schema-tune` can price a terser question set
```

### 4. You run it

```
$ census run --input tickets.parquet --questions support-triage.yaml --budget 10 --out results/

  [========--] 78%  374k docs  $5.51/$10.00  251 docs/s  cache 0%  eta 7m
```

### 5. You get three artifacts

**`results/cells.parquet`** — one row per (document, question). The distributions are kept, always,
because throwing them away means re-running the corpus to change your mind about a threshold.

| doc_id | question_id | type | noul | choice | score | probabilities | confidence | confidence_source |
|---|---|---|---|---|---|---|---|---|
| T-1041 | is_urgent | noul | **0.97** | – | – | – | 0.94 | derived |
| T-1041 | department | choice | – | **billing** | – | `{billing: .91, technical: .06, sales: .03}` | 0.88 | model |
| T-1041 | frustration | score | – | – | **2.7** | `{"0": .02, "1": .08, "2": .19, "3": .71}` | 0.74 | model |
| T-1041 | churn_risk | noul | **0.21** | – | – | – | 0.58 | derived |
| T-1042 | is_urgent | noul | **0.04** | – | – | – | 0.92 | derived |
| T-1042 | department | choice | – | **technical** | – | `{technical: .83, billing: .11, sales: .06}` | 0.79 | model |
| T-1043 | churn_risk | noul | **0.96** | – | – | – | 0.92 | derived |

**`results/validation_report.md`** — how much you may trust each column, measured against labels you
hand-checked, not asserted.

```
question        type    accuracy  n     ECE    threshold      coverage   verdict
is_urgent       noul    0.96      240   —      |p-0.5| > 0.31   94%      PASS
department      choice  0.93      300   0.031  conf > 0.74      86%      PASS
churn_risk      noul    0.91      240   —      |p-0.5| > 0.40   71%      PASS
frustration     score   0.68      200   0.190  —                 —       FAIL — exploratory only
```

**`results/review_queue.csv`** — the 6% that came back uncertain, for a human, instead of a confident
wrong answer.

### 6. You use it like any other table

```sql
SELECT date_trunc('week', created_at) AS week,
       count(*) FILTER (WHERE is_urgent > 0.8)              AS urgent,
       count(*) FILTER (WHERE churn_risk > 0.9)             AS at_risk,
       count(*) FILTER (WHERE department = 'billing')       AS billing
FROM  labelled
GROUP BY 1 ORDER BY 1;
```

Four columns that did not exist an hour ago, on 480,000 rows, for $7. A frontier LLM doing the same
work costs roughly **$1,600**. A team of humans at two minutes a ticket is about **eight person-years**.

---

## Why Jev, specifically

Jev is not an implementation detail here — the product only exists because of what this model does
differently, and every design decision downstream traces back to one of these properties.

| Jev property | What it makes possible |
|---|---|
| **Typed decisions, not text** — you predefine the classes and it picks among them | No JSON prompting, no parsing layer, nothing to validate. A malformed answer is not a failure mode that exists |
| **Calibrated probabilities on every answer** | The `validation_report.md` above. You can measure how much to trust a column and set a threshold from data instead of hope |
| **$0.042/MTok in, output free** | 480,000 tickets for $7. At frontier-LLM prices this table costs $1,600 and stops being worth making |
| **70–500ms** | Half an hour for the corpus, not a weekend |
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
| A corpus — Parquet, CSV, JSONL, DuckDB | `cells.parquet` — every answer with its full probability distribution and provenance |
| A question set — one YAML file | `validation_report.md` — per-question accuracy, calibration, recommended thresholds |
| A budget ceiling in dollars | `review_queue.csv` — the uncertain rows, for a human |
| A Jev API key | `manifest.json` — exactly how every number was produced |

---

## Why this isn't a `for` loop around the API

The loop works for 500 rows. Here is what happens at 480,000, and each line is a component of this
product:

1. **You'd send the ticket four times.** One state per request, and every question in a call sees it.
   Batching all four questions into one call pays for the ticket once instead of four times — 3.7x
   cheaper, verified by TypeSafe's own cookbook to return identical answers.
2. **It will die at row 310,000.** Without checkpointing, resuming means paying twice.
3. **You'll add a fifth question next week.** Without per-cell caching, that re-runs all five.
4. **You'll want a different threshold.** Without stored distributions, that's a full re-run.
5. **You won't know if `frustration` is any good.** It isn't — 0.68 above. Without a validation gate
   you'd have shipped a dashboard built on it.
6. **Nothing stops a bug at 3am.** Without a hard budget cap, a retry storm is a surprise invoice.

`census` is the difference between "I called a classifier a lot" and "here is a labelled dataset,
and here is how accurate each column is."

---

## What it is not

Not an agent, not a chat wrapper, not a server, not a vector database, not a text generator, and not
a general LLM framework. It turns a corpus and a question set into a calibrated table. Jev does the
classifying; `census` makes the result trustworthy, affordable and reproducible.

---

## Status

**Engine built, quality phase next.** Phases P0–P4 are complete and merged: `census run` and
`census estimate` work end to end against the live API, survive `kill -9` and `SIGINT`, cache at cell
granularity, cap spend in integer micro-dollars, and have run 120,000 documents unattended. P5–P8 —
projections, the validation report, packaging and the launch analysis — are specified and not yet
built, so the figures above are still design projections rather than measured numbers. P7 replaces
each of them with output from a command in this README.

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
| [`DECISIONS.md`](DECISIONS.md) | Divergences from the documents above, and why. Newest first. |

## The name

A census asks a fixed questionnaire of every member of a population and publishes a table with its
sampling method and error bars attached. That is exactly this: a fixed question set, every row, a
table, and a validation report. It also gives the launch analysis its headline — *a census of
5.8 million Hacker News posts.*

The `jev-` prefix is deliberate. Jev is what makes the product possible, and in an ecosystem this
young the model's name is also how people find the tool.

- Repository and package: **`jev-census`** — `pip install jev-census`
- Command: **`census`** — short enough to type in the examples above

Licence: Apache-2.0, matching the ecosystem norm.
