from fastapi import APIRouter, HTTPException

from app.graph.graphrag import answer_question
from app.models.schemas import ChatRequest, ChatResponse, JobStatus
from app.pipeline.job_manager import get_job_manager

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    job = get_job_manager().get_job(request.job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.status not in (JobStatus.COMPLETE, JobStatus.SYNTHESIZING, JobStatus.GRAPH_BUILD):
        raise HTTPException(409, "Job hasn't produced any indexed content yet.")
    return await answer_question(request.job_id, request.message, request.history)
