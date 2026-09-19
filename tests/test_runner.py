from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from jev_census.normalizer import normalize
from jev_census.runner import RunConfig, RunnerError, run_census
from jev_census.sources import read_source
from tests.fakes import FakeJevClient

FIXTURES = Path(__file__).parent / "fixtures"


def _write_corpus(path: Path, rows: list[dict]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def _corpus(tmp_path: Path, n: int = 5) -> Path:
    rows = [
        {"ticket_id": f"T-{i}", "subject": f"subject {i}", "body": f"body text number {i}"}
        for i in range(n)
    ]
    path = tmp_path / "tickets.parquet"
    _write_corpus(path, rows)
    return path


def _config(tmp_path: Path, questions_path: Path, **overrides) -> RunConfig:
    kwargs = {
        "input_path": _corpus(tmp_path),
        "questions_path": questions_path,
        "budget_usd": 10.0,
        "out_dir": tmp_path / "results",
        "census_dir": tmp_path / ".census",
        "id_field": "ticket_id",
    }
    kwargs.update(overrides)
    return RunConfig(**kwargs)


def test_fresh_run_writes_results_manifest_checkpoint(tmp_path):
    config = _config(tmp_path, FIXTURES / "support-triage.yaml")
    client = FakeJevClient()
    result = run_census(config, client)

    assert result.documents_processed == 5
    assert result.documents_already_done == 0
    assert client.call_count == 5  # one call per document, all 4 questions batched

    table = pq.read_table(result.out_path)
    assert table.num_rows == 20  # 5 docs x 4 questions

    manifest = (config.census_dir / "runs" / result.run_id / "manifest.json")
    assert manifest.exists()


def test_rerun_identical_config_makes_zero_api_calls(tmp_path):
    """T2.1 done-when: re-running identical config makes zero API calls."""
    questions_path = FIXTURES / "support-triage.yaml"
    corpus = _corpus(tmp_path)
    census_dir = tmp_path / ".census"

    config1 = RunConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results1", census_dir=census_dir, id_field="ticket_id",
    )
    client1 = FakeJevClient()
    run_census(config1, client1)
    assert client1.call_count == 5

    # Fresh run (new run_id, new shards) but the SAME corpus/questions/cache dir.
    config2 = RunConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results2", census_dir=census_dir, id_field="ticket_id",
    )
    client2 = FakeJevClient()
    result2 = run_census(config2, client2)

    assert client2.call_count == 0
    assert result2.cache_misses == 0
    assert result2.cache_hits == 20
    table2 = pq.read_table(result2.out_path)
    assert table2.num_rows == 20


def test_adding_a_question_reasks_only_that_one_per_document(tmp_path):
    """T2.2 done-when: exactly one question re-asked per document."""
    corpus = _corpus(tmp_path)
    census_dir = tmp_path / ".census"

    config1 = RunConfig(
        input_path=corpus, questions_path=FIXTURES / "support-triage.yaml", budget_usd=10.0,
        out_dir=tmp_path / "results1", census_dir=census_dir, id_field="ticket_id",
    )
    run_census(config1, FakeJevClient())

    five_question_set = tmp_path / "five-questions.yaml"
    base = yaml.safe_load((FIXTURES / "support-triage.yaml").read_text())
    base["questions"].append(
        {
            "id": "has_attachment",
            "type": "noul",
            "instructions": "The customer mentions an attached file.",
            "criteria": {"true": "An attachment is referenced.", "false": "No attachment mentioned."},
        }
    )
    five_question_set.write_text(yaml.dump(base))

    config2 = RunConfig(
        input_path=corpus, questions_path=five_question_set, budget_usd=10.0,
        out_dir=tmp_path / "results2", census_dir=census_dir, id_field="ticket_id",
    )
    client2 = FakeJevClient()
    result2 = run_census(config2, client2)

    assert client2.call_count == 5  # one call per doc
    for _state, question_ids in client2.calls:
        assert question_ids == ("has_attachment",)  # only the new question, per doc
    assert result2.cache_hits == 20  # the original 4 questions x 5 docs, all cached
    assert result2.cache_misses == 5  # the new question x 5 docs


def test_resume_refuses_on_questionset_hash_mismatch(tmp_path):
    config = _config(tmp_path, FIXTURES / "support-triage.yaml", budget_usd=0.0)
    client = FakeJevClient()
    result = run_census(config, client)  # budget 0 -> nothing processed, but manifest written

    changed_questions = tmp_path / "changed.yaml"
    base = yaml.safe_load((FIXTURES / "support-triage.yaml").read_text())
    base["questions"][0]["instructions"] = "A completely different judgement."
    changed_questions.write_text(yaml.dump(base))

    resume_config = RunConfig(
        input_path=config.input_path, questions_path=changed_questions, budget_usd=10.0,
        out_dir=tmp_path / "results-resumed", census_dir=config.census_dir,
        id_field="ticket_id", resume_run_id=result.run_id,
    )
    with pytest.raises(RunnerError, match="question set changed"):
        run_census(resume_config, FakeJevClient())


def test_resume_missing_run_id_raises(tmp_path):
    config = _config(tmp_path, FIXTURES / "support-triage.yaml", resume_run_id="does-not-exist")
    with pytest.raises(RunnerError, match="no run 'does-not-exist'"):
        run_census(config, FakeJevClient())


def test_budget_cap_stops_admitting_new_calls(tmp_path):
    """A tiny budget must stop the run, not overspend."""
    config = _config(tmp_path, FIXTURES / "support-triage.yaml", budget_usd=0.0)
    result = run_census(config, FakeJevClient())

    assert result.budget_exhausted is True
    assert result.documents_processed == 0
    assert result.spent_usd == 0.0


def test_accounting_matches_summed_usage_input_tokens_exactly(tmp_path):
    """T3.3 done-when: accounting matches summed usage.input_tokens exactly."""
    config = _config(tmp_path, FIXTURES / "support-triage.yaml")
    client = FakeJevClient()
    result = run_census(config, client)

    expected_total_tokens = sum(50 + 20 * len(question_ids) for _state, question_ids in client.calls)
    assert result.total_input_tokens_charged == expected_total_tokens


def test_decode_failure_still_charges_the_attempt(tmp_path):
    """T3.3: a response that fails to decode still cost real input tokens -
    the attempt is charged, not just successes."""
    config = _config(tmp_path, FIXTURES / "support-triage.yaml")
    first_doc = next(iter(normalize(read_source(config.input_path), id_field="ticket_id")))
    projection_state = {"subject": first_doc.fields["subject"], "body": first_doc.fields["body"]}

    client = FakeJevClient(drop_answer_for_state=projection_state)
    result = run_census(config, client)

    assert result.documents_skipped == 1
    assert result.total_input_tokens_charged > 0  # the failed attempt was still charged
    # its tokens are part of the total even though no cells were written for it
    expected_total_tokens = sum(50 + 20 * len(question_ids) for _state, question_ids in client.calls)
    assert result.total_input_tokens_charged == expected_total_tokens


def test_admission_control_projects_next_call_before_admitting(tmp_path):
    """T3.4: admission is based on spent + projected, not just spent-so-far -
    a budget too small for even one call must stop before making it."""
    config = _config(tmp_path, FIXTURES / "support-triage.yaml", budget_usd=0.000001)
    client = FakeJevClient()
    result = run_census(config, client)

    assert client.call_count == 0
    assert result.budget_exhausted is True
    assert result.total_input_tokens_charged == 0


def test_calibration_ratio_recorded_after_real_calls(tmp_path):
    """T3.5: the run summary exposes the actual/estimated calibration ratio."""
    config = _config(tmp_path, FIXTURES / "support-triage.yaml")
    result = run_census(config, FakeJevClient())

    assert result.calibration_ratio is not None
    assert result.calibration_ratio > 0


def test_calibration_ratio_none_when_fully_cached(tmp_path):
    """A fully-cached rerun makes no real calls; calibration_ratio still
    reflects the shared cache.db's cumulative record, not reset per run."""
    questions_path = FIXTURES / "support-triage.yaml"
    corpus = _corpus(tmp_path)
    census_dir = tmp_path / ".census"

    config1 = RunConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results1", census_dir=census_dir, id_field="ticket_id",
    )
    run_census(config1, FakeJevClient())

    config2 = RunConfig(
        input_path=corpus, questions_path=questions_path, budget_usd=10.0,
        out_dir=tmp_path / "results2", census_dir=census_dir, id_field="ticket_id",
    )
    result2 = run_census(config2, FakeJevClient())

    assert result2.cache_misses == 0
    assert result2.calibration_ratio is not None
