"""Legal evaluation-job lifecycle transitions."""

from app.jobs.models import JobStatus

_TRANSITIONS = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.RETRY_WAIT, JobStatus.FAILED}),
    JobStatus.RETRY_WAIT: frozenset({JobStatus.QUEUED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class InvalidJobTransitionError(ValueError):
    pass


def ensure_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _TRANSITIONS[current]:
        raise InvalidJobTransitionError(f"Illegal job transition: {current} -> {target}")
