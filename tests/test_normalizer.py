import pytest

from jev_census.normalizer import normalize

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
