"""Tests for storage/file_store.py's write_job_state -- previously zero
coverage. Guards against a race condition where the temp filename used
only os.getpid(), which is constant across every concurrent asyncio task
in this single-process app: simultaneously-processing files touching the
same job collided on the same temp path, and os.replace() failed with
FileNotFoundError for whichever write lost the race, silently dropping
that snapshot (confirmed live in production testing -- a job's entire
agent_activity history was lost on restart because it was never actually
reaching disk).
"""
import asyncio

import pytest

from app.models.schemas import AgentActivity, Job
from app.storage import file_store


@pytest.fixture(autouse=True)
def _redirect_storage_dirs(tmp_path, monkeypatch):
    """write_job_state resolves its target directory via get_settings() --
    point that at a throwaway tmp_path instead of the real ./data dirs."""
    settings = file_store.get_settings()

    class _FakeSettings:
        upload_dir = str(tmp_path / "uploads")
        output_dir = str(tmp_path / "outputs")

    monkeypatch.setattr(file_store, "get_settings", lambda: _FakeSettings())
    yield
    monkeypatch.setattr(file_store, "get_settings", lambda: settings)


def test_write_then_load_round_trip():
    job = Job(name="test job")
    file_store.write_job_state(job)

    loaded = file_store.load_all_job_states()

    assert len(loaded) == 1
    assert loaded[0].job_id == job.job_id
    assert loaded[0].name == "test job"


@pytest.mark.asyncio
async def test_concurrent_writes_to_the_same_job_never_raise():
    """The actual regression: many asyncio tasks calling write_job_state
    for the SAME job_id concurrently (exactly what touch() does from
    several files' start/end transitions firing close together) must
    never raise FileNotFoundError from a temp-path collision, and the
    on-disk state must always be the fully-written content of whichever
    write landed last -- never a truncated/missing file.
    """
    job = Job(name="race test")

    async def _touch(agent_name: str) -> None:
        job.agent_activity.append(
            AgentActivity(agent=agent_name, file="f.json", status="completed")
        )
        await asyncio.to_thread(file_store.write_job_state, job)

    # 20 concurrent writers to the same job -- the same order of magnitude
    # as MAX_PARALLEL_FILES(4) files each firing several touch() calls in
    # close succession.
    await asyncio.gather(*(_touch(f"agent-{i}") for i in range(20)))

    loaded = file_store.load_all_job_states()
    assert len(loaded) == 1
    assert loaded[0].job_id == job.job_id
    # Whichever write landed last, it must be a complete, valid,
    # parseable Job -- never a truncated/corrupt file (that's what
    # load_all_job_states silently drops, see its own docstring).


@pytest.mark.asyncio
async def test_concurrent_writes_across_different_jobs_all_survive():
    """Distinct job_ids write to distinct directories -- this should
    obviously never collide, but confirms concurrent writes to DIFFERENT
    jobs aren't affected by whatever locking/uniqueness fix guards the
    same-job case."""
    jobs = [Job(name=f"job {i}") for i in range(10)]

    await asyncio.gather(*(
        asyncio.to_thread(file_store.write_job_state, job) for job in jobs
    ))

    loaded = file_store.load_all_job_states()
    assert {j.job_id for j in loaded} == {j.job_id for j in jobs}
