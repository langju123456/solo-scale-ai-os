"""Queryable projections and durable job persistence for the web product."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from buildlog.persistence_models import (
    ArtifactTable,
    EvaluationTable,
    IterationTable,
    PublishReceiptTable,
    RunObservationTable,
    RunTable,
    WorkflowJobTable,
)
from buildlog.web_models import DashboardMetrics, RunDetail, RunSummary, WorkflowJob


class WebPersistenceError(RuntimeError):
    """Raised when a web read model or job transition cannot be persisted."""


class IdempotencyConflict(WebPersistenceError):
    """Raised when one idempotency key is reused for a different payload."""


@dataclass(frozen=True)
class JobCreation:
    """Result of creating or replaying an idempotent workflow request."""

    job: WorkflowJob
    created: bool


class SQLAlchemyWebRepository:
    """Read models and durable job lifecycle backed by the product database."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def ping(self) -> None:
        """Prove that the configured database accepts a minimal query."""
        try:
            with self.engine.connect() as connection:
                connection.execute(select(1))
        except SQLAlchemyError as exc:
            raise WebPersistenceError("database readiness check failed") from exc

    def dashboard(self) -> DashboardMetrics:
        """Aggregate product usage, quality, delivery, and latency evidence."""
        try:
            with self._sessions() as session:
                run_statuses = dict(
                    session.execute(
                        select(RunTable.status, func.count(RunTable.id)).group_by(
                            RunTable.status
                        )
                    ).all()
                )
                job_statuses = dict(
                    session.execute(
                        select(
                            WorkflowJobTable.status,
                            func.count(WorkflowJobTable.id),
                        ).group_by(WorkflowJobTable.status)
                    ).all()
                )
                total_artifacts = session.scalar(select(func.count(ArtifactTable.id))) or 0
                evaluations = session.scalars(select(EvaluationTable)).all()
                live_publications = (
                    session.scalar(
                        select(func.count(PublishReceiptTable.id)).where(
                            PublishReceiptTable.status == "succeeded"
                        )
                    )
                    or 0
                )
                latencies = list(
                    session.scalars(
                        select(RunObservationTable.duration_ms).where(
                            RunObservationTable.pipeline_status == "completed"
                        )
                    ).all()
                )
                recorded_tokens = (
                    session.scalar(
                        select(func.sum(RunObservationTable.total_tokens)).where(
                            RunObservationTable.total_tokens.is_not(None)
                        )
                    )
                    or 0
                )
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not build dashboard metrics") from exc

        total_runs = sum(run_statuses.values())
        completed_runs = run_statuses.get("completed", 0)
        all_scores = [
            score
            for row in evaluations
            for score in (
                row.technical_accuracy,
                row.specificity,
                row.readability,
                row.reader_value,
                row.evidence_coverage,
            )
        ]
        return DashboardMetrics(
            total_runs=total_runs,
            completed_runs=completed_runs,
            failed_runs=run_statuses.get("failed", 0),
            completion_rate=round(completed_runs / total_runs * 100, 1)
            if total_runs
            else 0.0,
            total_artifacts=int(total_artifacts),
            evaluated_runs=len(evaluations),
            average_evaluation_score=round(sum(all_scores) / len(all_scores), 2)
            if all_scores
            else None,
            live_publications=int(live_publications),
            queued_jobs=job_statuses.get("queued", 0),
            running_jobs=job_statuses.get("running", 0),
            failed_jobs=job_statuses.get("failed", 0),
            observed_runs=len(latencies),
            p50_pipeline_latency_ms=_percentile(latencies, 50),
            p95_pipeline_latency_ms=_percentile(latencies, 95),
            recorded_tokens=int(recorded_tokens),
        )

    def list_runs(self, *, limit: int = 50, status: str | None = None) -> list[RunSummary]:
        """Return recent generation and publishing runs newest first."""
        statement = select(RunTable).order_by(RunTable.started_at.desc()).limit(limit)
        if status:
            statement = statement.where(RunTable.status == status)
        try:
            with self._sessions() as session:
                rows = session.scalars(statement).all()
                return [self._run_summary(session, row) for row in rows]
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not list runs") from exc

    def get_run(self, run_id: str) -> RunDetail | None:
        """Return one run with artifact and evaluation details."""
        try:
            with self._sessions() as session:
                row = session.get(RunTable, run_id)
                if row is None:
                    return None
                summary = self._run_summary(session, row)
                artifacts = session.scalars(
                    select(ArtifactTable)
                    .where(ArtifactTable.run_id == run_id)
                    .order_by(ArtifactTable.created_at)
                ).all()
                evaluation = session.scalar(
                    select(EvaluationTable).where(EvaluationTable.run_id == run_id)
                )
                scores = _evaluation_scores(evaluation) if evaluation else None
                return RunDetail(
                    **summary.model_dump(),
                    artifact_types=[artifact.artifact_type for artifact in artifacts],
                    evaluation_scores=scores,
                    error_message=row.error_message,
                )
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not load run") from exc

    def create_job(
        self,
        *,
        input_payload: dict[str, object],
        idempotency_key: str,
    ) -> JobCreation:
        """Create one durable request or replay the existing equivalent request."""
        canonical = json.dumps(input_payload, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        row = WorkflowJobTable(
            id=str(uuid4()),
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            input_json=input_payload,
            status="queued",
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                existing = session.scalar(
                    select(WorkflowJobTable).where(
                        WorkflowJobTable.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return self._replayed_job(existing, input_hash)
                session.add(row)
            return JobCreation(_job_model(row), True)
        except IntegrityError:
            with self._sessions() as session:
                existing = session.scalar(
                    select(WorkflowJobTable).where(
                        WorkflowJobTable.idempotency_key == idempotency_key
                    )
                )
                if existing is None:
                    raise WebPersistenceError("job idempotency race could not be resolved")
                return self._replayed_job(existing, input_hash)
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not create workflow job") from exc

    def list_jobs(self, *, limit: int = 50) -> list[WorkflowJob]:
        """Return recent durable workflow requests newest first."""
        try:
            with self._sessions() as session:
                rows = session.scalars(
                    select(WorkflowJobTable)
                    .order_by(WorkflowJobTable.created_at.desc())
                    .limit(limit)
                ).all()
                return [_job_model(row) for row in rows]
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not list workflow jobs") from exc

    def get_job(self, job_id: str) -> WorkflowJob | None:
        """Return one durable workflow request."""
        try:
            with self._sessions() as session:
                row = session.get(WorkflowJobTable, job_id)
                return _job_model(row) if row else None
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not load workflow job") from exc

    def claim_next_job(self) -> tuple[WorkflowJob, dict[str, object]] | None:
        """Claim the oldest queued job for one worker process."""
        try:
            with self._sessions.begin() as session:
                statement = (
                    select(WorkflowJobTable)
                    .where(WorkflowJobTable.status == "queued")
                    .order_by(WorkflowJobTable.created_at)
                    .limit(1)
                )
                if self.engine.dialect.name != "sqlite":
                    statement = statement.with_for_update(skip_locked=True)
                row = session.scalar(statement)
                if row is None:
                    return None
                now = datetime.now(UTC)
                row.status = "running"
                row.attempt_count += 1
                row.started_at = now
                row.updated_at = now
                row.completed_at = None
                return _job_model(row), dict(row.input_json)
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not claim workflow job") from exc

    def complete_job(self, job_id: str, run_id: str) -> None:
        """Finish one job and attach the generated run identity."""
        self._transition_job(
            job_id,
            status="succeeded",
            run_id=run_id,
            error_category=None,
            safe_error_message=None,
            completed_at=datetime.now(UTC),
        )

    def fail_job(
        self,
        job_id: str,
        *,
        error_category: str,
        safe_error_message: str,
        max_attempts: int,
        run_id: str | None = None,
        retryable: bool = True,
    ) -> str:
        """Retry a generation job within policy, otherwise mark it failed."""
        try:
            with self._sessions.begin() as session:
                row = session.get(WorkflowJobTable, job_id)
                if row is None:
                    raise WebPersistenceError(f"workflow job does not exist: {job_id}")
                retry = retryable and row.attempt_count < max_attempts
                now = datetime.now(UTC)
                row.status = "queued" if retry else "failed"
                if run_id is not None:
                    row.run_id = run_id
                row.error_category = error_category
                row.safe_error_message = safe_error_message
                row.updated_at = now
                row.completed_at = None if retry else now
                return row.status
        except WebPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not fail workflow job") from exc

    def recover_stale_jobs(self, stale_after_seconds: int, max_attempts: int) -> int:
        """Recover jobs abandoned by a stopped worker without unbounded retries."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        recovered = 0
        try:
            with self._sessions.begin() as session:
                rows = session.scalars(
                    select(WorkflowJobTable).where(
                        WorkflowJobTable.status == "running",
                        WorkflowJobTable.started_at < cutoff,
                    )
                ).all()
                now = datetime.now(UTC)
                for row in rows:
                    row.status = "queued" if row.attempt_count < max_attempts else "failed"
                    row.error_category = "stale_worker"
                    row.safe_error_message = "worker stopped before completion"
                    row.updated_at = now
                    row.completed_at = now if row.status == "failed" else None
                    recovered += 1
            return recovered
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not recover stale workflow jobs") from exc

    def _run_summary(self, session, row: RunTable) -> RunSummary:
        iteration = session.get(IterationTable, row.iteration_id)
        artifact_count = session.scalar(
            select(func.count(ArtifactTable.id)).where(ArtifactTable.run_id == row.id)
        ) or 0
        evaluation = session.scalar(
            select(EvaluationTable).where(EvaluationTable.run_id == row.id)
        )
        observation = session.get(RunObservationTable, row.id)
        scores = list(_evaluation_scores(evaluation).values()) if evaluation else []
        return RunSummary(
            id=row.id,
            iteration_id=row.iteration_id,
            title=iteration.title if iteration else row.iteration_id,
            model=row.model,
            status=row.status,
            started_at=_as_utc(row.started_at),
            completed_at=_as_utc(row.completed_at) if row.completed_at else None,
            duration_ms=observation.duration_ms if observation else _duration_ms(row),
            artifact_count=int(artifact_count),
            average_evaluation_score=round(sum(scores) / len(scores), 2)
            if scores
            else None,
            revision_performed=row.revision_performed,
        )

    def _replayed_job(self, row: WorkflowJobTable, input_hash: str) -> JobCreation:
        if row.input_hash != input_hash:
            raise IdempotencyConflict(
                "idempotency key already belongs to a different request payload"
            )
        return JobCreation(_job_model(row), False)

    def _transition_job(self, job_id: str, **values: object) -> None:
        values["updated_at"] = datetime.now(UTC)
        statement = (
            update(WorkflowJobTable)
            .where(WorkflowJobTable.id == job_id)
            .values(**values)
        )
        try:
            with self._sessions.begin() as session:
                result = session.execute(statement)
                if result.rowcount != 1:
                    raise WebPersistenceError(f"workflow job does not exist: {job_id}")
        except WebPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise WebPersistenceError("could not transition workflow job") from exc


def _evaluation_scores(row: EvaluationTable) -> dict[str, int]:
    return {
        "technical_accuracy": row.technical_accuracy,
        "specificity": row.specificity,
        "readability": row.readability,
        "reader_value": row.reader_value,
        "evidence_coverage": row.evidence_coverage,
    }


def _duration_ms(row: RunTable) -> int | None:
    if row.completed_at is None:
        return None
    started = _as_utc(row.started_at)
    completed = _as_utc(row.completed_at)
    return max(0, int((completed - started).total_seconds() * 1000))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job_model(row: WorkflowJobTable) -> WorkflowJob:
    return WorkflowJob(
        id=row.id,
        idempotency_key=row.idempotency_key,
        input_hash=row.input_hash,
        status=row.status,
        attempt_count=row.attempt_count,
        run_id=row.run_id,
        error_category=row.error_category,
        safe_error_message=row.safe_error_message,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        started_at=_as_utc(row.started_at) if row.started_at else None,
        completed_at=_as_utc(row.completed_at) if row.completed_at else None,
    )


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if percentile == 50:
        return int(median(ordered))
    rank = (len(ordered) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return int(ordered[lower] * (1 - weight) + ordered[upper] * weight)
