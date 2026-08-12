"""Trace artifact persistence for BuildLog runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from buildlog.exceptions import TraceWriteError


class RunTrace:
    """Writer for all artifacts created by one pipeline run."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def write_json(self, filename: str, data: BaseModel | dict[str, Any]) -> Path:
        """Write JSON trace data and return the artifact path."""
        if isinstance(data, BaseModel):
            payload = data.model_dump(mode="json")
        else:
            payload = data
        path = self.run_dir / filename
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            raise TraceWriteError(f"could not write {path}: {exc}") from exc
        return path

    def write_text(self, filename: str, content: str) -> Path:
        """Write a text trace artifact and return the artifact path."""
        path = self.run_dir / filename
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise TraceWriteError(f"could not write {path}: {exc}") from exc
        return path


def create_run_trace(runs_dir: Path, iteration_id: str) -> RunTrace:
    """Create and return a unique run trace directory."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in iteration_id)
    run_dir = runs_dir / f"{timestamp}_{safe_id}_{uuid4().hex[:8]}"
    counter = 1
    while run_dir.exists():
        run_dir = runs_dir / f"{timestamp}_{safe_id}_{counter}"
        counter += 1
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise TraceWriteError(f"could not create run directory: {exc}") from exc
    return RunTrace(run_dir)
