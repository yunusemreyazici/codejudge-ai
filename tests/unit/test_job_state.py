import pytest

from app.jobs.models import JobStatus
from app.jobs.state import InvalidJobTransitionError, ensure_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.QUEUED, JobStatus.RUNNING),
        (JobStatus.RUNNING, JobStatus.COMPLETED),
        (JobStatus.RUNNING, JobStatus.RETRY_WAIT),
        (JobStatus.RETRY_WAIT, JobStatus.QUEUED),
        (JobStatus.RUNNING, JobStatus.FAILED),
    ],
)
def test_legal_job_transitions(current: JobStatus, target: JobStatus) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.COMPLETED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.QUEUED),
        (JobStatus.QUEUED, JobStatus.COMPLETED),
    ],
)
def test_illegal_or_terminal_job_transitions_are_rejected(
    current: JobStatus, target: JobStatus
) -> None:
    with pytest.raises(InvalidJobTransitionError):
        ensure_transition(current, target)
