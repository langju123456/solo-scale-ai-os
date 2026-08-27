"""Repository boundary for BuildLog metadata persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from buildlog.domain import (
    ArtifactRecord,
    EvaluationRecord,
    IterationRecord,
    ProjectRecord,
    PromptVersionRecord,
    RunRecord,
)
from buildlog.observability_repository import ObservabilityRepository


class RunRepository(Protocol):
    """Minimal persistence operations required by the v0.1 pipeline."""

    def initialize(self) -> None:
        """Create persistence structures required by the repository."""

    def save_project(self, project: ProjectRecord) -> None:
        """Persist a project."""

    def save_iteration(self, iteration: IterationRecord) -> None:
        """Persist a validated iteration."""

    def save_prompt_version(self, prompt: PromptVersionRecord) -> None:
        """Persist prompt version metadata."""

    def save_run(self, run: RunRecord) -> None:
        """Persist a new pipeline run."""

    def save_artifact(self, artifact: ArtifactRecord) -> None:
        """Persist metadata for a filesystem artifact."""

    def save_evaluation(self, evaluation: EvaluationRecord) -> None:
        """Persist structured evaluation scores and feedback."""

    def complete_run(
        self,
        run_id: str,
        revision_performed: bool,
        completed_at: datetime,
    ) -> None:
        """Mark a run as successfully completed."""

    def fail_run(self, run_id: str, error_message: str, completed_at: datetime) -> None:
        """Mark a run as failed."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return one run or ``None`` when it does not exist."""

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """Return all artifacts related to one run."""

    def get_evaluation(self, run_id: str) -> EvaluationRecord | None:
        """Return the evaluation related to one run."""


class BuildLogRepository(RunRepository, ObservabilityRepository, Protocol):
    """Combined repository capabilities required by the local pipeline."""
