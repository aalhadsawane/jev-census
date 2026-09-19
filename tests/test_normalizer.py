import pytest

from jev_census.normalizer import normalize
from jev_census.quarantine import QuarantineWriter

ROWS = [
    {"ticket_id": "T-1", "subject": "Payouts failing", "body": "Help!"},
    {"ticket_id": "T-2", "subject": "Export CSV", "body": "Where is it?"},
]


def test_id_field_used_when_given():
    docs = list(normalize(iter(ROWS), id_field="ticket_id"))
    assert [d.id for d in docs] == ["T-1", "T-2"]
    assert docs[0].meta["id_source"] == "column"
    assert docs[0].fields["subject"] == "Payouts failing"


def test_missing_id_field_raises():
    with pytest.raises(ValueError, match="missing id field"):
        list(normalize(iter(ROWS), id_field="does_not_exist"))


def test_content_hash_fallback_when_no_id_field():
    docs = list(normalize(iter(ROWS)))
    assert all(d.meta["id_source"] == "content_hash" for d in docs)
    assert docs[0].id != docs[1].id


def test_ids_stable_across_two_runs():
    run1 = [d.id for d in normalize(iter(ROWS), id_field="ticket_id")]
    run2 = [d.id for d in normalize(iter(ROWS), id_field="ticket_id")]
    assert run1 == run2

    run1_hashed = [d.id for d in normalize(iter(ROWS))]
    run2_hashed = [d.id for d in normalize(iter(ROWS))]
    assert run1_hashed == run2_hashed


def test_row_index_recorded_in_meta():
    docs = list(normalize(iter(ROWS), id_field="ticket_id"))
    assert [d.meta["row_index"] for d in docs] == [0, 1]


def test_duplicate_id_raises():
    dup_rows = [ROWS[0], {"ticket_id": "T-1", "subject": "dup", "body": "dup"}]
    with pytest.raises(ValueError, match="duplicate document id"):
        list(normalize(iter(dup_rows), id_field="ticket_id"))


def test_empty_string_id_raises():
    rows = [{"ticket_id": "", "subject": "x", "body": "y"}]
    with pytest.raises(ValueError, match="missing id field"):
        list(normalize(iter(rows), id_field="ticket_id"))


def test_bad_row_quarantined_and_skipped_not_aborted(tmp_path):
    """One corrupt row does not abort the run (T2.6 done-when) once a
    quarantine writer is given."""
    rows = [
        ROWS[0],
        {"subject": "no id column here", "body": "x"},  # missing ticket_id
        ROWS[1],
    ]
    q = QuarantineWriter(tmp_path / "quarantine.jsonl")
    docs = list(normalize(iter(rows), id_field="ticket_id", quarantine=q))

    assert [d.id for d in docs] == ["T-1", "T-2"]
    assert q.count == 1


def test_duplicate_id_quarantined_and_skipped_with_quarantine_writer(tmp_path):
    dup_rows = [ROWS[0], {"ticket_id": "T-1", "subject": "dup", "body": "dup"}]
    q = QuarantineWriter(tmp_path / "quarantine.jsonl")
    docs = list(normalize(iter(dup_rows), id_field="ticket_id", quarantine=q))

    assert [d.id for d in docs] == ["T-1"]
    assert q.count == 1


def test_quarantine_entry_records_reason_and_raw_row(tmp_path):
    import json

    rows = [{"subject": "no id", "body": "x"}]
    q = QuarantineWriter(tmp_path / "quarantine.jsonl")
    list(normalize(iter(rows), id_field="ticket_id", quarantine=q))

    entry = json.loads(q.path.read_text().splitlines()[0])
    assert entry["row_index"] == 0
    assert "missing id field" in entry["reason"]
    assert entry["raw"] == {"subject": "no id", "body": "x"}
