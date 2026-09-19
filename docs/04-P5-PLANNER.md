# 04 — P5: Planner and projections

Elaborated spec for phase P5 of `02-BUILD-PLAN.md`. Read `01-DESIGN.md` § Call planning first.

**What the phase changes:** today every question sees the same unioned state and `projection_id` is
the hardcoded string `"p0"` (`scheduler.py`, two places). After P5, questions are partitioned by
declared projection, each partition becomes its own call carrying only its own fields, and oversized
documents are chunked instead of silently sent whole.

---

## The worked example this phase must produce

`support-triage.yaml`, now with a second projection:

```yaml
version: 1
name: support-triage
defaults:
  projection: [subject, body]

questions:
  - id: is_urgent
    type: noul
    instructions: The customer states or implies the problem is time-sensitive.

  - id: department
    type: choice
    instructions: The team that should handle this ticket.
    criteria: { billing: ..., technical: ..., sales: ... }

  - id: thread_went_hostile
    type: noul
    projection: [subject, body, thread]     # different projection → different call
    instructions: The conversation became hostile after the first reply.
```

Two call groups. For document `T-1041`, exactly two requests go out:

```jsonc
// call 1 — projection a3f9c1b2, questions is_urgent + department
{ "state": { "body": "Help! My payouts...", "subject": "Payouts failing" },
  "questions": { "is_urgent": {...}, "department": {...} } }

// call 2 — projection 7e04d5aa, question thread_went_hostile
// `thread` is present here and ONLY here; is_urgent never saw it
{ "state": { "body": "Help! My payouts...", "subject": "Payouts failing", "thread": "[...]" },
  "questions": { "thread_went_hostile": {...} } }
```

and `census estimate` prints:

```
  480,000 documents · 3 questions · 2 call groups · state sent 2x per document
  tokens/doc:  state 265 (48%)  ·  schema 290 (52%)
    group a3f9c1b2  [subject, body]           2 questions   state 120  schema 210
    group 7e04d5aa  [subject, body, thread]   1 question    state 145  schema  80
  total:       266M tokens  ≈  $11.17
  runtime:     ~32 min at 250 docs/s
```

Three cells still land in `cells.parquet` for `T-1041`, but `projection_id` now differs between them
and `call_id` differs between the two groups.

---

## What changes in code that already exists

| File | Change | Why |
|---|---|---|
| `planner.py` | **new** | `CallGroup`, `plan_call_groups()`, `project_state()`, `split_for_context()` |
| `chunking.py` | **new** | `chunk_document()`, `aggregate_chunk_answers()` |
| `scheduler.py` | `_process_one_document` loops over call groups instead of building one state | The hardcoded `projection_id="p0"` at both `build_cell` sites disappears |
| `estimate.py` | per-group token totals; `call_group_count` stops being the literal `1` | T5.3 |
| `validator.py` | projection rules (below) | T5.1 |
| `cli.py` | `run` prints the group breakdown before starting | T5.3 |
| `runner.py` | **not touched** | It stays the sequential single-group reference implementation. Note that in a comment rather than porting it. |

---

## T5.1 — `projection` in the question schema, with defaults

`Question.projection` and `QuestionSet.resolved_projection()` already parse (`question_set.py`).
What is missing is validation, and it is missing in a way that currently fails silently: a typo'd
field name reaches `doc.fields.get(field)` and sends `{"subjct": null}` to the model, which then
answers confidently about nothing.

**Build:** three rules in `validator.py`, plus one runtime check in the planner.

| Rule | Where | Message |
|---|---|---|
| Resolved projection must be non-empty | validator | `question 'x': projection is empty; a question with no state cannot be answered` |
| Projection field names unique within a question | validator | names the duplicate |
| Every question must resolve to a projection | validator | `question 'x': no projection, and the question set declares no default` |
| Every projection field exists on the first document | planner, runtime | `projection field 'subjct' is not a column in the corpus; available: [body, created_at, subject, thread]` |

**Decisions already made — do not re-litigate:**

- `projection: []` in YAML is an *error*, not "send nothing". `projection: null`/absent means inherit
  the default. Pydantic already distinguishes the two; keep it that way.
- The runtime field check runs against the **first document only**, then never again. A corpus with
  ragged columns is a source problem; per-document checking would cost a dict scan per call for a
  guarantee the first row already gives.
- That runtime failure is **fatal, not quarantine**. It is a config error in the same family as `422`:
  every document will fail identically, so aborting immediately is the kind thing to do. Raise the
  existing `RunnerError`; do not add a new exception type.

**Done when:** a question set with a typo'd projection field aborts on document 1 with the field name
and the available columns in the message, and the test asserting that is committed.

---

## T5.2 — Grouping by projection; `CallGroup` construction

**Build `planner.py`:**

```python
@dataclass(frozen=True)
class CallGroup:
    projection_id: str               # stable_hash(fields)[:8]
    fields: tuple[str, ...]          # SORTED — the id is a hash of this
    questions: tuple[Question, ...]  # YAML order preserved within the group

def plan_call_groups(question_set: QuestionSet) -> list[CallGroup]:
    """Pure. Partition questions by resolved projection. Groups returned in
    order of first appearance in the YAML."""

def project_state(doc: Document, group: CallGroup) -> dict[str, Any]:
    """{field: doc.fields[field] for field in group.fields} — sorted key order,
    which is what makes the cache key stable across runs."""

def split_for_context(
    group: CallGroup, state: dict, *, context_limit: int, margin: float = 0.15
) -> list[tuple[str, ...]]:
    """Question-id batches that each fit. Greedy in YAML order. Returns one batch
    in the common case; several only when the schema is genuinely too large."""
```

**Decisions already made:**

- **`projection_id` is `stable_hash(sorted_fields)[:8]`, not `p0`/`p1`.** Ordinal ids change when
  someone reorders questions in the YAML, which would make two semantically identical runs produce
  diffing Parquet. A hash of the sorted field list is stable under reordering, stable across question
  sets, and comparable between runs. Readability is recovered by writing the map
  `{projection_id: [fields]}` into `manifest.json` and printing it in the estimate/run breakdown.
- **Sorted field order is load-bearing.** `cache_key()` hashes the projected state dict; an unsorted
  dict would miss the cache after a harmless YAML reorder. The existing scheduler already sorts —
  keep it, and put the reason in a comment where someone would be tempted to preserve declaration
  order.
- **`split_for_context` splits by question, never by projection.** Dropping a field to fit is a
  silent accuracy change; moving a question to the next call is not.
- Grouping is pure and network-free. No I/O in this module at all.

**Property tests** (the build plan's Testing section already asks for these):

- Every question appears in exactly one group; the union is the whole set.
- `plan_call_groups` is deterministic across processes — assert in a subprocess, as `test_hashing.py`
  already does for hashes.
- Two questions declaring the same fields in a different order land in the **same** group with the
  same `projection_id`.
- No batch returned by `split_for_context` exceeds the limit.

**Done when:** the four properties above are tested and `planner.py` imports nothing that touches the
network.

---

## T5.2b — Verify the context limit before depending on it (Rule Zero)

`00-JEV-API.md` says "Bounded context window, ~32k tokens. **Verify the exact figure.**"
`split_for_context` and all of T5.4 depend on that number, and nobody has observed it.

Send deliberately oversized states against the live API until it refuses. Record the boundary and the
error shape (is it a `422`, or something else?), and write the finding into `00-JEV-API.md` in the
same commit as the code that uses it. Budget: cents. If the limit turns out to be enforced in
characters or bytes rather than tokens, that changes `split_for_context`'s arithmetic — which is
exactly why this runs before the code that assumes otherwise.

Put the resulting number in one named constant, `CONTEXT_LIMIT_TOKENS`, in `planner.py`. Nothing else
may hardcode it.

---

## T5.3 — Group-count reporting in `estimate` and `run`

`EstimateResult` gains a per-group breakdown. Keep the existing top-level fields: the README's
single-group output block must still render byte-identically for a one-projection question set,
because `census estimate` on the README's own question set is one of the numbers T7.5 has to
reproduce.

```python
@dataclass(frozen=True)
class GroupEstimate:
    projection_id: str
    fields: tuple[str, ...]
    question_count: int
    avg_state_tokens: float
    avg_schema_tokens: float

@dataclass(frozen=True)
class EstimateResult:
    ...                                   # existing fields unchanged
    groups: tuple[GroupEstimate, ...]     # new
```

- `avg_state_tokens` / `avg_schema_tokens` at the top level become the **sum across groups** —
  per-document totals, which is what the cost line already means.
- `call_group_count` becomes `len(groups)` instead of the literal `1`.
- `format_estimate()` prints the per-group lines **only when `len(groups) > 1`**, so one-projection
  output is unchanged.
- The headline line gains `· state sent Nx per document` when N > 1. `01-DESIGN.md` specifies that
  wording; use it verbatim.
- `census run` prints the same breakdown once before the first call, so the cost multiplier is visible
  before money moves rather than in the summary afterwards.

**Done when:** `census estimate` on the README's question set produces the README's block character
for character, and on a two-projection set produces the block at the top of this document.

---

## T5.4 — Long-document chunking and per-type aggregation

The only part of P5 with real design risk. It triggers when one document's projected state does not
fit even alone: `tokens(state) + tokens(smallest viable schema) + margin > CONTEXT_LIMIT_TOKENS`.

**Build `chunking.py`:**

```python
def chunk_document(
    state: dict, group: CallGroup, *, context_limit: int,
    margin: float = 0.15, overlap: float = 0.10,
) -> list[dict]:
    """Split the LARGEST field of the state; carry every other field whole into
    every chunk. Returns [state] unchanged when it already fits."""

def aggregate_chunk_answers(answers: list[DecodedAnswer]) -> DecodedAnswer:
    """One question's answers across N chunks -> one cell. See the table below."""
```

**Aggregation, per type** (`01-DESIGN.md` § Long documents):

| Type | Value | `probabilities` |
|---|---|---|
| `noul` | **max** across chunks (mean available behind a config flag) | stays `None` |
| `choice` | argmax of the **mean** per-option distribution | the mean distribution |
| `score` | mean of the per-chunk weighted scores | the mean per-level distribution |

Max for `noul` because the question is almost always "does this document contain or exhibit X" — one
chunk finding it means the document has it. Mean would dilute a true positive across a long document,
which is the failure mode that matters here. The flag exists for the rarer "is the document as a
whole X" phrasing.

**Decisions already made:**

- **An aggregated cell's `confidence_source` is `"derived"` for every type, including `choice` and
  `score`.** The model reported a confidence per chunk; the number on the aggregated row is our
  arithmetic over several of them. Calling it `model` would be the same lie `01-DESIGN.md` D3 exists
  to prevent. The confidence is recomputed from the aggregated distribution, and for `noul` from
  `|p − 0.5| × 2` as usual.
- **`legend` must be identical across chunks or the aggregation is a bug** — same question, same
  criteria. Assert it rather than merging.
- **Cache per chunk; never cache the aggregate.** The cache key already hashes the projected state,
  and a chunk's state is just a smaller state, so chunks cache with no schema change at all. The
  aggregate is cheap to recompute and caching it would need a second key space. A resumed run
  therefore re-pays for nothing, which is the T2.7 invariant.
- **`chunk_count` is the number of chunks that actually answered**, so a partially failed chunk set is
  visible in the output rather than quietly averaged over.
- **If a single chunk still does not fit** — one unsplittable field longer than the whole window —
  quarantine the document with reason `state_exceeds_context`. Never truncate. `01-DESIGN.md`'s
  failure table already says this; the tempting `state[:limit]` is the line to put a comment beside.
- T6.1 excludes `chunk_count > 1` cells from validation samples by default. That is a P6 task, but the
  column it reads is written here — do not leave it at the `1` default.

**Done when:** a synthetic document three times the context limit produces N chunks with the declared
overlap, one cell per question with `chunk_count == N` and `confidence_source == "derived"`, and the
aggregation table above is reproduced by a unit test per type against hand-computed expected values.

---

## Phase exit

The literal criterion from `02-BUILD-PLAN.md`:

> a two-projection question set produces two calls per document, each state carrying only its group's
> fields.

Verify it the way P2–P4 were verified — not by inference:

1. A test with `FakeAsyncJevClient` recording every `state` it was asked with, asserting two calls per
   document **and** asserting `"thread" not in` the first call's state. The negative assertion is the
   one that matters; a call count alone would pass with both states unioned.
2. A live run on ~50 documents with a two-projection set, confirming the cost is meaningfully above
   the one-projection run of the same corpus. The multiplier is real money and should be observed
   once.
3. A `DECISIONS.md` entry recording the measured context limit, whether chunking was exercised, and
   any divergence from the aggregation table above.
