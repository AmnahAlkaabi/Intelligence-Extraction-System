"""Master Orchestrator (L0) — job lifecycle: dispatch files to domain \
managers, track completion, trigger the Synthesiser, persist outputs.

The live registry is in-memory, but every meaningful state change is also
snapshotted to disk (job_state.json per job, see storage/file_store.py) so
job history -- including completed results -- survives a backend restart.
A job caught mid-processing when the process restarts can't resume (no
in-flight asyncio task survives a restart), so it's loaded back as FAILED
with an explanatory message rather than shown stuck at its last progress
forever.

Large jobs (above settings.batch_threshold_files) run in importance-ranked
batches (see agents/importance.py) instead of firing every file at once:
files are triaged by likely intelligence value before any processing
starts, then processed batch_size_files at a time, pausing after each
batch but the last so the user can review a summary and decide whether to
continue or stop with what's been analyzed so far. The batch plan itself
(_BatchRun) is kept in memory only, like _domain_results/_tasks below --
consistent with the existing rule that an in-flight job can't resume
across a server restart anyway.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.agents.domain_managers import process_file
from app.agents.importance import RankedFile, make_batches, rank_files
from app.agents.synthesizer import synthesize
from app.config import get_settings
from app.graph.neo4j_client import get_store
from app.llm.client import get_llm_client
from app.models.schemas import BatchSummary, DomainResult, FileProgress, Job, JobStatus
from app.parsers.router import classify
from app.pipeline.agent_tracker import finish_activity, start_activity
from app.storage.file_store import (
    load_all_job_states,
    write_graph_json,
    write_job_state,
    write_json_report,
    write_mapping_csv,
    write_markdown_report,
    write_pii_csv,
    write_tables_csv,
)

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {
    JobStatus.QUEUED, JobStatus.PARSING, JobStatus.EXTRACTING,
    JobStatus.GRAPH_BUILD, JobStatus.SYNTHESIZING,
}
_TERMINAL_FILE_STATUSES = (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.SKIPPED)


@dataclass
class _BatchRun:
    """State threaded across a job's batches -- built once by
    _prepare_and_run, read/mutated by _run_batches on every batch
    (including resumed ones after a continue_batch() call), and dropped
    once _finalize_job runs. Kept out of the Job pydantic model itself
    since none of this is meant to be persisted or shown directly (Job
    exposes the user-facing subset via total_batches/current_batch/
    batch_summaries instead)."""
    batches: list[list[RankedFile]]
    unreachable_backends: set[str]
    neo4j_reachable: bool
    semaphore: asyncio.Semaphore
    all_results: list[DomainResult] = field(default_factory=list)
    next_batch_index: int = 0


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._domain_results: dict[str, list[DomainResult]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._batch_runs: dict[str, _BatchRun] = {}
        # Each job's own semaphore only bounds concurrency *within* that
        # job -- with several jobs running at once (multiple users
        # uploading concurrently) their per-job semaphores don't coordinate
        # with each other at all, so total in-flight file pipelines (each
        # hitting the LLM backends/Neo4j/embedder) is unbounded. This one
        # is shared across every job's _process_one to cap that total.
        self._global_semaphore = asyncio.Semaphore(get_settings().max_parallel_files_global)

    def create_job(self, file_paths: list[str]) -> Job:
        job = Job(files=[
            FileProgress(filename=fp, category=classify(fp), status=JobStatus.QUEUED) for fp in file_paths
        ])
        self._jobs[job.job_id] = job
        write_job_state(job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def load_persisted_jobs(self) -> None:
        """Called once at startup. Jobs that were still processing when the
        server stopped can't resume (their asyncio task is gone), so they're
        loaded back as FAILED with a clear reason instead of appearing
        forever stuck at their last progress percentage.
        """
        for job in load_all_job_states():
            if job.status not in (JobStatus.COMPLETE, JobStatus.FAILED):
                job.status = JobStatus.FAILED
                job.error = "Processing was interrupted by a server restart before this job finished."
            self._jobs[job.job_id] = job
        if self._jobs:
            logger.info("Restored %d job(s) from disk.", len(self._jobs))

    def get_domain_results(self, job_id: str) -> list[DomainResult]:
        return self._domain_results.get(job_id, [])

    def delete_job(self, job_id: str) -> bool:
        """Removes a job's in-memory state; the caller is responsible for
        deleting its on-disk files and Neo4j graph nodes (both I/O -- the
        former sync, the latter async -- so they don't belong on this
        otherwise-sync manager). Returns False if the job doesn't exist;
        raises ValueError if it's still actively processing, since deleting
        out from under a running asyncio task would leave that task writing
        state for a job the registry no longer knows about. A job paused
        at AWAITING_BATCH_CONFIRM has no task running at that moment, so
        it's safe to delete without waiting."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in _ACTIVE_STATUSES:
            raise ValueError(f"Job {job_id} is still {job.status.value} — wait for it to finish or fail before deleting it.")
        self._jobs.pop(job_id, None)
        self._domain_results.pop(job_id, None)
        self._tasks.pop(job_id, None)
        self._batch_runs.pop(job_id, None)
        return True

    def start(self, job_id: str, file_paths: list[str]) -> None:
        task = asyncio.create_task(self._guarded(job_id, self._prepare_and_run(job_id, file_paths)))
        self._tasks[job_id] = task

    def continue_batch(self, job_id: str) -> bool:
        """Resumes a job paused at AWAITING_BATCH_CONFIRM at its next
        batch. Returns False if the job doesn't exist; raises ValueError if
        it isn't actually paused (e.g. already running, already complete,
        or never entered batch mode)."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status != JobStatus.AWAITING_BATCH_CONFIRM:
            raise ValueError(f"Job {job_id} is not awaiting batch confirmation (status={job.status.value}).")
        task = asyncio.create_task(self._guarded(job_id, self._run_batches(job_id)))
        self._tasks[job_id] = task
        return True

    def stop_batches(self, job_id: str) -> bool:
        """Ends a paused job early: whatever batches have already been
        processed are synthesized into the final report now, and every
        file in a batch that never ran is marked SKIPPED rather than left
        looking permanently stuck at QUEUED."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status != JobStatus.AWAITING_BATCH_CONFIRM:
            raise ValueError(f"Job {job_id} is not awaiting batch confirmation (status={job.status.value}).")
        task = asyncio.create_task(self._guarded(job_id, self._finalize_job(job_id, stopped_early=True)))
        self._tasks[job_id] = task
        return True

    async def _guarded(self, job_id: str, coro) -> None:
        job = self._jobs[job_id]
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            # Belt-and-suspenders: every stage below already has its own
            # try/except, but this catches anything upstream of/between them
            # (e.g. the preflight reachability check itself raising) so a
            # job can never silently stall forever with status left at
            # QUEUED/PARSING/AWAITING_BATCH_CONFIRM and job.error unset --
            # asyncio.create_task has nothing else watching for this task's
            # exception.
            logger.exception("Unhandled failure running job %s", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            await self.touch(job)

    async def _prepare_and_run(self, job_id: str, file_paths: list[str]) -> None:
        job = self._jobs[job_id]
        settings = get_settings()

        # Preflight: check Qwen/Kimi2/Neo4j reachability up front (a few
        # seconds) instead of only discovering a dead service after several
        # minutes of doomed per-file retries with the progress bar stuck and
        # no indication why. Non-fatal -- the job still runs with whatever
        # degraded functionality is possible, but the user sees exactly
        # what's down and which features it affects, immediately.
        llm_client = get_llm_client()
        store = get_store()
        backend_checks, (neo4j_ok, neo4j_detail) = await asyncio.gather(
            llm_client.check_all_backends(),
            store.check_reachable(),
        )
        unreachable_backends = {b for b, (ok, _) in backend_checks.items() if not ok}
        warnings: list[str] = []
        for backend in unreachable_backends:
            _, detail = backend_checks[backend]
            roles = llm_client.roles_using(backend)
            warnings.append(f"{detail} — affects: {', '.join(roles) if roles else 'no active roles'}")
        if not neo4j_ok:
            warnings.append(f"{neo4j_detail} — affects: knowledge graph, chat")
        job.warnings = warnings

        # Importance-ranked batching: below the threshold this produces
        # exactly one batch holding every file in upload order (ranking's
        # sort is stable), so small/typical jobs behave identically to
        # before batching existed -- no reordering, no pause, no new UI.
        categories = {fp: classify(fp) for fp in file_paths}
        ranked = rank_files(file_paths, categories)
        use_batching = len(file_paths) > settings.batch_threshold_files
        batches = make_batches(ranked, settings.batch_size_files) if use_batching else [ranked]
        job.total_batches = len(batches)

        if use_batching:
            by_filename = {f.filename: f for f in job.files}
            ordered_files: list[FileProgress] = []
            for batch_idx, batch in enumerate(batches, start=1):
                for r in batch:
                    fp_entry = by_filename[r.path]
                    fp_entry.batch = batch_idx
                    fp_entry.importance_reason = r.reason
                    ordered_files.append(fp_entry)
            job.files = ordered_files

        job.status = JobStatus.PARSING
        await self.touch(job)

        self._batch_runs[job_id] = _BatchRun(
            batches=batches,
            unreachable_backends=unreachable_backends,
            neo4j_reachable=neo4j_ok,
            semaphore=asyncio.Semaphore(settings.max_parallel_files),
        )
        await self._run_batches(job_id)

    async def _run_batches(self, job_id: str) -> None:
        """Processes batches starting at state.next_batch_index, pausing
        (returning without finalizing) after any batch but the last when
        more than one batch exists. Safe to call again later via
        continue_batch() -- it picks up exactly where it left off because
        next_batch_index and all_results live on the shared _BatchRun, not
        as local variables in a suspended coroutine."""
        job = self._jobs[job_id]
        state = self._batch_runs.get(job_id)
        if state is None:
            raise RuntimeError(f"No batch plan found for job {job_id}.")

        job.status = JobStatus.EXTRACTING
        await self.touch(job)

        while state.next_batch_index < len(state.batches):
            batch = state.batches[state.next_batch_index]
            batch_number = state.next_batch_index + 1
            job.current_batch = batch_number
            await self.touch(job)

            async def _process_one(fp: str) -> DomainResult:
                async with state.semaphore, self._global_semaphore:
                    progress = next(f for f in job.files if f.filename == fp)
                    progress.status = JobStatus.EXTRACTING
                    await self.touch(job)
                    try:
                        result = await process_file(fp, unreachable_backends=state.unreachable_backends, job=job)
                        progress.status = JobStatus.COMPLETE
                        progress.warnings = result.errors
                        progress.detected_language = result.detected_language
                        progress.translated = result.translated
                        progress.entities_found = len(result.entities)
                        progress.relations_found = len(result.relations)
                        progress.pii_found = len(result.pii_findings)
                        progress.financial_facts_found = len(result.financial_facts)
                        progress.tables_found = len(result.tables)
                        progress.chunks_found = len(result.chunks)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Processing failed for %s", fp)
                        progress.status = JobStatus.FAILED
                        progress.error = str(exc)
                        result = DomainResult(domain="unknown", source_file=fp, errors=[str(exc)])
                    await self.touch(job)
                    return result

            batch_paths = [r.path for r in batch]
            batch_results = await asyncio.gather(*(_process_one(fp) for fp in batch_paths))
            state.all_results.extend(batch_results)
            self._domain_results[job_id] = list(state.all_results)
            state.next_batch_index += 1

            if len(state.batches) > 1:
                job.batch_summaries.append(_summarize_batch(batch_number, batch, batch_results))

            is_last = state.next_batch_index >= len(state.batches)
            if len(state.batches) > 1 and not is_last:
                job.status = JobStatus.AWAITING_BATCH_CONFIRM
                await self.touch(job)
                return

        await self._finalize_job(job_id, stopped_early=False)

    async def _finalize_job(self, job_id: str, stopped_early: bool) -> None:
        job = self._jobs[job_id]
        state = self._batch_runs.get(job_id)
        results = state.all_results if state else self._domain_results.get(job_id, [])
        self._domain_results[job_id] = results

        if stopped_early:
            processed = {r.source_file for r in results}
            for f in job.files:
                if f.filename not in processed and f.status == JobStatus.QUEUED:
                    f.status = JobStatus.SKIPPED
            job.stopped_early = True

        job.status = JobStatus.GRAPH_BUILD
        await self.touch(job)
        store = get_store()
        neo4j_reachable = state.neo4j_reachable if state else True
        if neo4j_reachable:
            try:
                await store.ensure_schema()
                all_entities = [e for r in results for e in r.entities]
                all_relations = [rel for r in results for rel in r.relations]
                all_chunks = [c for r in results for c in r.chunks]
                await store.ingest_job_graph(job_id, all_entities, all_relations, all_chunks)
            except Exception:
                logger.exception("Graph ingest failed for job %s — chat/GraphRAG will be degraded.", job_id)
        else:
            logger.info("Skipping graph ingest for job %s — Neo4j was unreachable at preflight.", job_id)

        job.status = JobStatus.SYNTHESIZING
        await self.touch(job)
        synth_activity = start_activity(job, "BI Synthesizer", "(all files)")
        try:
            llm_client = get_llm_client()
            unreachable_backends = state.unreachable_backends if state else set()
            synthesis_backend = llm_client.backend_for_role("synthesis")
            output = await synthesize(results, skip_llm=synthesis_backend in unreachable_backends)
            job.result = output
            finish_activity(synth_activity, "completed")

            write_json_report(job_id, output)
            write_markdown_report(job_id, output)
            write_pii_csv(job_id, output)
            write_graph_json(job_id, output)
            write_mapping_csv(job_id, output)
            all_tables = [t for r in results for t in r.tables]
            write_tables_csv(job_id, all_tables)

            job.status = JobStatus.COMPLETE
            job.progress_pct = 100.0
        except Exception as exc:  # noqa: BLE001
            logger.exception("Synthesis failed for job %s", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            finish_activity(synth_activity, "failed")
        await self.touch(job)
        self._batch_runs.pop(job_id, None)

    async def touch(self, job: Job) -> None:
        job.updated_at = datetime.now(timezone.utc)
        done = sum(1 for f in job.files if f.status in _TERMINAL_FILE_STATUSES)
        job.progress_pct = round(100.0 * done / max(len(job.files), 1) * 0.9, 1)  # reserve 10% for synth
        # touch() fires on nearly every state change during processing
        # (once per file per stage); a completed job's state can be sizeable
        # (full knowledge graph, all extraction results), so the disk write
        # is offloaded to a thread rather than blocking the event loop --
        # and with it, every other job's progress and every API request --
        # for the duration of the write.
        await asyncio.to_thread(write_job_state, job)


def _summarize_batch(batch_number: int, batch: list[RankedFile], results: list[DomainResult]) -> BatchSummary:
    return BatchSummary(
        batch_number=batch_number,
        file_count=len(batch),
        top_files=[Path(r.path).name for r in batch[:5]],
        entities_found=sum(len(r.entities) for r in results),
        relations_found=sum(len(r.relations) for r in results),
        pii_found=sum(len(r.pii_findings) for r in results),
        financial_facts_found=sum(len(r.financial_facts) for r in results),
    )


_manager_singleton: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = JobManager()
    return _manager_singleton
