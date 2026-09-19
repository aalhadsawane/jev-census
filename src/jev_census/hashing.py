"""Deterministic hashing shared by body_hash and questionset_hash.

Stable across processes and Python versions: explicit key ordering, whitespace
normalisation on every string, and UTF-8 encoding before hashing. Never rely on
dict insertion order or `hash()` (salted per-process) for anything persisted or
compared across runs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _normalize(value: Any) -> Any:
    """Recursively normalize whitespace in strings; sort dict keys; preserve list order.

    List order is semantic (e.g. `score` levels), so it is never sorted.
    """
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    """Whitespace-normalized, key-sorted, UTF-8-safe JSON serialization."""
    normalized = _normalize(value)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_hash(value: Any) -> str:
    """SHA-256 hex digest of `value`'s canonical JSON form."""
    canonical = canonical_json(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
