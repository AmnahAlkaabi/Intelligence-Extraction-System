"""Master Orchestrator (L0) — job lifecycle: dispatch files to domain \
managers, track completion, trigger the Synthesiser, persist outputs.

An in-memory job registry is sufficient for a single-node air-gapped
deployment; job state is also durably reflected on disk via the written
report artifacts so results survive a process restart even though the
live progress tracker does not.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.agents.domain_managers import process_file
from app.agents.synthesizer import synthesize
from app.config import get_settings
from app.graph.neo4j_client import get_store
from app.models.schemas import DomainResult, FileProgress, Job, JobStatus
from app.parsers.router import classify
from app.storage.file_store import (
    write_graph_json,
    write_json_report,
    write_markdown_report,
    write_pii_csv,
    write_tables_csv,
)

logger = logging.getLogger(__name__)


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
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_domain_results(self, job_id: str) -> list[DomainResult]:
        return self._domain_results.get(job_id, [])

    def start(self, job_id: str, file_paths: list[str]) -> None:
        task = asyncio.create_task(self._run(job_id, file_paths))
        self._tasks[job_id] = task

    async def _run(self, job_id: str, file_paths: list[str]) -> None:
        job = self._jobs[job_id]
        settings = get_settings()
        semaphore = asyncio.Semaphore(settings.max_parallel_files)

        job.status = JobStatus.PARSING
        self._touch(job)

        async def _process_one(fp: str) -> DomainResult:
            async with semaphore:
                progress = next(f for f in job.files if f.filename == fp)
                progress.status = JobStatus.EXTRACTING
                self._touch(job)
                try:
                    result = await process_file(fp)
                    progress.status = JobStatus.COMPLETE
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Processing failed for %s", fp)
                    progress.status = JobStatus.FAILED
                    progress.error = str(exc)
                    result = DomainResult(domain="unknown", source_file=fp, errors=[str(exc)])
                self._touch(job)
                return result

        results = await asyncio.gather(*(_process_one(fp) for fp in file_paths))
        self._domain_results[job_id] = results

        job.status = JobStatus.GRAPH_BUILD
        self._touch(job)
        try:
            store = get_store()
            await store.ensure_schema()
            all_entities = [e for r in results for e in r.entities]
            all_relations = [rel for r in results for rel in r.relations]
            all_chunks = [c for r in results for c in r.chunks]
            await store.ingest_job_graph(job_id, all_entities, all_relations, all_chunks)
        except Exception:
            logger.exception("Graph ingest failed for job %s — chat/GraphRAG will be degraded.", job_id)

        job.status = JobStatus.SYNTHESIZING
        self._touch(job)
        try:
            output = await synthesize(results)
            job.result = output

            write_json_report(job_id, output)
            write_markdown_report(job_id, output)
            write_pii_csv(job_id, output)
            write_graph_json(job_id, output)
            all_tables = [t for r in results for t in r.tables]
            write_tables_csv(job_id, all_tables)

            job.status = JobStatus.COMPLETE
            job.progress_pct = 100.0
        except Exception as exc:  # noqa: BLE001
            logger.exception("Synthesis failed for job %s", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
        self._touch(job)

    def _touch(self, job: Job) -> None:
        job.updated_at = datetime.now(timezone.utc)
        done = sum(1 for f in job.files if f.status in (JobStatus.COMPLETE, JobStatus.FAILED))
        job.progress_pct = round(100.0 * done / max(len(job.files), 1) * 0.9, 1)  # reserve 10% for synth


_manager_singleton: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = JobManager()
    return _manager_singleton
