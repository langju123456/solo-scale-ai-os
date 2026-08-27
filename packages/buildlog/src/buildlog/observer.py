"""Run, step, LLM-call, error, and artifact-lineage instrumentation."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from buildlog.config import Settings
from buildlog.event_writer import AppendOnlyRunEventWriter
from buildlog.hashing import sha256_file
from buildlog.models import Evaluation
from buildlog.observability_models import (
    FIXED_STEP_NAMES,
    ArtifactDependency,
    ErrorObservation,
    LLMCallObservation,
    ObservabilityStatus,
    PipelineStatus,
    ReproducibilityReport,
    ReproducibilityStatus,
    RevisionDecision,
    RevisionImprovementStatus,
    RevisionTrigger,
    RunObservation,
    StepObservation,
    StepStatus,
    Timeline,
    TokenUsageStatus,
)
from buildlog.observability_repository import ObservabilityRepository
from buildlog.observability_utils import (
    Clock,
    SystemClock,
    canonical_sha256,
    classify_error,
    duration_ms,
    inspect_git_state,
    sanitize_message,
    sanitized_error_message,
    sha256_text,
    split_provider_model,
)
from buildlog.prompt_loader import PromptFile


@dataclass(frozen=True)
class ActiveStep:
    """Observer and step metadata visible to nested LLM calls."""

    observer: RunObserver
    step_id: str
    step_name: str
    prompt_file_hash: str | None


@dataclass
class PendingLLMCall:
    """Mutable timing state while one provider call is in flight."""

    id: str
    step_id: str
    step_name: str
    prompt_file_hash: str | None
    rendered_prompt_hash: str
    attempt: int
    started_at: datetime
    start_ns: int


_ACTIVE_STEP: ContextVar[ActiveStep | None] = ContextVar(
    "buildlog_active_observation_step",
    default=None,
)


def get_active_step() -> ActiveStep | None:
    """Return the active observed step for the current execution context."""
    return _ACTIVE_STEP.get()


class RunObserver:
    """Capture best-effort telemetry without controlling business behavior."""

    def __init__(
        self,
        run_id: str,
        run_dir: Path,
        settings: Settings,
        project_root: Path,
        repository: ObservabilityRepository | None = None,
        *,
        clock: Clock | None = None,
        started_at: datetime | None = None,
        start_ns: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.settings = settings
        self.project_root = project_root
        self.repository = repository
        self.clock = clock or SystemClock()
        self.started_at = started_at or self.clock.now()
        self.start_ns = start_ns if start_ns is not None else self.clock.monotonic_ns()
        self.ended_at = self.started_at
        self.end_ns = self.start_ns
        self.iteration_id: str | None = None
        self.pipeline_status = PipelineStatus.RUNNING
        self.observability_status = ObservabilityStatus.COMPLETE
        self.prompts: dict[str, PromptFile] = {}
        self.steps = {
            name: StepObservation(
                id=f"{run_id}:step:{name}",
                run_id=run_id,
                sequence=index,
                step_name=name,
                timing_mode="aggregate" if name == "persistence" else "elapsed",
            )
            for index, name in enumerate(FIXED_STEP_NAMES, start=1)
        }
        self.llm_calls: list[LLMCallObservation] = []
        self.errors: list[ErrorObservation] = []
        self.artifact_dependencies: list[ArtifactDependency] = []
        self.artifact_hashes: dict[str, str] = {}
        self.revision_decision: RevisionDecision | None = None
        self.revision_performed = False
        self.revision_output_changed: bool | None = None
        self.revision_improvement_status = RevisionImprovementStatus.NOT_APPLICABLE
        self.observability_issues: list[str] = []
        self._recorded_error_objects: set[int] = set()
        self._projection_enabled = False
        self._persistence_duration_ms = 0
        self._persistence_last_at: datetime | None = None
        self._events_path = self.run_dir / "events.jsonl"
        self._timeline_path = self.run_dir / "timeline.json"
        self._events_available = True
        self._timeline_available = True
        self._event_writer: AppendOnlyRunEventWriter | None = None
        try:
            self._event_writer = AppendOnlyRunEventWriter(
                self._events_path,
                self.run_id,
                now=self.clock.now,
            )
        except OSError as exc:
            self._events_available = False
            self._mark_partial(f"event writer initialization failed: {exc}")
        self._finalized = False
        self._append_event("run_started", occurred_at=self.started_at)

    def bind_iteration(self, iteration_id: str) -> None:
        """Attach the validated iteration identity."""
        self.iteration_id = iteration_id

    def record_observability_issue(self, issue: str) -> None:
        """Mark capture partial without affecting the business pipeline."""
        self._mark_partial(issue)

    def bind_prompts(self, prompts: dict[str, PromptFile]) -> None:
        """Attach immutable prompt metadata used by this run."""
        self.prompts = dict(prompts)

    def enable_projection(self) -> None:
        """Allow SQLite projection after the business run row exists."""
        self._projection_enabled = True

    def record_preceding_step(
        self,
        step_name: str,
        *,
        started_at: datetime,
        ended_at: datetime,
        start_ns: int,
        end_ns: int,
        error: Exception | None = None,
    ) -> None:
        """Record a step measured before the run directory was available."""
        step = self.steps[step_name]
        step.started_at = started_at
        step.ended_at = ended_at
        step.duration_ms = duration_ms(start_ns, end_ns)
        step.attempt_count = 1
        step.status = StepStatus.FAILED if error else StepStatus.COMPLETED
        self._append_event(
            f"step_{step.status.value}",
            step_name,
            {"duration_ms": step.duration_ms, "attempt_count": 1},
            occurred_at=ended_at,
        )
        if error is not None:
            self.record_error(error, step_name=step_name, attempt=1, is_terminal=True)

    def step(
        self,
        step_name: str,
        *,
        prompt_file: PromptFile | None = None,
    ) -> _StepContext:
        """Return a context manager for one fixed elapsed-time step."""
        return _StepContext(self, step_name, prompt_file)

    def persistence_operation(self, operation: str) -> _PersistenceContext:
        """Return a context manager for one business persistence operation."""
        return _PersistenceContext(self, operation)

    def skip_step(self, step_name: str, reason: str) -> None:
        """Mark a fixed step as explicitly skipped."""
        step = self.steps[step_name]
        if step.status is not StepStatus.PENDING:
            return
        step.status = StepStatus.SKIPPED
        step.attempt_count = 0
        step.skip_reason = reason
        self._append_event(
            "step_skipped",
            step_name,
            {"attempt_count": 0, "skip_reason": reason},
        )

    def start_llm_call(self, prompt: str) -> PendingLLMCall | None:
        """Start one LLM call under the active observed step."""
        active = get_active_step()
        if active is None or active.observer is not self:
            return None
        call_number = sum(1 for call in self.llm_calls if call.step_id == active.step_id) + 1
        call = PendingLLMCall(
            id=f"{self.run_id}:llm:{len(self.llm_calls) + 1}",
            step_id=active.step_id,
            step_name=active.step_name,
            prompt_file_hash=active.prompt_file_hash,
            rendered_prompt_hash=sha256_text(prompt),
            attempt=call_number,
            started_at=self.clock.now(),
            start_ns=self.clock.monotonic_ns(),
        )
        self._append_event(
            "llm_call_started",
            active.step_name,
            {
                "llm_call_id": call.id,
                "attempt": call.attempt,
                "rendered_prompt_hash": call.rendered_prompt_hash,
            },
        )
        return call

    def finish_llm_call(
        self,
        pending: PendingLLMCall | None,
        *,
        provider_end_ns: int,
        provider_ended_at: datetime,
        usage: dict[str, int | None] | None,
        finish_reason: str | None,
        error: Exception | None,
    ) -> None:
        """Finish and record one provider call and any validation error."""
        if pending is None:
            return
        provider, model = split_provider_model(self.settings.model)
        usage_values = usage or {}
        has_usage = any(
            usage_values.get(name) is not None
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        classified = classify_error(error) if error is not None else None
        observation = LLMCallObservation(
            id=pending.id,
            run_id=self.run_id,
            step_id=pending.step_id,
            step_name=pending.step_name,
            status="failed" if error else "completed",
            provider=provider,
            model=model,
            model_digest=self.settings.model_digest,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            prompt_file_hash=pending.prompt_file_hash,
            rendered_prompt_hash=pending.rendered_prompt_hash,
            prompt_tokens=usage_values.get("prompt_tokens"),
            completion_tokens=usage_values.get("completion_tokens"),
            total_tokens=usage_values.get("total_tokens"),
            token_usage_status=(
                TokenUsageStatus.AVAILABLE if has_usage else TokenUsageStatus.UNAVAILABLE
            ),
            token_usage_source="provider" if has_usage else "provider_not_returned",
            finish_reason=finish_reason,
            started_at=pending.started_at,
            ended_at=provider_ended_at,
            duration_ms=duration_ms(pending.start_ns, provider_end_ns),
            attempt=pending.attempt,
            error_category=classified.category if classified else None,
            error_code=classified.code if classified else None,
        )
        self.llm_calls.append(observation)
        self._append_event(
            "llm_call_failed" if error else "llm_call_completed",
            pending.step_name,
            observation.model_dump(mode="json"),
        )
        if error is not None:
            self.record_error(
                error,
                step_name=pending.step_name,
                llm_call_id=pending.id,
                attempt=pending.attempt,
                is_terminal=True,
            )

    def record_artifact(
        self,
        artifact_type: str,
        path: Path,
        *,
        producer_step_name: str,
        source_artifact_types: list[str],
    ) -> None:
        """Record an artifact hash, producing step, and direct dependencies."""
        artifact_id = f"{self.run_id}:{artifact_type}"
        try:
            content_hash = sha256_file(path)
        except OSError as exc:
            self._mark_partial(f"could not hash artifact {artifact_type}: {exc}")
            return
        self.artifact_hashes[artifact_type] = content_hash
        source_ids = [
            f"{self.run_id}:{source_type}" for source_type in source_artifact_types
        ]
        if source_ids:
            for source_id in source_ids:
                self.artifact_dependencies.append(
                    ArtifactDependency(
                        id=f"{artifact_id}:depends:{source_id.rsplit(':', 1)[-1]}",
                        run_id=self.run_id,
                        artifact_id=artifact_id,
                        producer_step_name=producer_step_name,
                        source_artifact_id=source_id,
                    )
                )
        else:
            self.artifact_dependencies.append(
                ArtifactDependency(
                    id=f"{artifact_id}:root",
                    run_id=self.run_id,
                    artifact_id=artifact_id,
                    producer_step_name=producer_step_name,
                    source_artifact_id=None,
                )
            )
        self._append_event(
            "artifact_created",
            producer_step_name,
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "path": str(path),
                "content_hash": content_hash,
                "source_artifact_ids": source_ids,
            },
        )

    def record_revision_decision(
        self,
        evaluation: Evaluation,
        *,
        revision_required: bool,
    ) -> None:
        """Record the existing threshold and hard-failure decision."""
        metric_thresholds = (
            ("technical_accuracy", self.settings.threshold_accuracy),
            ("specificity", self.settings.threshold_specificity),
            ("readability", self.settings.threshold_readability),
            ("reader_value", self.settings.threshold_value),
            ("evidence_coverage", self.settings.threshold_evidence),
        )
        triggers = [
            RevisionTrigger(
                type="score_threshold",
                metric=metric,
                actual=getattr(evaluation, metric),
                operator="<",
                threshold=threshold,
            )
            for metric, threshold in metric_thresholds
            if getattr(evaluation, metric) < threshold
        ]
        if evaluation.hard_failure:
            triggers.append(RevisionTrigger(type="hard_failure", code=None))
        self.revision_decision = RevisionDecision(
            revision_required=revision_required,
            triggered_by=triggers,
        )
        self._append_event(
            "revision_decision_recorded",
            "revision_decision",
            self.revision_decision.model_dump(mode="json"),
        )

    def record_revision_result(self, draft: str, revised_draft: str) -> None:
        """Record change without inferring quality improvement."""
        self.revision_performed = True
        self.revision_output_changed = sha256_text(draft) != sha256_text(revised_draft)
        self.revision_improvement_status = RevisionImprovementStatus.NOT_MEASURED

    def record_error(
        self,
        error: Exception,
        *,
        step_name: str | None,
        attempt: int,
        is_terminal: bool,
        llm_call_id: str | None = None,
    ) -> None:
        """Record one sanitized error, deduplicating propagated exceptions."""
        object_id = id(error)
        if object_id in self._recorded_error_objects:
            return
        self._recorded_error_objects.add(object_id)
        classified = classify_error(error)
        observation = ErrorObservation(
            id=f"{self.run_id}:error:{len(self.errors) + 1}",
            run_id=self.run_id,
            step_name=step_name,
            llm_call_id=llm_call_id,
            error_category=classified.category,
            error_code=classified.code,
            exception_type=type(error).__name__,
            attempt=attempt,
            occurred_at=self.clock.now(),
            is_terminal=is_terminal,
            sanitized_message=sanitized_error_message(error, self.project_root),
        )
        self.errors.append(observation)
        self._append_event(
            "error_recorded",
            step_name,
            observation.model_dump(mode="json"),
        )

    def complete_pipeline(self, revision_performed: bool) -> RunObservation:
        """Finalize a successful pipeline and write best-effort projections."""
        self.pipeline_status = PipelineStatus.COMPLETED
        self.revision_performed = revision_performed
        if not revision_performed:
            self.revision_improvement_status = RevisionImprovementStatus.NOT_APPLICABLE
        self._finalize_steps("not_reached")
        self._finish_run_clock()
        self._append_event("run_completed")
        return self._finalize_outputs()

    def fail_pipeline(self, error: Exception, step_name: str | None = None) -> RunObservation:
        """Finalize a failed pipeline without swallowing its exception."""
        self.pipeline_status = PipelineStatus.FAILED
        self.record_error(
            error,
            step_name=step_name,
            attempt=1,
            is_terminal=True,
        )
        self._finalize_steps("upstream_failure")
        self._finish_run_clock()
        self._append_event("run_failed")
        return self._finalize_outputs()

    def run_metadata_payload(self) -> dict[str, Any]:
        """Return the expanded existing ``run_metadata.json`` payload."""
        observation = self._build_run_observation()
        return {
            "schema_version": "2",
            "run_id": self.run_id,
            "iteration_id": self.iteration_id,
            "output_type": observation.output_type,
            "pipeline_status": observation.pipeline_status.value,
            "observability_status": observation.observability_status.value,
            "reproducibility_status": observation.reproducibility_status.value,
            "status": observation.pipeline_status.value,
            "started_at": observation.started_at.isoformat(),
            "ended_at": observation.ended_at.isoformat(),
            "duration_ms": observation.duration_ms,
            "provider": observation.provider,
            "model": self.settings.model,
            "model_digest": observation.model_digest,
            "temperature": observation.temperature,
            "max_tokens": observation.max_tokens,
            "prompt_versions": observation.prompt_versions,
            "prompt_file_hashes": observation.prompt_file_hashes,
            "configuration_fingerprint": observation.configuration_fingerprint,
            "git_commit": observation.git_commit,
            "git_branch": observation.git_branch,
            "working_tree_dirty": observation.working_tree_dirty,
            "llm_call_count": observation.llm_call_count,
            "token_usage": {
                "prompt_tokens": observation.prompt_tokens,
                "completion_tokens": observation.completion_tokens,
                "total_tokens": observation.total_tokens,
            },
            "slowest_step": observation.slowest_step,
            "highest_token_step": observation.highest_token_step,
            "revision_performed": observation.revision_performed,
            "revision_decision": (
                observation.revision_decision.model_dump(mode="json")
                if observation.revision_decision
                else None
            ),
            "revision_output_changed": observation.revision_output_changed,
            "revision_improvement_status": observation.revision_improvement_status.value,
            "reproducibility": observation.reproducibility.model_dump(mode="json"),
            "observability_issues": observation.observability_issues,
        }

    def refresh_outputs(self) -> RunObservation:
        """Refresh timeline and SQLite projection after late metadata lineage."""
        observation = self._build_run_observation()
        self._write_timeline(self._build_timeline(observation))
        observation = self._build_run_observation()
        if self._projection_enabled and self.repository is not None:
            try:
                self.repository.save_observability_bundle(
                    observation,
                    list(self.steps.values()),
                    self.llm_calls,
                    self.errors,
                    self.artifact_dependencies,
                )
            except Exception as exc:
                self._mark_partial(f"SQLite observability projection failed: {exc}")
                observation = self._build_run_observation()
                self._write_timeline(self._build_timeline(observation))
        return observation

    def _finish_run_clock(self) -> None:
        self.ended_at = self.clock.now()
        self.end_ns = self.clock.monotonic_ns()

    def _finalize_steps(self, pending_reason: str) -> None:
        self._finish_persistence_step()
        for name in FIXED_STEP_NAMES:
            if self.steps[name].status is StepStatus.PENDING:
                self.skip_step(name, pending_reason)

    def _finish_persistence_step(self) -> None:
        step = self.steps["persistence"]
        if step.status is StepStatus.RUNNING:
            step.status = StepStatus.COMPLETED
            step.ended_at = self._persistence_last_at or self.clock.now()
            step.duration_ms = self._persistence_duration_ms
            self._append_event(
                "step_completed",
                "persistence",
                {
                    "duration_ms": step.duration_ms,
                    "attempt_count": step.attempt_count,
                    "timing_mode": "aggregate",
                },
            )

    def _finalize_outputs(self) -> RunObservation:
        observation = self._build_run_observation()
        timeline = self._build_timeline(observation)
        self._write_timeline(timeline)
        observation = self._build_run_observation()
        if self._projection_enabled and self.repository is not None:
            try:
                self.repository.save_observability_bundle(
                    observation,
                    list(self.steps.values()),
                    self.llm_calls,
                    self.errors,
                    self.artifact_dependencies,
                )
            except Exception as exc:
                self._mark_partial(f"SQLite observability projection failed: {exc}")
                observation = self._build_run_observation()
                self._write_timeline(self._build_timeline(observation))
        self._finalized = True
        return observation

    def _build_run_observation(self) -> RunObservation:
        provider, model = split_provider_model(self.settings.model)
        git_state = inspect_git_state(self.project_root)
        prompt_versions = {
            name: prompt.version for name, prompt in self.prompts.items()
        }
        prompt_hashes = {
            name: prompt.content_hash for name, prompt in self.prompts.items()
        }
        rendered_hashes = {
            call.step_name: call.rendered_prompt_hash for call in self.llm_calls
        }
        config = {
            "provider": provider,
            "model": model,
            "model_digest": self.settings.model_digest,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "output_type": "linkedin_post",
            "prompt_file_hashes": prompt_hashes,
            "revision_thresholds": {
                "technical_accuracy": self.settings.threshold_accuracy,
                "specificity": self.settings.threshold_specificity,
                "readability": self.settings.threshold_readability,
                "reader_value": self.settings.threshold_value,
                "evidence_coverage": self.settings.threshold_evidence,
            },
        }
        fingerprint = canonical_sha256(config)
        executed_llm_steps = {
            name
            for name in ("planner", "writer", "evaluator", "reviser")
            if self.steps[name].status in (StepStatus.COMPLETED, StepStatus.FAILED)
        }
        requirements = {
            "input_artifact_hash": "input" in self.artifact_hashes,
            "normalized_input_hash": "normalized_input" in self.artifact_hashes,
            "git_commit": git_state.commit is not None,
            "working_tree_state": git_state.working_tree_dirty is False,
            "prompt_file_hashes": len(prompt_hashes) == 4,
            "rendered_prompt_hashes": executed_llm_steps.issubset(rendered_hashes),
            "model": bool(model),
            "model_digest": bool(self.settings.model_digest),
            "temperature": True,
            "max_tokens": True,
            "provider": provider != "unknown",
            "configuration_fingerprint": bool(fingerprint),
        }
        missing = [name for name, available in requirements.items() if not available]
        reproducibility_status = (
            ReproducibilityStatus.COMPLETE
            if not missing
            else ReproducibilityStatus.PARTIAL
        )
        completed_steps = [
            step
            for step in self.steps.values()
            if step.duration_ms is not None and step.status is not StepStatus.SKIPPED
        ]
        slowest = max(completed_steps, key=lambda item: item.duration_ms or 0, default=None)
        token_totals_by_step: dict[str, int] = {}
        for call in self.llm_calls:
            if call.total_tokens is not None:
                token_totals_by_step[call.step_name] = (
                    token_totals_by_step.get(call.step_name, 0) + call.total_tokens
                )
        highest_token_step = (
            max(token_totals_by_step, key=token_totals_by_step.get)
            if token_totals_by_step
            else None
        )
        prompt_tokens = _sum_tokens(self.llm_calls, "prompt_tokens")
        completion_tokens = _sum_tokens(self.llm_calls, "completion_tokens")
        total_tokens = _sum_tokens(self.llm_calls, "total_tokens")
        return RunObservation(
            run_id=self.run_id,
            iteration_id=self.iteration_id,
            pipeline_status=self.pipeline_status,
            observability_status=self.observability_status,
            reproducibility_status=reproducibility_status,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_ms=duration_ms(self.start_ns, self.end_ns),
            provider=provider,
            model=model,
            model_digest=self.settings.model_digest,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            prompt_versions=prompt_versions,
            prompt_file_hashes=prompt_hashes,
            configuration_fingerprint=fingerprint,
            git_commit=git_state.commit,
            git_branch=git_state.branch,
            working_tree_dirty=git_state.working_tree_dirty,
            llm_call_count=len(self.llm_calls),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            slowest_step=slowest.step_name if slowest else None,
            highest_token_step=highest_token_step,
            revision_performed=self.revision_performed,
            revision_decision=self.revision_decision,
            revision_output_changed=self.revision_output_changed,
            revision_improvement_status=self.revision_improvement_status,
            reproducibility=ReproducibilityReport(
                status=reproducibility_status,
                requirements=requirements,
                missing_fields=missing,
            ),
            observability_issues=self.observability_issues,
        )

    def _build_timeline(self, observation: RunObservation) -> Timeline:
        return Timeline(
            run_id=self.run_id,
            pipeline_status=self.pipeline_status,
            observability_status=self.observability_status,
            total_duration_ms=observation.duration_ms,
            slowest_step=observation.slowest_step,
            highest_token_step=observation.highest_token_step,
            llm_call_count=len(self.llm_calls),
            revision_performed=self.revision_performed,
            steps=[self.steps[name] for name in FIXED_STEP_NAMES],
        )

    def _write_timeline(self, timeline: Timeline) -> None:
        try:
            self._timeline_path.write_text(
                timeline.model_dump_json(indent=2),
                encoding="utf-8",
            )
            self._timeline_available = True
        except OSError as exc:
            self._timeline_available = False
            self._mark_partial(f"timeline write failed: {exc}")

    def _append_event(
        self,
        event_type: str,
        step_name: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        if self._event_writer is None:
            return
        try:
            self._event_writer.append(
                event_type,
                step_name=step_name,
                payload=payload,
                occurred_at=occurred_at,
            )
            self._events_available = True
        except OSError as exc:
            self._events_available = False
            self._mark_partial(f"event write failed: {exc}")

    def _mark_partial(self, issue: str) -> None:
        if issue not in self.observability_issues:
            self.observability_issues.append(sanitize_message(issue, self.project_root))
        self.observability_status = (
            ObservabilityStatus.FAILED
            if not self._events_available and not self._timeline_available
            else ObservabilityStatus.PARTIAL
        )


class _StepContext:
    def __init__(
        self,
        observer: RunObserver,
        step_name: str,
        prompt_file: PromptFile | None,
    ) -> None:
        if step_name not in FIXED_STEP_NAMES or step_name == "persistence":
            raise ValueError(f"invalid elapsed-time step: {step_name}")
        self.observer = observer
        self.step = observer.steps[step_name]
        self.prompt_file = prompt_file
        self.start_ns = 0
        self.token: Token[ActiveStep | None] | None = None

    def __enter__(self) -> StepObservation:
        self.step.status = StepStatus.RUNNING
        self.step.attempt_count += 1
        self.step.started_at = self.observer.clock.now()
        self.start_ns = self.observer.clock.monotonic_ns()
        active = ActiveStep(
            observer=self.observer,
            step_id=self.step.id,
            step_name=self.step.step_name,
            prompt_file_hash=(
                self.prompt_file.content_hash if self.prompt_file is not None else None
            ),
        )
        self.token = _ACTIVE_STEP.set(active)
        self.observer._append_event(  # noqa: SLF001
            "step_started",
            self.step.step_name,
            {"attempt_count": self.step.attempt_count},
        )
        return self.step

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        end_ns = self.observer.clock.monotonic_ns()
        self.step.ended_at = self.observer.clock.now()
        self.step.duration_ms = duration_ms(self.start_ns, end_ns)
        self.step.status = StepStatus.FAILED if exc is not None else StepStatus.COMPLETED
        self.observer._append_event(  # noqa: SLF001
            f"step_{self.step.status.value}",
            self.step.step_name,
            {
                "duration_ms": self.step.duration_ms,
                "attempt_count": self.step.attempt_count,
            },
        )
        if self.token is not None:
            _ACTIVE_STEP.reset(self.token)
        if isinstance(exc, Exception):
            self.observer.record_error(
                exc,
                step_name=self.step.step_name,
                attempt=self.step.attempt_count,
                is_terminal=True,
            )
        return False


class _PersistenceContext:
    def __init__(self, observer: RunObserver, operation: str) -> None:
        self.observer = observer
        self.operation = operation
        self.start_ns = 0

    def __enter__(self) -> None:
        step = self.observer.steps["persistence"]
        if step.status is StepStatus.PENDING:
            step.status = StepStatus.RUNNING
            step.attempt_count = 1
            step.started_at = self.observer.clock.now()
        self.start_ns = self.observer.clock.monotonic_ns()
        self.observer._append_event(  # noqa: SLF001
            "persistence_operation_started",
            "persistence",
            {"operation": self.operation},
        )
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        end_ns = self.observer.clock.monotonic_ns()
        ended_at = self.observer.clock.now()
        operation_duration = duration_ms(self.start_ns, end_ns)
        self.observer._persistence_duration_ms += operation_duration  # noqa: SLF001
        self.observer._persistence_last_at = ended_at  # noqa: SLF001
        step = self.observer.steps["persistence"]
        if step.status is StepStatus.COMPLETED:
            step.ended_at = ended_at
            step.duration_ms = self.observer._persistence_duration_ms  # noqa: SLF001
        self.observer._append_event(  # noqa: SLF001
            "persistence_operation_failed" if exc else "persistence_operation_completed",
            "persistence",
            {
                "operation": self.operation,
                "duration_ms": operation_duration,
            },
        )
        if isinstance(exc, Exception):
            step.status = StepStatus.FAILED
            step.ended_at = ended_at
            step.duration_ms = self.observer._persistence_duration_ms  # noqa: SLF001
            self.observer.record_error(
                exc,
                step_name="persistence",
                attempt=1,
                is_terminal=True,
            )
        return False


def _sum_tokens(
    calls: list[LLMCallObservation],
    field: str,
) -> int | None:
    values = [getattr(call, field) for call in calls]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)
