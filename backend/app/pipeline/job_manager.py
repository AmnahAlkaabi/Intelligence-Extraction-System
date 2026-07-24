"""Master Orchestrator (L0) — job lifecycle: dispatch files to domain \
managers, track completion, trigger the Synthesiser, persist outputs.

The live registry is in-memory, but every meaningful state change is also
snapshotted to disk (job_state.json per job, see storage/file_store.py) so
job history -- including completed results -- survives a backend restart.
A job caught mid-processing when the process restarts can't resume (no
in-flight asyncio task survives a restart), so it's loaded back as FAILED
with an explanatory message rather than shown stuck at its last progress
forever.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.agents.domain_managers import process_file
from app.agents.synthesizer import synthesize
from app.config import get_settings
from app.graph.neo4j_client import get_store
from app.llm.client import get_llm_client
from app.models.schemas import DomainResult, FileProgress, Job, JobStatus
from app.parsers.router import classify
from app.pipeline.agent_tracker import finish_activity, start_activity
from app.storage.file_store import (
    delete_job_files,
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


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._domain_results: dict[str, list[DomainResult]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

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
        state for a job the registry no longer knows about."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in _ACTIVE_STATUSES:
            raise ValueError(f"Job {job_id} is still {job.status.value} — wait for it to finish or fail before deleting it.")
        self._jobs.pop(job_id, None)
        self._domain_results.pop(job_id, None)
        self._tasks.pop(job_id, None)
        return True

    def start(self, job_id: str, file_paths: list[str]) -> None:
        task = asyncio.create_task(self._run(job_id, file_paths))
        self._tasks[job_id] = task

    async def _run(self, job_id: str, file_paths: list[str]) -> None:
        job = self._jobs[job_id]
        settings = get_settings()
        semaphore = asyncio.Semaphore(settings.max_parallel_files)

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
        neo4j_reachable = neo4j_ok

        job.status = JobStatus.PARSING
        self.touch(job)

        async def _process_one(fp: str) -> DomainResult:
            async with semaphore:
                progress = next(f for f in job.files if f.filename == fp)
                progress.status = JobStatus.EXTRACTING
                self.touch(job)
                try:
                    result = await process_file(fp, unreachable_backends=unreachable_backends, job=job)
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
                self.touch(job)
                return result

        results = await asyncio.gather(*(_process_one(fp) for fp in file_paths))
        self._domain_results[job_id] = results

        job.status = JobStatus.GRAPH_BUILD
        self.touch(job)
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
        self.touch(job)
        synth_activity = start_activity(job, "BI Synthesizer", "(all files)")
        try:
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
        self.touch(job)

    def touch(self, job: Job) -> None:
        job.updated_at = datetime.now(timezone.utc)
        done = sum(1 for f in job.files if f.status in (JobStatus.COMPLETE, JobStatus.FAILED))
        job.progress_pct = round(100.0 * done / max(len(job.files), 1) * 0.9, 1)  # reserve 10% for synth
        write_job_state(job)


_manager_singleton: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = JobManager()
    return _manager_singleton
