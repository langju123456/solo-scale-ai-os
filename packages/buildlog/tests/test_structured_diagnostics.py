"""Tests for opt-in failed structured-output diagnostics."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from buildlog.config import load_settings
from buildlog.exceptions import ModelResponseError, StructuredOutputError
from buildlog.llm_client import LLMClient
from buildlog.models import Evaluation
from buildlog.observer import RunObserver
from buildlog.structured_diagnostics import capture_failed_structured_output


VALID_EVALUATION = {
    "technical_accuracy": 8,
    "specificity": 8,
    "readability": 8,
    "reader_value": 8,
    "evidence_coverage": 8,
    "unsupported_claims": [],
    "vague_sections": [],
    "revision_instructions": [],
    "hard_failure": False,
}


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(VALID_EVALUATION),
        f"```json\n{json.dumps(VALID_EVALUATION)}\n```",
    ],
    ids=["plain-json", "json-fence"],
)
def test_valid_structured_output_does_not_create_diagnostic(
    content: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        content,
        capture=True,
    )

    with observer.step("evaluator"):
        evaluation = client.complete_json("evaluate", Evaluation)

    assert evaluation.technical_accuracy == 8
    assert not (run_dir / "debug").exists()


@pytest.mark.parametrize(
    "content",
    [
        f"Here is the evaluation:\n{json.dumps(VALID_EVALUATION)}",
        f"<think>reasoning</think>\n{json.dumps(VALID_EVALUATION)}",
        f"{json.dumps(VALID_EVALUATION)}\ntrailing prose",
        f"{json.dumps(VALID_EVALUATION)}\n{json.dumps(VALID_EVALUATION)}",
        '{"technical_accuracy": 8, "specificity":',
    ],
    ids=[
        "prose-prefix",
        "reasoning-prefix",
        "trailing-prose",
        "multiple",
        "truncated",
    ],
)
def test_noisy_or_incomplete_json_remains_rejected(
    content: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        content,
        capture=False,
    )

    with pytest.raises(StructuredOutputError):
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert not (run_dir / "debug").exists()


def test_null_content_uses_model_response_error_without_capture(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        None,
        capture=True,
    )

    with pytest.raises(
        ModelResponseError,
        match="did not contain message content",
    ):
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert not (run_dir / "debug").exists()


def test_capture_flag_off_does_not_persist_raw_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        "sensitive invalid response",
        capture=False,
    )

    with pytest.raises(StructuredOutputError):
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert not (run_dir / "debug").exists()


def test_capture_remains_disabled_outside_development(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        "sensitive invalid response",
        capture=True,
        environment="production",
    )

    with pytest.raises(StructuredOutputError):
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert not (run_dir / "debug").exists()


def test_capture_flag_on_persists_raw_only_in_private_debug_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    raw = "sensitive-marker invalid response"
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        raw,
        capture=True,
    )

    with pytest.raises(StructuredOutputError) as error:
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)
    observer.fail_pipeline(error.value)

    debug_dir = run_dir / "debug"
    response_path = debug_dir / "failed_evaluator_response.txt"
    metadata_path = debug_dir / "failed_evaluator_response.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    run_metadata = json.dumps(observer.run_metadata_payload())

    assert response_path.read_text(encoding="utf-8") == raw
    assert metadata["sensitive"] is True
    assert metadata["sha256"] == sha256(raw.encode("utf-8")).hexdigest()
    assert metadata["truncated"] is False
    assert raw not in events
    assert raw not in run_metadata
    assert raw not in str(error.value)
    if os.name == "posix":
        assert stat.S_IMODE(debug_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(response_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600


def test_schema_failure_is_captured_without_changing_validation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_marker = "SENSITIVE-SCHEMA-MARKER-DO-NOT-LEAK"
    invalid = {**VALID_EVALUATION, "technical_accuracy": sensitive_marker}
    raw = json.dumps(invalid)
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        raw,
        capture=True,
    )

    with pytest.raises(StructuredOutputError) as error:
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)
    observer.fail_pipeline(error.value)

    response = run_dir / "debug" / "failed_evaluator_response.txt"
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    run_metadata = json.dumps(observer.run_metadata_payload())

    assert response.read_text(encoding="utf-8") == raw
    assert str(error.value) == (
        "model returned schema-invalid structured output"
    )
    assert isinstance(error.value.__cause__, ValidationError)
    assert sensitive_marker not in str(error.value)
    assert sensitive_marker not in events
    assert sensitive_marker not in run_metadata
    assert observer.errors[0].error_code == "llm_schema_invalid"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_symlinked_debug_directory_cannot_escape_run_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_marker = "SENSITIVE-SYMLINK-MARKER-DO-NOT-WRITE"
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        sensitive_marker,
        capture=True,
    )
    external_directory = tmp_path / "external"
    external_directory.mkdir(mode=0o755)
    external_mode = stat.S_IMODE(external_directory.stat().st_mode)
    (run_dir / "debug").symlink_to(
        external_directory,
        target_is_directory=True,
    )

    with pytest.raises(StructuredOutputError, match="invalid JSON") as error:
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert list(external_directory.iterdir()) == []
    assert stat.S_IMODE(external_directory.stat().st_mode) == external_mode
    assert str(error.value).startswith("model returned invalid JSON")
    assert observer.observability_status.value == "partial"
    assert len(observer.observability_issues) == 1
    assert observer.observability_issues[0].startswith(
        "could not capture failed structured output:"
    )


def test_capture_is_utf8_bounded_and_records_full_hash(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    raw = "é" * 10

    result = capture_failed_structured_output(
        run_dir,
        "evaluator",
        raw,
        max_bytes=9,
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert result.response_path.read_text(encoding="utf-8") == "é" * 4
    assert metadata["original_bytes"] == 20
    assert metadata["captured_bytes"] == 8
    assert metadata["captured_characters"] == 4
    assert metadata["truncated"] is True
    assert metadata["sha256"] == sha256(raw.encode("utf-8")).hexdigest()


def test_capture_write_failure_does_not_mask_structured_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, observer, run_dir = _client(
        tmp_path,
        monkeypatch,
        "invalid response",
        capture=True,
    )
    debug_dir = run_dir / "debug"
    debug_dir.mkdir()
    (debug_dir / "failed_evaluator_response.txt").write_text(
        "existing",
        encoding="utf-8",
    )

    with pytest.raises(StructuredOutputError, match="invalid JSON"):
        with observer.step("evaluator"):
            client.complete_json("evaluate", Evaluation)

    assert observer.observability_status.value == "partial"
    assert observer.observability_issues == [
        "could not capture failed structured output: FileExistsError"
    ]


def _client(
    tmp_path: Path,
    monkeypatch,
    content: object,
    *,
    capture: bool,
    environment: str = "development",
) -> tuple[LLMClient, RunObserver, Path]:
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    settings = replace(
        load_settings(Path.cwd()),
        capture_failed_structured_output=capture,
        environment=environment,
        runs_dir=tmp_path / "runs",
        prompts_dir=tmp_path / "prompts",
        database_url=f"sqlite:///{tmp_path / 'buildlog.db'}",
    )
    observer = RunObserver("run-001", run_dir, settings, tmp_path)
    client = LLMClient(settings)
    monkeypatch.setattr(
        client,
        "_completion",
        lambda _prompt: _response(content),
    )
    return client, observer, run_dir


def _response(content: object) -> object:
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
