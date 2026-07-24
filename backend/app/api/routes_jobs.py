import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.graph.neo4j_client import get_store
from app.models.schemas import Job
from app.pipeline.job_manager import get_job_manager
from app.storage.file_store import delete_job_files

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])


class RenameJobRequest(BaseModel):
    name: str


@router.get("/jobs", response_model=list[Job])
async def list_jobs() -> list[Job]:
    return get_job_manager().list_jobs()


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    job = get_job_manager().get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job


@router.patch("/jobs/{job_id}/name", response_model=Job)
async def rename_job(job_id: str, body: RenameJobRequest) -> Job:
    job = get_job_manager().get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    name = body.name.strip()
    job.name = name or None
    get_job_manager().touch(job)
    return job


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    try:
        existed = get_job_manager().delete_job(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not existed:
        raise HTTPException(404, "Job not found.")

    delete_job_files(job_id)
    try:
        await get_store().delete_job(job_id)
    except Exception:
        logger.exception("Neo4j cleanup failed for deleted job %s — graph nodes may be orphaned.", job_id)
