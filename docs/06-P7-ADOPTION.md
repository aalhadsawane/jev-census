# 06 — P7: Adoption

Elaborated spec for phase P7 of `02-BUILD-PLAN.md`.

**The exit criterion is behavioural, not structural:** a stranger with an API key and no knowledge of
this repository gets labelled results in five minutes. Every task below is scored against that
stopwatch, so the acceptance checks are scripts that actually run from a clean machine rather than
claims that it would work.

---

## The worked example this phase must produce

```
$ pip install jev-census
$ export TYPESAFE_API_KEY=...
$ census demo

  demo: 2,000 Hacker News stories · 5 questions · 1 call group
  estimate: 0.9M tokens ≈ $0.04 · budget $0.25

  [==========] 100%  2,000 docs  $0.04/$0.25  310 docs/s  cache 0%

  wrote demo-results/cells.parquet          10,000 cells
  wrote demo-results/validation_report.md   5 questions, 4 PASS, 1 exploratory

  next:  duckdb -c "SELECT * FROM 'demo-results/cells.parquet' LIMIT 5"
         docs/RECIPES.md has the long→wide pivot and thresholding queries
```

Four minutes, one command, one credential, no clone.

---

## T7.1 — Package and publish

**Build:** `uv build` producing a wheel and sdist; `pip install jev-census` works in a venv that never
saw the source; the `census` entry point is on `PATH`.

**"Publish" means two different things — don't let verifying one stand in for the other.**
`scripts/verify_clean_install.sh` proves the *packaging* is correct: it builds a wheel from this source
tree and installs that wheel file, in a venv outside the repo. It does not touch PyPI, so it cannot
prove `pip install jev-census` — installing the package *by name* — actually works, because the
package has never been published there. See `DECISIONS.md`'s "Flagged: pip install jev-census doesn't
work yet" entry before treating T7.1 as fully closed; `docs/RUNNING-LOCALLY.md` is the from-source
workaround until a real publish happens.

**Two specific traps in this codebase:**

1. **`cli.py` imports `tests.fakes`** inside `_build_async_client()` when `CENSUS_FAKE_CLIENT=1`. That
   import is function-local so it does not run on a normal invocation, but an installed wheel has no
   `tests` package and a future refactor that hoists the import to module scope would break every
   installed copy while every local test still passed. Add a test asserting the installed package
   imports cleanly with `tests` absent from `sys.path`.
2. **Package data.** The demo corpus and bundled question sets must be declared in `pyproject.toml` as
   package data, or they vanish from the wheel and `census demo` fails only for people who installed
   it — the exact population this phase exists for.

**Acceptance:** a script (`scripts/verify_clean_install.sh`) that creates a throwaway venv in a temp
directory, installs the built wheel, runs `census --help` and `census estimate` on the bundled demo
corpus, and fails loudly on any import of the source tree. Run it from a directory that is not the
repository, so a stray relative import cannot pass by accident.

---

## T7.2 — `census demo`

**Decision: the demo corpus ships inside the wheel.** Downloading at demo time adds a second network
dependency to a five-minute promise, and the failure mode — a stranger's first command hanging on a
dead URL — is the worst one available. Bundle roughly 2,000 rows, kept under ~1 MB, as Parquet in
`jev_census/demo_data/`.

**Corpus requirements:** public, redistributable, recognisable, and containing answers `grep` could
not produce. Hacker News story titles fit and line up with the P8 analysis, so the demo doubles as a
preview of it. **Verify and record the licence of whatever is bundled before committing it**, in
`demo_data/SOURCE.md`, with the retrieval date and the exact query or API used.

**`census demo` does, in order:** print the estimate, run with a hard default budget (`$0.25`, still
overridable), write to `./demo-results/`, then run `census validate` against a **bundled gold set** —
hand-labelled once, committed, so the demo produces a real validation report rather than an empty
template. That gold set is what makes the demo show the actual product instead of a classifier.

`--dry-run` runs everything except the API calls, for someone evaluating without a key.

**Acceptance:** timed from a clean venv, under five minutes wall clock including install, on a normal
connection. Record the real number in `DECISIONS.md`; if it is over five minutes, either the corpus
shrinks or the claim changes — do not publish a stopwatch number nobody ran.

---

## T7.3 — Three example question sets

One per common corpus shape, because the cost story differs by shape and the examples are where people
learn to write questions:

| Example | Shape | Shows |
|---|---|---|
| Support tickets | short documents | schema-dominated cost; the README's own example |
| Hacker News stories | very short documents | the extreme of schema dominance; the P8 corpus |
| Long-form (papers or READMEs) | long documents | state-dominated cost; the batching win; chunking if it triggers |

**Each ships with a committed validation report**, which means each needs a gold set. Budget 150–250
labels per question — this is the genuinely expensive part of P7 and the one most likely to get
quietly skipped. An example question set without a validation report teaches the wrong lesson: it says
the question set is fine, which is precisely the claim P6 exists to stop anyone making for free.

Include **one question that fails its gate** in at least one example, with the failure visible in the
committed report. Someone reading the examples should see what failure looks like before they hit it
on their own data.

---

## T7.4 — DuckDB recipes

`docs/RECIPES.md`, every query copy-pasteable against the demo output:

1. **Long → wide.** `PIVOT` over `question_id`, one row per document. The first thing everybody wants
   and the reason the long format needed defending in `01-DESIGN.md`.
2. **Thresholding on each type's own scale.** `noul` on `abs(noul - 0.5) > t`, `choice`/`score` on
   `confidence > t`, with the thresholds taken from `validation_report.md`. Show the three side by
   side and say in one line why they are not the same expression — this is where D4 becomes a thing a
   user does rather than a thing a document asserts.
3. **Overrides.** `COALESCE` of `overrides.parquet` over `cells.parquet`, joined on
   `(doc_id, question_id)` — the read-time merge T6.6 deliberately refused to do at write time.
4. **Time series.** Join `documents.parquet` for a timestamp, bucket by week, count above threshold,
   and **state the coverage** the threshold implies in the same query's output. A time series that
   silently drops 30% of the corpus is the chart that gets corrected in public.

**Acceptance — make the doc executable.** A test extracts every SQL block from `RECIPES.md`, runs it
against the demo output with DuckDB, and asserts it returns without error. A recipes file that has
drifted from the schema is worse than none, and this is cheap to prevent.

---

## T7.5 — README with only reproducible numbers

The README currently ends with **"Status: Design stage. No code yet."** and carries projected figures
from the design phase. Both have to go.

**Rule:** no number in the README that a command in the README cannot regenerate. Build
`scripts/readme_numbers.py` emitting every claimed figure with its source command, and a table in
`docs/PROVENANCE.md` mapping each README number to the command and the run id that produced it.

**Numbers to replace with measured ones:**

| Claim | Replace with |
|---|---|
| 480,000 tickets ≈ $7, ~half an hour | real `census estimate` output, or restated as the demo's actual figures |
| the `validation_report.md` block | the real report from a real gold set |
| `cells.parquet` example rows | real rows from a real run |
| "Design stage. No code yet." | install instructions, the demo command, and current phase status |
| the frontier-LLM cost comparison | recomputed from current prices, with the arithmetic and the output-token assumption shown |

`03-LAUNCH.md` already makes the last one a rule: *a number the reader can re-derive beats a bigger one
they cannot.*

---

## Phase exit

> a stranger can use it without reading source.

1. `scripts/verify_clean_install.sh` passes from outside the repository.
2. `census demo` timed under five minutes from a clean venv, number recorded.
3. Three example question sets, each with a committed validation report, at least one containing a
   disclosed failure.
4. Every SQL block in `RECIPES.md` executes in the test suite.
5. Every README number traceable through `docs/PROVENANCE.md`.
6. **The real test:** hand the install command to somebody who has not seen the project and watch
   without helping. Whatever they get stuck on is the actual P7 backlog, and it will not be on this
   list.
