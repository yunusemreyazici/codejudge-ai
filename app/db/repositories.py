"""Focused append-only evaluation repository."""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.models import AdversarialResult, AIAssessment, AIProvenance, AIStatus, JudgeResult
from app.db.models import EvaluationRecord
from app.evaluator.models import (
    ComplexityMetrics,
    EvaluationStatus,
    Finding,
    ScoreBreakdown,
    TestResult,
)
from app.snapshots.models import (
    EvaluationSnapshot,
    EvaluationSummary,
    ExecutionEnvironmentSnapshot,
)

logger = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
    """A sanitized persistence infrastructure failure."""


class EvaluationRepository(Protocol):
    async def create(self, snapshot: EvaluationSnapshot) -> EvaluationSnapshot: ...

    async def get(self, evaluation_id: UUID) -> EvaluationSnapshot | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationSummary]: ...

    async def check_capability(self) -> bool: ...


class SqlAlchemyEvaluationRepository:
    """Persist one complete immutable snapshot in one transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, snapshot: EvaluationSnapshot) -> EvaluationSnapshot:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(evaluation_record_from_snapshot(snapshot))
        except SQLAlchemyError as error:
            logger.error(
                "evaluation persistence failed evaluation_id=%s task_id=%s error_type=%s",
                snapshot.evaluation_id,
                snapshot.task_id,
                type(error).__name__,
            )
            raise PersistenceError("Evaluation persistence is unavailable.") from error
        return snapshot

    async def get(self, evaluation_id: UUID) -> EvaluationSnapshot | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(EvaluationRecord, evaluation_id)
        except SQLAlchemyError as error:
            logger.error(
                "evaluation lookup failed evaluation_id=%s error_type=%s",
                evaluation_id,
                type(error).__name__,
            )
            raise PersistenceError("Evaluation persistence is unavailable.") from error
        return None if record is None else _snapshot_from_record(record)

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
        query = _summary_query()
        if task_id is not None:
            query = query.where(EvaluationRecord.task_id == task_id)
        if language is not None:
            query = query.where(EvaluationRecord.language == language)
        if minimum_score is not None:
            query = query.where(EvaluationRecord.final_score >= minimum_score)
        if maximum_score is not None:
            query = query.where(EvaluationRecord.final_score <= maximum_score)
        query = (
            query.order_by(
                EvaluationRecord.created_at.desc(),
                EvaluationRecord.evaluation_id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(query)).all()
        except SQLAlchemyError as error:
            logger.error("evaluation history query failed error_type=%s", type(error).__name__)
            raise PersistenceError("Evaluation persistence is unavailable.") from error
        return [
            EvaluationSummary(
                evaluation_id=row.evaluation_id,
                created_at=row.created_at,
                task_id=row.task_id,
                task_version=row.task_version,
                language=row.language,
                source_hash=row.source_hash,
                status=EvaluationStatus(row.status),
                score=row.final_score,
                score_breakdown=ScoreBreakdown(
                    correctness=row.correctness_score,
                    code_quality=row.code_quality_score,
                    type_safety=row.type_safety_score,
                    security=row.security_score,
                    complexity=row.complexity_score,
                ),
                ai_status=None if row.ai_status is None else AIStatus(row.ai_status),
                ai_score=row.ai_score,
            )
            for row in rows
        ]

    async def check_capability(self) -> bool:
        try:
            async with self._session_factory() as session:
                await session.execute(select(1))
        except SQLAlchemyError as error:
            logger.error("database capability check failed error_type=%s", type(error).__name__)
            return False
        return True


def _summary_query() -> Select[tuple[object, ...]]:
    return select(
        EvaluationRecord.evaluation_id,
        EvaluationRecord.created_at,
        EvaluationRecord.task_id,
        EvaluationRecord.task_version,
        EvaluationRecord.language,
        EvaluationRecord.source_hash,
        EvaluationRecord.status,
        EvaluationRecord.final_score,
        EvaluationRecord.correctness_score,
        EvaluationRecord.code_quality_score,
        EvaluationRecord.type_safety_score,
        EvaluationRecord.security_score,
        EvaluationRecord.complexity_score,
        EvaluationRecord.ai_status,
        EvaluationRecord.ai_score,
    )


def evaluation_record_from_snapshot(snapshot: EvaluationSnapshot) -> EvaluationRecord:
    breakdown = snapshot.score_breakdown
    ai = snapshot.ai_assessment
    return EvaluationRecord(
        evaluation_id=snapshot.evaluation_id,
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
        duration_seconds=snapshot.duration_seconds,
        task_id=snapshot.task_id,
        task_version=snapshot.task_version,
        task_fingerprint=snapshot.task_fingerprint,
        tests_fingerprint=snapshot.tests_fingerprint,
        language=snapshot.language,
        source_text=snapshot.source_text,
        source_hash=snapshot.source_hash,
        source_size=snapshot.source_size,
        status=snapshot.status,
        execution_backend=snapshot.execution.backend,
        sandbox_image=snapshot.execution.sandbox_image,
        sandbox_image_id=snapshot.execution.sandbox_image_id,
        codejudge_version=snapshot.codejudge_version,
        scoring_policy_version=snapshot.scoring_policy_version,
        analyzer_versions=snapshot.analyzer_versions,
        tests_passed=snapshot.tests.passed,
        tests_failed=snapshot.tests.failed,
        tests_total=snapshot.tests.total,
        test_duration_seconds=snapshot.tests.duration_seconds,
        timed_out=snapshot.tests.timed_out,
        oom_killed=snapshot.oom_killed,
        correctness_score=breakdown.correctness,
        code_quality_score=breakdown.code_quality,
        type_safety_score=breakdown.type_safety,
        security_score=breakdown.security,
        complexity_score=breakdown.complexity,
        final_score=snapshot.final_score,
        complexity=(
            None if snapshot.complexity is None else snapshot.complexity.model_dump(mode="json")
        ),
        execution_findings=[
            finding.model_dump(mode="json", exclude_none=True)
            for finding in snapshot.execution_findings
        ],
        analysis_findings=[
            finding.model_dump(mode="json", exclude_none=True)
            for finding in snapshot.analysis_findings
        ],
        reproducibility_fingerprint=snapshot.reproducibility_fingerprint,
        ai_status=None if ai is None else ai.status,
        ai_reason=None if ai is None else ai.reason,
        ai_score=None if ai is None else ai.ai_score,
        judge_score=None if ai is None else ai.judge_score,
        adversarial_robustness=(
            None
            if ai is None or ai.adversarial_tests is None
            else ai.adversarial_tests.robustness_score
        ),
        ai_reproducibility_fingerprint=(None if ai is None else ai.ai_reproducibility_fingerprint),
        ai_judge_results=(
            None
            if ai is None
            else {
                "disputed": ai.judge_disputed,
                "disagreement_spread": ai.judge_disagreement_spread,
                "results": [item.model_dump(mode="json") for item in ai.judge_results],
            }
        ),
        ai_adversarial_results=(
            None
            if ai is None or ai.adversarial_tests is None
            else ai.adversarial_tests.model_dump(mode="json")
        ),
        ai_provenance=None if ai is None else ai.provenance.model_dump(mode="json"),
    )


def _snapshot_from_record(record: EvaluationRecord) -> EvaluationSnapshot:
    ai_assessment = None
    if record.ai_status is not None:
        if record.ai_provenance is None or record.ai_reproducibility_fingerprint is None:
            raise PersistenceError("Stored AI assessment metadata is incomplete.")
        ai_assessment = AIAssessment(
            status=AIStatus(record.ai_status),
            reason=record.ai_reason,
            ai_score=record.ai_score,
            judge_score=record.judge_score,
            judge_disputed=_stored_disputed(record.ai_judge_results, record.ai_status),
            judge_disagreement_spread=_stored_disagreement(record.ai_judge_results),
            judge_results=[
                JudgeResult.model_validate(item)
                for item in _stored_judge_results(record.ai_judge_results)
            ],
            adversarial_tests=(
                None
                if record.ai_adversarial_results is None
                else AdversarialResult.model_validate(record.ai_adversarial_results)
            ),
            provenance=AIProvenance.model_validate(record.ai_provenance),
            ai_reproducibility_fingerprint=record.ai_reproducibility_fingerprint,
        )
    return EvaluationSnapshot(
        evaluation_id=record.evaluation_id,
        created_at=record.created_at,
        completed_at=record.completed_at,
        duration_seconds=record.duration_seconds,
        task_id=record.task_id,
        task_version=record.task_version,
        task_fingerprint=record.task_fingerprint,
        tests_fingerprint=record.tests_fingerprint,
        language=record.language,
        source_text=record.source_text,
        source_hash=record.source_hash,
        source_size=record.source_size,
        status=EvaluationStatus(record.status),
        execution=ExecutionEnvironmentSnapshot(
            backend=record.execution_backend,
            sandbox_image=record.sandbox_image,
            sandbox_image_id=record.sandbox_image_id,
        ),
        codejudge_version=record.codejudge_version,
        scoring_policy_version=record.scoring_policy_version,
        analyzer_versions=record.analyzer_versions,
        tests=TestResult(
            passed=record.tests_passed,
            failed=record.tests_failed,
            total=record.tests_total,
            duration_seconds=record.test_duration_seconds,
            timed_out=record.timed_out,
        ),
        oom_killed=record.oom_killed,
        score_breakdown=ScoreBreakdown(
            correctness=record.correctness_score,
            code_quality=record.code_quality_score,
            type_safety=record.type_safety_score,
            security=record.security_score,
            complexity=record.complexity_score,
        ),
        final_score=record.final_score,
        complexity=(
            None
            if record.complexity is None
            else ComplexityMetrics.model_validate(record.complexity)
        ),
        execution_findings=[Finding.model_validate(item) for item in record.execution_findings],
        analysis_findings=[Finding.model_validate(item) for item in record.analysis_findings],
        reproducibility_fingerprint=record.reproducibility_fingerprint,
        ai_assessment=ai_assessment,
    )


def _stored_judge_results(artifact: dict[str, object] | None) -> list[object]:
    if artifact is None:
        return []
    results = artifact.get("results")
    return results if isinstance(results, list) else []


def _stored_disputed(artifact: dict[str, object] | None, status: str) -> bool:
    if artifact is not None and isinstance(artifact.get("disputed"), bool):
        return bool(artifact["disputed"])
    return status == AIStatus.DISPUTED


def _stored_disagreement(artifact: dict[str, object] | None) -> float | None:
    if artifact is None:
        return None
    value = artifact.get("disagreement_spread")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
