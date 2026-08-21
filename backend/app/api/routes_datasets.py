"""Browse/download/delete the persistent, cross-job dataset library (see
storage/dataset_library.py) -- distinct from routes_outputs.py's
per-job "/outputs/{job_id}/..." endpoints, since datasets here outlive
their source job.
"""
import csv
import io
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.models.schemas import DatasetRecord
from app.storage import dataset_library

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetRecord])
async def list_datasets() -> list[DatasetRecord]:
    return dataset_library.list_datasets()


@router.get("/{dataset_id}", response_model=DatasetRecord)
async def get_dataset(dataset_id: str) -> DatasetRecord:
    record = dataset_library.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(404, "Dataset not found.")
    return record


@router.get("/{dataset_id}/preview")
async def preview_dataset(dataset_id: str, limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)) -> dict:
    result = dataset_library.read_dataset_rows(dataset_id, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(404, "Dataset not found.")
    record, rows = result
    return {
        "dataset": record,
        "rows": rows,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{dataset_id}/download")
async def download_dataset(dataset_id: str) -> Response:
    result = dataset_library.read_dataset_rows(dataset_id, limit=None, offset=0)
    if result is None:
        raise HTTPException(404, "Dataset not found.")
    record, rows = result
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([name for name, _ in record.columns])
    writer.writerows(rows)
    filename = f"{record.table_name}.csv"
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict:
    deleted = dataset_library.delete_dataset(dataset_id)
    if not deleted:
        raise HTTPException(404, "Dataset not found.")
    return {"deleted": True}
