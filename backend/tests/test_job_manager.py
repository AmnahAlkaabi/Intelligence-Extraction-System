"""Tests for pipeline/job_manager.py -- previously zero coverage.
Covers two fixes:

1. cancel_job(): there was previously no way to stop a job once it
   started -- delete_job() refuses while active, and the only existing
   stop mechanism only worked for large jobs paused at
   AWAITING_BATCH_CONFIRM. cancel_job() calls asyncio.Task.cancel() on
   the job's running task; _guarded() must catch the resulting
   CancelledError and record a clean terminal state (FAILED, every
   unfinished file SKIPPED) rather than leaving the job stuck.

2. progress_pct: (a) previously only counted a file once its ENTIRE
   multi-stage pipeline finished, sitting frozen at 0% for however long
   the first file took despite real progress happening the whole time --
   now gives partial credit per file based on stages cleared. (b)
   completed jobs were stuck at 90% instead of 100% -- _finalize_job's
   explicit 100.0 assignment was immediately clobbered by the touch()
   call right after it.
"""
import asyncio

import pytest

from app.models.schemas import AgentActivity, FileProgress, Job, JobStatus
from app.pipeline.job_manager import JobManager, _compute_progress_pct


# ------------------------------------------------------------- progress_pct --

def test_progress_pct_gives_partial_credit_mid_pipeline():
    """Previously: 0% until the first file's ENTIRE pipeline finished,
    no matter how much real work had already happened."""
    job = Job(files=[
        FileProgress(filename="a.json", status=JobStatus.EXTRACTING),
        FileProgress(filename="b.json", status=JobStatus.QUEUED),
    ])
    # a.json has cleared 3 of up to 7 tracked stages.
    job.agent_activity = [
        AgentActivity(agent="JSON Specialist", file="a.json", status="completed"),
        AgentActivity(agent="Translator", file="a.json", status="completed"),
        AgentActivity(agent="Chunk/Embed Extractor", file="a.json", status="completed"),
    ]

    pct = _compute_progress_pct(job)

    assert pct > 0.0  # previously would have been exactly 0.0


def test_progress_pct_in_progress_file_never_reaches_a_whole_files_credit():
    """An in-progress file's partial credit must always be strictly less
    than what a fully-done file contributes, so it can never be mistaken
    for "done" -- and once the file actually finishes, `done` picks it up
    for a full slot instead."""
    almost_done = Job(files=[FileProgress(filename="a.json", status=JobStatus.EXTRACTING)])
    almost_done.agent_activity = [
        AgentActivity(agent=f"stage-{i}", file="a.json", status="completed") for i in range(6)
    ]
    fully_done = Job(files=[FileProgress(filename="a.json", status=JobStatus.COMPLETE)])

    assert _compute_progress_pct(almost_done) < _compute_progress_pct(fully_done)


def test_progress_pct_is_100_when_job_complete():
    """_finalize_job sets progress_pct = 100.0 on completion, but the
    touch() call immediately following it used to clobber that back down
    to 90.0 (the file-count-based formula always caps at 90%, reserving
    10% for synthesis, regardless of whether synthesis already ran)."""
    job = Job(
        status=JobStatus.COMPLETE,
        files=[FileProgress(filename="a.json", status=JobStatus.COMPLETE)],
    )

    assert _compute_progress_pct(job) == 100.0


def test_progress_pct_caps_at_90_percent_before_synthesis():
    job = Job(
        status=JobStatus.SYNTHESIZING,
        files=[FileProgress(filename="a.json", status=JobStatus.COMPLETE)],
    )

    assert _compute_progress_pct(job) == 90.0


# --------------------------------------------------------------- cancel_job --

@pytest.mark.asyncio
async def test_cancel_job_marks_failed_and_skips_unfinished_files():
    manager = JobManager()
    job = Job(status=JobStatus.EXTRACTING, files=[
        FileProgress(filename="a.json", status=JobStatus.EXTRACTING),
        FileProgress(filename="b.json", status=JobStatus.QUEUED),
    ])
    manager._jobs[job.job_id] = job

    async def _never_finishes():
        await asyncio.sleep(100)

    task = asyncio.create_task(manager._guarded(job.job_id, _never_finishes()))
    manager._tasks[job.job_id] = task
    # Let the task actually start and reach its first real suspension
    # point (asyncio.sleep(100), like a real in-flight LLM call or file
    # I/O would be) before cancelling -- cancelling a task before the
    # event loop has run it even once bypasses _guarded's own try/except
    # entirely (a real job is always already deep in execution when a
    # user cancels it, never at this instant).
    await asyncio.sleep(0)

    cancelled = await manager.cancel_job(job.job_id)

    assert cancelled is True
    assert job.status == JobStatus.FAILED
    assert job.error == "Job cancelled by user request."
    assert all(f.status == JobStatus.SKIPPED for f in job.files)
    assert task.done()


@pytest.mark.asyncio
async def test_cancel_job_returns_false_for_unknown_job():
    manager = JobManager()

    assert await manager.cancel_job("does-not-exist") is False


@pytest.mark.asyncio
async def test_cancel_job_rejects_a_job_that_is_not_active():
    manager = JobManager()
    job = Job(status=JobStatus.COMPLETE)
    manager._jobs[job.job_id] = job

    with pytest.raises(ValueError):
        await manager.cancel_job(job.job_id)


@pytest.mark.asyncio
async def test_cancelling_does_not_leave_an_unhandled_task_exception():
    """A cancelled task's CancelledError must be fully absorbed by
    _guarded() -- awaiting the task afterward must not re-raise."""
    manager = JobManager()
    job = Job(status=JobStatus.EXTRACTING, files=[])
    manager._jobs[job.job_id] = job

    async def _never_finishes():
        await asyncio.sleep(100)

    task = asyncio.create_task(manager._guarded(job.job_id, _never_finishes()))
    manager._tasks[job.job_id] = task
    await asyncio.sleep(0)  # let the task actually start first -- see comment above

    await manager.cancel_job(job.job_id)

    await task  # must not raise
