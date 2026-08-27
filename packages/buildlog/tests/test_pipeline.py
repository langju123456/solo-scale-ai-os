"""Tests for pipeline orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from buildlog.config import load_settings
from buildlog.llm_client import LLMClient
from buildlog.pipeline import run_pipeline
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository

def test_one_revision_limit(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "iteration.json"
    fixture = Path(__file__).parent / "fixtures" / "valid_iteration.json"
    input_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    settings = load_settings(Path.cwd())
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "runs_dir": tmp_path / "runs",
            "database_url": f"sqlite:///{tmp_path / 'buildlog.db'}",
            "model_digest": "test-model-digest",
        }
    )
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()

    responses = iter(
        [
            _response(
                json.dumps(
                    {
                        "central_idea": "Adapter boundaries made the local agent easier to reason about.",
                        "hook": "I ran into a compatibility issue while moving an agent tutorial local.",
                        "technical_points": ["LiteLLM", "Ollama", "Qwen3"],
                        "decision_story": "I kept the tutorial structure and changed only the model adapter.",
                        "reader_value": "Small adapter boundaries make debugging clearer.",
                        "ending": "The useful lesson was the boundary, not the tool list.",
                    }
                ),
                10,
                5,
            ),
            _response("First draft with one weak claim.", 20, 6),
            _response(
                json.dumps(
                    {
                        "technical_accuracy": 6,
                        "specificity": 8,
                        "readability": 8,
                        "reader_value": 8,
                        "evidence_coverage": 8,
                        "unsupported_claims": ["unsupported duration"],
                        "vague_sections": [],
                        "revision_instructions": ["Remove the unsupported duration."],
                        "hard_failure": False,
                    }
                ),
                30,
                7,
            ),
            _response("Revised draft using only supported evidence.", 40, 8),
        ]
    )
    monkeypatch.setattr(
        LLMClient,
        "_completion",
        lambda _self, _prompt: next(responses),
    )

    result = run_pipeline(input_path, settings, repository)

    revised_files = list(result.run_dir.glob("05_revised_draft.md"))
    stored_run = repository.get_run(result.run_dir.name)
    artifact_types = {
        artifact.artifact_type for artifact in repository.list_artifacts(result.run_dir.name)
    }
    stored_evaluation = repository.get_evaluation(result.run_dir.name)
    run_observation = repository.get_run_observation(result.run_dir.name)
    steps = repository.list_step_observations(result.run_dir.name)
    llm_calls = repository.list_llm_call_observations(result.run_dir.name)
    dependencies = repository.list_artifact_dependencies(result.run_dir.name)
    timeline = json.loads(
        (result.run_dir / "timeline.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (result.run_dir / "run_metadata.json").read_text(encoding="utf-8")
    )
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert result.revision_performed
    assert len(revised_files) == 1
    assert result.final_path.read_text(encoding="utf-8").startswith("Revised draft")
    assert stored_run is not None
    assert stored_run.status == "completed"
    assert stored_run.revision_performed
    assert "revised_draft" in artifact_types
    assert "final" in artifact_types
    assert stored_evaluation is not None
    assert stored_evaluation.technical_accuracy == 6
    assert run_observation is not None
    assert run_observation.pipeline_status.value == "completed"
    assert run_observation.llm_call_count == 4
    assert run_observation.highest_token_step == "reviser"
    assert run_observation.revision_improvement_status.value == "not_measured"
    assert [step.step_name for step in steps] == [
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
    ]
    assert all(step.attempt_count == 1 for step in steps)
    assert len(llm_calls) == 4
    assert sum(call.total_tokens or 0 for call in llm_calls) == 126
    final_dependencies = [
        dependency.source_artifact_id
        for dependency in dependencies
        if dependency.artifact_id.endswith(":final")
    ]
    assert final_dependencies == [f"{result.run_dir.name}:revised_draft"]
    assert timeline["llm_call_count"] == 4
    assert timeline["revision_performed"]
    assert len(timeline["steps"]) == 10
    assert metadata["pipeline_status"] == "completed"
    assert metadata["observability_status"] == "complete"
    assert metadata["revision_improvement_status"] == "not_measured"
    assert metadata["model_digest"] == "test-model-digest"
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1)
    )


def test_no_revision_records_draft_as_final_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = tmp_path / "iteration.json"
    fixture = Path(__file__).parent / "fixtures" / "valid_iteration.json"
    input_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    settings = load_settings(Path.cwd())
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "runs_dir": tmp_path / "runs",
            "database_url": f"sqlite:///{tmp_path / 'buildlog.db'}",
            "model_digest": "test-model-digest",
        }
    )
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()
    responses = iter(
        [
            _response(
                json.dumps(
                    {
                        "central_idea": "A clear plan.",
                        "hook": "A concrete hook.",
                        "technical_points": ["One detail"],
                        "decision_story": "A bounded decision.",
                        "reader_value": "A useful lesson.",
                        "ending": "A direct ending.",
                    }
                ),
                10,
                5,
            ),
            _response("Supported first draft.", 20, 6),
            _response(
                json.dumps(
                    {
                        "technical_accuracy": 9,
                        "specificity": 9,
                        "readability": 9,
                        "reader_value": 9,
                        "evidence_coverage": 9,
                        "unsupported_claims": [],
                        "vague_sections": [],
                        "revision_instructions": [],
                        "hard_failure": False,
                    }
                ),
                30,
                7,
            ),
        ]
    )
    monkeypatch.setattr(
        LLMClient,
        "_completion",
        lambda _self, _prompt: next(responses),
    )

    result = run_pipeline(input_path, settings, repository)
    steps = repository.list_step_observations(result.run_dir.name)
    dependencies = repository.list_artifact_dependencies(result.run_dir.name)
    observation = repository.get_run_observation(result.run_dir.name)

    reviser = next(step for step in steps if step.step_name == "reviser")
    final_sources = [
        dependency.source_artifact_id
        for dependency in dependencies
        if dependency.artifact_id.endswith(":final")
    ]
    assert not result.revision_performed
    assert reviser.status.value == "skipped"
    assert reviser.attempt_count == 0
    assert reviser.skip_reason == "revision_not_required"
    assert final_sources == [f"{result.run_dir.name}:draft"]
    assert observation is not None
    assert observation.revision_improvement_status.value == "not_applicable"


def test_failed_run_persists_only_a_sanitized_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = tmp_path / "iteration.json"
    fixture = Path(__file__).parent / "fixtures" / "valid_iteration.json"
    input_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    settings = load_settings(Path.cwd())
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "runs_dir": tmp_path / "runs",
            "database_url": f"sqlite:///{tmp_path / 'buildlog.db'}",
        }
    )
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()

    def fail_with_secret(_self, _prompt):
        raise RuntimeError("api_key=do-not-persist /Users/private/project")

    monkeypatch.setattr(LLMClient, "_completion", fail_with_secret)

    with pytest.raises(RuntimeError, match="do-not-persist"):
        run_pipeline(input_path, settings, repository)

    run_dir = next(path for path in (tmp_path / "runs").iterdir() if path.is_dir())
    stored = repository.get_run(run_dir.name)
    assert stored is not None
    assert stored.status == "failed"
    assert "do-not-persist" not in (stored.error_message or "")
    assert "/Users/" not in (stored.error_message or "")
    assert "<redacted>" in (stored.error_message or "")


def _response(content: str, prompt_tokens: int, completion_tokens: int) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
