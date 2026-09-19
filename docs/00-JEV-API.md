# 00 — Jev API: verified facts

Transcribed 2026-09-19 from `docs.typesafe.ai`: `/api.md`, `/concepts/state`, `/confidence`,
`/cookbooks/parallel_questions`, `/model-jaggedness/jev-1.13`.

**Re-verify before implementing.** Where the live API disagrees, the API wins and this file gets
fixed in the same commit.

---

## Access path (current)

TypeSafe's native endpoint below is **early access, not available to us yet.** We reach Jev through
**Vercel AI Gateway's evaluation modality** instead — a promo this week waives the charge but not
account verification (a card on file is required regardless). Verified live 2026-09-19 against the
real API; every number below is from an actual response, not a doc.

```ts
import { experimental_evaluate as evaluate } from "ai"; // "ai" >= 7.0.0

const result = await evaluate({
  model: "typesafe-ai/jev",
  state: "The support agent issued a full refund to the customer.",
  questions: {
    refunded: { type: "boolean", instructions: "Was a refund issued?" },
  },
});
```

`AI_GATEWAY_API_KEY` in the environment is all that's needed — no separate model import. Confirmed
live call, verbatim:

```json
{
  "answers": { "refunded": { "type": "boolean", "probability": 0.99 } },
  "usage": { "inputTokens": 282, "outputTokens": 21, "totalTokens": 303 },
  "rounding": { "probabilityDecimals": 2, "scoreDecimals": 2 },
  "providerMetadata": {
    "typesafe": { "confidence": {} },
    "gateway": { "cost": "0", "marketCost": "0.000011844" }
  }
}
```

Confirmed live, 5 of 6 planned cases (`choice`, `score`, a 3-question mixed-type batch, and a second
boolean all succeeded; structured object state did not — see below):

**Choice** — `state: "My card was charged twice for one order."`, options billing/shipping/technical:
```json
{ "type": "choice", "choice": "billing", "probabilities": { "billing": 1, "technical": 0, "shipping": 0 } }
```
`providerMetadata.typesafe.confidence` was `{ "route": 1 }` — **choice does carry confidence**, but it
lives in `providerMetadata`, not on the answer itself.

**Score** — rating a PR description against 4 ordered levels:
```json
{ "type": "score", "score": 2.93, "probabilities": { "0": 0, "1": 0, "2": 0.06, "3": 0.9400000000000001 } }
```
`confidence: { "quality": 0.93 }`. Note the float artifact on `0.94` — real floating-point noise from
summed rounded probabilities, not a formatting bug. **Never compare a `score` probability for equality;
round before comparing or displaying.**

**Mixed-type batch, one state, one round trip** (2 booleans + 1 score) — confirms D1's core claim:
```json
{
  "authIssue": { "type": "boolean", "probability": 0.97 },
  "wantsRefund": { "type": "boolean", "probability": 0.99 },
  "urgency": { "type": "score", "score": 1.62, "probabilities": { "0": 0, "1": 0.38, "2": 0.62 } }
}
```
`confidence` was `{ "urgency": 0.43 }` — **only choice/score question ids appear as keys in
`confidence`; boolean question ids are absent entirely, not present with a null or empty value.** This
is the precise, confirmed rule for the decoder: presence in the `confidence` map, not the answer
`type`, is what to branch on.

- **Naming shift at this layer.** `noul` → `type: "boolean"`, value key `probability` not `noul`.
  Semantics unchanged — still P(yes/true), still no distribution.
- **Pricing checks out exactly**, again: `marketCost` for 282 input tokens = `0.000011844` =
  `282 × 0.000000042`, matching $0.042/MTok to five decimal places — independent confirmation, not
  just cited from TypeSafe's own docs.
- **`outputTokens` is nonzero even though output is priced at $0.** Tokens are counted and reported;
  only billing is zero.
- **Results are rounded to 2 decimals** (`rounding` block on every response) — see the score float
  note above for why that doesn't mean clean arithmetic.

**Still not confirmed live:** structured (object) state. Both attempts failed on the rate limit below
rather than a validation error, so there's no reason to think it behaves differently from string
state — Vercel's own docs show a worked object-state example — but "no reason to think otherwise" is
not the same as observed, and it isn't in this document as fact.

### The rate limit persists after topping up paid credits — it's tied to the promo, not the balance

Every successful call above still shows `cost: "0"` even after adding paid credits to the account.
**Jev itself stays on its own $0 promotional quota regardless of account balance** — topping up fixed
the earlier account-verification block (a card-on-file requirement), but did not remove Jev's own
throttle, because paid credits were never being spent on Jev calls to begin with.

The quota looks small and per-window: two separate runs both got exactly **5 successful calls before
the 6th failed**, reproduced twice in a row regardless of which question was 6th. No `Retry-After` or
reset time is exposed anywhere in the error response, so the exact window (per-minute? per-hour?) is
still unknown — only the approximate size (~5 calls) is established.

This constrains the flagship analysis (`03-LAUNCH.md`), which assumes hundreds of thousands to
millions of calls. At ~5 calls per window with an unknown window length, that plan needs either the
real window measured, a paid (non-promotional) tier for the model itself, or TypeSafe's own early
access. Which of those to pursue, and when, is a cost/timeline decision for the project owner.

### For later: the direct-to-TypeSafe path (early access, not yet available)

`@ai-sdk/typesafe-ai` (real package, confirmed on npm, published by `vercel-release-bot`) gives the
same `evaluate()` shape but talks straight to `api.typesafe.ai` with a `TYPESAFE_AI_API_KEY`, no
Gateway involved. Same three primitives, explicitly documented limits — `choice`: 1–255 options,
`score`: 2–10 levels — and confidence for choice/score lives at
`result.providerMetadata.typesafe.confidence[questionId]`. Worth switching to once early access opens,
since it removes both the Gateway's rate limit and the Vercel billing dependency. Not usable today.

---

## Request (native endpoint — early access, not currently reachable)

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

```json
{
  "state": "...",                 // string | object | array   required
  "model": "jev-latest",          // string                    required
  "questions": {                  // map<string, Question>     required
    "your_id": { "type": "noul", "instructions": "..." }
  }
}
```

From `/concepts/state`:

> "Each request evaluates one state against one or more questions. All questions see the same state
> and are evaluated independently."

One state per request. No mechanism exists for a question to be answered separately per element of
the state.

Question ids are chosen by the caller and returned unchanged. The docs say the key "is not sent to
the underlying model and is not used in inference."

---

## Question types

| Type | `criteria` | Required | Shape |
|---|---|---|---|
| `noul` | what yes and no mean | **optional** | `{"true": "...", "false": "..."}` |
| `choice` | option → rubric | **required** | `{"opt_a": "desc", "opt_b": null}` |
| `score` | ordered level descriptions | **required**, ≥2 | `["Calm", "Frustrated", "Very angry"]` |

`instructions` accepts `string | object | array` for all three.

`noul` is the only type whose criteria are optional, making it the cheapest primitive per question.

---

## Answers

Returned under the same ids in `answers`, with `usage.input_tokens` / `usage.output_tokens`.

**The three shapes are not uniform:**

| | `noul` | `choice` | `score` |
|---|---|---|---|
| value | `noul` (0–1) | `choice` (string) | `score` (number, may be fractional) |
| `probabilities` | **absent** | option → p | level index as string → p |
| `confidence` | **absent** | present | present |
| `legend` | absent | absent | present — index → description |

From `/confidence`: "(Noul answers don't carry one.)"

Consequences:

- A `noul` answer is a single float that *is* P(yes). No distribution, no confidence.
- Any noul confidence must be **derived by us** and labelled as derived.
- `score` probabilities are keyed by level **index** (`"0"`, `"1"`). The `legend` maps back. Store it
  or the output is uninterpretable later.
- `confidence` is a statistic over `probabilities`; callers may compute their own.

---

## Errors

| Status | Meaning | Class |
|---|---|---|
| `401` | missing/invalid key | fatal, config |
| `422` | body failed validation; names the field | fatal, config |
| `429` | rate limited | transient — backoff |
| `529` | overloaded | transient — backoff |

Rate limit values are not published.

---

## Cost model

$0.042/MTok input, output free. Per call:

```
tokens(state) + Σ over questions of tokens(instructions + criteria)
```

**Batching every question about one document into one call is free and correct.**
From `/cookbooks/parallel_questions`:

> "The document dominates every request. N single-question calls pay for it N times, in N round
> trips; the batched call pays once. The bigger the document, the nearer that saving comes to a full
> Nx."

Measured there: **12.2x cheaper, 10.0x faster** for 13 questions over a 54,000-character article,
with answers identical either way across 5 repeats (most at std dev exactly 0.0), because "each
question is scored on its own against the document."

**The schema is paid on every document.** A 20-token title with a 650-token battery is 97% schema. On
short-document corpora the only levers are fewer questions, terser criteria, and preferring `noul`.

---

## Why we do not pack multiple documents into one state

Recorded so it is not reinvented. The idea was 20 documents per state, questions asked per index,
amortising the schema 20 ways. It does not work.

One question returns one answer about the whole state, so per-document answers need per-document
questions:

```
packed:    N×doc + N×Q×schema
unpacked:  N×(doc + Q×schema)
```

Equal. Packing saves zero tokens; it only reduces request count. And jaggedness #5:

> "Large state full of irrelevant detail — accuracy falls as the state grows with content unrelated
> to the decision." … "Jev suffers from context rot."

**Rule: one document per call, all questions batched.**

Indexing *into* a structured state is an official idiom for genuinely related items — the docs'
counting example passes `{"items": [...]}` and asks one `Noul` per index. We reserve it for chunks of
a single long document, never as a bulk cost trick.

---

## Model limitations (`jev-1.13`, reviewed 2026-09-17)

| # | Failure mode | What it forces |
|---|---|---|
| 1 | **Literal reading** — answers what you wrote, not what you meant | Exact conditions in `instructions`; boundary cases in `criteria` |
| 2 | **Math and counting** — unreliable, error grows with size | Never ask a counting question; count in code, one question per item |
| 3 | **Dates** — read as text, not ordered quantities | Extract components as `choice`, compare in code |
| 4 | **Indirection** — multi-hop reasoning costs accuracy | One hop per question; name the relevant field |
| 5 | **Large irrelevant state** — context rot | Project state to what the question needs |
| 6 | **Adversarial content** — text arguing for its own classification can steer it | Disclose as a limitation on public corpora |
| 7 | **Contradictory instructions vs criteria** | Criteria extend the instruction; align them |
| 8 | **Structural invariants do not hold** | See below |
| 9 | **Generation** — not trained for it | Bounded answer spaces only |

### #8 in detail — this constrains the validation design

Measured examples from the docs: the same question as a `Noul` and as a yes/no `Choice` returned
`noul = 0.22` against `probabilities["yes"] = 0.01`. A question and its negation as two Nouls returned
0.72 and 0.47 — summing to 1.19.

> "Don't carry a threshold tuned on a Noul over to a Choice, and don't hold the model to arithmetic
> identities between separate questions."

Thresholds are therefore **per question and per type**. No global threshold, no cross-question
arithmetic, no assuming P(x) + P(not x) = 1.

Also: "a Choice over options and one Noul per option answer different questions: the Choice is
relative, settling *which* option, while each Noul is absolute and can be low for all of them."

### Score arithmetic

> "Do not use score outputs to compute the exact magnitude of a number between two levels… score
> levels are weak in numerical calibration."

The weighted `score` is threshold material, not a measurement. Never average it across a corpus and
publish the mean.

---

## Other constraints

- Text only — string, JSON object, or array of text values. No images, audio, video.
- English is the primary training language; other languages including CJK are accepted at **lower
  accuracy**.
- Bounded context window, ~32k tokens. Verify the exact figure.
- Official SDKs: Python (sync + async, with retry policy) and JavaScript, from
  `--extra-index-url https://pypi.typesafe.ai/`.
- Published cookbooks pin explicit versions (`jev-1.12`, `jev-1.13`) rather than `jev-latest`.

---

## Open questions

Resolved: *can one question address N packed documents?* No — see above.

| # | Question | Resolve in |
|---|---|---|
| B | Per-call limit on question count? | T0.2 |
| D | Actual rate limits; does request-rate or token-rate bind first? | T0.5 |
| F | Pin an explicit model version for long runs? Cookbooks pin. | T0.6 |
| G | Are question ids billed, given they are not sent to the model? | T0.3 |
| H | How much does criteria verbosity cost in accuracy? | Phase 6, `schema-tune` |
| I | Does a projected state beat a full-record state on the same question? | Phase 6 |
