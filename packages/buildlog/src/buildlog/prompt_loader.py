"""Prompt loading helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from buildlog.exceptions import PromptFileError
from buildlog.hashing import sha256_file

PROMPT_NAMES = ("planner", "writer", "evaluator", "reviser")
OPTIONAL_PROMPT_NAMES = ("asset_planner",)
PROMPT_VERSION_PATTERN = re.compile(r"v[1-9][0-9]*")


@dataclass(frozen=True)
class PromptFile:
    """Versioned prompt file metadata."""

    name: str
    version: str
    path: Path
    content_hash: str


def load_prompt(prompts_dir: Path, name: str, version: str) -> str:
    """Load one named and versioned prompt file."""
    path = _prompt_path(prompts_dir, name, version)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptFileError(f"could not load prompt {path}: {exc}") from exc


def inspect_prompt_files(prompts_dir: Path, version: str) -> dict[str, PromptFile]:
    """Return content metadata for every prompt in one version."""
    prompts: dict[str, PromptFile] = {}
    for name in PROMPT_NAMES:
        path = _prompt_path(prompts_dir, name, version)
        try:
            content_hash = sha256_file(path)
        except OSError as exc:
            raise PromptFileError(f"could not inspect prompt {path}: {exc}") from exc
        prompts[name] = PromptFile(name, version, path, content_hash)
    return prompts


def _prompt_path(prompts_dir: Path, name: str, version: str) -> Path:
    if name not in (*PROMPT_NAMES, *OPTIONAL_PROMPT_NAMES):
        raise PromptFileError(f"unknown prompt name: {name}")
    if PROMPT_VERSION_PATTERN.fullmatch(version) is None:
        raise PromptFileError(f"invalid prompt version: {version}")
    return (prompts_dir / f"{name}_{version}.md").resolve()
