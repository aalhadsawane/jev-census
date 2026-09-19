# 07 — P8: Launch execution

Elaborated spec for phase P8 of `02-BUILD-PLAN.md`. `03-LAUNCH.md` holds the *why* — corpus choice,
question-design constraints, methodology, honesty rules. This document holds the *what to run, in what
order, and what must be true before the next step*.

**Do not start before P6.** Publishing corpus-scale claims without a validation report damages the
project rather than delaying it.

**This phase is mostly not coding.** By P8 the tool is finished; what remains is running it carefully
and writing honestly. The main failure mode is an agent treating P8 as a build phase and shipping the
analysis before the gold sets exist.

---

## The target

From `03-LAUNCH.md`: **Hacker News, 1M stratified sample, ~$26 estimated.** Validate on the sample,
publish the methodology, then decide whether the full 5.8M corpus is worth another ~$120. "We
validated on a sample first" is part of the credibility story, not a compromise.

Headline shape: *"I asked 14 questions of a million Hacker News posts. It cost $26."*

---

## T8.1 — Pilot 1,000 rows and read the answers by hand

**Not a smoke test.** The point is to discover that a question which felt sharp in the abstract is
ambiguous against real data — which `03-LAUNCH.md` says happens routinely.

1. Draft 12–20 questions per `03-LAUNCH.md` § Question design. Hold each against the model's
   documented failure modes before writing it, not after: no counting, no arithmetic, no date
   comparison, one hop per question, narrow projection, and a deliberate choice between one `choice`
   and N `nouls`.
2. Include **one question expected to fail**. Cheapest credibility available, and it inoculates
   against the charge that only flattering results were published.
3. Run on 1,000 rows. Read **every answer for at least two questions**, and a sample for the rest.
4. Revise. Expect to rewrite a third of the battery.

**Gate:** a written note listing which questions changed and why. If nothing changed, the reading was
not careful enough — that outcome has not happened to anyone yet.

---

## T8.2 — Gold sets and validation for every published question

This is the expensive step and the one that must not be compressed.

- `census label --question <id> --n 250` per question, stratified (T6.1).
- Hand-label. Budget real hours: 14 questions × 250 rows is a multi-day human task, and it is the
  thing the entire launch rests on.
- `census validate`, producing per-question accuracy, ECE, threshold and coverage.

**Gate — nothing publishes without these:**

| Check | Rule |
|---|---|
| Every published question has ≥200 usable labels | `INSUFFICIENT` is not publishable |
| Every published question PASSes its gate at its recommended threshold | failures are disclosed, never charted |
| `unclear` rate below ~15% | above that, the question is ambiguous, not the model wrong — rewrite it |
| Thresholds recorded per question on the correct scale | noul on `\|p−0.5\|`, choice/score on confidence |

---

## T8.3 — The full run

1. `census estimate` on the real corpus. **Replace every projected figure in `03-LAUNCH.md` with this
   output** before it is quoted anywhere.
2. `census run --budget <estimate × 1.3>`. The margin covers estimator drift; T3.5's calibration ratio
   measured 2.27× on the char-based estimator during P3, so size the budget against the *calibrated*
   projection, not the raw one. Getting this wrong means a clean, resumable stop partway — annoying,
   not dangerous, which is the point of the governor.
3. Record **actual spend and wall-clock time**, and publish both. The gap between estimate and actual
   is itself an honest, interesting number.
4. Publish `manifest.json` alongside the results.

**Gate:** actual spend and runtime recorded in `DECISIONS.md`, and the estimate-vs-actual gap stated.

---

## T8.4 — Analysis page, raw data, methodology

**Artifacts:**

- Analysis page, legible on a phone, **accuracy stated beside each chart** — not only in the
  methodology section.
- Raw results downloadable, with the question set and run manifest.
- `validation_report.md` linked prominently.
- The schema-economy curve (T6.7) as its own short write-up. `03-LAUNCH.md` calls this the prize: it
  means the project shaped how the ecosystem uses the model.

**The one chart that must not be drawn.** `03-LAUNCH.md` names this the most likely way the analysis
goes wrong, because it is the tempting one: **never average a `score` across the corpus and publish
the mean.** "Mean technical depth rose from 1.8 to 2.3" is indefensible — the docs say score levels
are weak in numerical calibration. "Share of posts at or above 'assumes working knowledge'" is the
defensible form of the same finding. Check every chart against this before publishing, because the
tooling will happily compute the wrong one.

**Disclosures that ship with the page:** the failed question with its number; adversarial content
(meta-posts about Hacker News argue for their own classification); English-primary accuracy; the
coverage each threshold implies; and the packing mistake in this project's own design history, which
`03-LAUNCH.md` notes costs nothing to admit and buys a lot.

---

## T8.5 — Distribution, roughly a week

Sequence matters — the repo must be usable before anyone is sent to it.

1. Repo public; demo verified from a clean clone **on a different machine**.
2. PRs to the awesome-jev lists (nine named in `03-LAUNCH.md`). Currently the ecosystem's main
   discovery path; costs an afternoon.
3. Analysis page published.
4. Show HN, timed with the page. If the corpus is Hacker News, say so in the title — the
   self-reference is the hook.
5. Social post: lead with cost and scale, one chart, **methodology link in the first reply**.
6. Notify TypeSafe. A provider with a young ecosystem amplifies substantial community work, and the
   schema-economy measurement is the kind of thing they want.

---

## Stop conditions

Do not publish if any of these is true. They are cheaper to honour than to correct in public.

- A headline chart rests on a question without a gold-set number.
- Any published figure came from averaging a `score` across the corpus.
- A single accuracy number spans question types (`00-JEV-API.md` #8: structural invariants do not
  hold, so the aggregate is meaningless).
- The demo does not run from a clean clone on a machine that is not the author's.
- A probability is described as a fact. "Jev classified 34% of posts as X, at 91% accuracy on a
  300-item gold set" — never "34% of posts are X."

`03-LAUNCH.md` § Honesty rules states the failure mode that ends the project: a viral chart built on a
question Jev answers at 68%, corrected in public by someone who checked. Every gate above exists to
make that specific outcome impossible.
