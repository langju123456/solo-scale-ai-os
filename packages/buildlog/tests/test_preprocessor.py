"""Tests for deterministic preprocessing."""

from __future__ import annotations

import json
from pathlib import Path

from buildlog.models import Iteration
from buildlog.preprocessor import normalize_iteration


def test_duplicate_normalization() -> None:
    data = json.loads((Path(__file__).parent / "fixtures" / "valid_iteration.json").read_text(encoding="utf-8"))
    data["actions"] = ["  First   action  ", "First action"]
    iteration = Iteration.model_validate(data)

    normalized = normalize_iteration(iteration)

    assert normalized.actions == ["First action"]
