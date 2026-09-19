"""SQLite cell cache (T2.1-T2.2).

`cache_key = hash(projected_state, question_id, question_body_hash, model)` per
01-DESIGN.md § Cache. The `model` in the key must be the concrete model the API
*returned*, not the alias requested (`jev-latest` must never silently mix two
resolved models) - see open question F in `00-JEV-API.md`, resolved: the alias
resolves to a concrete version returned on every call.

That creates a bootstrapping question: to skip a call, the cache must know
which concrete model an alias currently resolves to *before* making that call.
`model_aliases` records the last-seen resolution so a fully-cached re-run of
`--model jev-latest` needs no network call at all to find out what "latest"
means - it reuses the last answer, exactly like every other cached cell.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Self

from .decoder import DecodedAnswer
from .hashing import stable_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cells (
    cache_key TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    model TEXT NOT NULL,
    answer_json TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS model_aliases (
    alias TEXT PRIMARY KEY,
    resolved_model TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS calibration (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    estimated_tokens_total INTEGER NOT NULL DEFAULT 0,
    actual_tokens_total INTEGER NOT NULL DEFAULT 0
);
"""


def cache_key(projected_state: object, question_id: str, question_body_hash: str, model: str) -> str:
    return stable_hash(
        {
            "state": projected_state,
            "question_id": question_id,
            "question_body_hash": question_body_hash,
            "model": model,
        }
    )


@dataclasses.dataclass(frozen=True)
class CachedCell:
    answer: DecodedAnswer
    model: str
    input_tokens: int


class CellCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def resolve_model(self, alias: str) -> str | None:
        """The concrete model an alias last resolved to, or None if never seen."""
        row = self._conn.execute(
            "SELECT resolved_model FROM model_aliases WHERE alias = ?", (alias,)
        ).fetchone()
        return row[0] if row else None

    def record_model_resolution(self, alias: str, resolved_model: str) -> None:
        self._conn.execute(
            "INSERT INTO model_aliases (alias, resolved_model) VALUES (?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET resolved_model = excluded.resolved_model, "
            "updated_at = datetime('now')",
            (alias, resolved_model),
        )
        self._conn.commit()

    def get(self, key: str) -> CachedCell | None:
        row = self._conn.execute(
            "SELECT answer_json, model, input_tokens FROM cells WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        answer_json, model, input_tokens = row
        return CachedCell(
            answer=DecodedAnswer(**json.loads(answer_json)), model=model, input_tokens=input_tokens
        )

    def put(self, key: str, answer: DecodedAnswer, model: str, input_tokens: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cells (cache_key, question_id, model, answer_json, input_tokens) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, answer.question_id, model, json.dumps(dataclasses.asdict(answer)), input_tokens),
        )
        self._conn.commit()

    def record_calibration(self, estimated_tokens: int, actual_tokens: int) -> None:
        """T3.5: accumulate one call's (estimated, actual) input token counts
        into the running cumulative totals used by `calibration_ratio()`."""
        self._conn.execute(
            "INSERT INTO calibration (id, estimated_tokens_total, actual_tokens_total) "
            "VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "estimated_tokens_total = estimated_tokens_total + excluded.estimated_tokens_total, "
            "actual_tokens_total = actual_tokens_total + excluded.actual_tokens_total",
            (estimated_tokens, actual_tokens),
        )
        self._conn.commit()

    def calibration_ratio(self) -> float | None:
        """Cumulative actual/estimated token ratio across every call recorded
        so far, or None before the first one. Above 1 means the estimator is
        under-counting; below 1 means it's over-counting. `runner.py` widens
        its admission-control margin as this drifts from 1.0."""
        row = self._conn.execute(
            "SELECT estimated_tokens_total, actual_tokens_total FROM calibration WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        estimated_total, actual_total = row
        if estimated_total == 0:
            return None
        return actual_total / estimated_total

    def stats(self) -> dict:
        (count,) = self._conn.execute("SELECT COUNT(*) FROM cells").fetchone()
        return {"cells_cached": count}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
