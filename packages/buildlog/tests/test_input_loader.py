"""Tests for iteration input loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from buildlog.exceptions import InputFileError
from buildlog.input_loader import load_iteration


def test_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(InputFileError):
        load_iteration(tmp_path / "missing.json")


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")

    with pytest.raises(InputFileError):
        load_iteration(path)
