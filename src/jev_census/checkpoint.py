"""Checkpoint (T2.4): cursor + shard ledger, written atomically to
`.census/runs/<run_id>/checkpoint.json`. Same atomic-write pattern as
`shard_writer.py` — write to a `.tmp` file, then `os.replace` into place — so
a crash mid-write never leaves a half-written `checkpoint.json`; readers only
ever see the previous complete checkpoint or the new complete one.

`cursor` is the source row index up to and including which every document has
a *finalized* shard on disk — never a document only sitting in the shard
writer's in-memory buffer. Advancing the cursor past unflushed cells would let
a resume skip documents that were never actually written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict


class CheckpointData(TypedDict):
    run_id: str
    questionset_hash: str
    cursor: int
    shards: list[str]
    spent_usd: float


class Checkpoint:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> CheckpointData | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: CheckpointData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.path)
