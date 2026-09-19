import pytest

from jev_census.checkpoint import Checkpoint


def _data(**overrides):
    data = {
        "run_id": "run-1",
        "questionset_hash": "qh",
        "cursor": 0,
        "shards": [],
        "total_input_tokens_charged": 0,
    }
    data.update(overrides)
    return data


def test_read_missing_checkpoint_returns_none(tmp_path):
    cp = Checkpoint(tmp_path / "checkpoint.json")
    assert cp.read() is None


def test_write_then_read_round_trips(tmp_path):
    cp = Checkpoint(tmp_path / "checkpoint.json")
    cp.write(_data(cursor=42, shards=["000000.parquet"]))
    data = cp.read()
    assert data["cursor"] == 42
    assert data["shards"] == ["000000.parquet"]


def test_write_creates_parent_directories(tmp_path):
    cp = Checkpoint(tmp_path / "nested" / "run-1" / "checkpoint.json")
    cp.write(_data())
    assert cp.path.exists()


def test_rewrite_overwrites_cleanly(tmp_path):
    cp = Checkpoint(tmp_path / "checkpoint.json")
    cp.write(_data(cursor=1))
    cp.write(_data(cursor=2))
    assert cp.read()["cursor"] == 2
    # no stray .tmp file left behind
    assert not (tmp_path / "checkpoint.json.tmp").exists()


def test_crash_during_write_leaves_previous_checkpoint_intact(tmp_path, monkeypatch):
    """Checkpoint always readable, never half-written (T2.4 done-when)."""
    import jev_census.checkpoint as checkpoint_module

    cp = Checkpoint(tmp_path / "checkpoint.json")
    cp.write(_data(cursor=1))

    def crashing_replace(src, dst):
        raise OSError("simulated crash before rename completes")

    monkeypatch.setattr(checkpoint_module.os, "replace", crashing_replace)
    with pytest.raises(OSError, match="simulated crash"):
        cp.write(_data(cursor=2))

    # os.replace never ran, so checkpoint.json still holds the last good write.
    assert cp.read()["cursor"] == 1


def test_crash_leaves_no_corrupt_json_visible(tmp_path, monkeypatch):
    """Even on a first-ever write (no prior good checkpoint), a crash before
    the atomic rename must not leave a corrupt checkpoint.json readable."""
    import jev_census.checkpoint as checkpoint_module

    def crashing_replace(src, dst):
        raise OSError("simulated crash")

    cp = Checkpoint(tmp_path / "checkpoint.json")
    monkeypatch.setattr(checkpoint_module.os, "replace", crashing_replace)
    with pytest.raises(OSError):
        cp.write(_data(cursor=1))

    assert cp.read() is None
    assert (tmp_path / "checkpoint.json.tmp").exists()  # stray tmp is fine, final path is not
