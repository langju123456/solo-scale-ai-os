"""Pure helpers for observability metadata and reproducibility."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Protocol

from pydantic import ValidationError

from buildlog.exceptions import (
    InputFileError,
    ModelResponseError,
    PersistenceError,
    PromptFileError,
    StructuredOutputError,
    TraceWriteError,
)
from buildlog.observability_models import ErrorCategory

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token)"
    r"(\s*[:=]\s*|\s+)(bearer\s+)?[^\s,;]+"
)


class Clock(Protocol):
    """Clock boundary used by instrumentation and deterministic tests."""

    def now(self) -> datetime:
        """Return a timezone-aware wall-clock timestamp."""

    def monotonic_ns(self) -> int:
        """Return a monotonic timestamp in nanoseconds."""


class SystemClock:
    """Production clock implementation."""

    def now(self) -> datetime:
        """Return the current UTC timestamp."""
        from datetime import UTC, datetime

        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        """Return a monotonic timestamp in nanoseconds."""
        return perf_counter_ns()


@dataclass(frozen=True)
class GitState:
    """Version-control state relevant to run replay."""

    commit: str | None
    branch: str | None
    working_tree_dirty: bool | None


@dataclass(frozen=True)
class ClassifiedError:
    """Stable error category and incrementally extensible code."""

    category: ErrorCategory
    code: str


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest for UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sha256(value: dict[str, Any]) -> str:
    """Hash a mapping after deterministic JSON serialization."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def duration_ms(start_ns: int, end_ns: int) -> int:
    """Convert a monotonic interval to non-negative milliseconds."""
    return max(0, round((end_ns - start_ns) / 1_000_000))


def split_provider_model(configured_model: str) -> tuple[str, str]:
    """Split a LiteLLM model string into provider and model name."""
    provider, separator, model = configured_model.partition("/")
    if not separator:
        return "unknown", configured_model
    return provider, model


def classify_error(error: Exception) -> ClassifiedError:
    """Map an exception to the stable BuildLog error taxonomy."""
    message = str(error).lower()
    if isinstance(error, InputFileError):
        return ClassifiedError(ErrorCategory.INPUT_VALIDATION, "input_invalid")
    if isinstance(error, PromptFileError):
        return ClassifiedError(ErrorCategory.PROMPT_LOADING, "prompt_unavailable")
    if isinstance(error, StructuredOutputError):
        if "invalid json" in message:
            return ClassifiedError(ErrorCategory.JSON_PARSE, "llm_json_invalid")
        return ClassifiedError(ErrorCategory.SCHEMA_VALIDATION, "llm_schema_invalid")
    if isinstance(error, ModelResponseError):
        if "empty content" in message:
            return ClassifiedError(ErrorCategory.EMPTY_RESPONSE, "llm_empty_response")
        if "timeout" in message or "timed out" in message:
            return ClassifiedError(ErrorCategory.TIMEOUT, "llm_timeout")
        return ClassifiedError(ErrorCategory.TRANSPORT, "llm_transport_failure")
    if isinstance(error, TraceWriteError):
        return ClassifiedError(ErrorCategory.ARTIFACT_WRITE, "artifact_write_failed")
    if isinstance(error, PersistenceError):
        return ClassifiedError(ErrorCategory.PERSISTENCE, "persistence_failed")
    if isinstance(error, ValidationError):
        return ClassifiedError(ErrorCategory.SCHEMA_VALIDATION, "schema_invalid")
    return ClassifiedError(ErrorCategory.UNKNOWN, "unknown_error")


def sanitize_message(message: str, project_root: Path | None = None) -> str:
    """Redact likely secrets and local absolute paths from an error message."""
    sanitized = SECRET_PATTERN.sub(r"\1\2<redacted>", message)
    if project_root is not None:
        sanitized = sanitized.replace(str(project_root.resolve()), "<project_root>")
    sanitized = re.sub(r"/Users/[^/\s]+", "~", sanitized)
    return sanitized[:500]


def sanitized_error_message(error: Exception, project_root: Path) -> str:
    """Return a concise message without embedding structured payload content."""
    classified = classify_error(error)
    generic_messages = {
        ErrorCategory.INPUT_VALIDATION: "input file failed validation or parsing",
        ErrorCategory.JSON_PARSE: "model returned invalid JSON",
        ErrorCategory.SCHEMA_VALIDATION: "structured data failed schema validation",
        ErrorCategory.EMPTY_RESPONSE: "model returned empty content",
    }
    if classified.category in generic_messages:
        return generic_messages[classified.category]
    return sanitize_message(str(error).splitlines()[0], project_root)


def inspect_git_state(project_root: Path) -> GitState:
    """Return commit, branch, and dirty state without failing the pipeline."""
    commit = _run_git(project_root, "rev-parse", "HEAD")
    branch = _run_git(project_root, "branch", "--show-current")
    status = _run_git(project_root, "status", "--porcelain")
    dirty = None if status is None else bool(status)
    return GitState(commit=commit, branch=branch, working_tree_dirty=dirty)


def _run_git(project_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
