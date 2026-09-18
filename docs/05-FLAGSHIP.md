# 05 — The Flagship Analysis

> The engine earns stars; the analysis earns the attention that sends people to the engine.
> Do not start before M7 (validation). Publishing corpus-scale claims without a validation report is
> the one mistake that damages the project rather than merely delaying it.

---

## Strategy

A library reaches people who already have the problem. An analysis reaches people who do not yet know
the problem exists, through channels a README never touches. So `cartograph` ships with a flagship run
over a well-known public corpus, published as an interactive page with downloadable raw results and a
real methodology section. The page is what people share; the repository is what they star. Both
require the analysis to be *correct*, which is why the validation gate is not negotiable.

There is a second, quieter asset. This project produces two genuinely novel measurements as
by-products: **the accuracy-versus-schema-verbosity curve** (M6) and **the cost of context rot from
unprojected state** (open question I). Nobody in the Jev ecosystem has published either. They are
smaller posts than the flagship but they are the ones practitioners cite, and citation is worth more
than a spike of traffic.

---

## Choosing the corpus

Requirements: public and redistributable; recognisable to the audience you want sharing it;
affordable; and containing answers a competent analyst could not get with `grep`.

**The cost model inverts the usual intuition.** Cost per document is
`tokens(state) + tokens(schema)`, and the schema is paid on every row. So:

- **Short-document corpora** are schema-dominated. Cost scales almost purely with row count, and the
  lever is schema economy.
- **Long-document corpora** are state-dominated. They cost more per row, but they are where question
  batching shows its 12x win most dramatically.

### Candidates

Assuming a 14-question battery (~600 tokens of schema) and one call group, at $0.042/MTok:

| Corpus | Docs | ~State | Est. total | Character |
|---|---|---|---|---|
| **Hacker News stories (1M sample)** | 1M | ~25 tok | **~$26** | Recommended start. Highest recognition, cheapest entry. |
| Hacker News stories (full) | 5.8M | ~25 tok | ~$152, or ~$91 after schema-tune | The full claim. Schema is ~96% of spend. |
| arXiv abstracts | 2.5M | ~250 tok | ~$89 | Stronger science angle, weaker recognition. |
| Steam reviews (2M sample) | 2M | ~80 tok | ~$57 | Fun and shareable, but sentiment reads as solved. |
| npm READMEs | 3M | ~800 tok | ~$176 | High developer relevance, messy text. |
| US federal bills | 400k | ~20k tok | ~$346 | Best batching demo by far — unbatched would be ~$4,800 — but the most expensive and most chunking work. |

**Recommendation: Hacker News, starting with a 1M stratified sample at ~$26.** Validate, publish the
methodology, then decide whether the full corpus is worth another ~$120. The sample is not a
compromise — it is the correct order of operations, and "we validated on a sample first" is itself
part of the credibility story.

*Every figure above is an estimate from the published rate. Replace all of them with real
`cartograph estimate` output before publication, and state the actual spend afterwards.*

### The comparison that carries the post

A frontier LLM at roughly $3/MTok input and $15/MTok output would cost about $0.0049 per document for
the same battery, or **~$28,000** across 5.8M posts, against ~$152. That is the headline: **81 million
typed decisions for the price of a nice dinner, around 190x cheaper than the obvious alternative.**

Compute this yourself from current prices rather than quoting a vendor's number, show the arithmetic,
and state the assumption about output tokens. A number the reader can re-derive is worth more than a
bigger number they cannot.

---

## Designing the questions

Where the analysis is won or lost, and the part no tool can do for you.

**Ask what only semantics can answer.** Not "posts containing 'AI'" — that is `grep`. Ask for posts
*announcing something the author built*, regardless of phrasing. Every question should be one keyword
matching genuinely cannot do.

**Ask questions whose answers move over time.** A twenty-year corpus is a time series; questions
producing a flat line produce a boring chart. Tone, framing, self-promotion, optimism and required
expertise all trend, and trends are what get shared.

**Ask twelve to twenty questions.** The state is paid once per call and the schema is the real cost,
so the right number is a considered battery, not three questions and not fifty.

**Include at least one question you expect to fail**, and publish it as a disclosed failure. Cheapest
credibility you will ever buy, and it inoculates the analysis against the charge that you only
published what flattered you.

### Constraints imposed by the model's documented failure modes

These are not style preferences. Each maps to a jaggedness entry in `00-API-NOTES.md` §7.

- **No counting.** "How many times does X appear" is unreliable and the error grows with size. Count
  in code by asking one question per item.
- **No arithmetic and no date comparison.** Extract with a `choice` over enumerated options, compare
  in code.
- **Write the exact condition.** Jev answers what you wrote, not what you meant. When you catch
  yourself explaining what you really meant, that explanation is the missing half of the instruction.
- **One hop.** Questions about a property of a property lose accuracy.
- **Project the state.** Send the fields the question needs, not the whole record.
- **Choose deliberately between one `choice` and N `nouls`.** A choice is *relative* and settles which
  option; nouls are *absolute* and can all be low. For "what is this post about" a choice is right;
  for "which of these tags apply" nouls are right.
- **Never average a `score` across the corpus and publish the mean.** The docs warn score levels are
  weak in numerical calibration and expressly say not to reconstruct magnitudes from them. A score is
  threshold material: "the share of posts at or above 'assumes working knowledge'" is defensible; "mean
  technical depth rose from 1.8 to 2.3" is not. **This is the most likely way this analysis goes
  wrong, because that chart is so tempting to draw.**
- **English is the primary training language**; other languages are accepted at lower accuracy. If the
  corpus is multilingual, either filter or report per-language accuracy separately.

**Pilot on 1,000 rows and read the answers by hand.** Questions that felt sharp in the abstract are
routinely ambiguous against real data, and discovering that after a full run is an expensive way to
learn it.

---

## Non-negotiable methodology

Ships with the analysis, every time:

- A hand-labelled gold set per published question, stratified across the probability range, with
  sample size stated.
- Per-question accuracy and calibration error from `cartograph validate`. **Never a single headline
  accuracy across question types** — structural invariants do not hold, so the aggregate is
  meaningless.
- Disclosed failures: questions that missed the gate, named, with their numbers, excluded from every
  chart.
- The threshold used per question, on its correct scale (confidence for choice/score, distance from
  0.5 for noul), and the coverage it implies. If a chart covers 78% of the corpus, it says so.
- Raw results published, with the question set and run manifest.
- **A note on adversarial content.** On a public corpus some documents argue for their own
  classification — meta-posts about Hacker News being a prime example. The docs state Jev does not
  treat state as hostile by default. Disclose this as a known limitation rather than waiting for a
  reader to find it.
- Total spend and wall-clock time, stated plainly.

The methodology section is not an appendix for pedants. It is why a sceptical reader concludes the
work is real, and sceptical readers are exactly the ones whose sharing matters.

---

## Publication checklist

**Artifacts**
- [ ] Interactive analysis page, legible on a phone — most sharing happens there.
- [ ] Raw results downloadable, with question set and manifest.
- [ ] `validation_report.md` linked prominently, not buried.
- [ ] README leading with one verified, reproducible headline number.
- [ ] A genuine one-command demo reproducing a slice of the analysis.
- [ ] The schema-economy curve from M6, as its own short write-up.

**Distribution**, roughly a week
- [ ] Repository public; demo verified from a clean clone.
- [ ] PRs adding the project to the awesome-jev lists — there are at least nine (`cobanov`, `yibie`,
      `AbdelStark`, `hellogumbo`, `AnotiaWang`, `Anil-matcha`, `doeixd`, `keltokhy`, `daftAI2026`).
      Currently the ecosystem's main discovery path; costs an afternoon.
- [ ] Analysis page published.
- [ ] Show HN, timed with the page. If the corpus is Hacker News, say so in the title — the
      self-reference is the hook.
- [ ] Social post: lead with cost and scale, one chart, methodology link in the *first reply* rather
      than the post.
- [ ] Notify TypeSafe. A provider with a young ecosystem is actively looking for substantial community
      work to amplify, and the schema-economy measurement is the kind of thing they would want.

**Post shape.** Lead with the number that sounds impossible — *"I asked 14 questions of a million
Hacker News posts. It cost $26."* Then one chart containing a genuine surprise. Then the link. The
engineering goes in the blog post; people who care will click, and people who would not have read a
thread about adaptive concurrency still get the point.

---

## Honesty rules

The failure mode that ends this project is a viral chart built on a question Jev answers at 68%,
corrected in public by someone who checked.

1. **No claim without a gold-set number behind it.**
2. **Never call a probability a fact.** "Jev classified 34% of posts as X, at 91% accuracy on a
   300-item gold set" — not "34% of posts are X."
3. **Publish failures at the same prominence as findings.**
4. **State the accuracy next to the chart**, not only in the methodology.
5. **Do not overclaim about the model.** Jev is fast, cheap and well calibrated on System One tasks.
   It is not a frontier LLM, it cannot count, and it reads literally. Accurate positioning is more
   impressive than inflation and is the claim you can defend in a thread.
6. **Correct errors publicly and quickly.** A visible correction costs far less credibility than a
   quiet edit — and this project already has one such correction in its own design history, which is
   worth saying out loud when the methodology is discussed.

---

## What good looks like

Six weeks after publication: the analysis has been shared beyond the developer audience; the
repository has stars from people who ran it on their own data; at least one issue exists from someone
who hit a real bug at a scale you never tested; and the schema-economy curve gets cited when people
discuss what questions to write for Jev.

That last one is the real prize. It means the project shaped how the ecosystem uses the model, which
is a far stronger thing to put in front of an employer than a star count.
