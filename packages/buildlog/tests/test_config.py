"""Tests for deterministic project-root configuration loading."""

from __future__ import annotations

import os
from pathlib import Path

from buildlog.config import load_settings


def test_settings_load_dotenv_from_explicit_project_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text(
        "BUILDLOG_PROMPT_VERSION=project-root-version\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BUILDLOG_PROMPT_VERSION", raising=False)
    monkeypatch.chdir(tmp_path)

    try:
        settings = load_settings(project_root)
    finally:
        os.environ.pop("BUILDLOG_PROMPT_VERSION", None)

    assert settings.prompt_version == "project-root-version"
    assert settings.prompts_dir == project_root / "prompts"
    assert settings.runs_dir == project_root / "runs"


def test_failed_structured_output_capture_is_explicitly_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BUILDLOG_CAPTURE_FAILED_STRUCTURED_OUTPUT", "true")

    settings = load_settings(tmp_path)

    assert settings.capture_failed_structured_output
