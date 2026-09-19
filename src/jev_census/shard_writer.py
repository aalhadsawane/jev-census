"""Append-only shard writer (T2.3): `Cell`s -> immutable `.census/runs/<run_id>/shards/NNNNNN.parquet`
files. Finalisation writes to a `.tmp` file in the same directory, then
`os.replace`s it into place - atomic on the same filesystem, so a crash before
the replace leaves only a stray `.tmp` file that `shard_paths()` (glob
`*.parquet`) never returns. A finalized shard is never rewritten.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow.parquet as pq

from .cell import Cell
from .writer import cells_to_table


class ShardWriter:
    def __init__(self, shards_dir: str | Path, shard_size: int = 5000):
        self.shards_dir = Path(shards_dir)
        self.shards_dir.mkdir(parents=True, exist_ok=True)
        self.shard_size = shard_size
        self._buffer: list[Cell] = []
        self._next_index = self._infer_next_index()

    def _infer_next_index(self) -> int:
        """Resuming into a directory with existing finalized shards continues
        numbering after the highest one, never overwriting."""
        existing = self.shard_paths()
        if not existing:
            return 0
        return max(int(p.stem) for p in existing) + 1

    def add(self, cells: list[Cell]) -> list[Path]:
        """Buffer cells; finalize as many full shards as the buffer allows.
        Returns the paths of any shards finalized by this call."""
        self._buffer.extend(cells)
        finalized: list[Path] = []
        while len(self._buffer) >= self.shard_size:
            chunk, self._buffer = self._buffer[: self.shard_size], self._buffer[self.shard_size :]
            finalized.append(self._finalize(chunk))
        return finalized

    def flush(self) -> Path | None:
        """Finalize whatever remains buffered as one last (possibly short) shard."""
        if not self._buffer:
            return None
        chunk, self._buffer = self._buffer, []
        return self._finalize(chunk)

    def _finalize(self, cells: list[Cell]) -> Path:
        index = self._next_index
        self._next_index += 1
        final_path = self.shards_dir / f"{index:06d}.parquet"
        tmp_path = self.shards_dir / f"{index:06d}.parquet.tmp"
        table = cells_to_table(cells)
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, final_path)
        return final_path

    def shard_paths(self) -> list[Path]:
        return sorted(self.shards_dir.glob("*.parquet"))

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)
