"""Question set: Pydantic models mirroring the YAML format in `docs/01-DESIGN.md`,
plus the loader. Validation rules live in `validator.py`; this module only shapes
the data and computes hashes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .hashing import stable_hash

QuestionType = Literal["noul", "choice", "score"]
Gate = Literal["strict", "exploratory"]


class Question(BaseModel):
    """One question. Field names mirror the Jev API exactly (`00-JEV-API.md`)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: QuestionType
    instructions: Any
    criteria: Any = None
    projection: Optional[list[str]] = None
    gate: Optional[Gate] = None

    @property
    def body_hash(self) -> str:
        """Covers everything that changes meaning. Renaming `id` is deliberately
        excluded — an id rename is a new question and drops its cache by design
        (01-DESIGN.md), but the *content* hash should still recognize identical
        questions asked under two different ids as the same body.
        """
        return stable_hash(
            {
                "type": self.type,
                "instructions": self.instructions,
                "criteria": self.criteria,
                "projection": self.projection,
            }
        )


class QuestionSetDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: Gate = "strict"
    projection: Optional[list[str]] = None


class QuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    name: str
    defaults: QuestionSetDefaults = Field(default_factory=QuestionSetDefaults)
    questions: list[Question]

    @property
    def questionset_hash(self) -> str:
        """Ordered hash of all body hashes + version. Order preserved from YAML."""
        return stable_hash(
            {"version": self.version, "body_hashes": [q.body_hash for q in self.questions]}
        )

    def resolved_projection(self, question: Question) -> list[str]:
        if question.projection is not None:
            return question.projection
        return self.defaults.projection or []

    def resolved_gate(self, question: Question) -> Gate:
        return question.gate if question.gate is not None else self.defaults.gate


def load_question_set(path: str | Path) -> QuestionSet:
    """Parse and validate a question set YAML file. Raises `ValidationError`
    (see `validator.py`) on any rule violation."""
    from .validator import validate_question_set  # avoid import cycle

    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    question_set = QuestionSet.model_validate(raw)
    validate_question_set(question_set)
    return question_set
