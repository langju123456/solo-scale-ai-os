"""SQLAlchemy-backed repository using SQLite for BuildLog."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from buildlog.domain import (
    ArtifactRecord,
    EvaluationRecord,
    IterationRecord,
    ProjectRecord,
    PromptVersionRecord,
    RunRecord,
)
from buildlog.exceptions import PersistenceError
from buildlog.observability_models import (
    ArtifactDependency,
    ErrorObservation,
    LLMCallObservation,
    RunObservation,
    StepObservation,
)
from buildlog.persistence_models import (
    ArtifactTable,
    Base,
    EvaluationTable,
    IterationTable,
    ProjectTable,
    PromptVersionTable,
    RunTable,
)
from buildlog.sqlalchemy_observability_repository import (
    SQLAlchemyObservabilityRepository,
)
from buildlog.sqlalchemy_publishing_repository import (
    SQLAlchemyPublishingRepository,
)
from buildlog.publishing_models import (
    PublishReceipt,
    PublicationPlatform,
)


class SQLAlchemyRunRepository:
    """Persist BuildLog metadata through SQLAlchemy 2.0."""

    def __init__(self, database_url: str) -> None:
        database = make_url(database_url)
        if (
            database.get_backend_name() == "sqlite"
            and database.database not in (None, "", ":memory:")
        ):
            self.engine = create_engine(database_url, poolclass=NullPool)
        else:
            self.engine = create_engine(database_url)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._observability = SQLAlchemyObservabilityRepository(self.engine)
        self._publishing = SQLAlchemyPublishingRepository(self.engine)

    def initialize(self) -> None:
        """Create all current tables when they do not exist."""
        try:
            Base.metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not initialize database: {exc}") from exc

    def save_project(self, project: ProjectRecord) -> None:
        """Persist a project idempotently."""
        try:
            with self._sessions.begin() as session:
                row = session.get(ProjectTable, project.id)
                if row is None:
                    session.add(
                        ProjectTable(
                            id=project.id,
                            name=project.name,
                            description=project.description,
                            created_at=project.created_at,
                            updated_at=project.updated_at,
                        )
                    )
                else:
                    row.name = project.name
                    row.description = project.description
                    row.updated_at = project.updated_at
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not persist project: {exc}") from exc

    def save_iteration(self, iteration: IterationRecord) -> None:
        """Persist an iteration idempotently."""
        try:
            with self._sessions.begin() as session:
                row = session.get(IterationTable, iteration.id)
                if row is None:
                    session.add(
                        IterationTable(
                            id=iteration.id,
                            project_id=iteration.project_id,
                            title=iteration.title,
                            goal=iteration.goal,
                            context=iteration.context,
                            problem=iteration.problem,
                            audience=iteration.audience,
                            raw_input_json=iteration.raw_input,
                            created_at=iteration.created_at,
                        )
                    )
                else:
                    row.project_id = iteration.project_id
                    row.title = iteration.title
                    row.goal = iteration.goal
                    row.context = iteration.context
                    row.problem = iteration.problem
                    row.audience = iteration.audience
                    row.raw_input_json = iteration.raw_input
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not persist iteration: {exc}") from exc

    def save_prompt_version(self, prompt: PromptVersionRecord) -> None:
        """Persist prompt metadata idempotently."""
        try:
            with self._sessions.begin() as session:
                row = session.get(PromptVersionTable, prompt.id)
                if row is None:
                    session.add(
                        PromptVersionTable(
                            id=prompt.id,
                            prompt_name=prompt.prompt_name,
                            version=prompt.version,
                            file_path=prompt.file_path,
                            content_hash=prompt.content_hash,
                            created_at=prompt.created_at,
                        )
                    )
                else:
                    row.file_path = prompt.file_path
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not persist prompt version: {exc}") from exc

    def save_run(self, run: RunRecord) -> None:
        """Persist a new pipeline run."""
        row = RunTable(
            id=run.id,
            iteration_id=run.iteration_id,
            model=run.model,
            status=run.status,
            revision_performed=run.revision_performed,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_message=run.error_message,
            planner_prompt_version_id=run.planner_prompt_version_id,
            writer_prompt_version_id=run.writer_prompt_version_id,
            evaluator_prompt_version_id=run.evaluator_prompt_version_id,
            reviser_prompt_version_id=run.reviser_prompt_version_id,
        )
        self._add(row, "run")

    def save_artifact(self, artifact: ArtifactRecord) -> None:
        """Persist metadata for one filesystem artifact."""
        row = ArtifactTable(
            id=artifact.id,
            run_id=artifact.run_id,
            artifact_type=artifact.artifact_type,
            file_path=artifact.file_path,
            content_hash=artifact.content_hash,
            created_at=artifact.created_at,
        )
        self._add(row, "artifact")

    def save_evaluation(self, evaluation: EvaluationRecord) -> None:
        """Persist structured scores and feedback."""
        row = EvaluationTable(
            id=evaluation.id,
            run_id=evaluation.run_id,
            technical_accuracy=evaluation.technical_accuracy,
            specificity=evaluation.specificity,
            readability=evaluation.readability,
            reader_value=evaluation.reader_value,
            evidence_coverage=evaluation.evidence_coverage,
            feedback_json=evaluation.feedback,
            created_at=evaluation.created_at,
        )
        self._add(row, "evaluation")

    def complete_run(
        self,
        run_id: str,
        revision_performed: bool,
        completed_at: datetime,
    ) -> None:
        """Mark a run as completed."""
        self._update_run(
            run_id,
            status="completed",
            revision_performed=revision_performed,
            completed_at=completed_at,
            error_message=None,
        )

    def fail_run(self, run_id: str, error_message: str, completed_at: datetime) -> None:
        """Mark a run as failed."""
        self._update_run(
            run_id,
            status="failed",
            completed_at=completed_at,
            error_message=error_message,
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return one persisted run."""
        try:
            with self._sessions() as session:
                row = session.get(RunTable, run_id)
                return _run_record(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load run: {exc}") from exc

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """Return artifacts for one run in creation order."""
        statement = (
            select(ArtifactTable)
            .where(ArtifactTable.run_id == run_id)
            .order_by(ArtifactTable.created_at, ArtifactTable.artifact_type)
        )
        try:
            with self._sessions() as session:
                rows = session.scalars(statement).all()
                return [_artifact_record(row) for row in rows]
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load artifacts: {exc}") from exc

    def get_evaluation(self, run_id: str) -> EvaluationRecord | None:
        """Return the evaluation for one run."""
        statement = select(EvaluationTable).where(EvaluationTable.run_id == run_id)
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
                return _evaluation_record(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load evaluation: {exc}") from exc

    def save_observability_bundle(
        self,
        run: RunObservation,
        steps: list[StepObservation],
        llm_calls: list[LLMCallObservation],
        errors: list[ErrorObservation],
        artifact_dependencies: list[ArtifactDependency],
    ) -> None:
        """Persist one atomic query projection of observability metadata."""
        self._observability.save_observability_bundle(
            run,
            steps,
            llm_calls,
            errors,
            artifact_dependencies,
        )

    def get_run_observation(self, run_id: str) -> RunObservation | None:
        """Return one persisted run observation."""
        return self._observability.get_run_observation(run_id)

    def list_step_observations(self, run_id: str) -> list[StepObservation]:
        """Return fixed step observations in persisted order."""
        return self._observability.list_step_observations(run_id)

    def list_llm_call_observations(self, run_id: str) -> list[LLMCallObservation]:
        """Return LLM calls in call-id order."""
        return self._observability.list_llm_call_observations(run_id)

    def list_error_observations(self, run_id: str) -> list[ErrorObservation]:
        """Return structured errors in occurrence order."""
        return self._observability.list_error_observations(run_id)

    def list_artifact_dependencies(self, run_id: str) -> list[ArtifactDependency]:
        """Return direct artifact dependencies in id order."""
        return self._observability.list_artifact_dependencies(run_id)

    def save_publish_receipt(self, receipt: PublishReceipt) -> None:
        """Persist one downstream publication receipt."""
        self._publishing.save_publish_receipt(receipt)

    def get_publish_receipt(self, receipt_id: str) -> PublishReceipt | None:
        """Return one publication receipt."""
        return self._publishing.get_publish_receipt(receipt_id)

    def find_successful_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest matching successful publication."""
        return self._publishing.find_successful_publication(
            platform=platform,
            account_reference=account_reference,
            content_hash=content_hash,
        )

    def find_indeterminate_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest matching unresolved publication attempt."""
        return self._publishing.find_indeterminate_publication(
            platform=platform,
            account_reference=account_reference,
            content_hash=content_hash,
        )

    def _add(self, row: object, label: str) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not persist {label}: {exc}") from exc

    def _update_run(self, run_id: str, **values: object) -> None:
        statement = update(RunTable).where(RunTable.id == run_id).values(**values)
        try:
            with self._sessions.begin() as session:
                result = session.execute(statement)
                if result.rowcount != 1:
                    raise PersistenceError(f"run does not exist: {run_id}")
        except PersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not update run: {exc}") from exc


def _enable_sqlite_foreign_keys(
    connection: sqlite3.Connection,
    _connection_record: object,
) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _run_record(row: RunTable) -> RunRecord:
    return RunRecord(
        id=row.id,
        iteration_id=row.iteration_id,
        model=row.model,
        planner_prompt_version_id=row.planner_prompt_version_id,
        writer_prompt_version_id=row.writer_prompt_version_id,
        evaluator_prompt_version_id=row.evaluator_prompt_version_id,
        reviser_prompt_version_id=row.reviser_prompt_version_id,
        status=row.status,
        revision_performed=row.revision_performed,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_message=row.error_message,
    )


def _artifact_record(row: ArtifactTable) -> ArtifactRecord:
    return ArtifactRecord(
        id=row.id,
        run_id=row.run_id,
        artifact_type=row.artifact_type,
        file_path=row.file_path,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


def _evaluation_record(row: EvaluationTable) -> EvaluationRecord:
    return EvaluationRecord(
        id=row.id,
        run_id=row.run_id,
        technical_accuracy=row.technical_accuracy,
        specificity=row.specificity,
        readability=row.readability,
        reader_value=row.reader_value,
        evidence_coverage=row.evidence_coverage,
        feedback=row.feedback_json,
        created_at=row.created_at,
    )
