"""Deterministic in-process fake of the Jev client. Tests never make live API
calls (02-BUILD-PLAN.md Testing rules) but still need to exercise runner.py's
call-vs-cache-hit branching, transient-error handling, and the T2.7 chaos
test — which runs `census run` as a real OS subprocess so `kill -9` means
something. `cli.py` swaps this in when `CENSUS_FAKE_CLIENT=1` is set, a
test-only seam never taken in a normal invocation.
"""

from __future__ import annotations

import hashlib
import json
import time

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse, Usage

from jev_census.client import JevTransientError
from jev_census.question_set import Question


def _fake_answer(state: object, question_id: str, question: Question):
    seed = json.dumps({"state": state, "question_id": question_id}, sort_keys=True, default=str)
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)

    if question.type == "noul":
        return NoulAnswer(noul=round((digest % 101) / 100, 2))
    if question.type == "choice":
        options = list(question.criteria.keys())
        chosen = options[digest % len(options)]
        probabilities = {opt: (1.0 if opt == chosen else 0.0) for opt in options}
        return ChoiceAnswer(choice=chosen, confidence=0.9, probabilities=probabilities)
    if question.type == "score":
        levels = list(question.criteria)
        level = digest % len(levels)
        legend = {i: lvl for i, lvl in enumerate(levels)}
        probabilities = {i: (1.0 if i == level else 0.0) for i in range(len(levels))}
        return ScoreAnswer(score=float(level), confidence=0.9, legend=legend, probabilities=probabilities)
    raise ValueError(f"unknown question type: {question.type!r}")


class FakeJevClient:
    """Same `.ask()` shape as `JevClient`, no network. `calls` records every
    invocation for assertions (e.g. "zero API calls on a fully-cached rerun")."""

    def __init__(
        self,
        model: str = "jev-1.13.0-fake",
        fail_doc_state: object | None = None,
        delay_seconds: float = 0.0,
    ):
        self.model = model
        self.calls: list[tuple[object, tuple[str, ...]]] = []
        # Optional: make one specific state raise JevTransientError once, to
        # test the skip-and-continue path without needing a real flaky server.
        self._fail_doc_state = fail_doc_state
        self._failed_once = False
        # Optional: artificial per-call latency so a subprocess-based chaos
        # test (T2.7) has a wide, reliable window to kill -9 mid-run.
        self._delay_seconds = delay_seconds

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def ask(self, state: object, questions: dict[str, Question], model: str | None = None):
        if self._delay_seconds:
            time.sleep(self._delay_seconds)

        if self._fail_doc_state is not None and state == self._fail_doc_state and not self._failed_once:
            self._failed_once = True
            raise JevTransientError("simulated transient failure")

        self.calls.append((state, tuple(sorted(questions.keys()))))
        answers = {qid: _fake_answer(state, qid, q) for qid, q in questions.items()}
        input_tokens = 50 + 20 * len(questions)
        return SystemOneResponse(
            model=self.model, usage=Usage(input_tokens=input_tokens, output_tokens=5), answers=answers
        )
