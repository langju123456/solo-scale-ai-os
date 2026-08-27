"""Tests for trace directory creation."""

from __future__ import annotations

from pathlib import Path

from buildlog.trace import create_run_trace


def test_trace_directory_creation(tmp_path: Path) -> None:
    trace = create_run_trace(tmp_path, "iteration/one")

    assert trace.run_dir.exists()
    assert "iteration-one" in trace.run_dir.name
