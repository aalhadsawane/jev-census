# Running it yourself, locally

**`pip install jev-census` (as shown in the README) does not work yet — the package has never been
published to PyPI.** Every install verified so far (`scripts/verify_clean_install.sh`, P7) installs a
wheel *built from this source tree*, not a package fetched from PyPI. That is a real, tracked gap, not
just a wording issue — see `DECISIONS.md`'s dated entry on it. This page is the workaround: everything
below is exactly what `pip install jev-census` is supposed to feel like once that gap is closed, run
from source in the meantime.

Every command below has actually been run, not just written — if one doesn't work for you exactly as
shown, something in this doc is out of date and worth reporting as a bug, not a personal mistake.

---

## 1. Prerequisites

- **Python 3.11 or newer.** Check with `python3 --version`.
- **A TypeSafe API key** (`TYPESAFE_API_KEY`) — required for anything that actually calls the model
  (`census demo`, `census run`, ...). `census estimate` needs no key; it never spends.
- **`git`**, to get the code.

You do **not** need `uv`, Rust, or anything beyond a standard Python install. If your system Python
refuses `pip install` outright with an `externally-managed-environment` error (common on current
macOS/Homebrew Python and many Linux distros, PEP 668) — that's expected and handled below by using a
virtual environment for everything, which sidesteps it entirely.

---

## 2. Get the code

```
git clone https://github.com/aalhadsawane/jev-census.git
cd jev-census
```

(If you already have a local clone, `git pull` inside it instead.)

---

## 3. Create a virtual environment and install

```
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -e ".[dev]"
```

`-e` (editable) means the installed package points straight at this source tree — no rebuild needed
if you edit code, and the fastest way to just try the tool. `[dev]` pulls in `pytest`, `ruff`, and
`duckdb` too, in case you want to run the test suite or the DuckDB recipes later (§6, §7).

Confirm it worked:

```
census --help
```

If you'd rather verify it the way a real installed *package* would look — no editable link back to
this source tree — build and install a wheel instead:

```
python -m pip install build
python -m build --wheel
pip install dist/jev_census-0.1.0-py3-none-any.whl
```

(`scripts/verify_clean_install.sh` does exactly this, plus creates a throwaway venv away from the repo
to prove nothing leaks in from the source tree — run it directly if you want that specific proof:
`bash scripts/verify_clean_install.sh`.)

---

## 4. Set your API key

```
export TYPESAFE_API_KEY=sk-...
```

Or create a `.env.local` file in whichever directory you run `census` from:

```
TYPESAFE_API_KEY=sk-...
```

`census run`/`census demo` load `.env.local` automatically if the environment variable isn't already
set.

---

## 5. Try the demo

```
census demo
```

Runs the bundled Hacker News corpus (1,910 real story titles, shipped inside the package) end to end:
estimate, live run, validation report. Costs about $0.06 and finishes in under a minute. Add
`--dry-run` to see the cost estimate without spending anything or needing a key at all:

```
census demo --dry-run
```

Look at the output:

```
cat demo-results/validation_report.md
```

---

## 6. Try the other examples

Three corpus shapes live in `examples/`, each already run once with a committed result you can read
without spending anything (`examples/*/validation_report.md`), and each re-runnable:

```
cd examples/support-tickets
census run --input tickets.parquet --questions questions.yaml --budget 1 --out results --id-field ticket_id
cat validation_report.md   # the committed one; results/validation_report.md would need `census validate` too
cd ../..
```

`examples/long-form/` is the same shape, and the one that exercises chunking (one of its 18 real
READMEs is long enough to need it) — see `examples/long-form/README.md` for the story. `examples/hacker-news/`
*is* the bundled demo; see `examples/hacker-news/README.md` rather than re-running it separately.

---

## 7. Try the DuckDB recipes

Needs the `duckdb` CLI ([install instructions](https://duckdb.org/docs/installation/)) or just the
Python package already installed above via `[dev]`:

```
duckdb -c "SELECT * FROM 'demo-results/cells.parquet' LIMIT 5"
```

`docs/RECIPES.md` has four full recipes (long→wide pivot, thresholding, overrides, time series),
copy-pasteable against the demo output above.

---

## 8. Run the test suite (optional)

```
pytest -q
```

Should show every test passing, network-free (no API key needed — the suite never makes live calls).

---

## What this doesn't cover

Publishing to PyPI itself — that needs a PyPI account/token and is a genuine, deliberate, hard-to-undo
public action, not something to do as a side effect of testing. It's tracked, not forgotten: see
`DECISIONS.md`'s entry on it for exactly what's blocking it.
