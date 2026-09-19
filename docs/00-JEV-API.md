# 00 — Verified API Notes

> Transcribed from TypeSafe's published documentation on **2026-09-19**:
> `docs.typesafe.ai/api.md`, `/concepts/state`, `/confidence`, `/cookbooks/parallel_questions`,
> `/model-jaggedness/jev-1.13`.
>
> This document exists so the implementing agent does not re-derive the contract or repeat the
> mistake recorded in §6. **Re-verify before implementation** — the model is young and moving.

---

## 1. Request

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

```json
{
  "state":  "...",            // string | object | array   (required)
  "model":  "jev-latest",     // string                    (required)
  "questions": {              // map<string, Question>     (required)
    "your_question_id": { "type": "noul", "instructions": "..." }
  }
}
```

**The single most important sentence in the docs**, from `/concepts/state`:

> *"Each request evaluates one state against one or more questions. All questions see the same
> state and are evaluated independently."*

One state per request. Every question in the call is asked about that whole state. There is no
mechanism for a question to be answered separately per element of the state.

**Question ids are free.** The docs state the key *"is not sent to the underlying model and is not
used in inference."* Use descriptive ids without worrying about tokens. (Confirm they are also not
billed — see Open Questions.)

---

## 2. Question types

All three share `type` and `instructions`. Each has its own `criteria`.

| Type | `criteria` | Required? | Shape |
|---|---|---|---|
| `noul` | what yes and no mean | **optional** | `{"true": "...", "false": "..."}` |
| `choice` | option → rubric | **required** | `{"option_a": "description", "option_b": null}` |
| `score` | ordered level descriptions | **required**, ≥2 | `["Calm", "Frustrated", "Very angry"]` |

`instructions` accepts `string | object | array` for all three.

Note the cost asymmetry: **`noul` is the only type whose criteria are optional**, which makes it the
cheapest primitive per question. This matters enormously — see §5.

---

## 3. Answers

Returned under the same ids, in an `answers` map, alongside `usage.input_tokens` /
`usage.output_tokens`.

**The three answer shapes are not uniform, and the design must not pretend they are:**

| | `noul` | `choice` | `score` |
|---|---|---|---|
| value field | `noul` (0–1) | `choice` (string) | `score` (number, can be fractional) |
| `probabilities` | **absent** | option → prob, sums to 1 | level index (string) → prob, sums to 1 |
| `confidence` | **absent** | present | present |
| `legend` | absent | absent | present — level index → description |

> From `/confidence`: *"(Noul answers don't carry one.)"*

**Consequences for the design:**

- A `noul` answer is a single float. The float *is* the probability of yes. There is no
  distribution to store and no confidence to threshold on.
- Any confidence for a `noul` must be **derived by us** (distance from 0.5 is the obvious choice) and
  must be labelled as derived, never presented as model-reported.
- `score` probabilities are keyed by **level index as a string** (`"0"`, `"1"`, `"2"`), not by level
  name. The `legend` maps them back. Store the legend or resolve names at write time, or the output
  is uninterpretable later.
- `confidence` is a convenience statistic derived from `probabilities`. The docs are explicit that
  callers may compute their own. Since we store full distributions anyway, this is available.

---

## 4. Errors

| Status | Meaning | Our class |
|---|---|---|
| `401` | missing/invalid key | fatal, config |
| `422` | body failed validation; body names the field | fatal, config |
| `429` | rate limited | transient — backoff |
| `529` | overloaded | transient — backoff |

Docs prescribe exponential backoff for `429`/`529`. Rate limit values are **not published**.

---

## 5. The cost model, derived

Billing is on input tokens at $0.042/MTok; output is free. Input for one call is:

```
tokens(state)  +  Σ over questions of tokens(instructions + criteria)
```

Two consequences, and they point in opposite directions.

**(a) Batching all questions about one document into one call is a genuine, free win.**
From `/cookbooks/parallel_questions`:

> *"The document dominates every request. N single-question calls pay for it N times, in N round
> trips; the batched call pays once. The bigger the document, the nearer that saving comes to a
> full Nx."*

That cookbook measured **12.2x cheaper and 10.0x faster** for 13 questions over a ~54,000-character
article. It also verified, across 5 repeats per strategy, that **answers are identical either way** —
*"each question is scored on its own against the document, so its answer doesn't depend on what else
is in the request."* Most answers had a run-to-run standard deviation of exactly 0.0.

So question batching carries **no accuracy cost**. Always batch. The saving scales with state size.

**(b) The question schema is paid on every single document.**
For short documents the schema dominates completely. A 20-token title with a 650-token question
battery is 97% schema. On a short-document corpus, the only real cost levers are: fewer questions,
terser criteria, and preferring `noul` (whose criteria are optional).

---

## 6. Why we do **not** pack multiple documents into one state

This was the original architecture. It is wrong, and the reasoning is recorded here so it is not
reinvented.

The idea was to put 20 documents in one state and ask questions per index, amortising the schema 20
ways. Since one question yields exactly one answer about the whole state, per-document answers
require **per-document questions**. So:

```
packed:    N×tokens(doc)  +  N × Q × tokens(schema)
unpacked:  N × [ tokens(doc) + Q × tokens(schema) ]
```

These are **equal**. Packing saves nothing. It only reduces HTTP request count.

And it costs accuracy. Jaggedness failure mode #5, verbatim:

> *"Large state full of irrelevant detail — accuracy falls as the state grows with content unrelated
> to the decision. Unrelated detail acts as a distractor."*
> *"Jev suffers from context rot, so unrelated material in the `state` costs you accuracy."*

Nineteen unrelated documents sitting beside the one being asked about is precisely that
anti-pattern. Packing trades measurable accuracy for zero tokens.

**However**, indexing *into* a structured state is an official idiom — for genuinely related items.
The jaggedness doc's counting example passes `{"items": [...]}` and asks one `Noul` per index
(`"Is items[3] the name of a fruit?"`). We reserve this pattern for the long-document chunking case
and for related-record states, never as a bulk cost optimisation.

**Standing rule: one document per call, all questions batched.**

---

## 7. Known model limitations (`jev-1.13`, reviewed 2026-09-17)

Directly shapes question design and what the flagship analysis may claim.

| # | Failure mode | Consequence for us |
|---|---|---|
| 1 | **Literal reading** — answers the question as written, not as meant | Instructions must state the exact condition; boundary cases belong in criteria |
| 2 | **Math and counting** — does not count reliably; error grows with size | Never ask a counting question. Count in code by asking one question per item |
| 3 | **Dates and times** — reads dates as text, not ordered quantities | Extract components as `choice`, compare in code |
| 4 | **Indirection** — multi-hop reasoning costs accuracy | Keep questions one hop; name the relevant part of state |
| 5 | **Large state with irrelevant detail** — context rot | Project state down to what the question needs. See §6 |
| 6 | **Adversarial content** — text arguing for its own classification can steer the model | Real risk on public corpora. Disclose in methodology |
| 7 | **Contradictory instructions vs criteria** | Criteria are an extension of the instruction; align them |
| 8 | **Structural invariants do not hold** | See below — this one has teeth |
| 9 | **Generation** — not trained for it | Bounded answer spaces only |

### On #8, because it directly constrains the validation design

The docs give measured examples: the same question asked as a `Noul` and as a yes/no `Choice`
returned `noul = 0.22` against `choice.probabilities["yes"] = 0.01`. A question and its negation as
two Nouls returned 0.72 and 0.47 — summing to 1.19, not 1.0.

> *"Don't carry a threshold tuned on a Noul over to a Choice, and don't hold the model to arithmetic
> identities between separate questions."*

Therefore thresholds are **per question and per type**, always. No global threshold, no
cross-question arithmetic, no assuming `P(x) + P(not x) = 1`.

Also: *"a Choice over options and one Noul per option answer different questions: the Choice is
relative, settling which option, while each Noul is absolute and can be low for all of them."*
That is a genuine modelling decision for each question, not an implementation detail.

### On score arithmetic

> *"Do not use score outputs to compute the exact magnitude of a number between two levels... you can
> use the expectation to check if it passes a particular threshold, but score levels are weak in
> numerical calibration."*

The weighted `score` is threshold material, not a measurement. Never average it across a corpus and
present the mean as a quantity.

---

## 8. Other constraints

- **Text only.** String, JSON object, or array of text values. No images, audio or video.
- **English is the primary training language.** Other languages including CJK are accepted at
  *lower accuracy*. Relevant to corpus selection and to any per-language accuracy claims.
- **Bounded context window**, ~32k tokens per the model page. Verify the exact figure.
- Official SDKs: Python (sync + async, with a retry policy) and JavaScript. Installed from
  TypeSafe's own index: `--extra-index-url https://pypi.typesafe.ai/`.
- Published cookbook code pins an explicit version (`jev-1.12`) rather than using `jev-latest`.
  Worth weighing for long runs — see Open Questions.

---

## 9. Open questions still outstanding

Question A from the original design — *how does one question address N packed documents?* — is
**resolved: it cannot, and it should not.** See §6. The remainder:

- **B.** Is there a per-call limit on question count? Not documented. Discover and encode it.
- **D.** What are the actual rate limits? Measure. Feed into adaptive concurrency.
- **F.** Should long runs pin an explicit version rather than `jev-latest`, so the model cannot change
  mid-run? The cookbooks pin. Leaning pin-and-record.
- **G.** Are question ids billed, given they are not sent to the model? Measure with a controlled pair
  of calls.
- **H.** How much does criteria verbosity actually cost in accuracy? This is the central empirical
  question of the schema-economy work in `03-DESIGN.md` §2.
