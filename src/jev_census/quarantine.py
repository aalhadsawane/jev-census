"""Quarantine (T2.6): unprocessable rows are logged and skipped, never abort
the run. Per 01-DESIGN.md Failure handling: "Corrupt source row -> Skip,
quarantine with reason." Appends one JSON object per line to
`.census/runs/<run_id>/quarantine.jsonl`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class QuarantineWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def write(self, row_index: int, reason: str, raw: Any) -> None:
        entry = {"row_index": row_index, "reason": reason, "raw": raw}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
        self._count += 1

    @property
    def count(self) -> int:
        return self._count
