import json
import subprocess
import sys

from jev_census.hashing import stable_hash


def test_same_dict_different_key_order_same_hash():
    a = {"type": "noul", "instructions": "x", "criteria": {"true": "1", "false": "2"}}
    b = {"criteria": {"false": "2", "true": "1"}, "instructions": "x", "type": "noul"}
    assert stable_hash(a) == stable_hash(b)


def test_whitespace_normalized():
    a = {"instructions": "The   customer  is\nangry."}
    b = {"instructions": "The customer is angry."}
    assert stable_hash(a) == stable_hash(b)


def test_list_order_is_significant():
    a = {"criteria": ["Calm", "Angry"]}
    b = {"criteria": ["Angry", "Calm"]}
    assert stable_hash(a) != stable_hash(b)


def test_different_content_different_hash():
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_stable_across_processes():
    """Same input hashed in a fresh interpreter must match — guards against
    relying on anything process-salted (e.g. Python's built-in `hash()`)."""
    payload = {"type": "score", "criteria": ["Calm", "Annoyed", "Angry"], "instructions": "How angry?"}
    here = stable_hash(payload)

    code = (
        "import json,sys;"
        "from jev_census.hashing import stable_hash;"
        "print(stable_hash(json.loads(sys.argv[1])))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=True,
    )
    there = result.stdout.strip()
    assert here == there
