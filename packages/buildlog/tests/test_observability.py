"""Tests for observability schemas, telemetry, and replay evidence."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildlog.config import load_settings
from buildlog.exceptions import (
    InputFileError,
    ModelResponseError,
    PersistenceError,
    StructuredOutputError,
)
from buildlog.llm_client import LLMClient
from buildlog.models import Evaluation
from buildlog.observer import RunObserver
from buildlog.observability_models import StepStatus
from buildlog.observability_utils import (
    GitState,
    classify_error,
    sanitize_message,
)
from buildlog.prompt_loader import PromptFile
from buildlog.pipeline import run_pipeline
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


class FakeClock:
    """Manually advanced deterministic wall and monotonic clock."""

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 28, tzinfo=UTC)
        self.current_ns = 0

    def now(self) -> datetime:
        return self.current

    def monotonic_ns(self) -> int:
        return self.current_ns

    def advance(self, milliseconds: int) -> None:
        self.current += timedelta(milliseconds=milliseconds)
        self.current_ns += milliseconds * 1_000_000


def test_run_observer_records_timeline_and_reproducibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = FakeClock()
    settings = _settings(tmp_path)
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    observer = RunObserver(
        "run-001",
        run_dir,
        settings,
        tmp_path,
        clock=clock,
    )
    observer.bind_iteration("iteration-001")
    observer.bind_prompts(_prompts(tmp_path))
    monkeypatch.setattr(
        "buildlog.observer.inspect_git_state",
        lambda _root: GitState("abc123", "master", False),
    )

    started_at = clock.now()
    start_ns = clock.monotonic_ns()
    clock.advance(5)
    observer.record_preceding_step(
        "validation",
        started_at=started_at,
        ended_at=clock.now(),
        start_ns=start_ns,
        end_ns=clock.monotonic_ns(),
    )
    started_at = clock.now()
    start_ns = clock.monotonic_ns()
    clock.advance(5)
    observer.record_preceding_step(
        "preprocessing",
        started_at=started_at,
        ended_at=clock.now(),
        start_ns=start_ns,
        end_ns=clock.monotonic_ns(),
    )
    with observer.step("prompt_loading"):
        clock.advance(10)
    with observer.step("planner", prompt_file=_prompts(tmp_path)["planner"]):
        pending = observer.start_llm_call("rendered planner prompt")
        clock.advance(1500)
        observer.finish_llm_call(
            pending,
            provider_end_ns=clock.monotonic_ns(),
            provider_ended_at=clock.now(),
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
            finish_reason="stop",
            error=None,
        )

    input_path = run_dir / "00_input.json"
    normalized_path = run_dir / "01_normalized_input.json"
    input_path.write_text("{}", encoding="utf-8")
    normalized_path.write_text("{}", encoding="utf-8")
    observer.record_artifact(
        "input",
        input_path,
        producer_step_name="validation",
        source_artifact_types=[],
    )
    observer.record_artifact(
        "normalized_input",
        normalized_path,
        producer_step_name="preprocessing",
        source_artifact_types=["input"],
    )
    observer.skip_step("reviser", "revision_not_required")
    observation = observer.complete_pipeline(False)

    assert observation.pipeline_status.value == "completed"
    assert observation.observability_status.value == "complete"
    assert observation.reproducibility_status.value == "complete"
    assert observation.slowest_step == "planner"
    assert observation.highest_token_step == "planner"
    assert observation.llm_call_count == 1
    assert observation.total_tokens == 20
    assert observer.steps["reviser"].status is StepStatus.SKIPPED
    assert observer.steps["reviser"].attempt_count == 0
    assert observer.steps["reviser"].skip_reason == "revision_not_required"
    assert len(json.loads((run_dir / "timeline.json").read_text())["steps"]) == 10


def test_revision_trigger_and_improvement_status(tmp_path: Path) -> None:
    observer = RunObserver(
        "run-002",
        _run_dir(tmp_path, "run-002"),
        _settings(tmp_path),
        tmp_path,
    )
    evaluation = Evaluation(
        technical_accuracy=6,
        specificity=8,
        readability=8,
        reader_value=8,
        evidence_coverage=8,
        unsupported_claims=["unsupported"],
        vague_sections=[],
        revision_instructions=["Remove unsupported claim."],
        hard_failure=True,
    )

    observer.record_revision_decision(evaluation, revision_required=True)
    observer.record_revision_result("draft", "revised")

    assert observer.revision_decision is not None
    assert observer.revision_decision.revision_required
    assert [
        trigger.type for trigger in observer.revision_decision.triggered_by
    ] == ["score_threshold", "hard_failure"]
    assert observer.revision_decision.triggered_by[0].metric == "technical_accuracy"
    assert observer.revision_decision.triggered_by[1].code is None
    assert observer.revision_output_changed
    assert observer.revision_improvement_status.value == "not_measured"


def test_llm_client_records_usage_and_structured_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observer = RunObserver(
        "run-003",
        _run_dir(tmp_path, "run-003"),
        _settings(tmp_path),
        tmp_path,
    )
    prompt = _prompts(tmp_path)["planner"]
    client = LLMClient(_settings(tmp_path))
    responses = iter(
        [
            _response("plain response"),
            _response("not-json"),
        ]
    )
    monkeypatch.setattr(client, "_completion", lambda _prompt: next(responses))

    with observer.step("planner", prompt_file=prompt):
        assert client.complete_text("rendered prompt") == "plain response"
    with pytest.raises(StructuredOutputError):
        with observer.step("evaluator", prompt_file=_prompts(tmp_path)["evaluator"]):
            client.complete_json("bad evaluator prompt", Evaluation)

    assert observer.llm_calls[0].prompt_tokens == 11
    assert observer.llm_calls[0].completion_tokens == 7
    assert observer.llm_calls[0].finish_reason == "stop"
    assert observer.llm_calls[1].status == "failed"
    assert observer.llm_calls[1].error_category is not None
    assert observer.llm_calls[1].error_category.value == "json_parse"
    assert len(observer.errors) == 1
    assert observer.errors[0].error_code == "llm_json_invalid"


def test_missing_provider_usage_remains_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observer = RunObserver(
        "run-no-usage",
        _run_dir(tmp_path, "run-no-usage"),
        _settings(tmp_path),
        tmp_path,
    )
    client = LLMClient(_settings(tmp_path))
    monkeypatch.setattr(
        client,
        "_completion",
        lambda _prompt: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="plain response"),
                    finish_reason="stop",
                )
            ]
        ),
    )

    with observer.step("writer", prompt_file=_prompts(tmp_path)["writer"]):
        assert client.complete_text("rendered prompt") == "plain response"

    call = observer.llm_calls[0]
    assert call.prompt_tokens is None
    assert call.completion_tokens is None
    assert call.total_tokens is None
    assert call.token_usage_status.value == "unavailable"
    assert call.token_usage_source == "provider_not_returned"


def test_error_classification_and_sanitization(tmp_path: Path) -> None:
    classified = classify_error(ModelResponseError("model returned empty content"))
    message = sanitize_message(
        f"token=secret-value failed at {tmp_path}/private.json",
        tmp_path,
    )

    assert classified.category.value == "empty_response"
    assert classified.code == "llm_empty_response"
    assert "secret-value" not in message
    assert "<project_root>" in message


def test_input_validation_failure_still_writes_observability(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()
    input_path = tmp_path / "invalid.json"
    input_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(InputFileError):
        run_pipeline(input_path, settings, repository)

    run_dirs = list(settings.runs_dir.iterdir())
    assert len(run_dirs) == 1
    metadata = json.loads(
        (run_dirs[0] / "run_metadata.json").read_text(encoding="utf-8")
    )
    timeline = json.loads(
        (run_dirs[0] / "timeline.json").read_text(encoding="utf-8")
    )
    errors = [
        json.loads(line)
        for line in (run_dirs[0] / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"event_type":"error_recorded"' in line
    ]

    assert metadata["pipeline_status"] == "failed"
    assert timeline["steps"][0]["status"] == "failed"
    assert all(
        step["status"] == "skipped" for step in timeline["steps"][1:]
    )
    assert errors[0]["payload"]["error_category"] == "input_validation"


def test_sqlite_observability_failure_marks_partial_without_raising(
    tmp_path: Path,
) -> None:
    class FailingRepository:
        def save_observability_bundle(self, *args, **kwargs) -> None:
            raise PersistenceError("telemetry database unavailable")

    observer = RunObserver(
        "run-004",
        _run_dir(tmp_path, "run-004"),
        _settings(tmp_path),
        tmp_path,
        FailingRepository(),
    )
    observer.enable_projection()

    observation = observer.complete_pipeline(False)

    assert observation.pipeline_status.value == "completed"
    assert observation.observability_status.value == "partial"
    assert "SQLite observability projection failed" in observation.observability_issues[0]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_event_writer_initialization_failure_does_not_block_pipeline(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(tmp_path, "run-events-unavailable")
    outside = tmp_path / "outside-events.jsonl"
    outside.write_text("unchanged\n", encoding="utf-8")
    (run_dir / "events.jsonl").symlink_to(outside)

    observer = RunObserver(
        "run-events-unavailable",
        run_dir,
        _settings(tmp_path),
        tmp_path,
    )
    observation = observer.complete_pipeline(False)

    assert observation.pipeline_status.value == "completed"
    assert observation.observability_status.value == "partial"
    assert "event writer initialization failed" in (
        observation.observability_issues[0]
    )
    assert outside.read_text(encoding="utf-8") == "unchanged\n"


def _settings(tmp_path: Path):
    settings = load_settings(Path.cwd())
    return settings.__class__(
        **{
            **settings.__dict__,
            "model": "ollama_chat/qwen3:8b",
            "model_digest": "digest-001",
            "prompts_dir": tmp_path / "prompts",
            "runs_dir": tmp_path / "runs",
            "database_url": f"sqlite:///{tmp_path / 'buildlog.db'}",
        }
    )


def _prompts(tmp_path: Path) -> dict[str, PromptFile]:
    return {
        name: PromptFile(
            name=name,
            version="v2",
            path=tmp_path / "prompts" / f"{name}_v2.md",
            content_hash=(name[0] * 64),
        )
        for name in ("planner", "writer", "evaluator", "reviser")
    }


def _run_dir(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "runs" / name
    path.mkdir(parents=True)
    return path


def _response(content: str) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        ),
    )
