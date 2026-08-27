"""HTTP-facing contracts for the hosted BuildLog application."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DashboardMetrics(BaseModel):
    """Operational and product evidence shown on the internal dashboard."""

    total_runs: int
    completed_runs: int
    failed_runs: int
    completion_rate: float
    total_artifacts: int
    evaluated_runs: int
    average_evaluation_score: float | None
    live_publications: int
    queued_jobs: int
    running_jobs: int
    failed_jobs: int
    observed_runs: int
    p50_pipeline_latency_ms: int | None
    p95_pipeline_latency_ms: int | None
    recorded_tokens: int


class RunSummary(BaseModel):
    """One generation run projected for list and detail views."""

    id: str
    iteration_id: str
    title: str
    model: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    artifact_count: int
    average_evaluation_score: float | None
    revision_performed: bool


class RunDetail(RunSummary):
    """Run summary plus its persisted output and evaluation metadata."""

    artifact_types: list[str]
    evaluation_scores: dict[str, int] | None
    error_message: str | None


class WorkflowJob(BaseModel):
    """Durable state of an API-submitted generation request."""

    id: str
    idempotency_key: str
    input_hash: str
    status: str
    attempt_count: int
    run_id: str | None
    error_category: str | None
    safe_error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobAccepted(BaseModel):
    """Response returned for both new and idempotently replayed requests."""

    job: WorkflowJob
    created: bool


class APIMessage(BaseModel):
    """Small status response used by health endpoints."""

    status: str
    version: str
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
