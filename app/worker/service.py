"""Idempotent evaluation message processing with leases and integrity checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.db.repositories import PersistenceError
from app.evaluator.engine import EvaluationInfrastructureError
from app.evaluator.models import EvaluationRequest
from app.evaluator.service import EvaluationService
from app.jobs.models import TERMINAL_JOB_STATUSES, EvaluationJob, JobStatus
from app.jobs.repositories import EvaluationJobRepository
from app.jobs.retry import EvaluationIntegrityError, classify_failure
from app.jobs.service import utc_now
from app.queue.redis_streams import EvaluationQueue, QueueMessage
from app.snapshots.fingerprints import source_identity, task_fingerprint, tests_fingerprint

logger = logging.getLogger(__name__)


class EvaluationWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        evaluation_service: EvaluationService,
        job_repository: EvaluationJobRepository,
        queue: EvaluationQueue,
        lease_seconds: float,
        retry_base_delay_seconds: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.worker_id = worker_id
        self._evaluation_service = evaluation_service
        self._jobs = job_repository
        self._queue = queue
        self._lease_seconds = lease_seconds
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._clock = clock

    async def process_message(self, message: QueueMessage) -> None:
        try:
            job = await self._jobs.get(message.evaluation_id)
        except PersistenceError:
            return
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            await self._queue.acknowledge(message.message_id)
            return
        if job.status not in {JobStatus.QUEUED, JobStatus.RETRY_WAIT}:
            await self._queue.acknowledge(message.message_id)
            return

        try:
            claimed = await self._jobs.claim(
                job.evaluation_id,
                self.worker_id,
                self._clock(),
                self._lease_seconds,
            )
        except PersistenceError:
            return
        if claimed is None:
            await self._queue.acknowledge(message.message_id)
            return

        logger.info(
            "job running evaluation_id=%s worker_id=%s attempt=%d",
            claimed.evaluation_id,
            self.worker_id,
            claimed.attempt_count,
        )
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._renew_lease(claimed.evaluation_id, stop_heartbeat)
        )
        transition_recorded = False
        try:
            request = await self._validated_request(claimed)
            snapshot = await self._evaluation_service.evaluate_snapshot(
                request,
                evaluation_id=claimed.evaluation_id,
                created_at=claimed.created_at,
                expected_ai_identity=claimed.expected_ai_identity,
            )
            transition_recorded = await self._jobs.complete(
                claimed.evaluation_id,
                self.worker_id,
                snapshot,
                self._clock(),
            )
            if transition_recorded:
                logger.info(
                    "job completed evaluation_id=%s worker_id=%s attempt=%d",
                    claimed.evaluation_id,
                    self.worker_id,
                    claimed.attempt_count,
                )
        except (EvaluationIntegrityError, EvaluationInfrastructureError) as error:
            decision = classify_failure(error)
            status = await self._jobs.record_failure(
                claimed.evaluation_id,
                self.worker_id,
                decision,
                self._clock(),
                self._retry_base_delay_seconds,
            )
            transition_recorded = status is not None
            logger.warning(
                "job failure evaluation_id=%s worker_id=%s attempt=%d retryable=%s "
                "code=%s status=%s",
                claimed.evaluation_id,
                self.worker_id,
                claimed.attempt_count,
                decision.retryable,
                decision.code,
                status,
            )
        except PersistenceError:
            transition_recorded = False
        except Exception as error:
            decision = classify_failure(error)
            status = await self._jobs.record_failure(
                claimed.evaluation_id,
                self.worker_id,
                decision,
                self._clock(),
                self._retry_base_delay_seconds,
            )
            transition_recorded = status is not None
            logger.error(
                "job failed safely evaluation_id=%s worker_id=%s attempt=%d "
                "error_type=%s status=%s",
                claimed.evaluation_id,
                self.worker_id,
                claimed.attempt_count,
                type(error).__name__,
                status,
            )
        finally:
            stop_heartbeat.set()
            await heartbeat_task

        if transition_recorded:
            await self._queue.acknowledge(message.message_id)

    async def recover_stale(self) -> int:
        return await self._jobs.recover_stale(self._clock(), self._retry_base_delay_seconds)

    async def _validated_request(self, job: EvaluationJob) -> EvaluationRequest:
        source_hash, source_size = source_identity(job.source_text)
        if source_hash != job.source_hash or source_size != job.source_size:
            raise EvaluationIntegrityError("source_identity_mismatch")
        request = EvaluationRequest(
            task_id=job.task_id,
            language=job.language,
            code=job.source_text,
        )
        task = self._evaluation_service.prepare_request(request)
        current_tests = tests_fingerprint(task)
        current_task = task_fingerprint(task, current_tests)
        if task.specification.version != job.task_version:
            raise EvaluationIntegrityError("task_version_mismatch")
        if current_tests != job.tests_fingerprint:
            raise EvaluationIntegrityError("tests_fingerprint_mismatch")
        if current_task != job.task_fingerprint:
            raise EvaluationIntegrityError("task_fingerprint_mismatch")
        (
            execution,
            analyzers,
            scoring_policy,
            application_version,
        ) = await self._evaluation_service.runtime_identity()
        if analyzers != job.expected_analyzer_versions:
            raise EvaluationIntegrityError("analyzer_versions_mismatch")
        if scoring_policy != job.expected_scoring_policy_version:
            raise EvaluationIntegrityError("scoring_policy_version_mismatch")
        if application_version != job.expected_codejudge_version:
            raise EvaluationIntegrityError("codejudge_version_mismatch")
        if execution != job.expected_execution:
            raise EvaluationIntegrityError("execution_environment_mismatch")
        return request

    async def _renew_lease(self, evaluation_id: UUID, stop: asyncio.Event) -> None:
        interval = max(0.1, self._lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                try:
                    renewed = await self._jobs.renew_lease(
                        evaluation_id,
                        self.worker_id,
                        self._clock(),
                        self._lease_seconds,
                    )
                except PersistenceError:
                    logger.warning(
                        "lease renewal deferred evaluation_id=%s worker_id=%s",
                        evaluation_id,
                        self.worker_id,
                    )
                    continue
                if not renewed:
                    return
