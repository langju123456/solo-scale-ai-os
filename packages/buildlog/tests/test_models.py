"""Tests for BuildLog Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from buildlog.models import Iteration


FIXTURE = Path(__file__).parent / "fixtures" / "valid_iteration.json"


def test_valid_input() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    iteration = Iteration.model_validate(data)

    assert iteration.id == "local-agent-001"


def test_missing_required_field() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data.pop("evidence")

    with pytest.raises(ValidationError):
        Iteration.model_validate(data)


def test_blank_list_values() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["actions"] = ["  "]

    with pytest.raises(ValidationError):
        Iteration.model_validate(data)
