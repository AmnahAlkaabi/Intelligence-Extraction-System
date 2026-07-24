from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.schemas import Job
from app.pipeline.job_manager import get_job_manager

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
