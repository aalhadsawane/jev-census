# Example: Hacker News stories

Very short documents; the extreme of schema dominance.

This example **is** the bundled demo (T7.2) — not duplicated here, since the whole point of `census
demo` is to be exactly this example, runnable with one command and no clone. See:

- [`src/jev_census/demo_data/questions.yaml`](../../src/jev_census/demo_data/questions.yaml) — the
  5-question battery
- [`src/jev_census/demo_data/stories.parquet`](../../src/jev_census/demo_data/stories.parquet) — 1,910
  real Hacker News story titles
- [`src/jev_census/demo_data/SOURCE.md`](../../src/jev_census/demo_data/SOURCE.md) — corpus provenance
  and licence
- [`src/jev_census/demo_data/gold/`](../../src/jev_census/demo_data/gold/) — the bundled gold set

Run it with:

```
census demo
```

**Result — real, live, unedited** (`census demo`'s own run, 42.8s total wall clock):

```
question                type    accuracy  n   ECE    threshold       coverage  verdict
is_question_post        noul    1.00      30  —      |p-0.5| > 0.12  100%      PASS
likely_controversial    noul    0.97      30  —      —               —         PASS
shows_something_built   noul    0.90      30  —      —               —         PASS
technical_depth         score   0.57      30  0.230  —               —         FAIL — exploratory only
topic_area              choice  0.83      30  0.094  —               —         FAIL
```

**A real, disclosed failure**: `topic_area` misses its 0.90 gate at 0.83, not marked exploratory —
this example was not edited or re-run to hide it. Five categories with an `other` catch-all is a
harder discrimination task than the noul questions, and this is what that costs in practice, measured
rather than assumed. `technical_depth` is `gate: exploratory` by design and shows exactly the pattern
`01-DESIGN.md` predicts for `score`-type subjective judgments: low exact agreement (0.57) but perfect
adjacent agreement (never more than one level off) — noisy, but ordered, not random.

Same corpus, questions, and gold set as `census demo` — this page exists to satisfy the "three shapes"
structure of `06-P7-ADOPTION.md` T7.3 without asking a stranger to download the same data twice.
