from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Surface(StrEnum):
    CHAT = "CHAT"
    PLUGIN = "PLUGIN"
    CODEX = "CODEX"
    RUNTIME = "RUNTIME"
    HUMAN = "HUMAN"


class ReasoningDepth(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LatencyTolerance(StrEnum):
    INTERACTIVE = "interactive"
    BATCH = "batch"
    BACKGROUND = "background"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    FIXING = "FIXING"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class TaskEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:12]}")
    title: str = Field(min_length=3, max_length=160)
    goal: str = Field(min_length=10)
    repository: str | None = None
    requested_paths: list[str] = Field(default_factory=list)
    reasoning_depth: ReasoningDepth = ReasoningDepth.MEDIUM
    latency_tolerance: LatencyTolerance = LatencyTolerance.BATCH
    risk: RiskLevel = RiskLevel.MEDIUM

    requires_local_files: bool = False
    requires_terminal: bool = False
    requires_realtime: bool = False
    requires_scheduled_execution: bool = False
    plugin_can_complete: bool = False
    plugin_name: str | None = None
    high_risk_action: bool = False
    irreversible_action: bool = False
    public_action: bool = False

    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.NEW
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_plugin(self) -> "TaskEnvelope":
        if self.plugin_can_complete and not self.plugin_name:
            raise ValueError("plugin_name is required when plugin_can_complete is true")
        return self


class RouteDecision(BaseModel):
    primary: Surface
    secondary: list[Surface] = Field(default_factory=list)
    rationale: list[str]
    human_gate_required: bool = False


class RunEvent(BaseModel):
    run_id: str
    task_id: str
    event_type: str
    status: TaskStatus
    timestamp: datetime = Field(default_factory=utc_now)
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionPacket(BaseModel):
    task_id: str
    goal: str
    frozen_decisions: list[str] = Field(default_factory=list)
    repository: str | None = None
    branch: str | None = None
    requested_paths: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    expected_return_report: list[str] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    decision: str
    reason: str
    alternatives_considered: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    id: str
    title: str
    goal: str
    context: str
    problem: str
    actions: list[str]
    decisions: list[DecisionRecord]
    trade_offs: list[str]
    result: str
    lessons: list[str]
    evidence: list[str]
    audience: str = "AI engineers, software engineers, technical recruiters, and builders"
    metadata: dict[str, Any] = Field(default_factory=dict)
