"""Validator: the rules from `01-DESIGN.md` § Validator rules.

Every rule here has a matching failing-case test in `tests/test_validator.py` (T1.2).
Structural rules (uniqueness, criteria shape) are hard errors. The proposition/command
and counting/math/date checks are lints over free text and cannot be exact, so they
are conservative pattern matches — documented as heuristics, not full NLP.
"""

from __future__ import annotations

import re

from .question_set import Question, QuestionSet

_IMPERATIVE_VERBS = {
    "determine", "find", "count", "extract", "list", "calculate", "compute",
    "identify", "check", "decide", "classify", "tell", "give", "return",
    "rate", "judge", "assess", "compare", "sum", "average",
}

_COUNTING_PATTERNS = [
    re.compile(r"\bhow many\b", re.IGNORECASE),
    re.compile(r"\bcount(?:ing|s)?\b", re.IGNORECASE),
    re.compile(r"\baverage\b", re.IGNORECASE),
    re.compile(r"\bmean number\b", re.IGNORECASE),
]

_DATE_WORD = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"yesterday|today|tomorrow|deadline)"
)
_DATE_COMPARISON_PATTERN = re.compile(rf"\b(before|after)\b\s+.{{0,20}}?{_DATE_WORD}", re.IGNORECASE)


class ValidationError(Exception):
    """Raised with every rule violation found, not just the first."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _instructions_text(instructions: object) -> str:
    """`instructions` may be a string, object, or array (00-JEV-API.md). Flatten
    to text for the lints, which only make sense against natural language."""
    if isinstance(instructions, str):
        return instructions
    if isinstance(instructions, list):
        return " ".join(_instructions_text(item) for item in instructions)
    if isinstance(instructions, dict):
        return " ".join(_instructions_text(v) for v in instructions.values())
    return str(instructions)


def _check_proposition(question: Question, errors: list[str]) -> None:
    text = _instructions_text(question.instructions).strip()
    if not text:
        errors.append(f"question '{question.id}': instructions must not be empty")
        return
    first_word = re.split(r"\W+", text, maxsplit=1)[0].lower()
    if first_word in _IMPERATIVE_VERBS:
        errors.append(
            f"question '{question.id}': instructions read as a command "
            f"('{first_word.capitalize()} ...'), not a declarative proposition — "
            "rephrase as a statement Jev judges true or false (01-DESIGN.md validator rules)"
        )


def _check_counting_math_date(question: Question, errors: list[str]) -> None:
    text = _instructions_text(question.instructions)
    for pattern in _COUNTING_PATTERNS:
        if pattern.search(text):
            errors.append(
                f"question '{question.id}': counting/arithmetic language "
                f"('{pattern.pattern}') is unreliable for Jev — see jaggedness #2 "
                "in docs/00-JEV-API.md; count in code, one question per item"
            )
            return
    if _DATE_COMPARISON_PATTERN.search(text):
        errors.append(
            f"question '{question.id}': date comparison in instructions is unreliable "
            "for Jev — see jaggedness #3 in docs/00-JEV-API.md; extract components as "
            "`choice` and compare in code"
        )


def _check_noul(question: Question, errors: list[str]) -> None:
    if question.criteria is None:
        return
    if not isinstance(question.criteria, dict) or set(question.criteria.keys()) != {"true", "false"}:
        errors.append(
            f"question '{question.id}': noul criteria, when given, must be exactly "
            '{"true": ..., "false": ...}'
        )
        return
    true_text = str(question.criteria["true"]).strip().lower()
    false_text = str(question.criteria["false"]).strip().lower()
    if true_text and true_text == false_text:
        errors.append(
            f"question '{question.id}': noul criteria 'true' and 'false' are identical — "
            "cannot be a real distinction, check for an inverted or copy-pasted rubric"
        )


def _check_choice(question: Question, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(question.criteria, dict):
        errors.append(f"question '{question.id}': choice requires a criteria map of option -> rubric")
        return
    if len(question.criteria) < 2:
        errors.append(f"question '{question.id}': choice requires at least 2 criteria entries")
    for option, rubric in question.criteria.items():
        if rubric is None:
            warnings.append(
                f"question '{question.id}': option '{option}' has a null rubric — "
                "boundary cases belong in criteria (01-DESIGN.md)"
            )


def _check_score(question: Question, errors: list[str]) -> None:
    if not isinstance(question.criteria, list):
        errors.append(f"question '{question.id}': score requires an ordered criteria array")
        return
    if len(question.criteria) < 2:
        errors.append(f"question '{question.id}': score requires at least 2 ordered levels")


def _check_projection(question: Question, question_set: QuestionSet, errors: list[str]) -> None:
    """T5.1 (04-P5-PLANNER.md): a question with no resolved projection has no
    state to answer against, and a projection field that reaches the planner
    with a typo sends `{"subjct": null}` to the model silently — this only
    catches the structural cases (empty, duplicated, unresolved); whether the
    field names actually exist on the corpus is a runtime check the planner
    makes against the first document, since that needs the source open."""
    if question.projection is not None and len(question.projection) == 0:
        errors.append(
            f"question '{question.id}': projection is empty; a question with no state "
            "cannot be answered"
        )
        return
    resolved = question_set.resolved_projection(question)
    if len(resolved) == 0:
        errors.append(
            f"question '{question.id}': no projection, and the question set declares no default"
        )
        return
    if len(resolved) != len(set(resolved)):
        dupes = sorted({f for f in resolved if resolved.count(f) > 1})
        errors.append(
            f"question '{question.id}': projection has duplicate field name(s) {dupes}"
        )


def validate_question_set(question_set: QuestionSet) -> list[str]:
    """Validate every question. Returns non-fatal warnings; raises `ValidationError`
    with all fatal errors found."""
    errors: list[str] = []
    warnings: list[str] = []

    seen_ids: set[str] = set()
    for question in question_set.questions:
        if question.id in seen_ids:
            errors.append(f"question id '{question.id}' is not unique")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", question.id):
            errors.append(
                f"question id '{question.id}' is not safe as a column name "
                "(must match [a-zA-Z_][a-zA-Z0-9_]*)"
            )
        seen_ids.add(question.id)

        if question.type == "noul":
            _check_noul(question, errors)
        elif question.type == "choice":
            _check_choice(question, errors, warnings)
        elif question.type == "score":
            _check_score(question, errors)

        _check_proposition(question, errors)
        _check_counting_math_date(question, errors)
        _check_projection(question, question_set, errors)

    if errors:
        raise ValidationError(errors)
    return warnings
