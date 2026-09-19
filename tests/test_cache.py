from jev_census.cache import CellCache, cache_key
from jev_census.decoder import DecodedAnswer


def _answer(question_id="is_urgent") -> DecodedAnswer:
    return DecodedAnswer(
        question_id=question_id,
        type="noul",
        noul=0.9,
        choice=None,
        score=None,
        probabilities=None,
        legend=None,
        confidence=0.8,
        confidence_source="derived",
    )


def test_cache_key_stable_and_sensitive_to_each_component():
    base = cache_key({"body": "x"}, "q1", "bodyhash", "jev-1.13.0")
    assert base == cache_key({"body": "x"}, "q1", "bodyhash", "jev-1.13.0")
    assert base != cache_key({"body": "y"}, "q1", "bodyhash", "jev-1.13.0")
    assert base != cache_key({"body": "x"}, "q2", "bodyhash", "jev-1.13.0")
    assert base != cache_key({"body": "x"}, "q1", "otherhash", "jev-1.13.0")
    assert base != cache_key({"body": "x"}, "q1", "bodyhash", "jev-1.14.0")


def test_miss_then_hit(tmp_path):
    cache = CellCache(tmp_path / "cache.db")
    key = cache_key({"body": "x"}, "is_urgent", "bodyhash", "jev-1.13.0")
    assert cache.get(key) is None

    cache.put(key, _answer(), model="jev-1.13.0", input_tokens=120)
    cached = cache.get(key)
    assert cached is not None
    assert cached.answer.noul == 0.9
    assert cached.model == "jev-1.13.0"
    assert cached.input_tokens == 120
    cache.close()


def test_cache_persists_across_reopen(tmp_path):
    path = tmp_path / "cache.db"
    key = cache_key({"body": "x"}, "is_urgent", "bodyhash", "jev-1.13.0")
    with CellCache(path) as cache:
        cache.put(key, _answer(), model="jev-1.13.0", input_tokens=100)

    with CellCache(path) as reopened:
        cached = reopened.get(key)
        assert cached is not None
        assert cached.answer.question_id == "is_urgent"


def test_model_alias_resolution_round_trips(tmp_path):
    cache = CellCache(tmp_path / "cache.db")
    assert cache.resolve_model("jev-latest") is None
    cache.record_model_resolution("jev-latest", "jev-1.13.0")
    assert cache.resolve_model("jev-latest") == "jev-1.13.0"
    cache.close()


def test_model_alias_resolution_updates_on_bump(tmp_path):
    cache = CellCache(tmp_path / "cache.db")
    cache.record_model_resolution("jev-latest", "jev-1.13.0")
    cache.record_model_resolution("jev-latest", "jev-1.14.0")
    assert cache.resolve_model("jev-latest") == "jev-1.14.0"
    cache.close()


def test_stats_counts_cells(tmp_path):
    cache = CellCache(tmp_path / "cache.db")
    assert cache.stats()["cells_cached"] == 0
    cache.put(
        cache_key({"body": "x"}, "q1", "h1", "jev-1.13.0"), _answer("q1"), "jev-1.13.0", 100
    )
    cache.put(
        cache_key({"body": "x"}, "q2", "h2", "jev-1.13.0"), _answer("q2"), "jev-1.13.0", 100
    )
    assert cache.stats()["cells_cached"] == 2
    cache.close()


def test_put_overwrites_existing_key(tmp_path):
    cache = CellCache(tmp_path / "cache.db")
    key = cache_key({"body": "x"}, "q1", "h1", "jev-1.13.0")
    cache.put(key, _answer("q1"), "jev-1.13.0", 100)
    updated = DecodedAnswer(
        question_id="q1", type="noul", noul=0.1, choice=None, score=None,
        probabilities=None, legend=None, confidence=0.8, confidence_source="derived",
    )
    cache.put(key, updated, "jev-1.13.0", 100)
    assert cache.get(key).answer.noul == 0.1
    assert cache.stats()["cells_cached"] == 1
    cache.close()
