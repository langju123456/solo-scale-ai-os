"""Tests for versioned prompt loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from buildlog.exceptions import PromptFileError
from buildlog.prompt_loader import PROMPT_NAMES, inspect_prompt_files, load_prompt


def test_load_and_inspect_prompt_version(tmp_path: Path) -> None:
    for name in PROMPT_NAMES:
        (tmp_path / f"{name}_v2.md").write_text(
            f"{name} prompt v2",
            encoding="utf-8",
        )

    prompts = inspect_prompt_files(tmp_path, "v2")

    assert set(prompts) == set(PROMPT_NAMES)
    assert all(prompt.version == "v2" for prompt in prompts.values())
    assert all(len(prompt.content_hash) == 64 for prompt in prompts.values())
    assert load_prompt(tmp_path, "writer", "v2") == "writer prompt v2"


def test_reject_invalid_prompt_version(tmp_path: Path) -> None:
    with pytest.raises(PromptFileError, match="invalid prompt version"):
        inspect_prompt_files(tmp_path, "../v2")


def test_load_optional_asset_planner_without_changing_pipeline_prompt_set(
    tmp_path: Path,
) -> None:
    (tmp_path / "asset_planner_v1.md").write_text(
        "asset planner prompt",
        encoding="utf-8",
    )

    assert (
        load_prompt(tmp_path, "asset_planner", "v1")
        == "asset planner prompt"
    )
    assert "asset_planner" not in PROMPT_NAMES
