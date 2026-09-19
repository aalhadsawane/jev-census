# 05 — P6: Quality

Elaborated spec for phase P6 of `02-BUILD-PLAN.md`. Read `01-DESIGN.md` §§ Thresholds / review queue
/ validation and Schema economy first, and `00-JEV-API.md` § "#8 in detail" — that section is the
reason almost every rule below is per-question and per-type.

**Why this phase is the project.** P1–P5 produce a table. Any competent engineer can produce a table.
P6 produces the sentence *"`department` is 93% accurate on a 300-item stratified gold set, and here is
the threshold that buys you 99%"* — which is what makes the table usable and what nobody else in the
Jev ecosystem has published. `02-BUILD-PLAN.md` says "do not compress it". Take that literally: the
statistics below are where this phase is won or quietly lost.

---

## The worked example this phase must produce

```
$ census label --run 20260920T2106Z-a1b2 --question department --n 300
  sampled 300 of 412,006 cells across 5 confidence strata (12 excluded: chunk_count > 1)
  wrote results/label/department.csv — fill in `gold_answer`, then run `census validate`

$ census validate --run 20260920T2106Z-a1b2 --gold results/label/
  wrote results/validation_report.md
  3 of 4 questions PASS · frustration FAIL (exploratory, excluded from headline claims)

$ cat results/validation_report.md
question        type    accuracy  n     ECE    threshold      coverage   verdict
is_urgent       noul    0.96      240   —      |p-0.5| > 0.31   94%      PASS
department      choice  0.93      300   0.031  conf > 0.74      86%      PASS
churn_risk      noul    0.91      240   —      |p-0.5| > 0.40   71%      PASS
frustration     score   0.68      200   0.190  —                 —       FAIL — exploratory only
```

That block is in the README already. P6 is done when the tool actually generates it.

---

## New CLI surface

```
census label   --run <run_id> --question <id> [--n 200] [--strata 5] [--seed 0]
                                              [--include-chunked]
census validate --run <run_id> --gold <csv-or-dir> [--target-accuracy 0.99]
                                                   [--gate-floor 0.90]
census report  --run <run_id>
census review  export --run <run_id>  |  import --run <run_id> --file <csv>
census schema-tune --input <src> --questions <yaml> --budget <usd>
                   [--variants <yaml>] [--sample 300]
```

New modules: `sampling.py` (T6.1), `scoring.py` (T6.2–T6.4), `report.py` (T6.5), `overrides.py`
(T6.6), `schema_tune.py` (T6.7). Keep the statistics in `scoring.py` pure and network-free — it is the
part most worth unit-testing against hand-computed numbers.

---

## T6.1 — `census label`: stratified sampling

Uniform sampling on a skewed corpus returns easy cases and a flattering, useless number. That trap is
already named in `02-BUILD-PLAN.md`; this task is the defence.

**Strata, per type:**

| Type | Stratify on | Default strata |
|---|---|---|
| `noul` | `noul` (P(yes)) | 5 equal-width bins over [0, 1] |
| `choice` | (chosen option, `confidence` bin) | 5 confidence bins × each observed option |
| `score` | (modal level, `confidence` bin) | 5 confidence bins × each observed level |

**Allocation is equal per stratum, not proportional.** The point is to cover the decision boundary,
where accuracy is actually in question, not to mirror the corpus. A stratum with fewer cells than its
allocation contributes all of them and the remainder is redistributed.

**This is the subtle part, and getting it wrong invalidates every number in the report:** because
allocation is equal rather than proportional, the raw accuracy over the sample is *not* the corpus
accuracy. Every downstream statistic must be reweighted. So `census label` writes the weights into the
CSV itself:

```
stratum_weight = (cells in this stratum in the corpus) / (cells sampled from this stratum)
```

and T6.2–T6.4 compute weighted statistics with them. A coding agent that skips this will produce a
report that looks right, passes its tests, and is wrong — put the formula in a module docstring.

**Output — `results/label/<question_id>.csv`:**

```
doc_id, question_id, type, stratum, stratum_weight,
model_noul, model_choice, model_score, model_modal_level, confidence,
<every field in the question's projection>,
gold_answer, notes
```

The projection fields are included deliberately: a labeller who has to join back to the corpus to see
the ticket will not label 300 rows. `gold_answer` and `notes` ship empty.

**`gold_answer` accepted values, per type:**

| Type | Accepted | |
|---|---|---|
| `noul` | `true` / `false` / `unclear` | |
| `choice` | one of the question's option keys, or `unclear` | |
| `score` | an integer level index, or `unclear` | |

**`unclear` is a first-class answer, not a missing one.** A human who genuinely cannot tell is not
evidence the model is wrong. `unclear` rows are excluded from accuracy and **reported as their own
count** in the validation report — a question with 20% `unclear` is a badly written question, and
that is worth surfacing rather than hiding.

**Decisions already made:**

- `chunk_count > 1` cells are **excluded by default** (`01-DESIGN.md`: aggregated cells are our
  arithmetic, not a model answer, so scoring them measures the aggregation rather than Jev).
  `--include-chunked` opts back in and the report says so when used.
- Sampling is deterministic under `--seed`, so a labelling effort is reproducible and re-runnable.
- `census label` never calls the API and never spends. It reads the run's finalized shards.
- Re-running `label` for a question that already has a partly filled CSV **must not clobber it**.
  Refuse and say so; require an explicit `--overwrite`. Losing a half-day of human labelling to a
  re-run is the single most expensive bug available in this phase.

**Done when:** a test asserts the sample is not uniform (strata populations differ from corpus
proportions in the expected direction), that weights reconstruct corpus proportions
(`Σ stratum_weight ≈ corpus cell count`), and that re-running without `--overwrite` refuses.

---

## T6.2 — Accuracy against the gold set

**Correctness rule, per type** — this is where the model's documented jaggedness bites:

| Type | Correct when | Note |
|---|---|---|
| `noul` | `(noul > 0.5) == (gold == "true")` | the default decision rule; T6.4 then searches for a better one |
| `choice` | `choice == gold` | argmax agreement |
| `score` | **modal level** `== gold` | *not* `round(score)` — see below |

**Score compares the modal level, not the rounded weighted score.** `00-JEV-API.md` quotes the docs:
"Do not use score outputs to compute the exact magnitude of a number between two levels… score levels
are weak in numerical calibration." The weighted `score` is threshold material; the level the model
actually put its probability mass on is the answer it gave. Also report **adjacent agreement**
(`|modal − gold| ≤ 1`) as a secondary column — an ordered scale that is never more than one level out
is useful even when exact agreement is mediocre, and it distinguishes "noisy but ordered" from
"random".

**Statistics:**

- **Point estimate: weighted accuracy** — `Σ wᵢ·correctᵢ / Σ wᵢ` over labelled, non-`unclear` rows.
  This is the corpus-projected number and the one the report prints.
- **Also report unweighted sample accuracy.** When the two diverge a lot, the strata disagree with
  each other, and that is information the reader deserves.
- **Interval: Wilson score interval at 95%**, not Wald. n is small (200–300) and accuracies sit near
  0.95, exactly where Wald produces intervals that run past 1.0.
- **Use Kish effective sample size for the interval**, `n_eff = (Σw)² / Σw²`, not the raw row count.
  Weighting reduces the information content of the sample and an interval computed on raw n overstates
  precision. Report the raw n in the table (it is what the reader wants to know about the human
  effort) and compute the interval on `n_eff`.
- **Never aggregate accuracy across question types or across questions.** Enforce it structurally:
  `scoring.py` should have no function that takes more than one question's results and returns a
  single accuracy. There is nowhere to put the headline number, so nobody can accidentally print one.

**Gold-file hygiene — refuse rather than guess:**

- Unknown `doc_id`, or one that does not belong to this run → error naming the row.
- `gold_answer` not in the accepted set for the type → error naming the row and the accepted values.
- Fewer than 30 usable rows for a question → report the question as `INSUFFICIENT`, not as an
  accuracy. A 100% accuracy on 7 rows is worse than no number, because it will get quoted.

**Done when:** each type's correctness rule and the Wilson/Kish arithmetic have unit tests against
hand-computed expected values, and a malformed gold file produces a message naming the offending row.

---

## T6.3 — Expected calibration error and reliability data

**ECE:** partition into 10 equal-width bins over the confidence scale, then

```
ECE = Σ_bins (weight of bin / total weight) × | weighted accuracy(bin) − weighted mean confidence(bin) |
```

Weighted throughout, with the same `stratum_weight`s — an ECE computed on the unweighted stratified
sample is measuring the sample's shape, not the corpus's calibration.

**Per type:**

- `choice` and `score`: ECE on the **model-reported `confidence`**. This is the number in the report's
  `ECE` column.
- `noul`: the `ECE` column prints `—`, matching the README. A noul carries no model confidence
  (`00-JEV-API.md`), and our derived `|p−0.5|×2` is not the model's claim, so putting it in the same
  column as a choice's ECE invites exactly the cross-type comparison D4 forbids.
- **But `noul` probability calibration is still worth measuring, and is arguably the more meaningful
  number**: `noul` *is* P(yes), so binning on the raw probability and comparing to the empirical
  frequency of `gold == "true"` is a genuine reliability curve. Compute it, write it to the
  reliability data, and print it in the report as a separate footnote line — never in the `ECE`
  column. Call the field `ece_probability` and the choice/score one `ece_confidence` so the two can
  never be silently unioned.

**Output — `results/reliability/<question_id>.csv`:** `bin_lower, bin_upper, n, weighted_n,
mean_confidence, weighted_accuracy`. This is chart source data for P8; the analysis page should never
recompute it.

**Done when:** ECE has a unit test against a hand-computed toy example (a deliberately miscalibrated
5-row set with known answer), and the noul/choice ECE fields are separate in the data model.

---

## T6.4 — Threshold recommendation with coverage

**The scale is per type, and the types do not share one** (`01-DESIGN.md` D4, and the measured
examples in `00-JEV-API.md` #8 — the same question returned 0.22 as a noul against 0.01 as a choice
probability):

| Type | Threshold on |
|---|---|
| `noul` | `\|noul − 0.5\|` — distance from the coin flip |
| `choice` | model `confidence` |
| `score` | model `confidence` |

**Search:** sweep candidate thresholds over the observed values for that question (every distinct
value, or a 200-point grid if there are more). For each, compute weighted accuracy over the rows at or
above it, and the weighted coverage it implies. Return the **lowest** threshold reaching
`--target-accuracy` (default 0.99) — lowest, because the goal is the most coverage that buys the
required accuracy, not the safest-looking number.

**Guards, all of which matter:**

- If no threshold reaches the target, report `—` for both threshold and coverage. That is the
  `frustration` row in the README, and it is the honest answer.
- **Minimum surviving sample.** A threshold that keeps 6 rows will show 1.00 accuracy and is noise.
  Require at least 30 rows (weighted `n_eff` ≥ 30) above the threshold, or reject that candidate.
  Without this guard the search reliably returns a garbage threshold with a flattering number.
- Coverage is **corpus coverage**, computed with the stratum weights, not the fraction of the sample
  that survived. These differ a lot under equal allocation.
- Thresholds are keyed by `(question_id, type)` in every data structure that holds them, so there is
  no shape in the code that could carry one across questions.

**Done when:** a synthetic question with a known ideal threshold recovers it; a question where no
threshold works returns `—`; and the minimum-sample guard has its own test showing a high-threshold
candidate rejected rather than returned.

---

## T6.5 — `validation_report.md`

Reproduce the README's block exactly — columns, order, and the `FAIL — exploratory only` phrasing.
Below the table, print the things the table has no room for: per-question `n`, `unclear` count,
weighted vs unweighted accuracy, the Wilson interval, `n_eff`, the noul `ece_probability` footnote,
and the exclusion note when chunked cells were skipped.

**Verdict rules:**

- `PASS` when weighted accuracy at the recommended threshold ≥ `--gate-floor` (default 0.90).
- `FAIL` otherwise. A question with `gate: exploratory` still prints `FAIL` — it is marked
  `FAIL — exploratory only` and excluded from headline claims, exactly as the README shows.
- `INSUFFICIENT` when fewer than 30 usable labels (T6.2).
- **Exit code is non-zero only if a `gate: strict` question FAILs.** An exploratory failure is an
  expected, disclosed outcome, not a broken build. This makes `census validate` usable as a CI gate
  before publication.

`census report --run <id>` regenerates the file from stored validation output without re-reading the
gold set, so P8 can rebuild the artifact without the labelling CSVs at hand.

**Done when:** a fixture run + fixture gold set generates a report whose table matches the README's
block, asserted as a string comparison in a test.

---

## T6.6 — Review queue and the override layer

**Export:** cells below the recommended threshold for their question, on that question's own scale, to
`results/review_queue.csv` — the same column shape as the labelling CSV (projection fields included)
plus a blank `human_answer`.

**Import:** `census review import` writes `results/overrides.parquet`:
`doc_id, question_id, human_answer, reviewer, ts, source_run_id`.

**The one rule that matters:** overrides are **never** merged into `cells.parquet`. They are a
separate file, joined at read time. `01-DESIGN.md` is explicit, and the reason is that a model output
column must stay a model output column or the validation numbers attached to it become fiction.

Enforce it with a test, not a convention: after `census review import`, assert `cells.parquet` is
byte-identical to before. The shard writer has no rewrite path (P2 made finalized shards immutable),
so this should be structurally true already — the test is there to keep it true when someone later
adds a convenience flag.

The DuckDB `COALESCE` recipe for reading the two together belongs in T7.4, not here.

**Done when:** the byte-identity test passes and `overrides.parquet` round-trips.

---

## T6.7 — `census schema-tune`

The accuracy-vs-verbosity curve. `02-BUILD-PLAN.md` calls it "the most publishable artifact in the
repository — nobody in the Jev ecosystem has measured it," and it closes open question **H** in
`00-JEV-API.md`.

**Variants are authored by hand, not generated.** `01-DESIGN.md`: "question wording is the author's
accountability," and the anti-goals forbid LLM-generated question sets. The tool prices variants; it
does not write them.

**`--variants variants.yaml`:**

```yaml
version: 1
reference: support-triage.yaml     # the verbose set, the baseline
variants:
  - name: terse-criteria
    overrides:
      department:
        criteria: { billing: Payments., technical: Bugs., sales: Pricing. }
  - name: no-criteria
    overrides:
      is_urgent: { criteria: null }
```

**Method:** sample N documents once (default 300, stratified is unnecessary here — this measures
agreement across the corpus, not accuracy at a boundary), run the reference set, run each variant on
the same documents, compare per type against the floors already set in `01-DESIGN.md`:

| Type | Metric | Floor |
|---|---|---|
| `noul` | thresholded agreement + mean absolute probability drift | ≥ 0.97, drift ≤ 0.05 |
| `choice` | argmax agreement + drift in the chosen option's probability | ≥ 0.95 |
| `score` | exact-level agreement + mean shift in weighted score | ≥ 0.92, shift ≤ 0.15 levels |

**Outputs:** `results/schema_tune.md` (the table an author acts on) and `results/schema_tune.csv`
(tokens-per-document against agreement, per variant per question — the curve).

**Decisions already made:**

- `schema-tune` **charges the same `--budget`** as `run`; `--budget` is required. No command spends
  without a cap.
- It uses the normal cell cache, so re-running an unchanged variant costs nothing and iterating on one
  variant only pays for that one.
- The report must print, verbatim, the sentence from `01-DESIGN.md`: *agreement is not accuracy — it
  only means the terse variant answers like the verbose one, which matters only if the verbose one was
  validated.* This is the caveat that keeps the artifact honest when it gets quoted.
- Output is a table, never an automatic rewrite of the question set.

**Done when:** a real curve is committed for a real corpus, and the floors table above is enforced in
the pass/fail column.

---

## T6.8 — Projection ablation (closes open question I)

`00-JEV-API.md` assigns open question **I** — *does a projected state beat a full-record state on the
same question?* — to "Phase 6", but the P6 task table in `02-BUILD-PLAN.md` has no task for it. This
is that task. It is not new scope; it is a gap between two existing documents.

It also matters more than it looks: projections are P5's entire reason for existing, and the argument
for them is jaggedness #5 (context rot) rather than anything measured on this corpus. If a projected
state does *not* beat a full-record state here, that is worth knowing before the launch analysis
claims it does.

**Method:** on the same sampled documents and the same gold set from T6.1/T6.2, ask each question
twice — once with its declared projection, once with the whole record — and compare accuracy, not
agreement. Report per question: accuracy projected, accuracy full-record, token cost of each.

**Done when:** the measurement is in `DECISIONS.md` and open question I is marked resolved in
`00-JEV-API.md` with the evidence, whichever way it goes.

---

## Phase exit

> every published column has a measured accuracy behind it.

Concretely, before P6 is done:

1. Every question in the demo question set has a gold set of ≥200 stratified labels and a row in
   `validation_report.md`.
2. `census validate` exits non-zero when a strict question fails, verified in a test.
3. The schema-economy curve (T6.7) is committed as a real measurement on a real corpus, not a fixture.
4. Open questions **H** and **I** in `00-JEV-API.md` are closed with evidence.
5. A `DECISIONS.md` entry recording: the gate floor actually used, any question that failed and why it
   was kept or cut, and the weighted-vs-unweighted accuracy gap observed (that gap is the evidence
   that the stratification and reweighting are doing real work).
