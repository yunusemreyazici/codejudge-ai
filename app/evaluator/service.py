"""Application service joining evaluation with optional immutable persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.version import codejudge_version
from app.db.repositories import EvaluationRepository, PersistenceError
from app.evaluator.engine import EvaluationEngine, EvaluationInfrastructureError
from app.evaluator.models import EvaluationRequest, EvaluationResult
from app.evaluator.scoring import SCORING_POLICY_VERSION
from app.snapshots.builder import build_evaluation_snapshot
from app.snapshots.metadata import (
    ExecutionMetadataProvider,
    canonical_analyzer_versions,
)
from app.snapshots.models import (
    EvaluationDetail,
    EvaluationSnapshot,
    EvaluationSummary,
    ExecutionEnvironmentSnapshot,
)
from app.tasks.registry import RegisteredTask


class EvaluationHistoryUnavailableError(RuntimeError):
    """History was requested while persistence is disabled."""


class EvaluationService:
    def __init__(
        self,
        engine: EvaluationEngine,
        execution_metadata: ExecutionMetadataProvider,
        repository: EvaluationRepository | None = None,
    ) -> None:
        self._engine = engine
        self._execution_metadata = execution_metadata
        self._repository = repository

    @property
    def persistence_configured(self) -> bool:
        return self._repository is not None

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        if self._repository is None:
            return await self._engine.evaluate(request)

        created_at = datetime.now(UTC)
        snapshot = await self.evaluate_snapshot(
            request,
            evaluation_id=uuid4(),
            created_at=created_at,
        )
        try:
            stored = await self._repository.create(snapshot)
        except PersistenceError as error:
            raise EvaluationInfrastructureError("Evaluation persistence is unavailable.") from error
        return EvaluationResult(
            evaluation_id=stored.evaluation_id,
            created_at=stored.created_at,
            task_id=stored.task_id,
            status=stored.status,
            score=stored.final_score,
            tests=stored.tests,
            score_breakdown=stored.score_breakdown,
            analysis=EvaluationDetail.from_snapshot(stored).analysis,
            findings=stored.execution_findings,
        )

    def prepare_request(self, request: EvaluationRequest) -> RegisteredTask:
        return self._engine.prepare_request(request)

    async def runtime_identity(
        self,
    ) -> tuple[ExecutionEnvironmentSnapshot, dict[str, str], str, str]:
        return (
            await self._execution_metadata.snapshot(),
            canonical_analyzer_versions() if self._engine.analysis_enabled else {},
            SCORING_POLICY_VERSION,
            codejudge_version(),
        )

    async def evaluate_snapshot(
        self,
        request: EvaluationRequest,
        *,
        evaluation_id: UUID,
        created_at: datetime,
    ) -> EvaluationSnapshot:
        outcome = await self._engine.evaluate_outcome(request)
        completed_at = datetime.now(UTC)
        execution = await self._execution_metadata.snapshot()
        versions = canonical_analyzer_versions() if outcome.result.analysis is not None else {}
        return build_evaluation_snapshot(
            request=request,
            task=outcome.task,
            result=outcome.result,
            runner_result=outcome.runner_result,
            created_at=created_at,
            completed_at=completed_at,
            execution=execution,
            analyzer_versions=versions,
            codejudge_version=codejudge_version(),
            scoring_policy_version=SCORING_POLICY_VERSION,
            evaluation_id=evaluation_id,
        )

    async def get(self, evaluation_id: UUID) -> EvaluationDetail | None:
        repository = self._required_repository()
        try:
            snapshot = await repository.get(evaluation_id)
        except PersistenceError as error:
            raise EvaluationInfrastructureError("Evaluation persistence is unavailable.") from error
        return None if snapshot is None else EvaluationDetail.from_snapshot(snapshot)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationSummary]:
        repository = self._required_repository()
        try:
            return await repository.list(
                limit=limit,
                offset=offset,
                task_id=task_id,
                language=language,
                minimum_score=minimum_score,
                maximum_score=maximum_score,
            )
        except PersistenceError as error:
            raise EvaluationInfrastructureError("Evaluation persistence is unavailable.") from error

    async def database_available(self) -> bool:
        if self._repository is None:
            return False
        return await self._repository.check_capability()

    def _required_repository(self) -> EvaluationRepository:
        if self._repository is None:
            raise EvaluationHistoryUnavailableError(
                "Evaluation history is unavailable because persistence is disabled."
            )
        return self._repository
