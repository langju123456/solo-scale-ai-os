"""Persistence-facing domain records independent of SQLAlchemy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class ProjectRecord:
    """A project that owns one or more development iterations."""

    id: str
    name: str
    description: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class IterationRecord:
    """A persisted development iteration and its validated raw input."""

    id: str
    project_id: str
    title: str
    goal: str
    context: str
    problem: str
    audience: str
    raw_input: dict[str, Any]
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class PromptVersionRecord:
    """Metadata for one immutable prompt file version."""

    id: str
    prompt_name: str
    version: str
    file_path: str
    content_hash: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class RunRecord:
    """Metadata and prompt lineage for one pipeline execution."""

    id: str
    iteration_id: str
    model: str
    planner_prompt_version_id: str
    writer_prompt_version_id: str
    evaluator_prompt_version_id: str
    reviser_prompt_version_id: str
    status: str = "running"
    revision_performed: bool = False
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata for one readable artifact stored on the filesystem."""

    id: str
    run_id: str
    artifact_type: str
    file_path: str
    content_hash: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EvaluationRecord:
    """Structured evaluation scores and feedback for one run."""

    id: str
    run_id: str
    technical_accuracy: int
    specificity: int
    readability: int
    reader_value: int
    evidence_coverage: int
    feedback: dict[str, Any]
    created_at: datetime = field(default_factory=utc_now)
