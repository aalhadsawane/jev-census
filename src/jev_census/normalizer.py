"""Normalizer (T1.5): raw source rows -> `Document`, with a stable `id`.

Length-bounding fields to the model's context limit is the call planner's job
(P5), not this stage's — the normalizer's contract is just id stability and
clean UTF-8 text, per the stage table in `01-DESIGN.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .hashing import stable_hash


@dataclass(frozen=True)
class Document:
    id: str
    fields: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)


def normalize(rows: Iterator[dict[str, Any]], id_field: Optional[str] = None) -> Iterator[Document]:
    """Turn raw rows into `Document`s.

    With `id_field`, that column's value becomes the id. Without one, the id is
    a content hash of the row's fields, and `meta["id_source"]` records the
    fallback so the manifest can note it (01-DESIGN.md: "content-hash fallback
    recorded in manifest"). Either way, a duplicate id aborts the run — it would
    silently merge two documents' cells downstream.
    """
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        fields = dict(row)

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
        seen_ids.add(doc_id)

        yield Document(id=doc_id, fields=fields, meta={"row_index": index, "id_source": id_source})
