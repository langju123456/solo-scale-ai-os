"""Orchestrate the BuildLog v0.1 pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from buildlog.config import Settings
from buildlog.evaluator import evaluate_draft, passes_thresholds
from buildlog.exceptions import PersistenceError
from buildlog.input_loader import load_iteration
from buildlog.llm_client import LLMClient
from buildlog.observer import RunObserver
from buildlog.observability_utils import SystemClock, sanitized_error_message
from buildlog.planner import create_plan
from buildlog.preprocessor import normalize_iteration
from buildlog.prompt_loader import inspect_prompt_files
from buildlog.repository import BuildLogRepository
from buildlog.review_policy import HUMAN_REVIEW_WARNING
from buildlog.reviser import revise_draft
from buildlog.run_persistence import (
    create_run_record,
    persist_artifact,
    persist_evaluation,
    persist_run_inputs,
)
from buildlog.trace import RunTrace, create_run_trace
from buildlog.writer import write_draft

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """Summary of a completed BuildLog run."""

    run_dir: Path
    final_path: Path
    evaluation_scores: dict[str, int]
    revision_performed: bool


def run_pipeline(
    input_path: Path,
    settings: Settings,
    repository: BuildLogRepository,
) -> PipelineResult:
    """Run the complete BuildLog pipeline for one iteration file."""
    clock = SystemClock()
    run_started_at = clock.now()
    run_start_ns = clock.monotonic_ns()
    LOGGER.info("pipeline start")

    validation_started_at = clock.now()
    validation_start_ns = clock.monotonic_ns()
    try:
        iteration = load_iteration(input_path)
    except Exception as exc:
        validation_ended_at = clock.now()
        validation_end_ns = clock.monotonic_ns()
        _record_early_failure(
            input_path,
            settings,
            repository,
            run_started_at,
            run_start_ns,
            [
                (
                    "validation",
                    validation_started_at,
                    validation_ended_at,
                    validation_start_ns,
                    validation_end_ns,
                    exc,
                )
            ],
            exc,
        )
        raise
    validation_ended_at = clock.now()
    validation_end_ns = clock.monotonic_ns()
    LOGGER.info("validation success")

    preprocessing_started_at = clock.now()
    preprocessing_start_ns = clock.monotonic_ns()
    try:
        normalized = normalize_iteration(iteration)
    except Exception as exc:
        preprocessing_ended_at = clock.now()
        preprocessing_end_ns = clock.monotonic_ns()
        _record_early_failure(
            input_path,
            settings,
            repository,
            run_started_at,
            run_start_ns,
            [
                (
                    "validation",
                    validation_started_at,
                    validation_ended_at,
                    validation_start_ns,
                    validation_end_ns,
                    None,
                ),
                (
                    "preprocessing",
                    preprocessing_started_at,
                    preprocessing_ended_at,
                    preprocessing_start_ns,
                    preprocessing_end_ns,
                    exc,
                ),
            ],
            exc,
            iteration_id=iteration.id,
        )
        raise
    preprocessing_ended_at = clock.now()
    preprocessing_end_ns = clock.monotonic_ns()

    trace = create_run_trace(settings.runs_dir, iteration.id)
    run_id = trace.run_dir.name
    observer = RunObserver(
        run_id,
        trace.run_dir,
        settings,
        settings.prompts_dir.parent,
        repository,
        clock=clock,
        started_at=run_started_at,
        start_ns=run_start_ns,
    )
    observer.bind_iteration(normalized.id)
    observer.record_preceding_step(
        "validation",
        started_at=validation_started_at,
        ended_at=validation_ended_at,
        start_ns=validation_start_ns,
        end_ns=validation_end_ns,
    )
    observer.record_preceding_step(
        "preprocessing",
        started_at=preprocessing_started_at,
        ended_at=preprocessing_ended_at,
        start_ns=preprocessing_start_ns,
        end_ns=preprocessing_end_ns,
    )
    run_saved = False

    try:
        with observer.step("prompt_loading"):
            prompts = inspect_prompt_files(
                settings.prompts_dir,
                settings.prompt_version,
            )
        observer.bind_prompts(prompts)

        with observer.persistence_operation("persist_run_inputs"):
            persist_run_inputs(repository, iteration, normalized, prompts)
        with observer.persistence_operation("save_run"):
            repository.save_run(
                create_run_record(
                    run_id,
                    normalized,
                    settings,
                    prompts,
                    started_at=run_started_at,
                )
            )
        run_saved = True
        observer.enable_projection()

        _write_json_artifact(
            trace,
            repository,
            observer,
            run_id,
            "00_input.json",
            iteration,
            "input",
            "validation",
            [],
        )
        _write_json_artifact(
            trace,
            repository,
            observer,
            run_id,
            "01_normalized_input.json",
            normalized,
            "normalized_input",
            "preprocessing",
            ["input"],
        )

        client = LLMClient(settings)

        LOGGER.info("planner start")
        with observer.step("planner", prompt_file=prompts["planner"]):
            plan = create_plan(normalized, client, settings)
        _write_json_artifact(
            trace,
            repository,
            observer,
            run_id,
            "02_plan.json",
            plan,
            "plan",
            "planner",
            ["normalized_input"],
        )
        LOGGER.info("planner complete")

        LOGGER.info("writer start")
        with observer.step("writer", prompt_file=prompts["writer"]):
            draft = write_draft(normalized, plan, client, settings)
        _write_text_artifact(
            trace,
            repository,
            observer,
            run_id,
            "03_draft.md",
            draft,
            "draft",
            "writer",
            ["normalized_input", "plan"],
        )
        LOGGER.info("writer complete")

        LOGGER.info("evaluator start")
        with observer.step("evaluator", prompt_file=prompts["evaluator"]):
            evaluation = evaluate_draft(normalized, draft, client, settings)
        _write_json_artifact(
            trace,
            repository,
            observer,
            run_id,
            "04_evaluation.json",
            evaluation,
            "evaluation",
            "evaluator",
            ["normalized_input", "draft"],
        )
        with observer.persistence_operation("persist_evaluation"):
            persist_evaluation(repository, run_id, evaluation)
        LOGGER.info("evaluator complete")

        with observer.step("revision_decision"):
            revision_required = not passes_thresholds(evaluation, settings)
            observer.record_revision_decision(
                evaluation,
                revision_required=revision_required,
            )

        revision_performed = revision_required
        final_draft = draft
        if revision_required:
            LOGGER.info("revision required")
            with observer.step("reviser", prompt_file=prompts["reviser"]):
                final_draft = revise_draft(
                    normalized,
                    draft,
                    evaluation,
                    client,
                    settings,
                )
            observer.record_revision_result(draft, final_draft)
            _write_text_artifact(
                trace,
                repository,
                observer,
                run_id,
                "05_revised_draft.md",
                final_draft,
                "revised_draft",
                "reviser",
                ["normalized_input", "draft", "evaluation"],
            )
        else:
            LOGGER.info("revision not required")
            observer.skip_step("reviser", "revision_not_required")

        with observer.step("finalization"):
            final_path = _write_text_artifact(
                trace,
                repository,
                observer,
                run_id,
                "06_final.md",
                final_draft + HUMAN_REVIEW_WARNING,
                "final",
                "finalization",
                ["revised_draft"] if revision_performed else ["draft"],
            )
        with observer.persistence_operation("complete_run"):
            repository.complete_run(
                run_id,
                revision_performed,
                datetime.now(UTC),
            )
        observer.complete_pipeline(revision_performed)
        metadata_path = _write_json_artifact(
            trace,
            repository,
            observer,
            run_id,
            "run_metadata.json",
            observer.run_metadata_payload(),
            "run_metadata",
            "finalization",
            ["final"],
        )
        observer.refresh_outputs()
    except Exception as exc:
        if run_saved:
            _mark_run_failed(
                repository,
                run_id,
                exc,
                settings.prompts_dir.parent,
            )
        observer.fail_pipeline(exc)
        _write_failed_metadata(trace, repository, observer, run_id, run_saved)
        raise

    LOGGER.info("pipeline complete")
    return PipelineResult(
        run_dir=trace.run_dir,
        final_path=final_path,
        evaluation_scores={
            "technical_accuracy": evaluation.technical_accuracy,
            "specificity": evaluation.specificity,
            "readability": evaluation.readability,
            "reader_value": evaluation.reader_value,
            "evidence_coverage": evaluation.evidence_coverage,
        },
        revision_performed=revision_performed,
    )


def _mark_run_failed(
    repository: BuildLogRepository,
    run_id: str,
    error: Exception,
    project_root: Path,
) -> None:
    try:
        repository.fail_run(
            run_id,
            sanitized_error_message(error, project_root),
            datetime.now(UTC),
        )
    except PersistenceError:
        LOGGER.exception("could not mark failed run %s", run_id)


def _write_json_artifact(
    trace: RunTrace,
    repository: BuildLogRepository,
    observer: RunObserver,
    run_id: str,
    filename: str,
    data: BaseModel | dict[str, Any],
    artifact_type: str,
    producer_step_name: str,
    source_artifact_types: list[str],
) -> Path:
    with observer.persistence_operation(f"write_{artifact_type}"):
        path = trace.write_json(filename, data)
        _record_artifact_safely(
            observer,
            artifact_type,
            path,
            producer_step_name,
            source_artifact_types,
        )
        persist_artifact(repository, run_id, artifact_type, path)
    return path


def _write_text_artifact(
    trace: RunTrace,
    repository: BuildLogRepository,
    observer: RunObserver,
    run_id: str,
    filename: str,
    content: str,
    artifact_type: str,
    producer_step_name: str,
    source_artifact_types: list[str],
) -> Path:
    with observer.persistence_operation(f"write_{artifact_type}"):
        path = trace.write_text(filename, content)
        _record_artifact_safely(
            observer,
            artifact_type,
            path,
            producer_step_name,
            source_artifact_types,
        )
        persist_artifact(repository, run_id, artifact_type, path)
    return path


def _record_artifact_safely(
    observer: RunObserver,
    artifact_type: str,
    path: Path,
    producer_step_name: str,
    source_artifact_types: list[str],
) -> None:
    try:
        observer.record_artifact(
            artifact_type,
            path,
            producer_step_name=producer_step_name,
            source_artifact_types=source_artifact_types,
        )
    except Exception as exc:
        observer.record_observability_issue(
            f"could not record artifact lineage for {artifact_type}: {exc}"
        )


def _record_early_failure(
    input_path: Path,
    settings: Settings,
    repository: BuildLogRepository,
    run_started_at: datetime,
    run_start_ns: int,
    measurements: list[
        tuple[str, datetime, datetime, int, int, Exception | None]
    ],
    error: Exception,
    *,
    iteration_id: str | None = None,
) -> None:
    try:
        trace = create_run_trace(
            settings.runs_dir,
            iteration_id or f"{input_path.stem}-validation-failed",
        )
        observer = RunObserver(
            trace.run_dir.name,
            trace.run_dir,
            settings,
            settings.prompts_dir.parent,
            repository,
            started_at=run_started_at,
            start_ns=run_start_ns,
        )
        if iteration_id is not None:
            observer.bind_iteration(iteration_id)
        for (
            step_name,
            started_at,
            ended_at,
            start_ns,
            end_ns,
            step_error,
        ) in measurements:
            observer.record_preceding_step(
                step_name,
                started_at=started_at,
                ended_at=ended_at,
                start_ns=start_ns,
                end_ns=end_ns,
                error=step_error,
            )
        observer.fail_pipeline(error)
        trace.write_json("run_metadata.json", observer.run_metadata_payload())
    except Exception:
        LOGGER.exception("could not persist early-failure observability")


def _write_failed_metadata(
    trace: RunTrace,
    repository: BuildLogRepository,
    observer: RunObserver,
    run_id: str,
    run_saved: bool,
) -> None:
    try:
        metadata_path = trace.write_json(
            "run_metadata.json",
            observer.run_metadata_payload(),
        )
        _record_artifact_safely(
            observer,
            "run_metadata",
            metadata_path,
            "finalization",
            [],
        )
        if run_saved:
            try:
                persist_artifact(repository, run_id, "run_metadata", metadata_path)
            except PersistenceError as exc:
                observer.record_observability_issue(
                    f"could not index failed run metadata: {exc}"
                )
        observer.refresh_outputs()
    except Exception as exc:
        observer.record_observability_issue(
            f"could not write failed run metadata: {exc}"
        )
