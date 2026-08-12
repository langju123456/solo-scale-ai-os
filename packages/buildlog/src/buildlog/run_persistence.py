"""Map pipeline data to repository records without SQLAlchemy dependencies."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from buildlog.config import Settings
from buildlog.domain import (
    ArtifactRecord,
    EvaluationRecord,
    IterationRecord,
    ProjectRecord,
    PromptVersionRecord,
    RunRecord,
)
from buildlog.hashing import sha256_file
from buildlog.models import Evaluation, Iteration
from buildlog.prompt_loader import PromptFile
from buildlog.repository import RunRepository


def persist_run_inputs(
    repository: RunRepository,
    original: Iteration,
    normalized: Iteration,
    prompts: dict[str, PromptFile],
) -> None:
    """Persist the project, iteration, and prompt records required by a run."""
    project_id, project_name = _project_identity(normalized)
    repository.save_project(ProjectRecord(id=project_id, name=project_name))
    repository.save_iteration(
        IterationRecord(
            id=normalized.id,
            project_id=project_id,
            title=normalized.title,
            goal=normalized.goal,
            context=normalized.context,
            problem=normalized.problem,
            audience=normalized.audience,
            raw_input=original.model_dump(mode="json"),
            created_at=normalized.created_at or datetime.now(UTC),
        )
    )
    for prompt in prompts.values():
        repository.save_prompt_version(_prompt_record(prompt))


def create_run_record(
    run_id: str,
    iteration: Iteration,
    settings: Settings,
    prompts: dict[str, PromptFile],
    *,
    started_at: datetime | None = None,
) -> RunRecord:
    """Create run metadata with explicit prompt-version lineage."""
    prompt_ids = {name: _prompt_record(prompt).id for name, prompt in prompts.items()}
    return RunRecord(
        id=run_id,
        iteration_id=iteration.id,
        model=settings.model,
        planner_prompt_version_id=prompt_ids["planner"],
        writer_prompt_version_id=prompt_ids["writer"],
        evaluator_prompt_version_id=prompt_ids["evaluator"],
        reviser_prompt_version_id=prompt_ids["reviser"],
        started_at=started_at or datetime.now(UTC),
    )


def persist_artifact(
    repository: RunRepository,
    run_id: str,
    artifact_type: str,
    path: Path,
) -> None:
    """Persist the path and content hash for one filesystem artifact."""
    repository.save_artifact(
        ArtifactRecord(
            id=f"{run_id}:{artifact_type}",
            run_id=run_id,
            artifact_type=artifact_type,
            file_path=str(path.resolve()),
            content_hash=sha256_file(path),
        )
    )


def persist_evaluation(
    repository: RunRepository,
    run_id: str,
    evaluation: Evaluation,
) -> None:
    """Persist evaluation scores and structured feedback."""
    repository.save_evaluation(
        EvaluationRecord(
            id=f"{run_id}:evaluation",
            run_id=run_id,
            technical_accuracy=evaluation.technical_accuracy,
            specificity=evaluation.specificity,
            readability=evaluation.readability,
            reader_value=evaluation.reader_value,
            evidence_coverage=evaluation.evidence_coverage,
            feedback={
                "unsupported_claims": evaluation.unsupported_claims,
                "vague_sections": evaluation.vague_sections,
                "revision_instructions": evaluation.revision_instructions,
                "hard_failure": evaluation.hard_failure,
            },
        )
    )


def _project_identity(iteration: Iteration) -> tuple[str, str]:
    configured_name = iteration.metadata.get("project")
    project_name = (
        configured_name.strip()
        if isinstance(configured_name, str) and configured_name.strip()
        else "BuildLog"
    )
    configured_id = iteration.metadata.get("project_id")
    if isinstance(configured_id, str) and configured_id.strip():
        return configured_id.strip(), project_name
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    suffix = sha256(project_name.encode("utf-8")).hexdigest()[:10]
    return f"{slug or 'project'}-{suffix}", project_name


def _prompt_record(prompt: PromptFile) -> PromptVersionRecord:
    prompt_id = f"{prompt.name}-{prompt.version}-{prompt.content_hash[:16]}"
    return PromptVersionRecord(
        id=prompt_id,
        prompt_name=prompt.name,
        version=prompt.version,
        file_path=str(prompt.path),
        content_hash=prompt.content_hash,
    )
