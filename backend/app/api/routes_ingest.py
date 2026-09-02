import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import get_settings
from app.models.schemas import FileProgress, Job, JobStatus
from app.parsers.archive_expand import expand_archive, is_archive
from app.parsers.router import classify
from app.pipeline.job_manager import get_job_manager
from app.storage import obs_client
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
        filename = f.filename or "unnamed"
        path = await save_upload(job.job_id, filename, content)
        saved_paths.append(path)
        # Optional OBS mirror (see storage/obs_client.py) -- fire-and-forget,
        # not awaited: mirror_upload already no-ops instantly when the
        # feature is off (the default) or no credential is active, and
        # when it IS active this must never make the upload response wait
        # on a possibly slow/unreachable external bucket. The function
        # swallows its own errors and just logs, so there's nothing here
        # to await or handle.
        asyncio.create_task(obs_client.mirror_upload(job.job_id, filename, content))

    # Archive Agent (L2): expand any ZIP/TAR uploads into their member files
    # before the job's file list is finalized, so each extracted file flows
    # through the normal per-file pipeline like anything else. The archive
    # itself is not processed as a "file" -- only its contents are.
    final_paths: list[str] = []
    archive_warnings: list[str] = []
    for path in saved_paths:
        if is_archive(path):
            extract_dir = str(Path(path).parent / "extracted")
            # expand_archive does synchronous disk I/O and can decompress a
            # sizeable amount of data -- running it directly on the event
            # loop would block every other in-flight request (job status
            # polling, other uploads) for as long as extraction takes.
            members, warnings = await asyncio.to_thread(expand_archive, path, extract_dir)
            archive_warnings.extend(warnings)
            if not members:
                archive_warnings.append(f"{Path(path).name}: archive produced no usable files.")
            final_paths.extend(members)
        else:
            final_paths.append(path)

    job.files = [
        FileProgress(filename=p, category=classify(p), status=JobStatus.QUEUED) for p in final_paths
    ]
    job.warnings = archive_warnings

    manager.start(job.job_id, final_paths)
    return job
