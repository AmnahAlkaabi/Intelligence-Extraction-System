import logging

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import get_settings
from app.models.schemas import Job
from app.pipeline.job_manager import get_job_manager
from app.storage.file_store import save_upload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=Job)
async def ingest_files(files: list[UploadFile]) -> Job:
    if not files:
        raise HTTPException(400, "No files provided.")

    settings = get_settings()
    manager = get_job_manager()

    # Job id is needed before files are saved so uploads land in a per-job dir.
    job = manager.create_job(file_paths=[f.filename or "unnamed" for f in files])

    saved_paths: list[str] = []
    for f in files:
        content = await f.read()
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} exceeds max upload size of {settings.max_upload_mb} MB.")
        path = await save_upload(job.job_id, f.filename or "unnamed", content)
        saved_paths.append(path)

    # Re-point job's file progress entries at the saved disk paths.
    for progress, path in zip(job.files, saved_paths):
        progress.filename = path

    manager.start(job.job_id, saved_paths)
    return job
