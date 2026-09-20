# DuckDB recipes

Copy-pasteable against the bundled demo's output. Run `census demo` first, then either paste a query
into `duckdb demo-results/cells.parquet` interactively, or run it directly:

```
$ duckdb -c "$(cat query.sql)"
```

Every query below is verified against real `demo-results/cells.parquet` output (T7.4,
`06-P7-ADOPTION.md`) and tested in the suite (`tests/test_recipes.py`), which extracts every SQL block
on this page and runs it. If a query here ever stops working against a fresh `census demo` run, that
test fails — this page cannot silently drift from the schema.

---

## 1. Long → wide

`cells.parquet` is long format — one row per `(doc_id, question_id)` — because adding a question
appends rows instead of migrating a schema (`01-DESIGN.md`). Most analysis wants one row per document,
so pivot at read time, never at write time:

```sql
PIVOT (
  SELECT doc_id, question_id,
         COALESCE(noul::VARCHAR, choice, score::VARCHAR) AS value
  FROM 'demo-results/cells.parquet'
)
ON question_id
USING FIRST(value)
ORDER BY doc_id
LIMIT 10;
```

`COALESCE` picks whichever of `noul`/`choice`/`score` is non-null for that row's type — exactly one of
the three ever is, by construction (`01-DESIGN.md`'s output schema). The result is a wide table, one
column per question, values as text; cast a column back to `DOUBLE` if you need to filter or sort on a
`noul`/`score` question numerically.

---

## 2. Thresholding — on each type's own scale

`noul` and `choice`/`score` are never the same scale (`01-DESIGN.md` D4): a `noul`'s own value *is*
P(yes), so its threshold is distance from the coin flip; `choice` and `score` have no such value, so
their threshold is on the model's reported `confidence` instead. Take the actual numbers from your own
`validation_report.md`, not the placeholders below — a threshold is only meaningful once it has been
validated (T6.4).

```sql
SELECT
  count(*) FILTER (WHERE question_id = 'is_question_post' AND abs(noul - 0.5) > 0.12)  AS confident_noul,
  count(*) FILTER (WHERE question_id = 'topic_area'        AND confidence > 0.7)        AS confident_choice,
  count(*) FILTER (WHERE question_id = 'technical_depth'    AND confidence > 0.7)        AS confident_score
FROM 'demo-results/cells.parquet';
```

The three `WHERE` clauses look almost identical and are not: `abs(noul - 0.5) > t` only makes sense for
`noul` (it has no `confidence` column semantics comparable across a coin-flip boundary); `confidence > t`
only makes sense for `choice`/`score`, whose `confidence` is model-reported directly. Mixing the two —
thresholding a `choice` column on `abs(x - 0.5)`, say — is exactly the cross-type comparison `01-DESIGN.md`
D4 forbids, and DuckDB will happily run the wrong query without complaint.

`is_question_post`'s `0.12` above is this demo's own real, validated threshold
(`demo-results/validation_report.md`, `census validate`'s T6.4 search) — replace it with your own. The
`0.7` placeholders for `topic_area`/`technical_depth` are illustrative only: on this demo's small
bundled gold set (30 labels), the threshold search found no confidence cutoff meeting its 0.99-accuracy
target while keeping enough rows to trust (T6.4's minimum-sample guard) — the honest "—" in the report,
not a number to copy.

---

## 3. Overrides — merged at read time, never at write time

`census review import` writes `results/overrides.parquet` as a separate file
(`doc_id, question_id, human_answer, reviewer, ts, source_run_id`) and never touches `cells.parquet`
(T6.6, `01-DESIGN.md`) — a model output column has to stay a model output column, or the accuracy
numbers already measured against it become fiction. The merge happens here, in the query, every time:

```sql
SELECT
  c.doc_id,
  c.question_id,
  COALESCE(o.human_answer, c.choice, c.noul::VARCHAR, c.score::VARCHAR) AS final_answer,
  o.human_answer IS NOT NULL AS was_overridden
FROM 'demo-results/cells.parquet' c
LEFT JOIN 'demo-results/overrides.parquet' o
  ON c.doc_id = o.doc_id AND c.question_id = o.question_id
LIMIT 10;
```

The demo doesn't run a review cycle, so `demo-results/overrides.parquet` won't exist unless you've run
`census review export` / `census review import` yourself first; the test for this page supplies a small
fixture file with the right schema so the query itself stays verified either way.

---

## 4. Time series — with coverage stated

`cells.parquet` carries no document timestamp (`01-DESIGN.md` reserves that for a `documents.parquet`
source-passthrough file, which nothing in this codebase writes yet — a known gap, not a feature; see
`DECISIONS.md`'s P7 entry). Until that exists, join back to your original source corpus instead — you
already have it, since you supplied it as `--input`:

```sql
WITH threshold AS (SELECT 0.12 AS t),
weekly AS (
  SELECT
    date_trunc('week', epoch_ms(s.time * 1000)) AS week,
    count(*) FILTER (WHERE c.question_id = 'is_question_post' AND abs(c.noul - 0.5) > t.t) AS above_threshold,
    count(*) FILTER (WHERE c.question_id = 'is_question_post') AS total
  FROM 'demo-results/cells.parquet' c
  JOIN read_parquet('demo_data_path_placeholder/stories.parquet') s ON c.doc_id = s.id
  CROSS JOIN threshold t
  GROUP BY 1
)
SELECT
  week,
  above_threshold,
  total,
  round(100.0 * SUM(total) OVER (ORDER BY week) / SUM(total) OVER (), 1) AS cumulative_pct_of_corpus
FROM weekly
ORDER BY week
LIMIT 10;
```

`epoch_ms(unix_seconds * 1000)`, not `to_timestamp(unix_seconds)` — the latter goes through a timezone
lookup that needs `pytz` installed for DuckDB's Python bindings specifically (irrelevant if you're
running the standalone `duckdb` CLI, but this page is tested through the Python bindings, so it uses the
form that needs nothing extra either way).

**State the coverage.** `cumulative_pct_of_corpus` is there on purpose: a time series built only from
cells above a confidence threshold silently drops whatever the threshold's own coverage excludes (T6.4)
— a chart with an unstated 30% missing is the chart that gets corrected in public (`03-LAUNCH.md`'s
honesty rules). Show the running coverage next to the trend, not just the trend.

`demo_data_path_placeholder` above is filled in by the test that runs this page — replace it with your
own corpus's path when you copy this query.
