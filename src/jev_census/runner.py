"""Runner (P2): the durable version of T1.9's inline loop. Wires together the
cell cache (T2.1-T2.2), the atomic shard writer (T2.3), the atomic checkpoint
(T2.4), `--resume` (T2.5), and quarantine (T2.6) into one `run_census()` call
that `cli.py` invokes. Framework-agnostic and network-free except through the
injected `client` — tests pass a fake (`tests/fakes.py`) so nothing here ever
touches the real API.

Resume correctness: which documents are already durably written is decided by
reading finalized shards' `doc_id`s, not by trusting `checkpoint.json`'s
`cursor` number alone. Finalizing a shard and updating the checkpoint are two
separate steps; a crash between them must never produce a duplicate. Every
answer is cached the moment it's decoded, before it's ever written to a
shard — so even a document reprocessed after a crash (because its shard
never got finalized) is re-derived from cache, never re-paid for.
"""

from __future__ import annotations

import itertools
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from .cache import CachedCell, CellCache, cache_key
from .cell import build_cell
from .checkpoint import Checkpoint
from .client import JevTransientError
from .decoder import DecodeError, decode_answers
from .normalizer import normalize
from .quarantine import QuarantineWriter
from .question_set import Question, QuestionSet, load_question_set
from .shard_writer import ShardWriter
from .sources import read_source
from .writer import cells_to_table

INPUT_COST_PER_MTOK_USD = 0.042


class AskingClient(Protocol):
    def ask(self, state: object, questions: dict[str, Question], model: str | None = None): ...


class RunnerError(Exception):
    """Fatal, config-class problems the CLI should report and abort on:
    resuming a run that doesn't exist, or a question set that changed since
    the run being resumed started."""


@dataclass(frozen=True)
class RunConfig:
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


@dataclass(frozen=True)
class RunResult:
    run_id: str
    documents_processed: int
    documents_already_done: int
    documents_skipped: int
    documents_quarantined: int
    spent_usd: float
    cache_hits: int
    cache_misses: int
    budget_exhausted: bool
    out_path: Path


def _questionset_diff(old_body_hashes: dict[str, str], question_set: QuestionSet) -> str:
    new_hashes = {q.id: q.body_hash for q in question_set.questions}
    added = sorted(set(new_hashes) - set(old_body_hashes))
    removed = sorted(set(old_body_hashes) - set(new_hashes))
    changed = sorted(
        qid
        for qid in set(old_body_hashes) & set(new_hashes)
        if old_body_hashes[qid] != new_hashes[qid]
    )
    lines = []
    if added:
        lines.append(f"  added: {', '.join(added)}")
    if removed:
        lines.append(f"  removed: {', '.join(removed)}")
    if changed:
        lines.append(f"  changed: {', '.join(changed)}")
    return "\n".join(lines) if lines else "  (hash differs; check `version` or question order)"


def _already_done_ids(shard_writer: ShardWriter) -> set[str]:
    done: set[str] = set()
    for shard_path in shard_writer.shard_paths():
        table = pq.read_table(shard_path, columns=["doc_id"])
        done.update(table.column("doc_id").to_pylist())
    return done


def _write_results(shard_writer: ShardWriter, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shard_paths = shard_writer.shard_paths()
    if not shard_paths:
        table = cells_to_table([])
    else:
        table = pa.concat_tables([pq.read_table(p) for p in shard_paths])
    pq.write_table(table, out_path)


def run_census(config: RunConfig, client: AskingClient) -> RunResult:
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
    spent_usd = prior_checkpoint["spent_usd"] if prior_checkpoint else 0.0

    projection_fields: set[str] = set()
    for q in question_set.questions:
        projection_fields.update(question_set.resolved_projection(q))

    docs_iter = normalize(read_source(config.input_path), id_field=config.id_field, quarantine=quarantine)
    if config.limit is not None:
        docs_iter = itertools.islice(docs_iter, config.limit)

    documents_processed = 0
    documents_already_done = 0
    documents_skipped = 0
    cache_hits_total = 0
    cache_misses_total = 0
    budget_exhausted = False

    # A fatal JevConfigError (401/422/...) propagates straight out of this
    # try block; the finally clause below still flushes and checkpoints
    # whatever already completed, so nothing legitimately finished is lost.
    try:
        for doc in docs_iter:
            if doc.id in already_done_ids:
                documents_already_done += 1
                continue

            state = (
                {field: doc.fields.get(field) for field in sorted(projection_fields)}
                if projection_fields
                else doc.fields
            )

            model_for_key = cache.resolve_model(config.model_alias)
            hits: dict[str, CachedCell] = {}
            misses: dict[str, Question] = {}
            if model_for_key is not None:
                for qid, question in questions_by_id.items():
                    key = cache_key(state, qid, question.body_hash, model_for_key)
                    cached = cache.get(key)
                    if cached is not None:
                        hits[qid] = cached
                    else:
                        misses[qid] = question
            else:
                misses = dict(questions_by_id)

            cache_hits_total += len(hits)
            cache_misses_total += len(misses)

            resolved_model = model_for_key
            input_tokens_new = 0
            call_id = "cached"
            decoded_new = []

            if misses:
                if spent_usd >= config.budget_usd:
                    budget_exhausted = True
                    break

                call_id = str(uuid.uuid4())
                try:
                    response = client.ask(state, misses, model=config.model_alias)
                except JevTransientError:
                    documents_skipped += 1
                    continue

                try:
                    decoded_new = decode_answers(response, misses)
                except DecodeError:
                    documents_skipped += 1
                    continue

                resolved_model = response.model
                input_tokens_new = response.usage.input_tokens or 0
                spent_usd += input_tokens_new / 1_000_000 * INPUT_COST_PER_MTOK_USD
                cache.record_model_resolution(config.model_alias, resolved_model)
                for decoded_answer in decoded_new:
                    q = questions_by_id[decoded_answer.question_id]
                    key = cache_key(state, decoded_answer.question_id, q.body_hash, resolved_model)
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

            finalized = shard_writer.add(cells)
            documents_processed += 1
            if finalized:
                checkpoint.write(
                    {
                        "run_id": run_id,
                        "questionset_hash": question_set.questionset_hash,
                        "cursor": documents_processed,
                        "shards": [p.name for p in shard_writer.shard_paths()],
                        "spent_usd": spent_usd,
                    }
                )
    finally:
        shard_writer.flush()
        checkpoint.write(
            {
                "run_id": run_id,
                "questionset_hash": question_set.questionset_hash,
                "cursor": documents_processed,
                "shards": [p.name for p in shard_writer.shard_paths()],
                "spent_usd": spent_usd,
            }
        )
        out_path = config.out_dir / "cells.parquet"
        _write_results(shard_writer, out_path)
        cache.close()

    return RunResult(
        run_id=run_id,
        documents_processed=documents_processed,
        documents_already_done=documents_already_done,
        documents_skipped=documents_skipped,
        documents_quarantined=quarantine.count,
        spent_usd=spent_usd,
        cache_hits=cache_hits_total,
        cache_misses=cache_misses_total,
        budget_exhausted=budget_exhausted,
        out_path=out_path,
    )
