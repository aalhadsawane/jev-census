# Example: long-form documents

Long documents; state-dominated cost, the batching win, and — for the one document long enough to
trigger it — real chunking.

**`readmes.parquet`** — 18 real open-source project READMEs. See [`SOURCE.md`](SOURCE.md) for exact
sources and licenses.

**`questions.yaml`** — 5 questions about documentation quality: whether the README gives inline
install steps, whether it has code examples, its primary technology ecosystem, an overall clarity
score (`gate: exploratory`), and whether it assumes prior domain expertise.

**`gold/`** — hand-labelled ground truth for all 18 documents, one file per question, judged by reading
each README directly. 18 documents — smaller even than the other two examples, disclosed rather than
padded; see below for what that costs the report.

## Result — real, live, unedited

```
question               type    accuracy  n   ECE    threshold  coverage  verdict
assumes_expertise      noul    0.61      18  —      —          —         INSUFFICIENT
documentation_clarity  score   0.22      18  0.513  —          —         INSUFFICIENT
has_code_examples      noul    0.83      18  —      —          —         INSUFFICIENT
has_quickstart         noul    0.72      18  —      —          —         INSUFFICIENT
primary_ecosystem      choice  0.94      18  0.052  —          —         INSUFFICIENT
```

Full report: [`validation_report.md`](validation_report.md).

**Every question shows `INSUFFICIENT` — correctly, not as a bug.** T6.2's rule is `n < 30 →
INSUFFICIENT`, and this example has 18 gold labels. Rather than pad the gold set past its honest size
to force a PASS/FAIL verdict, this example is left exactly as measured: `INSUFFICIENT` is what the tool
is supposed to say when the evidence doesn't support a real number yet, and this is what that output
looks like. The raw weighted-accuracy numbers above (0.94 for `primary_ecosystem`, 0.22 for
`documentation_clarity`) are still informative as a preview — `documentation_clarity` in particular
looks like a genuinely hard, subjective judgment even before a large enough sample can confirm it — but
none of them are a validated claim at this size.

**The batching win, measured directly.** These 18 documents average roughly 2,800 tokens of state each
against ~90–130 tokens of schema per question — the opposite ratio from the Hacker News example, where
schema dominates. Run `census estimate --input readmes.parquet --questions questions.yaml --id-field repo`
to see the real split for this corpus.

**Real chunking, exercised live, and a real bug it found.** `axios`'s README is ~108,000 characters —
long enough to exceed the measured ~32.8k-token context limit (`04-P5-PLANNER.md` T5.2b) on its own. The
first live run against this corpus quarantined it instead of chunking it: `chunk_document`'s
char-count-based budget didn't account for JSON-escaping overhead (every literal quote and newline in
the markdown costs extra characters once serialized into the request), so a chunk that "fit" by the
naive character count came out over budget once actually measured — a real, live-discovered bug, fixed
by verifying the real token estimate and shrinking the chunk if it disagrees. See `DECISIONS.md`'s P7
entry for the full story. After the fix, `axios` splits into 2 real chunks and every cell for it carries
`chunk_count=2` and `confidence_source=derived`, exactly as `01-DESIGN.md` specifies.
