"""Normalizer (T1.5): raw source rows -> `Document`, with a stable `id`.

Length-bounding fields to the model's context limit is the call planner's job
(P5), not this stage's — the normalizer's contract is just id stability and
clean UTF-8 text, per the stage table in `01-DESIGN.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .hashing import stable_hash
from .quarantine import QuarantineWriter


@dataclass(frozen=True)
class Document:
    id: str
    fields: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)


def normalize(
    rows: Iterator[dict[str, Any]],
    id_field: Optional[str] = None,
    quarantine: Optional[QuarantineWriter] = None,
) -> Iterator[Document]:
    """Turn raw rows into `Document`s.

    With `id_field`, that column's value becomes the id. Without one, the id is
    a content hash of the row's fields, and `meta["id_source"]` records the
    fallback so the manifest can note it (01-DESIGN.md: "content-hash fallback
    recorded in manifest").

    A missing id field or a duplicate id is a data error. Without a
    `quarantine` writer it raises, aborting the stream (T1.5's original,
    strict behaviour). With one, per 01-DESIGN.md Failure handling ("Corrupt
    source row -> Skip, quarantine with reason"), the row is logged and
    skipped instead — one bad row never aborts a long run (T2.6).
    """
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        fields = dict(row)

        try:
            if id_field is not None:
                if id_field not in fields or fields[id_field] in (None, ""):
                    raise ValueError(f"row {index}: missing id field '{id_field}'")
                doc_id = str(fields[id_field])
                id_source = "column"
            else:
                doc_id = stable_hash(fields)
                id_source = "content_hash"

            if doc_id in seen_ids:
                raise ValueError(f"row {index}: duplicate document id '{doc_id}'")
        except ValueError as exc:
            if quarantine is None:
                raise
            quarantine.write(row_index=index, reason=str(exc), raw=row)
            continue

        seen_ids.add(doc_id)
        yield Document(id=doc_id, fields=fields, meta={"row_index": index, "id_source": id_source})
