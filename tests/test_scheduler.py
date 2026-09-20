from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from jev_census.runner import RunnerError
from jev_census.scheduler import ProgressSnapshot, SchedulerConfig, format_progress, run_scheduled
from tests.fakes import FakeAsyncJevClient, rate_limit_error, server_error

FIXTURES = Path(__file__).parent / "fixtures"


def _corpus(tmp_path: Path, n: int = 20) -> Path:
    rows = [
        {"ticket_id": f"T-{i:04d}", "subject": f"subject {i}", "body": f"unique body text for row {i}"}
        for i in range(n)
    ]
    path = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _config(tmp_path: Path, questions_path: Path, **overrides) -> SchedulerConfig:
    kwargs = {
        "input_path": _corpus(tmp_path),
        "questions_path": questions_path,
        "budget_usd": 10.0,
        "out_dir": tmp_path / "results",
        "census_dir": tmp_path / ".census",
        "id_field": "ticket_id",
        "concurrency_initial": 4,
        "concurrency_ceiling": 8,
    }
    kwargs.update(overrides)
    return SchedulerConfig(**kwargs)


async def test_fresh_run_produces_correct_table(tmp_path):
    config = _config(tmp_path, FIXTURES / "support-triage.yaml")
    client = FakeAsyncJevClient()
    result = await run_scheduled(config, client)

    assert result.documents_processed == 20
    assert result.documents_already_done == 0
    assert client.call_count == 20

    table = pq.read_table(result.out_path)
    assert table.num_rows == 80  # 20 docs x 4 questions
    doc_ids = {row["doc_id"] for row in table.to_pylist()}
    assert doc_ids == {f"T-{i:04d}" for i in range(20)}


async def test_rerun_identical_config_makes_zero_api_calls(tmp_path):
    questions_path = FIXTURES / "support-triage.yaml"
    corpus = _corpus(tmp_path)
    census_dir = tmp_path / ".census"

    config1 = SchedulerConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results1", census_dir=census_dir, id_field="ticket_id",
    )
    await run_scheduled(config1, FakeAsyncJevClient())

    config2 = SchedulerConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results2", census_dir=census_dir, id_field="ticket_id",
    )
    client2 = FakeAsyncJevClient()
    result2 = await run_scheduled(config2, client2)

    assert client2.call_count == 0
    assert result2.cache_misses == 0
    assert result2.cache_hits == 80


async def test_budget_cap_stops_admitting_new_calls(tmp_path):
    config = _config(tmp_path, FIXTURES / "support-triage.yaml", budget_usd=0.0)
    result = await run_scheduled(config, FakeAsyncJevClient())

    assert result.budget_exhausted is True
    assert result.documents_processed == 0
    assert result.spent_usd == 0.0


async def test_resume_refuses_on_questionset_hash_mismatch(tmp_path):
    import yaml

    config = _config(tmp_path, FIXTURES / "support-triage.yaml", budget_usd=0.0)
    result = await run_scheduled(config, FakeAsyncJevClient())

    changed_questions = tmp_path / "changed.yaml"
    base = yaml.safe_load((FIXTURES / "support-triage.yaml").read_text())
    base["questions"][0]["instructions"] = "A completely different judgement."
    changed_questions.write_text(yaml.dump(base))

    resume_config = SchedulerConfig(
        input_path=config.input_path, questions_path=changed_questions, budget_usd=10.0,
        out_dir=tmp_path / "results-resumed", census_dir=config.census_dir,
        id_field="ticket_id", resume_run_id=result.run_id,
    )
    with pytest.raises(RunnerError, match="question set changed"):
        await run_scheduled(resume_config, FakeAsyncJevClient())


async def test_server_error_retries_then_succeeds(tmp_path):
    """T4.2/T4.3: a 5xx gets bounded retries (not concurrency reduction)."""
    corpus = _corpus(tmp_path, n=1)
    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=10.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
    )
    state_key = '{"body": "unique body text for row 0", "subject": "subject 0"}'
    client = FakeAsyncJevClient(fail_plan={state_key: [server_error()]})
    result = await run_scheduled(config, client)

    assert result.documents_processed == 1
    assert result.documents_skipped == 0
    assert client.call_count == 1  # the successful attempt; the failed one didn't reach `calls`


async def test_rate_limit_retries_then_succeeds(tmp_path):
    """T4.3: a 429 triggers a retry (and reduces concurrency) rather than
    failing the document."""
    corpus = _corpus(tmp_path, n=1)
    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=10.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
        concurrency_initial=4, concurrency_ceiling=8,
    )
    state_key = '{"body": "unique body text for row 0", "subject": "subject 0"}'
    client = FakeAsyncJevClient(fail_plan={state_key: [rate_limit_error(retry_after_ms=1)]})
    result = await run_scheduled(config, client)

    assert result.documents_processed == 1
    assert result.documents_skipped == 0


async def test_server_error_exhausts_retries_and_quarantines_document(tmp_path):
    corpus = _corpus(tmp_path, n=1)
    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=10.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
    )
    state_key = '{"body": "unique body text for row 0", "subject": "subject 0"}'
    # max_attempts_server defaults to 3 -> 3 failures exhausts it entirely.
    client = FakeAsyncJevClient(fail_plan={state_key: [server_error(), server_error(), server_error()]})
    result = await run_scheduled(config, client)

    assert result.documents_processed == 0
    assert result.documents_skipped == 1


def test_format_progress_shows_done_and_paid_separately():
    snapshot = ProgressSnapshot(
        documents_total=100, documents_done=40, documents_processed=30, documents_already_done=10,
        cache_hits=60, cache_misses=60, spent_usd=1.23, budget_usd=10.0,
        concurrency_limit=8, elapsed_seconds=20.0, stopping=False,
    )
    text = format_progress(snapshot)
    assert "40/100" in text
    assert "$1.2300/$10.00" in text
    assert "concurrency 8" in text
    assert "cache 50%" in text
    assert "eta" in text


def test_format_progress_handles_zero_elapsed_and_unknown_total():
    snapshot = ProgressSnapshot(
        documents_total=None, documents_done=0, documents_processed=0, documents_already_done=0,
        cache_hits=0, cache_misses=0, spent_usd=0.0, budget_usd=10.0,
        concurrency_limit=4, elapsed_seconds=0.0, stopping=False,
    )
    text = format_progress(snapshot)
    assert "eta -" in text


def test_format_progress_marks_stopping():
    snapshot = ProgressSnapshot(
        documents_total=10, documents_done=5, documents_processed=5, documents_already_done=0,
        cache_hits=0, cache_misses=20, spent_usd=0.5, budget_usd=1.0,
        concurrency_limit=2, elapsed_seconds=5.0, stopping=True,
    )
    assert "[stopping]" in format_progress(snapshot)


async def test_progress_callback_invoked(tmp_path):
    corpus = _corpus(tmp_path, n=5)
    snapshots = []
    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=10.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
        progress_interval_seconds=0.01,
        on_progress=snapshots.append,
    )
    client = FakeAsyncJevClient(delay_seconds=0.02)
    await run_scheduled(config, client)
    assert len(snapshots) >= 1
    assert isinstance(snapshots[0], ProgressSnapshot)


async def test_tiny_bounded_queue_still_produces_correct_results(tmp_path):
    """T4.1 backpressure: asyncio.Queue(maxsize=1) forces the producer to
    block on every single put() until a worker drains an item — the
    tightest possible backpressure. Correctness must survive it: no lost,
    duplicated, or dropped documents, proving admission never races ahead
    of what's actually been queued for processing."""
    n = 50
    config = SchedulerConfig(
        input_path=_corpus(tmp_path, n=n), questions_path=FIXTURES / "support-triage.yaml",
        budget_usd=1000.0, out_dir=tmp_path / "results", census_dir=tmp_path / ".census",
        id_field="ticket_id", concurrency_initial=2, concurrency_ceiling=4, queue_maxsize=1,
    )
    result = await run_scheduled(config, FakeAsyncJevClient())

    assert result.documents_processed == n
    table = pq.read_table(result.out_path)
    assert table.num_rows == n * 4
    doc_ids = {row["doc_id"] for row in table.to_pylist()}
    assert doc_ids == {f"T-{i:04d}" for i in range(n)}


async def test_larger_corpus_completes(tmp_path):
    """A scaled-down stand-in for T4.1/T4.5's 'runs unattended' exit
    criterion — proves the pipeline holds together well past a handful of
    documents, with real concurrency (not sequential) doing the work."""
    n = 500
    config = SchedulerConfig(
        input_path=_corpus(tmp_path, n=n), questions_path=FIXTURES / "support-triage.yaml",
        budget_usd=1000.0, out_dir=tmp_path / "results", census_dir=tmp_path / ".census",
        id_field="ticket_id", concurrency_initial=8, concurrency_ceiling=16, shard_size=50,
    )
    client = FakeAsyncJevClient()
    result = await run_scheduled(config, client)

    assert result.documents_processed == n
    table = pq.read_table(result.out_path)
    assert table.num_rows == n * 4
    pairs = [(r["doc_id"], r["question_id"]) for r in table.to_pylist()]
    assert len(pairs) == len(set(pairs))


# --- P5 exit criterion: real call groups, not a hardcoded single group -----


def _corpus_with_thread(tmp_path: Path, n: int = 20) -> Path:
    rows = [
        {
            "ticket_id": f"T-{i:04d}",
            "subject": f"subject {i}",
            "body": f"unique body text for row {i}",
            "thread": f"full thread text for row {i}",
        }
        for i in range(n)
    ]
    path = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


async def test_two_projection_question_set_makes_two_calls_per_document(tmp_path):
    """The literal P5 exit criterion (02-BUILD-PLAN.md): a two-projection
    question set produces two calls per document, each state carrying only
    its group's fields. The call-count check alone would pass even with both
    states unioned into one call's worth of fields -- the field-absence
    assertion is what actually proves the groups are isolated."""
    n = 10
    config = SchedulerConfig(
        input_path=_corpus_with_thread(tmp_path, n=n),
        questions_path=FIXTURES / "support-triage-two-projections.yaml",
        budget_usd=1000.0,
        out_dir=tmp_path / "results",
        census_dir=tmp_path / ".census",
        id_field="ticket_id",
        concurrency_initial=4,
        concurrency_ceiling=8,
    )
    client = FakeAsyncJevClient()
    result = await run_scheduled(config, client)

    assert result.documents_processed == n
    # 2 questions in the [subject, body] group + 1 in the [subject, body,
    # thread] group = 2 calls per document.
    assert client.call_count == n * 2

    calls_per_doc: dict[tuple, list] = {}
    for state, qids in client.calls:
        calls_per_doc.setdefault(state.get("body"), []).append((state, qids))
    for calls in calls_per_doc.values():
        assert len(calls) == 2
        states_by_qids = {qids: state for state, qids in calls}
        narrow_state = states_by_qids[("department", "is_urgent")]
        wide_state = states_by_qids[("thread_went_hostile",)]
        # The negative assertion that actually matters: the narrow group's
        # call never saw the wide group's extra field.
        assert "thread" not in narrow_state
        assert "thread" in wide_state

    table = pq.read_table(result.out_path)
    assert table.num_rows == n * 3  # 3 questions total, one row per (doc, question)

    rows = table.to_pylist()
    projection_ids = {r["question_id"]: r["projection_id"] for r in rows}
    assert projection_ids["is_urgent"] == projection_ids["department"]
    assert projection_ids["thread_went_hostile"] != projection_ids["is_urgent"]

    call_ids = {r["question_id"]: r["call_id"] for r in rows}
    assert call_ids["is_urgent"] == call_ids["department"]
    assert call_ids["thread_went_hostile"] != call_ids["is_urgent"]


async def test_two_projection_resume_matches_clean_run(tmp_path):
    """Resume correctness (T2.7's invariant) must hold across multiple call
    groups too, not just the single-group case it was originally proven on."""
    n = 20
    corpus = _corpus_with_thread(tmp_path, n=n)
    questions_path = FIXTURES / "support-triage-two-projections.yaml"

    partial_config = SchedulerConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=1000.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census",
        id_field="ticket_id", shard_size=3, limit=8,
    )
    partial_result = await run_scheduled(partial_config, FakeAsyncJevClient())
    assert partial_result.documents_processed == 8

    resume_config = SchedulerConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=1000.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census",
        id_field="ticket_id", shard_size=3, resume_run_id=partial_result.run_id,
    )
    resumed_result = await run_scheduled(resume_config, FakeAsyncJevClient())
    assert resumed_result.documents_already_done == 8
    assert resumed_result.documents_processed == n - 8

    table = pq.read_table(resumed_result.out_path)
    assert table.num_rows == n * 3
    pairs = [(r["doc_id"], r["question_id"]) for r in table.to_pylist()]
    assert len(pairs) == len(set(pairs)), "duplicate cells after resuming a multi-group run"


async def test_long_document_is_chunked_and_aggregated_end_to_end(tmp_path):
    """T5.4 through the real Scheduler pipeline, not just chunking.py's unit
    tests: a document whose body alone exceeds CONTEXT_LIMIT_TOKENS gets
    split, answered per chunk, and aggregated back into one cell per
    question with confidence_source='derived' and chunk_count > 1 -- while
    ordinary short documents in the same corpus are untouched
    (chunk_count == 1, confidence_source unchanged)."""
    n_short = 5
    rows = [
        {"ticket_id": f"T-{i:04d}", "subject": f"subject {i}", "body": f"short body text {i}"}
        for i in range(n_short)
    ]
    # ~200,000 chars is comfortably over the measured ~32.8k-token limit.
    long_body = "word " * 40_000
    rows.append({"ticket_id": "T-LONG", "subject": "a long ticket", "body": long_body})
    corpus = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(rows), corpus)

    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=1000.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
        concurrency_initial=2, concurrency_ceiling=4,
    )
    result = await run_scheduled(config, FakeAsyncJevClient())
    assert result.documents_processed == n_short + 1
    assert result.documents_quarantined == 0

    table = pq.read_table(result.out_path)
    rows_out = table.to_pylist()

    long_rows = [r for r in rows_out if r["doc_id"] == "T-LONG"]
    assert len(long_rows) == 4  # all 4 questions still answered
    for r in long_rows:
        assert r["chunk_count"] > 1
        assert r["confidence_source"] == "derived"

    short_rows = [r for r in rows_out if r["doc_id"] == "T-0000"]
    assert len(short_rows) == 4
    for r in short_rows:
        assert r["chunk_count"] == 1


async def test_document_that_cannot_fit_even_chunked_is_quarantined(tmp_path):
    """T5.4: when even a single chunk can't fit -- here, two fields that are
    each individually oversized -- the document is quarantined with reason
    state_exceeds_context and the run continues rather than aborting
    (01-DESIGN.md: 'Chunk or quarantine. Never truncate silently.')."""
    n_short = 3
    rows = [
        {"ticket_id": f"T-{i:04d}", "subject": f"subject {i}", "body": f"short body text {i}"}
        for i in range(n_short)
    ]
    # Both fields individually huge: splitting the larger one still leaves
    # the other (now the "other_fields" remainder) too big to fit alongside
    # even the cheapest question.
    rows.append(
        {
            "ticket_id": "T-HUGE",
            "subject": "word " * 40_000,
            "body": "word " * 40_000,
        }
    )
    corpus = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(rows), corpus)

    config = SchedulerConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=1000.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
        concurrency_initial=2, concurrency_ceiling=4,
    )
    result = await run_scheduled(config, FakeAsyncJevClient())
    assert result.documents_processed == n_short
    assert result.documents_quarantined == 1

    table = pq.read_table(result.out_path)
    doc_ids = {r["doc_id"] for r in table.to_pylist()}
    assert "T-HUGE" not in doc_ids

    quarantine_path = config.census_dir / "runs" / result.run_id / "quarantine.jsonl"
    entries = [line for line in quarantine_path.read_text().splitlines() if line.strip()]
    assert any("state_exceeds_context" in line for line in entries)


async def test_typo_projection_field_aborts_with_clear_message(tmp_path):
    """T5.1's runtime check: a projection field that isn't a real column
    aborts on document 1 with the field name and the available columns,
    rather than silently sending null."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        "version: 1\nname: bad\ndefaults:\n  projection: [subjct, body]\n"
        "questions:\n  - id: q1\n    type: noul\n    instructions: The text is urgent.\n"
    )
    config = SchedulerConfig(
        input_path=_corpus(tmp_path), questions_path=bad_yaml, budget_usd=10.0,
        out_dir=tmp_path / "results", census_dir=tmp_path / ".census", id_field="ticket_id",
    )
    with pytest.raises(RunnerError, match="subjct"):
        await run_scheduled(config, FakeAsyncJevClient())
