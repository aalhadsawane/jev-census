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
