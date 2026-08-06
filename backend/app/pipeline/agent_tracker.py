"""Live per-agent activity tracking -- powers the agent status panel.

Deliberately not a context manager: several call sites already run each
step inside a try/except that swallows the error and records it as a
DomainResult warning rather than re-raising (a failed NER call shouldn't
crash the whole file). A context manager driven purely by exceptions would
mark those as "completed" since nothing propagates out. Explicit
start/finish calls let the caller report the real outcome either way.
"""
from app.models.schemas import AgentActivity, Job, _utc_now


def start_activity(job: Job | None, agent: str, file: str) -> AgentActivity | None:
    """job=None (e.g. unit-testing process_file in isolation) makes this a
    no-op rather than forcing every caller to guard against a missing job.
    """
    if job is None:
        return None
    activity = AgentActivity(agent=agent, file=file, status="running", started_at=_utc_now())
    job.agent_activity.append(activity)
    return activity


def finish_activity(activity: AgentActivity | None, status: str = "completed") -> None:
    if activity is None:
        return
    activity.status = status
    activity.finished_at = _utc_now()
    activity.duration_ms = int((activity.finished_at - activity.started_at).total_seconds() * 1000)
