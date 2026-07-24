from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models.schemas import (
    BIReport,
    ComplianceReport,
    JobStatus,
    KnowledgeGraphExport,
    SourceTargetMapping,
    TableBlock,
)
from app.pipeline.job_manager import get_job_manager
from app.storage.file_store import job_output_dir

router = APIRouter(tags=["outputs"])


def _completed_job(job_id: str):
    job = get_job_manager().get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.status != JobStatus.COMPLETE or job.result is None:
        raise HTTPException(409, f"Job is not complete yet (status={job.status.value}).")
    return job


@router.get("/outputs/{job_id}/bi-report", response_model=BIReport)
async def get_bi_report(job_id: str) -> BIReport:
    return _completed_job(job_id).result.bi_report


@router.get("/outputs/{job_id}/compliance-report", response_model=ComplianceReport)
async def get_compliance_report(job_id: str) -> ComplianceReport:
    return _completed_job(job_id).result.compliance_report


@router.get("/outputs/{job_id}/knowledge-graph", response_model=KnowledgeGraphExport)
async def get_knowledge_graph(job_id: str) -> KnowledgeGraphExport:
    return _completed_job(job_id).result.knowledge_graph


@router.get("/outputs/{job_id}/source-target-mapping", response_model=SourceTargetMapping)
async def get_source_target_mapping(job_id: str) -> SourceTargetMapping:
    return _completed_job(job_id).result.source_target_mapping


@router.get("/outputs/{job_id}/data-dump/tables", response_model=list[TableBlock])
async def get_data_dump_tables(job_id: str) -> list[TableBlock]:
    _completed_job(job_id)
    results = get_job_manager().get_domain_results(job_id)
    return [t for r in results for t in r.tables]


@router.get("/outputs/{job_id}/files/{artifact}")
async def download_artifact(job_id: str, artifact: str) -> FileResponse:
    _completed_job(job_id)
    allowed = {
        "report.json": "application/json",
        "report.md": "text/markdown",
        "pii_inventory.csv": "text/csv",
        "knowledge_graph.json": "application/json",
        "source_target_mapping.csv": "text/csv",
    }
    if artifact not in allowed:
        raise HTTPException(404, "Unknown artifact.")
    path = job_output_dir(job_id) / artifact
    if not path.exists():
        raise HTTPException(404, "Artifact not found on disk.")
    return FileResponse(path, media_type=allowed[artifact], filename=artifact)


@router.get("/outputs/{job_id}/files/tables/{table_filename}")
async def download_table(job_id: str, table_filename: str) -> FileResponse:
    _completed_job(job_id)
    path = job_output_dir(job_id) / "tables" / Path(table_filename).name
    if not path.exists():
        raise HTTPException(404, "Table file not found.")
    return FileResponse(path, media_type="text/csv", filename=table_filename)
