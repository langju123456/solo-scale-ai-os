"""SQLAlchemy table mappings for BuildLog metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for BuildLog persistence tables."""


class ProjectTable(Base):
    """Stored project metadata."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    iterations: Mapped[list[IterationTable]] = relationship(back_populates="project")


class IterationTable(Base):
    """Stored iteration metadata and raw validated input."""

    __tablename__ = "iterations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    goal: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    problem: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)
    raw_input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectTable] = relationship(back_populates="iterations")
    runs: Mapped[list[RunTable]] = relationship(back_populates="iteration")


class PromptVersionTable(Base):
    """Stored prompt file metadata."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "prompt_name",
            "version",
            "content_hash",
            name="uq_prompt_name_version_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    prompt_name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50))
    file_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunTable(Base):
    """Stored pipeline run state and prompt lineage."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    iteration_id: Mapped[str] = mapped_column(ForeignKey("iterations.id"), index=True)
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), index=True)
    revision_performed: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    planner_prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    writer_prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    evaluator_prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    reviser_prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))

    iteration: Mapped[IterationTable] = relationship(back_populates="runs")
    artifacts: Mapped[list[ArtifactTable]] = relationship(back_populates="run")
    evaluation: Mapped[EvaluationTable | None] = relationship(back_populates="run")


class ArtifactTable(Base):
    """Stored path and hash for one filesystem artifact."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "artifact_type", name="uq_artifact_run_type"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(100))
    file_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    run: Mapped[RunTable] = relationship(back_populates="artifacts")


class EvaluationTable(Base):
    """Stored evaluation scores and feedback."""

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"),
        unique=True,
        index=True,
    )
    technical_accuracy: Mapped[int]
    specificity: Mapped[int]
    readability: Mapped[int]
    reader_value: Mapped[int]
    evidence_coverage: Mapped[int]
    feedback_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    run: Mapped[RunTable] = relationship(back_populates="evaluation")


class RunObservationTable(Base):
    """Queryable run-level observability summary."""

    __tablename__ = "run_observations"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"),
        primary_key=True,
    )
    iteration_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_type: Mapped[str] = mapped_column(String(100))
    pipeline_status: Mapped[str] = mapped_column(String(30), index=True)
    observability_status: Mapped[str] = mapped_column(String(30), index=True)
    reproducibility_status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(255))
    model_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    temperature: Mapped[float] = mapped_column(Float)
    max_tokens: Mapped[int] = mapped_column(Integer)
    prompt_versions_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_file_hashes_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    working_tree_dirty: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    llm_call_count: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slowest_step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    highest_token_step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revision_performed: Mapped[bool] = mapped_column(Boolean)
    revision_decision_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    revision_output_changed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    revision_improvement_status: Mapped[str] = mapped_column(String(30))
    reproducibility_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    observability_issues_json: Mapped[list[str]] = mapped_column(JSON)


class StepObservationTable(Base):
    """Queryable timing and status for one fixed pipeline step."""

    __tablename__ = "step_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "step_name", name="uq_step_observation_run_name"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    step_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer)
    skip_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timing_mode: Mapped[str] = mapped_column(String(30))


class LLMCallObservationTable(Base):
    """Queryable provider metadata for one model call."""

    __tablename__ = "llm_call_observations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("step_observations.id"))
    step_name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(255))
    model_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    temperature: Mapped[float] = mapped_column(Float)
    max_tokens: Mapped[int] = mapped_column(Integer)
    prompt_file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rendered_prompt_hash: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_usage_status: Mapped[str] = mapped_column(String(30))
    token_usage_source: Mapped[str] = mapped_column(String(100))
    finish_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ErrorObservationTable(Base):
    """Queryable sanitized error metadata."""

    __tablename__ = "error_observations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_call_observations.id"),
        nullable=True,
    )
    error_category: Mapped[str] = mapped_column(String(50), index=True)
    error_code: Mapped[str] = mapped_column(String(100), index=True)
    exception_type: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_terminal: Mapped[bool] = mapped_column(Boolean)
    sanitized_message: Mapped[str] = mapped_column(Text)


class ArtifactDependencyTable(Base):
    """Direct artifact dependency and producing-step projection."""

    __tablename__ = "artifact_dependencies"

    id: Mapped[str] = mapped_column(String(500), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    producer_step_name: Mapped[str] = mapped_column(String(100))
    source_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"),
        nullable=True,
    )


class PublishReceiptTable(Base):
    """Operational receipt for one downstream publication attempt."""

    __tablename__ = "publish_receipts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_publish_receipt_attempt"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(255), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    account_reference: Mapped[str] = mapped_column(String(128), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    external_post_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    api_endpoint: Mapped[str] = mapped_column(Text)
    api_version: Mapped[str] = mapped_column(String(20))
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey("publish_receipts.id"),
        nullable=True,
    )


class WorkflowJobTable(Base):
    """Durable request state for an asynchronously executed generation run."""

    __tablename__ = "workflow_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_workflow_job_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
