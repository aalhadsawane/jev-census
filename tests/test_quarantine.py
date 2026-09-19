import json

from jev_census.quarantine import QuarantineWriter


def test_write_appends_jsonl_entries(tmp_path):
    path = tmp_path / "quarantine.jsonl"
    q = QuarantineWriter(path)
    q.write(row_index=3, reason="missing id field 'ticket_id'", raw={"body": "x"})
    q.write(row_index=7, reason="duplicate document id 'T-1'", raw={"ticket_id": "T-1"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["row_index"] == 3
    assert "missing id field" in first["reason"]
    assert first["raw"] == {"body": "x"}


def test_count_tracks_writes(tmp_path):
    q = QuarantineWriter(tmp_path / "quarantine.jsonl")
    assert q.count == 0
    q.write(row_index=0, reason="x", raw={})
    q.write(row_index=1, reason="y", raw={})
    assert q.count == 2


def test_creates_parent_directories(tmp_path):
    q = QuarantineWriter(tmp_path / "nested" / "run-1" / "quarantine.jsonl")
    q.write(row_index=0, reason="x", raw={})
    assert q.path.exists()


def test_non_serializable_raw_falls_back_to_str(tmp_path):
    """`default=str` keeps a weird raw value from crashing the whole write."""
    q = QuarantineWriter(tmp_path / "quarantine.jsonl")
    q.write(row_index=0, reason="x", raw={"created_at": object()})
    entry = json.loads(q.path.read_text().splitlines()[0])
    assert "object" in entry["raw"]["created_at"]
