# 03 — Launch

How the project becomes visible. Do not start before P6 (validation) — publishing corpus-scale claims
without a validation report damages the project rather than delaying it.

---

## What each goal actually needs

| Goal | What satisfies it | Where it comes from |
|---|---|---|
| **Resume point** | A system with measured behaviour: cost model, calibration, idempotent recovery under failure | P3, P6, T2.7 — the phases nobody skips to |
| **Social post** | One impossible-sounding number and one surprising chart | P8 |
| **GitHub stars** | A tool a stranger can run on their own data in five minutes | P7 |
| **Credibility** | A measurement nobody else has published | T6.7, the accuracy-vs-verbosity curve |

These are different deliverables. The analysis without the engine is a blog post; the engine without
the analysis is invisible. Ship both.

---

## Corpus selection

Requirements: public and redistributable, recognisable to the audience you want sharing it,
affordable, and containing answers `grep` could not produce.

Cost per document is `tokens(state) + tokens(schema)`, and **schema is paid on every row**. So short
corpora are schema-dominated and scale with row count; long corpora are state-dominated and show the
batching win best.

Assuming a 14-question battery (~600 tokens of schema), one call group, $0.042/MTok:

| Corpus | Docs | ~State | Est. | Notes |
|---|---|---|---|---|
| **HN stories (1M sample)** | 1M | 25 tok | **~$26** | Recommended start |
| HN stories (full) | 5.8M | 25 tok | ~$152, ~$91 after schema-tune | Schema is ~96% of spend |
| arXiv abstracts | 2.5M | 250 tok | ~$89 | Science angle, weaker recognition |
| Steam reviews (2M sample) | 2M | 80 tok | ~$57 | Shareable, but sentiment reads as solved |
| npm READMEs | 3M | 800 tok | ~$176 | Developer-relevant, messy text |
| US federal bills | 400k | 20k tok | ~$346 | Best batching demo — unbatched ~$4,800 — most chunking work |

**Recommendation: Hacker News, 1M stratified sample at ~$26.** Validate, publish the methodology,
then decide whether the full corpus is worth another ~$120. "We validated on a sample first" is part
of the credibility story, not a compromise.

Replace every figure above with real `census estimate` output before publishing, and state actual
spend afterwards.

### The comparison that carries the post

A frontier LLM at ~$3/MTok in and ~$15/MTok out costs ~$0.0049 per document for the same battery —
**~$28,000** across 5.8M posts, against ~$152. Roughly **190x**.

Compute this from current prices yourself, show the arithmetic, and state the output-token
assumption. A number the reader can re-derive beats a bigger one they cannot.

---

## Question design

**Ask what only semantics can answer.** Not "posts containing 'AI'" — that is `grep`. Ask for posts
*announcing something the author built*, regardless of phrasing.

**Ask questions whose answers move over time.** A twenty-year corpus is a time series. Flat lines make
boring charts; tone, framing, self-promotion and required expertise all trend.

**Ask twelve to twenty questions.** The state is paid once per call; the schema is the real cost. A
considered battery, not three questions and not fifty.

**Include one question you expect to fail**, and publish it as a disclosed failure. Cheapest
credibility available, and it inoculates against the charge that you published only what flattered
you.

### Constraints from the model's documented failure modes

Each maps to `00-JEV-API.md`.

- **No counting.** Unreliable, error grows with size. Count in code, one question per item.
- **No arithmetic, no date comparison.** Extract with `choice`, compare in code.
- **Write the exact condition.** Jev answers what you wrote. If you catch yourself explaining what you
  meant, that explanation is the missing half of the instruction.
- **One hop per question.**
- **Project the state** to the fields the question needs.
- **Choose deliberately between one `choice` and N `nouls`.** A choice is relative and settles *which*;
  nouls are absolute and can all be low.
- **Never average a `score` across the corpus and publish the mean.** The docs say score levels are
  weak in numerical calibration and expressly warn against reconstructing magnitudes. "Share of posts
  at or above 'assumes working knowledge'" is defensible; "mean technical depth rose from 1.8 to 2.3"
  is not. **This is the most likely way the analysis goes wrong, because that chart is the tempting
  one to draw.**
- **English is primary**; other languages are lower accuracy. Filter, or report per-language accuracy.

**Pilot on 1,000 rows and read the answers by hand** (T8.1). Questions that felt sharp in the abstract
are routinely ambiguous against real data.

---

## Methodology that ships with the analysis

- Hand-labelled gold set per published question, stratified across the probability range, sample size
  stated.
- Per-question accuracy and calibration error. **Never one headline accuracy across question types** —
  structural invariants do not hold, so the aggregate is meaningless.
- Disclosed failures: questions that missed the gate, named, with numbers, excluded from every chart.
- Threshold used per question on its correct scale, and the coverage it implies. If a chart covers 78%
  of the corpus, it says so.
- Raw results published, with the question set and run manifest.
- A note on adversarial content: some documents argue for their own classification — meta-posts about
  Hacker News especially. The docs state Jev does not treat state as hostile by default. Disclose it
  rather than waiting for a reader to find it.
- Total spend and wall-clock time.

---

## Publication checklist

**Artifacts**
- [ ] Analysis page, legible on a phone
- [ ] Raw results downloadable, with question set and manifest
- [ ] `validation_report.md` linked prominently
- [ ] README leading with one verified, reproducible number
- [ ] One-command demo reproducing a slice of the analysis
- [ ] The schema-economy curve (T6.7) as its own short write-up

**Distribution, roughly a week**
- [ ] Repo made public; demo verified from a clean clone
- [ ] PRs to the awesome-jev lists — at least nine exist (`cobanov`, `yibie`, `AbdelStark`,
      `hellogumbo`, `AnotiaWang`, `Anil-matcha`, `doeixd`, `keltokhy`, `daftAI2026`). Currently the
      ecosystem's main discovery path; costs an afternoon
- [ ] Analysis page published
- [ ] Show HN timed with the page. If the corpus is Hacker News, say so in the title — the
      self-reference is the hook
- [ ] Social post: lead with cost and scale, one chart, methodology link in the **first reply**
- [ ] Notify TypeSafe — a provider with a young ecosystem amplifies substantial community work, and
      the schema-economy measurement is the kind of thing they want

**Post shape.** Lead with the impossible-sounding number — *"I asked 14 questions of a million Hacker
News posts. It cost $26."* Then one chart with a genuine surprise. Then the link. Engineering goes in
the blog post.

---

## Honesty rules

The failure mode that ends this project is a viral chart built on a question Jev answers at 68%,
corrected in public by someone who checked.

1. No claim without a gold-set number behind it.
2. Never call a probability a fact. "Jev classified 34% of posts as X, at 91% accuracy on a 300-item
   gold set" — not "34% of posts are X."
3. Publish failures at the same prominence as findings.
4. State accuracy next to the chart, not only in the methodology.
5. Do not overclaim about the model. Jev is fast, cheap and well calibrated on System One tasks. It
   cannot count and it reads literally. Accurate positioning is defensible in a thread; inflation is
   not.
6. Correct errors publicly and quickly. This project already has one such correction in its own design
   history — the packing mistake — and saying so out loud when methodology is discussed costs nothing
   and buys a lot.

---

## What good looks like at six weeks

The analysis shared beyond the developer audience. Stars from people who ran it on their own data. An
issue from someone who hit a real bug at a scale you never tested. And the schema-economy curve cited
when people discuss what questions to write for Jev.

That last one is the prize: it means the project shaped how the ecosystem uses the model, which is
worth more in front of an employer than a star count.
