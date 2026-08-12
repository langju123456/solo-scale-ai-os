"""SQLAlchemy projection for queryable observability metadata."""

from __future__ import annotations

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from buildlog.exceptions import PersistenceError
from buildlog.observability_models import (
    ArtifactDependency,
    ErrorObservation,
    LLMCallObservation,
    RunObservation,
    StepObservation,
)
from buildlog.persistence_models import (
    ArtifactDependencyTable,
    ErrorObservationTable,
    LLMCallObservationTable,
    RunObservationTable,
    StepObservationTable,
)


class SQLAlchemyObservabilityRepository:
    """Persist and query SQLite observability projections."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def save_observability_bundle(
        self,
        run: RunObservation,
        steps: list[StepObservation],
        llm_calls: list[LLMCallObservation],
        errors: list[ErrorObservation],
        artifact_dependencies: list[ArtifactDependency],
    ) -> None:
        """Persist one atomic query projection of observability metadata."""
        try:
            with self._sessions.begin() as session:
                _delete_observability_rows(session, run.run_id)
                session.add(_run_observation_table(run))
                session.add_all([_step_observation_table(step) for step in steps])
                session.flush()
                session.add_all([_llm_call_observation_table(call) for call in llm_calls])
                session.flush()
                session.add_all([_error_observation_table(error) for error in errors])
                session.add_all(
                    [
                        _artifact_dependency_table(dependency)
                        for dependency in artifact_dependencies
                    ]
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(
                f"could not persist observability bundle: {exc}"
            ) from exc

    def get_run_observation(self, run_id: str) -> RunObservation | None:
        """Return one persisted run observation."""
        try:
            with self._sessions() as session:
                row = session.get(RunObservationTable, run_id)
                return _run_observation(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load run observation: {exc}") from exc

    def list_step_observations(self, run_id: str) -> list[StepObservation]:
        """Return fixed step observations in pipeline order."""
        statement = (
            select(StepObservationTable)
            .where(StepObservationTable.run_id == run_id)
            .order_by(StepObservationTable.sequence)
        )
        try:
            with self._sessions() as session:
                return [
                    _step_observation(row)
                    for row in session.scalars(statement).all()
                ]
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load step observations: {exc}") from exc

    def list_llm_call_observations(self, run_id: str) -> list[LLMCallObservation]:
        """Return LLM calls in call-id order."""
        statement = (
            select(LLMCallObservationTable)
            .where(LLMCallObservationTable.run_id == run_id)
            .order_by(LLMCallObservationTable.id)
        )
        try:
            with self._sessions() as session:
                return [
                    _llm_call_observation(row)
                    for row in session.scalars(statement).all()
                ]
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load LLM observations: {exc}") from exc

    def list_error_observations(self, run_id: str) -> list[ErrorObservation]:
        """Return structured errors in occurrence order."""
        statement = (
            select(ErrorObservationTable)
            .where(ErrorObservationTable.run_id == run_id)
            .order_by(ErrorObservationTable.occurred_at, ErrorObservationTable.id)
        )
        try:
            with self._sessions() as session:
                return [
                    _error_observation(row)
                    for row in session.scalars(statement).all()
                ]
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load error observations: {exc}") from exc

    def list_artifact_dependencies(self, run_id: str) -> list[ArtifactDependency]:
        """Return direct artifact dependencies in id order."""
        statement = (
            select(ArtifactDependencyTable)
            .where(ArtifactDependencyTable.run_id == run_id)
            .order_by(ArtifactDependencyTable.id)
        )
        try:
            with self._sessions() as session:
                return [
                    _artifact_dependency(row)
                    for row in session.scalars(statement).all()
                ]
        except SQLAlchemyError as exc:
            raise PersistenceError(f"could not load artifact dependencies: {exc}") from exc


def _delete_observability_rows(session: Session, run_id: str) -> None:
    session.execute(
        delete(ArtifactDependencyTable).where(
            ArtifactDependencyTable.run_id == run_id
        )
    )
    session.execute(
        delete(ErrorObservationTable).where(ErrorObservationTable.run_id == run_id)
    )
    session.execute(
        delete(LLMCallObservationTable).where(
            LLMCallObservationTable.run_id == run_id
        )
    )
    session.execute(
        delete(StepObservationTable).where(StepObservationTable.run_id == run_id)
    )
    session.execute(
        delete(RunObservationTable).where(RunObservationTable.run_id == run_id)
    )


def _run_observation_table(run: RunObservation) -> RunObservationTable:
    return RunObservationTable(
        run_id=run.run_id,
        iteration_id=run.iteration_id,
        output_type=run.output_type,
        pipeline_status=run.pipeline_status.value,
        observability_status=run.observability_status.value,
        reproducibility_status=run.reproducibility_status.value,
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_ms=run.duration_ms,
        provider=run.provider,
        model=run.model,
        model_digest=run.model_digest,
        temperature=run.temperature,
        max_tokens=run.max_tokens,
        prompt_versions_json=run.prompt_versions,
        prompt_file_hashes_json=run.prompt_file_hashes,
        configuration_fingerprint=run.configuration_fingerprint,
        git_commit=run.git_commit,
        git_branch=run.git_branch,
        working_tree_dirty=run.working_tree_dirty,
        llm_call_count=run.llm_call_count,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        slowest_step=run.slowest_step,
        highest_token_step=run.highest_token_step,
        revision_performed=run.revision_performed,
        revision_decision_json=(
            run.revision_decision.model_dump(mode="json")
            if run.revision_decision
            else None
        ),
        revision_output_changed=run.revision_output_changed,
        revision_improvement_status=run.revision_improvement_status.value,
        reproducibility_json=run.reproducibility.model_dump(mode="json"),
        observability_issues_json=run.observability_issues,
    )


def _step_observation_table(step: StepObservation) -> StepObservationTable:
    return StepObservationTable(
        id=step.id,
        run_id=step.run_id,
        sequence=step.sequence,
        step_name=step.step_name,
        status=step.status.value,
        started_at=step.started_at,
        ended_at=step.ended_at,
        duration_ms=step.duration_ms,
        attempt_count=step.attempt_count,
        skip_reason=step.skip_reason,
        timing_mode=step.timing_mode,
    )


def _llm_call_observation_table(
    call: LLMCallObservation,
) -> LLMCallObservationTable:
    return LLMCallObservationTable(
        id=call.id,
        run_id=call.run_id,
        step_id=call.step_id,
        step_name=call.step_name,
        status=call.status,
        provider=call.provider,
        model=call.model,
        model_digest=call.model_digest,
        temperature=call.temperature,
        max_tokens=call.max_tokens,
        prompt_file_hash=call.prompt_file_hash,
        rendered_prompt_hash=call.rendered_prompt_hash,
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        total_tokens=call.total_tokens,
        token_usage_status=call.token_usage_status.value,
        token_usage_source=call.token_usage_source,
        finish_reason=call.finish_reason,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration_ms=call.duration_ms,
        attempt=call.attempt,
        error_category=call.error_category.value if call.error_category else None,
        error_code=call.error_code,
    )


def _error_observation_table(error: ErrorObservation) -> ErrorObservationTable:
    return ErrorObservationTable(
        id=error.id,
        run_id=error.run_id,
        step_name=error.step_name,
        llm_call_id=error.llm_call_id,
        error_category=error.error_category.value,
        error_code=error.error_code,
        exception_type=error.exception_type,
        attempt=error.attempt,
        occurred_at=error.occurred_at,
        is_terminal=error.is_terminal,
        sanitized_message=error.sanitized_message,
    )


def _artifact_dependency_table(
    dependency: ArtifactDependency,
) -> ArtifactDependencyTable:
    return ArtifactDependencyTable(
        id=dependency.id,
        run_id=dependency.run_id,
        artifact_id=dependency.artifact_id,
        producer_step_name=dependency.producer_step_name,
        source_artifact_id=dependency.source_artifact_id,
    )


def _run_observation(row: RunObservationTable) -> RunObservation:
    return RunObservation.model_validate(
        {
            "run_id": row.run_id,
            "iteration_id": row.iteration_id,
            "output_type": row.output_type,
            "pipeline_status": row.pipeline_status,
            "observability_status": row.observability_status,
            "reproducibility_status": row.reproducibility_status,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "duration_ms": row.duration_ms,
            "provider": row.provider,
            "model": row.model,
            "model_digest": row.model_digest,
            "temperature": row.temperature,
            "max_tokens": row.max_tokens,
            "prompt_versions": row.prompt_versions_json,
            "prompt_file_hashes": row.prompt_file_hashes_json,
            "configuration_fingerprint": row.configuration_fingerprint,
            "git_commit": row.git_commit,
            "git_branch": row.git_branch,
            "working_tree_dirty": row.working_tree_dirty,
            "llm_call_count": row.llm_call_count,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.total_tokens,
            "slowest_step": row.slowest_step,
            "highest_token_step": row.highest_token_step,
            "revision_performed": row.revision_performed,
            "revision_decision": row.revision_decision_json,
            "revision_output_changed": row.revision_output_changed,
            "revision_improvement_status": row.revision_improvement_status,
            "reproducibility": row.reproducibility_json,
            "observability_issues": row.observability_issues_json,
        }
    )


def _step_observation(row: StepObservationTable) -> StepObservation:
    return StepObservation.model_validate(
        {
            "id": row.id,
            "run_id": row.run_id,
            "sequence": row.sequence,
            "step_name": row.step_name,
            "status": row.status,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "duration_ms": row.duration_ms,
            "attempt_count": row.attempt_count,
            "skip_reason": row.skip_reason,
            "timing_mode": row.timing_mode,
        }
    )


def _llm_call_observation(row: LLMCallObservationTable) -> LLMCallObservation:
    return LLMCallObservation.model_validate(
        {
            column: getattr(row, column)
            for column in (
                "id",
                "run_id",
                "step_id",
                "step_name",
                "status",
                "provider",
                "model",
                "model_digest",
                "temperature",
                "max_tokens",
                "prompt_file_hash",
                "rendered_prompt_hash",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "token_usage_status",
                "token_usage_source",
                "finish_reason",
                "started_at",
                "ended_at",
                "duration_ms",
                "attempt",
                "error_category",
                "error_code",
            )
        }
    )


def _error_observation(row: ErrorObservationTable) -> ErrorObservation:
    return ErrorObservation.model_validate(
        {
            column: getattr(row, column)
            for column in (
                "id",
                "run_id",
                "step_name",
                "llm_call_id",
                "error_category",
                "error_code",
                "exception_type",
                "attempt",
                "occurred_at",
                "is_terminal",
                "sanitized_message",
            )
        }
    )


def _artifact_dependency(row: ArtifactDependencyTable) -> ArtifactDependency:
    return ArtifactDependency(
        id=row.id,
        run_id=row.run_id,
        artifact_id=row.artifact_id,
        producer_step_name=row.producer_step_name,
        source_artifact_id=row.source_artifact_id,
    )
