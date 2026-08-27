"""Validated schemas for BuildLog run observability."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

FIXED_STEP_NAMES = (
    "validation",
    "preprocessing",
    "prompt_loading",
    "planner",
    "writer",
    "evaluator",
    "revision_decision",
    "reviser",
    "finalization",
    "persistence",
)


class PipelineStatus(StrEnum):
    """Business pipeline outcome."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ObservabilityStatus(StrEnum):
    """Completeness of observability capture."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ReproducibilityStatus(StrEnum):
    """Completeness of the run replay manifest."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class StepStatus(StrEnum):
    """Lifecycle state for one fixed pipeline step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorCategory(StrEnum):
    """Stable top-level error taxonomy."""

    INPUT_VALIDATION = "input_validation"
    PROMPT_LOADING = "prompt_loading"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    EMPTY_RESPONSE = "empty_response"
    JSON_PARSE = "json_parse"
    SCHEMA_VALIDATION = "schema_validation"
    ARTIFACT_WRITE = "artifact_write"
    PERSISTENCE = "persistence"
    UNKNOWN = "unknown"


class TokenUsageStatus(StrEnum):
    """Availability state for provider token usage."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RevisionImprovementStatus(StrEnum):
    """Whether revision quality improvement was measured."""

    NOT_APPLICABLE = "not_applicable"
    NOT_MEASURED = "not_measured"
    MEASURED = "measured"


class RevisionTrigger(BaseModel):
    """One structured reason for requiring revision."""

    type: str
    metric: str | None = None
    actual: int | float | None = None
    operator: str | None = None
    threshold: int | float | None = None
    code: str | None = None


class RevisionDecision(BaseModel):
    """Traceable representation of the existing revision rule."""

    revision_required: bool
    decision_rule_version: str = "v1"
    triggered_by: list[RevisionTrigger] = Field(default_factory=list)


class StepObservation(BaseModel):
    """Timing and status for one fixed pipeline step."""

    id: str
    run_id: str
    sequence: int
    step_name: str
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    attempt_count: int = 0
    skip_reason: str | None = None
    timing_mode: str = "elapsed"


class LLMCallObservation(BaseModel):
    """Metadata for one model call without prompt or response payloads."""

    id: str
    run_id: str
    step_id: str
    step_name: str
    status: str
    provider: str
    model: str
    model_digest: str | None = None
    temperature: float
    max_tokens: int
    prompt_file_hash: str | None = None
    rendered_prompt_hash: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    token_usage_status: TokenUsageStatus
    token_usage_source: str
    finish_reason: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    attempt: int
    error_category: ErrorCategory | None = None
    error_code: str | None = None


class ErrorObservation(BaseModel):
    """Sanitized structured error metadata."""

    id: str
    run_id: str
    step_name: str | None = None
    llm_call_id: str | None = None
    error_category: ErrorCategory
    error_code: str
    exception_type: str
    attempt: int
    occurred_at: datetime
    is_terminal: bool
    sanitized_message: str


class ArtifactDependency(BaseModel):
    """Direct artifact lineage and producing step."""

    id: str
    run_id: str
    artifact_id: str
    producer_step_name: str
    source_artifact_id: str | None = None


class ReproducibilityReport(BaseModel):
    """Checklist for replaying the same run conditions."""

    status: ReproducibilityStatus
    requirements: dict[str, bool]
    missing_fields: list[str]


class RunObservation(BaseModel):
    """Queryable summary for one pipeline execution."""

    run_id: str
    iteration_id: str | None = None
    output_type: str = "linkedin_post"
    pipeline_status: PipelineStatus
    observability_status: ObservabilityStatus
    reproducibility_status: ReproducibilityStatus
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    provider: str
    model: str
    model_digest: str | None = None
    temperature: float
    max_tokens: int
    prompt_versions: dict[str, str]
    prompt_file_hashes: dict[str, str]
    configuration_fingerprint: str
    git_commit: str | None = None
    git_branch: str | None = None
    working_tree_dirty: bool | None = None
    llm_call_count: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    slowest_step: str | None = None
    highest_token_step: str | None = None
    revision_performed: bool
    revision_decision: RevisionDecision | None = None
    revision_output_changed: bool | None = None
    revision_improvement_status: RevisionImprovementStatus
    reproducibility: ReproducibilityReport
    observability_issues: list[str] = Field(default_factory=list)


class Timeline(BaseModel):
    """Human-readable per-run timing summary."""

    run_id: str
    pipeline_status: PipelineStatus
    observability_status: ObservabilityStatus
    total_duration_ms: int
    slowest_step: str | None
    highest_token_step: str | None
    llm_call_count: int
    revision_performed: bool
    steps: list[StepObservation]


class ObservationEvent(BaseModel):
    """Append-only event written to ``events.jsonl``."""

    event_id: str
    sequence: int
    run_id: str
    event_type: str
    occurred_at: datetime
    step_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
