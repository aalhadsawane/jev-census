# cartograph

**Ask twenty typed questions of every row in a million-row corpus, and know what it will cost before
you start.**

`cartograph` is a batch semantic-labelling engine for [TypeSafe's Jev](https://typesafe.ai), the
first System One model. Jev does not generate text. It evaluates *typed questions* — `Choice`,
`Score`, `Noul` — against a state and returns calibrated probability distributions. Input costs
$0.042 per million tokens; output is free.

That pricing makes corpus-scale semantic labelling affordable for the first time. What it does not
do is make it *easy*: a run over millions of rows needs batching, resumption, cost control,
calibration and provenance, or it is a script that burns money and produces numbers nobody should
trust. `cartograph` is that missing layer.

> **Status: pre-implementation.** This repository contains design documents only. No code is
> written yet. The documents come first because several decisions here cannot be retrofitted, and
> because the first version of this design was **wrong in an instructive way** — see below.
>
> Start with [`docs/00-API-NOTES.md`](docs/00-API-NOTES.md), then
> [`docs/01-MOTIVATION.md`](docs/01-MOTIVATION.md).

---

## The economics, correctly

Billing per call is:

```
tokens(state)  +  Σ over questions of tokens(instructions + criteria)
```

Three consequences follow, and together they define the engine.

**1. Batch every question about a document into one call. This is free.**
One state per request, and all questions see it. N separate calls pay for the document N times; one
batched call pays once. TypeSafe's own cookbook measures **12.2x cheaper and 10.0x faster** for 13
questions over a 54,000-character article — and verifies across repeated runs that the answers are
*identical* either way, because each question is scored independently. There is no accuracy cost.
Always batch. The saving grows with document size.

**2. The question schema is paid on every single document.**
A 20-token title carrying a 650-token question battery is 97% schema. On short-document corpora the
levers are therefore: ask fewer questions, write terser criteria, and prefer `noul` — the only
primitive whose criteria are optional. Finding the *minimum sufficient schema* is a measurable
optimisation problem, and it is this project's core technical contribution.

**3. State must be projected down to what each question needs.**
Jev suffers documented context rot: *"accuracy falls as the state grows with content unrelated to
the decision."* But batching forces every question in a call to share one state. Cheap wants one big
call; accurate wants narrow states. Resolving that tension — grouping questions into calls by the
state projection they need — is the planner's job.

### The mistake this design already made

The first version of this plan proposed packing 20 documents into a single state to amortise the
schema twenty ways, projecting a 10x saving. **That is wrong.** Because one question returns exactly
one answer about the whole state, per-document answers need per-document questions, so packed and
unpacked cost *identical* tokens — packing only reduces request count. And it walks directly into
the documented context-rot failure mode by surrounding each document with nineteen irrelevant ones.

The reasoning is preserved in [`docs/00-API-NOTES.md`](docs/00-API-NOTES.md) §6 so it is not
reinvented. **One document per call, all questions batched** is the standing rule.

---

## Intended experience

```
$ cartograph estimate --input hn.parquet --questions hn.yaml
  5,847,221 documents · 14 questions · 2 call groups
  unbatched (14 calls/doc):  3.60B tokens   ~$151   
  batched   (2 calls/doc):   2.28B tokens    ~$96    ← planned
  schema is 94% of spend — run `cartograph schema-tune` to reduce it

$ cartograph validate --run hn-2026 --gold labels.csv
  is_show_hn    noul    acc 0.97  n=240  →  |p-0.5|>0.34 covers 94% at 0.99
  topic         choice  acc 0.91  ECE 0.04  n=300  →  conf>0.80 covers 78% at 0.99
  tone          score   acc 0.68  ECE 0.19  n=200  →  FAILS gate — exploratory only

$ cartograph run --input hn.parquet --questions hn.yaml --budget 100 --out results/
  [====------] 41%  2.4M docs  $39.10/$100.00  1,840 docs/s  cache 12%  eta 47m
```

*Numbers above are illustrative. `cartograph estimate` produces the real ones, and every figure
published anywhere in this repository must be reproducible by a command in it.*

---

## Documents

| Document | What it is | Read when |
|---|---|---|
| [`docs/00-API-NOTES.md`](docs/00-API-NOTES.md) | Verified API contract, the derived cost model, model limitations, and why document packing was rejected. | **First.** |
| [`docs/01-MOTIVATION.md`](docs/01-MOTIVATION.md) | Design drivers, each with its retrofit cost. Anti-goals. Success criteria. | **Second.** |
| [`docs/02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md) | Pipeline, stage contracts, data model, failure and recovery model. | Before fixing module boundaries. |
| [`docs/03-DESIGN.md`](docs/03-DESIGN.md) | Question-set format, call planning, schema economy, cache keys, output schema, CLI. | While implementing a stage. |
| [`docs/04-AGENT-GUIDE.md`](docs/04-AGENT-GUIDE.md) | Build order, hard rules, testing strategy, traps. | Before the first commit. |
| [`docs/05-FLAGSHIP.md`](docs/05-FLAGSHIP.md) | The launch analysis: corpus choice, question design, methodology, honesty rules. | Once the engine runs end to end. |

---

## Non-goals

Not an agent framework, not a chat wrapper, not a server, not a vector database, not a
multi-provider abstraction, and it generates no text. It turns a corpus and a question set into a
calibrated, provenanced table. Each exclusion is justified in `docs/01-MOTIVATION.md`.

## Jev facts, verified 2026-09-19

Re-verify before relying on these. Full detail and citations in `docs/00-API-NOTES.md`.

| | |
|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Auth | `Authorization: Bearer $TYPESAFE_API_KEY` |
| Model | `jev-latest`; cookbooks pin explicit versions such as `jev-1.13` |
| Price | $0.042 / MTok input · output free |
| Latency | 70–500ms end to end |
| Context | bounded, ~32k tokens |
| Request | exactly **one** `state` + a map of questions; all questions see the same state |
| `noul` | criteria optional · returns a bare probability · **no confidence, no distribution** |
| `choice` | criteria required · returns choice + distribution + confidence |
| `score` | ordered levels required · returns weighted score + legend + distribution + confidence |
| Modality | text only; English primary, other languages lower accuracy |
| Cannot | generate text, count reliably, do arithmetic, or compare dates |

## Licence

Apache-2.0, matching the ecosystem norm. Confirm before first publish.
