"""Persistence boundary for queryable observability metadata."""

from __future__ import annotations

from typing import Protocol

from buildlog.observability_models import (
    ArtifactDependency,
    ErrorObservation,
    LLMCallObservation,
    RunObservation,
    StepObservation,
)


class ObservabilityRepository(Protocol):
    """Store one complete SQLite projection of a run trace."""

    def save_observability_bundle(
        self,
        run: RunObservation,
        steps: list[StepObservation],
        llm_calls: list[LLMCallObservation],
        errors: list[ErrorObservation],
        artifact_dependencies: list[ArtifactDependency],
    ) -> None:
        """Persist queryable run, step, call, error, and lineage metadata."""

    def get_run_observation(self, run_id: str) -> RunObservation | None:
        """Return one run observation or ``None``."""

    def list_step_observations(self, run_id: str) -> list[StepObservation]:
        """Return fixed steps for one run."""

    def list_llm_call_observations(self, run_id: str) -> list[LLMCallObservation]:
        """Return model calls for one run."""

    def list_error_observations(self, run_id: str) -> list[ErrorObservation]:
        """Return structured errors for one run."""

    def list_artifact_dependencies(self, run_id: str) -> list[ArtifactDependency]:
        """Return direct artifact lineage for one run."""
