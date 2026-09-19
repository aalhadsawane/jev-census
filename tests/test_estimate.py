from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from jev_census.estimate import estimate, format_estimate

FIXTURES = Path(__file__).parent / "fixtures"


def _corpus(tmp_path: Path, n: int = 50) -> Path:
    rows = [
        {
            "ticket_id": f"T-{i}",
            "subject": f"subject line number {i}",
            "body": f"This is a somewhat longer support ticket body for row {i}. " * 3,
        }
        for i in range(n)
    ]
    path = tmp_path / "tickets.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def test_estimate_document_and_question_counts(tmp_path):
    corpus = _corpus(tmp_path, n=50)
    result = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id")

    assert result.document_count == 50
    assert result.question_count == 4
    assert result.call_group_count == 1
    assert result.sample_size == 50  # sample size > corpus size -> whole corpus sampled


def test_estimate_sample_size_caps_at_requested(tmp_path):
    corpus = _corpus(tmp_path, n=50)
    result = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id", sample_size=10)
    assert result.sample_size == 10


def test_estimate_scales_total_tokens_to_full_corpus(tmp_path):
    corpus_small = _corpus(tmp_path, n=10)
    result_small = estimate(corpus_small, FIXTURES / "support-triage.yaml", id_field="ticket_id")

    # Same per-doc content repeated more times -> total tokens should scale
    # roughly linearly with document count (same avg tokens/doc).
    corpus_large_path = tmp_path / "large.parquet"
    rows = [
        {"ticket_id": f"T-{i}", "subject": "subject line number 0", "body": "This is a somewhat longer support ticket body for row 0. " * 3}
        for i in range(100)
    ]
    pq.write_table(pa.Table.from_pylist(rows), corpus_large_path)
    result_large = estimate(corpus_large_path, FIXTURES / "support-triage.yaml", id_field="ticket_id")

    assert result_large.document_count == 100
    assert result_large.total_tokens > result_small.total_tokens


def test_estimate_cost_matches_money_module(tmp_path):
    corpus = _corpus(tmp_path, n=20)
    result = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id")

    from jev_census.money import micro_usd_to_usd, tokens_to_micro_usd

    expected_cost = micro_usd_to_usd(tokens_to_micro_usd(result.total_tokens))
    assert result.estimated_cost_usd == expected_cost


def test_estimate_deterministic_given_seed(tmp_path):
    corpus = _corpus(tmp_path, n=50)
    r1 = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id", sample_size=10, seed=42)
    r2 = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id", sample_size=10, seed=42)
    assert r1 == r2


def test_estimate_empty_corpus(tmp_path):
    path = tmp_path / "empty.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=pa.schema([("ticket_id", pa.string())])), path)
    result = estimate(path, FIXTURES / "support-triage.yaml", id_field="ticket_id")
    assert result.sample_size == 0
    assert result.total_tokens == 0


def test_format_estimate_matches_readme_shape(tmp_path):
    corpus = _corpus(tmp_path, n=50)
    result = estimate(corpus, FIXTURES / "support-triage.yaml", id_field="ticket_id")
    text = format_estimate(result)

    assert "documents ·" in text
    assert "questions ·" in text
    assert "call group" in text
    assert "tokens/doc:" in text
    assert "state" in text and "schema" in text
    assert "total:" in text
    assert "≈  $" in text
    assert "runtime:" in text
    assert "docs/s" in text


def test_format_estimate_empty_corpus_does_not_crash():
    from jev_census.estimate import EstimateResult

    result = EstimateResult(
        document_count=0, sample_size=0, question_count=4, call_group_count=1,
        avg_state_tokens=0.0, avg_schema_tokens=0.0, total_tokens=0,
        estimated_cost_usd=0.0, estimated_runtime_seconds=0.0,
    )
    text = format_estimate(result)
    assert "0 documents" in text
