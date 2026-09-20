# Example: support tickets

Short documents; schema-dominated cost. This is the README's own worked example, run for real.

**`tickets.parquet`** — 30 **synthetic** support tickets, written by hand for this example. Not real
customer data; no privacy concerns, but also not a claim about real-world ticket distributions.

**`questions.yaml`** — identical to the README's own `support-triage.yaml`: `is_urgent` (noul),
`department` (choice), `frustration` (score), `churn_risk` (noul).

**`gold/`** — hand-labelled ground truth for all 30 tickets, one file per question. Written by reading
each ticket and judging it directly — 30 documents, well short of the 150–250 recommended for a
production validation gold set (`05-P6-QUALITY.md`), disclosed as such rather than inflated.

## Result — real, live, unedited

```
$ census run --input tickets.parquet --questions questions.yaml --budget 1 --out results --id-field ticket_id
  spent $0.0007, 30 documents

question     type    accuracy  n   ECE    threshold  coverage  verdict
churn_risk   noul    0.97      30  —      —          —         PASS
department   choice  0.87      30  0.110  —          —         FAIL
frustration  score   0.80      30  0.130  —          —         FAIL
is_urgent    noul    0.90      30  —      —          —         PASS
```

Full report: [`validation_report.md`](validation_report.md).

**Two real, disclosed failures** — this example was not edited or re-run to make the numbers look
better. `department` (0.87) and `frustration` (0.80) both fall short of the default 0.90 gate.
`frustration`'s failure is unsurprising: scoring how frustrated a short synthetic ticket sounds is a
genuinely more subjective judgment than routing it to a department, for both the model and whoever
wrote the gold labels — exactly why `01-DESIGN.md` treats `score` as the type most likely to need
`gate: exploratory` in practice. `department`'s miss is worth a closer look before trusting it in
production: at n=30 the Wilson interval is wide enough ([0.70, 0.95]) that a larger gold set could
land either side of the 0.90 line.

No threshold or coverage numbers appear for most questions: T6.4's search requires enough
surviving rows to trust a candidate (`n_eff ≥ 30`), and a 30-document sample has no room to spare once
any filtering happens — the same limitation the bundled demo hits. A real deployment's gold set should
be large enough that thresholding has something to work with.
