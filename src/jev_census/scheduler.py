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
from .client import JevConfigError, JevTransientError
from .decoder import DecodeError, decode_answers
from .failure import FailureClass, classify
from .money import micro_usd_to_usd, tokens_to_micro_usd, usd_to_micro_usd
from .normalizer import normalize
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
    questions_by_id: dict[str, Question],
    projection_fields: set[str],
    client: AsyncAskingClient,
    cache: CellCache,
    shard_writer: ShardWriter,
    checkpoint: Checkpoint,
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
                questions_by_id=questions_by_id,
                projection_fields=projection_fields,
                client=client,
                cache=cache,
                shard_writer=shard_writer,
                checkpoint=checkpoint,
                run_id=run_id,
                config=config,
                gate=gate,
                state=state,
            )
        except JevConfigError as exc:
            state.fatal_error = exc
            state.stop_event.set()


async def _process_one_document(
    doc,
    *,
    question_set: QuestionSet,
    questions_by_id: dict[str, Question],
    projection_fields: set[str],
    client: AsyncAskingClient,
    cache: CellCache,
    shard_writer: ShardWriter,
    checkpoint: Checkpoint,
    run_id: str,
    config: SchedulerConfig,
    gate: _ConcurrencyGate,
    state: _SharedState,
) -> None:
    state_dict = (
        {field: doc.fields.get(field) for field in sorted(projection_fields)}
        if projection_fields
        else doc.fields
    )

    model_for_key = cache.resolve_model(config.model_alias)
    hits: dict[str, CachedCell] = {}
    misses: dict[str, Question] = {}
    if model_for_key is not None:
        for qid, question in questions_by_id.items():
            key = cache_key(state_dict, qid, question.body_hash, model_for_key)
            cached = cache.get(key)
            if cached is not None:
                hits[qid] = cached
            else:
                misses[qid] = question
    else:
        misses = dict(questions_by_id)

    resolved_model = model_for_key
    input_tokens_new = 0
    call_id = "cached"
    decoded_new = []

    if misses:
        projected_state_tokens, projected_schema_tokens = estimate_call_tokens(state_dict, misses)
        projected_tokens = projected_state_tokens + projected_schema_tokens
        if state.spent_micro_usd() + tokens_to_micro_usd(projected_tokens) > state.budget_micro_usd:
            state.budget_exhausted = True
            state.stop_event.set()
            return

        response = await _ask_with_retries(
            client, state_dict, misses, config, gate, state, fatal_holder=state
        )
        if response is None:
            return  # exhausted retries or was skipped; already accounted for

        resolved_model = response.model
        input_tokens_new = response.usage.input_tokens or 0
        state.total_input_tokens_charged += input_tokens_new
        cache.record_model_resolution(config.model_alias, resolved_model)
        cache.record_calibration(estimated_tokens=projected_tokens, actual_tokens=input_tokens_new)

        try:
            decoded_new = decode_answers(response, misses)
        except DecodeError:
            state.documents_skipped += 1
            return

        call_id = str(uuid.uuid4())
        for decoded_answer in decoded_new:
            q = questions_by_id[decoded_answer.question_id]
            key = cache_key(state_dict, decoded_answer.question_id, q.body_hash, resolved_model)
            cache.put(key, decoded_answer, resolved_model, input_tokens_new)

    ts = datetime.now(UTC)
    cells = []
    for qid, cached_cell in hits.items():
        q = questions_by_id[qid]
        cells.append(
            build_cell(
                cached_cell.answer,
                doc_id=doc.id,
                gate=question_set.resolved_gate(q),
                projection_id="p0",
                call_id="cached",
                run_id=run_id,
                model=cached_cell.model,
                questionset_hash=question_set.questionset_hash,
                question_body_hash=q.body_hash,
                input_tokens=cached_cell.input_tokens,
                ts=ts,
            )
        )
    for decoded_answer in decoded_new:
        q = questions_by_id[decoded_answer.question_id]
        cells.append(
            build_cell(
                decoded_answer,
                doc_id=doc.id,
                gate=question_set.resolved_gate(q),
                projection_id="p0",
                call_id=call_id,
                run_id=run_id,
                model=resolved_model,
                questionset_hash=question_set.questionset_hash,
                question_body_hash=q.body_hash,
                input_tokens=input_tokens_new,
                ts=ts,
            )
        )

    state.cache_hits_total += len(hits)
    state.cache_misses_total += len(misses)

    finalized = shard_writer.add(cells)
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
    questions_by_id = {q.id: q for q in question_set.questions}

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

    projection_fields: set[str] = set()
    for q in question_set.questions:
        projection_fields.update(question_set.resolved_projection(q))

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
                    questions_by_id=questions_by_id,
                    projection_fields=projection_fields,
                    client=client,
                    cache=cache,
                    shard_writer=shard_writer,
                    checkpoint=checkpoint,
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
