"""Submission and polling service for durable asynchronous evaluations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.db.repositories import EvaluationRepository, PersistenceError
from app.evaluator.engine import EvaluationInfrastructureError
from app.evaluator.models import EvaluationRequest
from app.evaluator.service import EvaluationService
from app.jobs.integrity import request_fingerprint
from app.jobs.models import (
    EvaluationAccepted,
    EvaluationJob,
    EvaluationJobDetail,
    EvaluationJobSummary,
    JobStatus,
)
from app.jobs.repositories import EvaluationJobRepository
from app.snapshots.fingerprints import source_identity, task_fingerprint, tests_fingerprint
from app.snapshots.models import EvaluationDetail

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class EvaluationJobService:
    def __init__(
        self,
        evaluation_service: EvaluationService,
        job_repository: EvaluationJobRepository,
        evaluation_repository: EvaluationRepository,
        *,
        max_attempts: int,
        clock: Clock = utc_now,
    ) -> None:
        self._evaluation_service = evaluation_service
        self._jobs = job_repository
        self._evaluations = evaluation_repository
        self._max_attempts = max_attempts
        self._clock = clock

    async def submit(
        self, request: EvaluationRequest, idempotency_key: str | None
    ) -> EvaluationAccepted:
        task = self._evaluation_service.prepare_request(request)
        source_hash, source_size = source_identity(request.code)
        tests_hash = tests_fingerprint(task)
        task_hash = task_fingerprint(task, tests_hash)
        ai_identity = self._evaluation_service.ai_identity(task)
        (
            execution,
            analyzers,
            scoring_policy,
            application_version,
        ) = await self._evaluation_service.runtime_identity()
        now = self._clock()
        job = EvaluationJob(
            evaluation_id=uuid4(),
            created_at=now,
            updated_at=now,
            task_id=task.specification.id,
            task_version=task.specification.version,
            task_fingerprint=task_hash,
            tests_fingerprint=tests_hash,
            language=request.language,
            source_text=request.code,
            source_hash=source_hash,
            source_size=source_size,
            request_fingerprint=request_fingerprint(request),
            idempotency_key=idempotency_key,
            status=JobStatus.QUEUED,
            attempt_count=0,
            max_attempts=self._max_attempts,
            queued_at=now,
            expected_execution=execution,
            expected_analyzer_versions=analyzers,
            expected_scoring_policy_version=scoring_policy,
            expected_codejudge_version=application_version,
            expected_ai_identity=ai_identity,
        )
        try:
            stored, _ = await self._jobs.create(job)
        except PersistenceError as error:
            raise EvaluationInfrastructureError(
                "Evaluation job persistence is unavailable."
            ) from error
        return EvaluationAccepted(
            evaluation_id=stored.evaluation_id,
            status=stored.status,
            created_at=stored.created_at,
            status_url=f"/api/v1/evaluations/{stored.evaluation_id}",
        )

    async def get(self, evaluation_id: UUID) -> EvaluationDetail | EvaluationJobDetail | None:
        try:
            job = await self._jobs.get(evaluation_id)
            if job is None:
                legacy_snapshot = await self._evaluations.get(evaluation_id)
                return (
                    None
                    if legacy_snapshot is None
                    else EvaluationDetail.from_snapshot(legacy_snapshot)
                )
            if job.status is JobStatus.COMPLETED:
                snapshot = await self._evaluations.get(evaluation_id)
                if snapshot is None:
                    raise EvaluationInfrastructureError(
                        "Completed evaluation snapshot is unavailable."
                    )
                return EvaluationDetail.from_snapshot(snapshot)
            return EvaluationJobDetail.from_job(job)
        except PersistenceError as error:
            raise EvaluationInfrastructureError(
                "Evaluation job persistence is unavailable."
            ) from error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationJobSummary]:
        try:
            return await self._jobs.list(
                limit=limit,
                offset=offset,
                task_id=task_id,
                language=language,
                minimum_score=minimum_score,
                maximum_score=maximum_score,
            )
        except PersistenceError as error:
            raise EvaluationInfrastructureError(
                "Evaluation job persistence is unavailable."
            ) from error
