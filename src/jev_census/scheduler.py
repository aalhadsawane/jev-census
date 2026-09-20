"""Scheduler (T4.1, T4.4, T4.5): the concurrent replacement for `runner.py`'s
sequential loop, built for the same correctness (cache, atomic shards,
checkpoint, resume, quarantine, integer accounting, admission control) at
scale. `runner.py`'s sequential `run_census` is kept as-is — still fully
tested as the pure reference implementation — while this module is what
`cli.py` actually calls once P4 ships.

Concurrency model: a bounded `asyncio.Queue` between the document producer
and a pool of worker coroutines gives backpressure (T4.1) — if the workers
fall behind, `queue.put()` blocks the producer rather than growing memory
unboundedly. Each worker processes one document end to end: cache lookup,
the one truly concurrent step (the API call, or a backoff sleep), decode,
cache write, shard write, and an occasional checkpoint. Every one of those
non-`await` steps is atomic with respect to the other workers under
asyncio's cooperative scheduling — no lock is needed around the shared
`CellCache` or `ShardWriter`, only the discipline (already true of both)
that neither ever awaits mid-operation. AIMD concurrency (T4.3) gates how
many workers may have a call in flight at once; retry classification (T4.2)
drives what a worker does on failure. A SIGINT or budget breach (T4.4) stops
the producer and idle workers, lets already-dispatched calls finish, and
flushes/checkpoints cleanly before exiting non-zero with the resume command.

Admission control intentionally allows overshoot bounded by the current
concurrency limit, not by one call — 01-DESIGN.md's own words ("overshoot is
bounded by in-flight concurrency") anticipate exactly this once concurrency
is real: several workers can pass the budget check before any of them
updates the shared spend.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .aimd import AIMDConcurrency
from .cache import CachedCell, CellCache, cache_key
from .cell import build_cell
from .checkpoint import Checkpoint
from .chunking import aggregate_chunk_answers, chunk_document
from .client import JevConfigError, JevTransientError
from .decoder import DecodeError, decode_answers
from .failure import FailureClass, classify
from .money import micro_usd_to_usd, tokens_to_micro_usd, usd_to_micro_usd
from .normalizer import normalize
from .planner import (
    CallGroup,
    ContextOverflowError,
    plan_call_groups,
    project_state,
    split_for_context,
    verify_projection_fields,
)
from .quarantine import QuarantineWriter
from .question_set import Question, QuestionSet, load_question_set
from .retry import RetryPolicy
from .runner import RunnerError, RunResult, _already_done_ids, _questionset_diff, _write_results
from .shard_writer import ShardWriter
from .sources import count_rows, read_source
from .token_estimator import estimate_call_tokens

_SENTINEL = object()


class AsyncAskingClient(Protocol):
    async def ask(self, state: object, questions: dict[str, Question], model: str | None = None): ...


@dataclass(frozen=True)
class SchedulerConfig:
    input_path: Path
    questions_path: Path
    budget_usd: float
    out_dir: Path
    census_dir: Path
    limit: int | None = None
    id_field: str | None = None
    resume_run_id: str | None = None
    model_alias: str = "jev-latest"
    shard_size: int = 5000
    concurrency_initial: int = 4
    concurrency_ceiling: int = 64
    queue_maxsize: int = 256
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    progress_interval_seconds: float = 2.0
    on_progress: Callable[[ProgressSnapshot], None] | None = None  # T4.5


@dataclass
class ProgressSnapshot:
    documents_total: int | None
    documents_done: int  # processed + already_done, i.e. durably written
    documents_processed: int
    documents_already_done: int
    cache_hits: int
    cache_misses: int
    spent_usd: float
    budget_usd: float
    concurrency_limit: int
    elapsed_seconds: float
    stopping: bool


def format_progress(snapshot: ProgressSnapshot) -> str:
    """Pure and unit-testable; T4.5's done-when (cached vs. paid counted
    separately, throughput, concurrency, ETA) lives in what this renders."""
    throughput = snapshot.documents_done / snapshot.elapsed_seconds if snapshot.elapsed_seconds > 0 else 0.0
    total_cells = snapshot.cache_hits + snapshot.cache_misses
    cache_rate = (snapshot.cache_hits / total_cells * 100) if total_cells else 0.0

    if snapshot.documents_total and throughput > 0:
        remaining = max(0, snapshot.documents_total - snapshot.documents_done)
        eta_seconds = remaining / throughput
        eta = f"eta {eta_seconds / 60:.1f}m" if eta_seconds >= 60 else f"eta {eta_seconds:.0f}s"
        done_of_total = f"{snapshot.documents_done:,}/{snapshot.documents_total:,}"
    else:
        eta = "eta -"
        done_of_total = f"{snapshot.documents_done:,}"

    stopping = " [stopping]" if snapshot.stopping else ""
    return (
        f"{done_of_total} docs · ${snapshot.spent_usd:.4f}/${snapshot.budget_usd:.2f} · "
        f"{throughput:.1f} docs/s · concurrency {snapshot.concurrency_limit} · "
        f"cache {cache_rate:.0f}% · {eta}{stopping}"
    )


class _ConcurrencyGate:
    """Async wrapper around `AIMDConcurrency`: the permitted concurrency can
    change live (unlike a plain `asyncio.Semaphore`, whose initial value is
    fixed), so this tracks in-flight count itself and wakes waiters whenever
    either the limit rises or a slot frees up."""

    def __init__(self, aimd: AIMDConcurrency):
        self._aimd = aimd
        self._in_flight = 0
        self._condition = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._in_flight < self._aimd.limit)
            self._in_flight += 1

    async def release(self) -> None:
        async with self._condition:
            self._in_flight -= 1
            self._condition.notify_all()

    async def on_rate_limited(self) -> None:
        async with self._condition:
            self._aimd.on_rate_limited()
            self._condition.notify_all()  # limit may have dropped; nothing to wake, but harmless

    async def on_success(self) -> None:
        async with self._condition:
            self._aimd.on_success()
            self._condition.notify_all()  # limit may have risen

    @property
    def limit(self) -> int:
        return self._aimd.limit


class _SharedState:
    def __init__(self, budget_micro_usd: int):
        self.budget_micro_usd = budget_micro_usd
        self.total_input_tokens_charged = 0
        self.documents_processed = 0
        self.documents_already_done = 0
        self.documents_skipped = 0
        self.documents_quarantined = 0
        self.cache_hits_total = 0
        self.cache_misses_total = 0
        self.budget_exhausted = False
        self.interrupted = False
        self.fatal_error: BaseException | None = None
        self.stop_event = asyncio.Event()

    def spent_micro_usd(self) -> int:
        return tokens_to_micro_usd(self.total_input_tokens_charged)


async def _worker(
    worker_queue: asyncio.Queue,
    *,
    question_set: QuestionSet,
    call_groups: list[CallGroup],
    client: AsyncAskingClient,
    cache: CellCache,
    shard_writer: ShardWriter,
    checkpoint: Checkpoint,
    quarantine: QuarantineWriter,
    run_id: str,
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
) -> None:
    while True:
        doc = await worker_queue.get()
        if doc is _SENTINEL:
            return
        if state.stop_event.is_set():
            continue  # drain without processing; picked up again on --resume

        try:
            await _process_one_document(
                doc,
                question_set=question_set,
                call_groups=call_groups,
                client=client,
                cache=cache,
                shard_writer=shard_writer,
                checkpoint=checkpoint,
                quarantine=quarantine,
                run_id=run_id,
                config=config,
                gate=gate,
                state=state,
            )
        except JevConfigError as exc:
            state.fatal_error = exc
            state.stop_event.set()


async def _ask_batch(
    *,
    chunk_state: dict,
    batch_questions: dict[str, Question],
    questions_by_id_group: dict[str, Question],
    client: AsyncAskingClient,
    cache: CellCache,
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
    answers_by_question: dict[str, list[tuple]],
) -> str | None:
    """One real API call for one chunk's missing-question batch. Updates
    `answers_by_question` and the cache in place on success. Returns the
    resolved model on success, or `None` if the document should be abandoned
    (budget breach, exhausted retries, or a decode failure — every one of
    these already accounts for itself in `state` before returning)."""
    projected_state_tokens, projected_schema_tokens = estimate_call_tokens(chunk_state, batch_questions)
    projected_tokens = projected_state_tokens + projected_schema_tokens
    if state.spent_micro_usd() + tokens_to_micro_usd(projected_tokens) > state.budget_micro_usd:
        state.budget_exhausted = True
        state.stop_event.set()
        return None

    response = await _ask_with_retries(
        client, chunk_state, batch_questions, config, gate, state, fatal_holder=state
    )
    if response is None:
        return None  # exhausted retries or was skipped; already accounted for

    resolved_model = response.model
    input_tokens_new = response.usage.input_tokens or 0
    state.total_input_tokens_charged += input_tokens_new
    cache.record_model_resolution(config.model_alias, resolved_model)
    cache.record_calibration(estimated_tokens=projected_tokens, actual_tokens=input_tokens_new)

    try:
        decoded = decode_answers(response, batch_questions)
    except DecodeError:
        state.documents_skipped += 1
        return None

    state.cache_misses_total += len(batch_questions)
    for decoded_answer in decoded:
        q = questions_by_id_group[decoded_answer.question_id]
        key = cache_key(chunk_state, decoded_answer.question_id, q.body_hash, resolved_model)
        cache.put(key, decoded_answer, resolved_model, input_tokens_new)
        answers_by_question[decoded_answer.question_id].append((decoded_answer, input_tokens_new))

    return resolved_model


async def _process_one_call_group(
    doc,
    group: CallGroup,
    *,
    question_set: QuestionSet,
    client: AsyncAskingClient,
    cache: CellCache,
    quarantine: QuarantineWriter,
    run_id: str,
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
) -> list | None:
    """Everything one call group needs for one document: project the state,
    chunk it if it doesn't fit alone (T5.4), ask every chunk (splitting the
    question battery across several calls per chunk if the schema alone
    doesn't fit — T5.2), and aggregate multi-chunk answers back into one
    cell per question. Returns the group's cells, or `None` if the document
    should be abandoned (already accounted for in `state`/`quarantine`)."""
    questions_by_id_group = {q.id: q for q in group.questions}
    state_dict = project_state(doc.fields, group)

    try:
        chunks = chunk_document(state_dict, group)
    except ContextOverflowError as exc:
        quarantine.write(
            row_index=doc.meta.get("row_index", -1),
            reason=f"state_exceeds_context: {exc}",
            raw={"doc_id": doc.id, "projection_id": group.projection_id},
        )
        return None

    chunk_count = len(chunks)
    model_for_key = cache.resolve_model(config.model_alias)
    answers_by_question: dict[str, list[tuple]] = {q.id: [] for q in group.questions}
    group_had_new_call = False
    resolved_model_group = model_for_key

    for chunk_state in chunks:
        hits: dict[str, CachedCell] = {}
        misses: dict[str, Question] = {}
        if model_for_key is not None:
            for q in group.questions:
                key = cache_key(chunk_state, q.id, q.body_hash, model_for_key)
                cached = cache.get(key)
                if cached is not None:
                    hits[q.id] = cached
                else:
                    misses[q.id] = q
        else:
            misses = dict(questions_by_id_group)

        for qid, cached_cell in hits.items():
            answers_by_question[qid].append((cached_cell.answer, cached_cell.input_tokens))
        state.cache_hits_total += len(hits)

        if not misses:
            continue

        missing_group = CallGroup(
            projection_id=group.projection_id, fields=group.fields, questions=tuple(misses.values())
        )
        try:
            batches = split_for_context(missing_group, chunk_state)
        except ContextOverflowError as exc:
            quarantine.write(
                row_index=doc.meta.get("row_index", -1),
                reason=f"state_exceeds_context: {exc}",
                raw={"doc_id": doc.id, "projection_id": group.projection_id},
            )
            return None

        for batch_ids in batches:
            batch_questions = {qid: misses[qid] for qid in batch_ids}
            resolved_model = await _ask_batch(
                chunk_state=chunk_state,
                batch_questions=batch_questions,
                questions_by_id_group=questions_by_id_group,
                client=client,
                cache=cache,
                config=config,
                gate=gate,
                state=state,
                answers_by_question=answers_by_question,
            )
            if resolved_model is None:
                return None
            resolved_model_group = resolved_model
            group_had_new_call = True

    call_id = str(uuid.uuid4()) if group_had_new_call else "cached"
    ts = datetime.now(UTC)
    cells = []
    for q in group.questions:
        per_chunk = answers_by_question[q.id]
        if not per_chunk:
            continue  # only reachable if a prior return already abandoned the document
        total_tokens_for_q = sum(tokens for _, tokens in per_chunk)
        final_answer = per_chunk[0][0] if chunk_count == 1 else aggregate_chunk_answers(
            [answer for answer, _ in per_chunk]
        )
        model_for_cell = resolved_model_group if resolved_model_group is not None else "unknown"
        cells.append(
            build_cell(
                final_answer,
                doc_id=doc.id,
                gate=question_set.resolved_gate(q),
                projection_id=group.projection_id,
                call_id=call_id,
                run_id=run_id,
                model=model_for_cell,
                questionset_hash=question_set.questionset_hash,
                question_body_hash=q.body_hash,
                input_tokens=total_tokens_for_q,
                ts=ts,
                chunk_count=chunk_count,
            )
        )
    return cells


async def _process_one_document(
    doc,
    *,
    question_set: QuestionSet,
    call_groups: list[CallGroup],
    client: AsyncAskingClient,
    cache: CellCache,
    shard_writer: ShardWriter,
    checkpoint: Checkpoint,
    quarantine: QuarantineWriter,
    run_id: str,
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
) -> None:
    all_cells = []
    for group in call_groups:
        group_cells = await _process_one_call_group(
            doc,
            group,
            question_set=question_set,
            client=client,
            cache=cache,
            quarantine=quarantine,
            run_id=run_id,
            config=config,
            gate=gate,
            state=state,
        )
        if group_cells is None:
            return  # abandoned: budget breach, retries exhausted, decode failure, or quarantined
        all_cells.extend(group_cells)

    finalized = shard_writer.add(all_cells)
    state.documents_processed += 1
    if finalized:
        checkpoint.write(
            {
                "run_id": run_id,
                "questionset_hash": question_set.questionset_hash,
                "cursor": state.documents_processed,
                "shards": [p.name for p in shard_writer.shard_paths()],
                "total_input_tokens_charged": state.total_input_tokens_charged,
            }
        )


async def _ask_with_retries(
    client: AsyncAskingClient,
    state_dict: object,
    misses: dict[str, Question],
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
    *,
    fatal_holder: _SharedState,
):
    attempt = 0
    while True:
        attempt += 1
        await gate.acquire()
        try:
            response = await client.ask(state_dict, misses, model=config.model_alias)
        except JevConfigError:
            raise  # fatal: propagate straight out, caught by the worker
        except JevTransientError as exc:
            failure_class = classify(exc)
            if failure_class == FailureClass.RATE_LIMIT_OR_OVERLOAD:
                await gate.on_rate_limited()
            if not config.retry_policy.should_retry(failure_class, attempt):
                state.documents_skipped += 1
                return None
            retry_after_ms = getattr(exc.__cause__, "retry_after_ms", None)
            backoff = config.retry_policy.backoff_seconds(attempt, retry_after_ms=retry_after_ms)
            await asyncio.sleep(backoff)
            continue
        else:
            await gate.on_success()
            return response
        finally:
            await gate.release()


async def _produce(
    queue: asyncio.Queue, docs_iter, state: _SharedState, worker_count: int
) -> None:
    for doc in docs_iter:
        if state.stop_event.is_set():
            break
        await queue.put(doc)
    for _ in range(worker_count):
        await queue.put(_SENTINEL)


async def _report_progress(
    state: _SharedState,
    gate: _ConcurrencyGate,
    *,
    documents_total: int | None,
    budget_usd: float,
    started_at: float,
    interval_seconds: float,
    on_progress,
) -> None:
    if on_progress is None:
        return
    while not state.stop_event.is_set():
        await asyncio.sleep(interval_seconds)
        snapshot = ProgressSnapshot(
            documents_total=documents_total,
            documents_done=state.documents_processed + state.documents_already_done,
            documents_processed=state.documents_processed,
            documents_already_done=state.documents_already_done,
            cache_hits=state.cache_hits_total,
            cache_misses=state.cache_misses_total,
            spent_usd=micro_usd_to_usd(state.spent_micro_usd()),
            budget_usd=budget_usd,
            concurrency_limit=gate.limit,
            elapsed_seconds=time.monotonic() - started_at,
            stopping=state.stop_event.is_set(),
        )
        on_progress(snapshot)


async def run_scheduled(config: SchedulerConfig, client: AsyncAskingClient) -> RunResult:
    question_set = load_question_set(config.questions_path)
    call_groups = plan_call_groups(question_set)

    # T5.1's runtime check: every projection field must actually be a column
    # in the corpus, checked once against the first document (04-P5-PLANNER.md
    # -- a corpus with ragged columns is a source problem; per-document
    # checking would cost a dict scan per call for a guarantee the first row
    # already gives). An empty corpus has nothing to check against.
    first_doc_fields: set[str] = set()
    for doc in normalize(read_source(config.input_path), id_field=config.id_field):
        first_doc_fields = set(doc.fields.keys())
        break
    if first_doc_fields:
        try:
            verify_projection_fields(call_groups, first_doc_fields)
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc

    if config.resume_run_id is not None:
        run_id = config.resume_run_id
        run_dir = config.census_dir / "runs" / run_id
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise RunnerError(f"cannot resume: no run '{run_id}' found under {run_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["questionset_hash"] != question_set.questionset_hash:
            diff = _questionset_diff(manifest["questions"], question_set)
            raise RunnerError(
                f"cannot resume run '{run_id}': the question set changed since this run "
                f"started.\n{diff}\nStart a new run instead, or restore the original "
                "question set."
            )
    else:
        run_id = str(uuid.uuid4())
        run_dir = config.census_dir / "runs" / run_id
        manifest = {
            "run_id": run_id,
            "questionset_hash": question_set.questionset_hash,
            "questions": {q.id: q.body_hash for q in question_set.questions},
            "input_path": str(config.input_path),
            "created_at": datetime.now(UTC).isoformat(),
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checkpoint = Checkpoint(run_dir / "checkpoint.json")
    quarantine = QuarantineWriter(run_dir / "quarantine.jsonl")
    shard_writer = ShardWriter(run_dir / "shards", shard_size=config.shard_size)
    cache = CellCache(config.census_dir / "cache.db")

    already_done_ids = _already_done_ids(shard_writer)
    prior_checkpoint = checkpoint.read()
    state = _SharedState(budget_micro_usd=usd_to_micro_usd(config.budget_usd))
    state.total_input_tokens_charged = (
        prior_checkpoint["total_input_tokens_charged"] if prior_checkpoint else 0
    )

    # Unsupported extensions fail loudly right here, same as read_source
    # would inside _docs_iter() — no reason to defer that failure.
    documents_total = count_rows(config.input_path)
    if config.limit is not None:
        documents_total = min(documents_total, config.limit)

    def _docs_iter():
        stream = normalize(read_source(config.input_path), id_field=config.id_field, quarantine=quarantine)
        count = 0
        for doc in stream:
            if config.limit is not None and count >= config.limit:
                break
            count += 1
            if doc.id in already_done_ids:
                state.documents_already_done += 1
                continue
            yield doc

    aimd = AIMDConcurrency(initial=config.concurrency_initial, ceiling=config.concurrency_ceiling)
    gate = _ConcurrencyGate(aimd)
    queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_maxsize)
    worker_count = config.concurrency_ceiling

    def _on_sigint() -> None:
        state.interrupted = True
        state.stop_event.set()

    loop = asyncio.get_running_loop()
    sigint_installed = False
    try:
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
        sigint_installed = True
    except (NotImplementedError, RuntimeError):
        pass  # unsupported on this platform/loop; SIGINT still works, just not gracefully

    started_at = time.monotonic()
    try:
        producer_task = asyncio.create_task(_produce(queue, _docs_iter(), state, worker_count))
        worker_tasks = [
            asyncio.create_task(
                _worker(
                    queue,
                    question_set=question_set,
                    call_groups=call_groups,
                    client=client,
                    cache=cache,
                    shard_writer=shard_writer,
                    checkpoint=checkpoint,
                    quarantine=quarantine,
                    run_id=run_id,
                    config=config,
                    gate=gate,
                    state=state,
                )
            )
            for _ in range(worker_count)
        ]
        progress_task = asyncio.create_task(
            _report_progress(
                state,
                gate,
                documents_total=documents_total,
                budget_usd=config.budget_usd,
                started_at=started_at,
                interval_seconds=config.progress_interval_seconds,
                on_progress=config.on_progress,
            )
        )

        await producer_task
        await asyncio.gather(*worker_tasks)
        state.stop_event.set()  # let the progress task exit
        await progress_task
    finally:
        if sigint_installed:
            loop.remove_signal_handler(signal.SIGINT)
        shard_writer.flush()
        checkpoint.write(
            {
                "run_id": run_id,
                "questionset_hash": question_set.questionset_hash,
                "cursor": state.documents_processed,
                "shards": [p.name for p in shard_writer.shard_paths()],
                "total_input_tokens_charged": state.total_input_tokens_charged,
            }
        )
        out_path = config.out_dir / "cells.parquet"
        _write_results(shard_writer, out_path)
        calibration_ratio = cache.calibration_ratio()
        cache.close()

    if state.fatal_error is not None:
        raise state.fatal_error

    return RunResult(
        run_id=run_id,
        documents_processed=state.documents_processed,
        documents_already_done=state.documents_already_done,
        documents_skipped=state.documents_skipped,
        documents_quarantined=quarantine.count,
        spent_usd=micro_usd_to_usd(state.spent_micro_usd()),
        total_input_tokens_charged=state.total_input_tokens_charged,
        cache_hits=state.cache_hits_total,
        cache_misses=state.cache_misses_total,
        budget_exhausted=state.budget_exhausted,
        out_path=out_path,
        calibration_ratio=calibration_ratio,
        interrupted=state.interrupted,
    )
