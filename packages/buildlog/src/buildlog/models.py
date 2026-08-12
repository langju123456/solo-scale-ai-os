"""Pydantic models used by the BuildLog pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _reject_blank(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class Decision(BaseModel):
    """A decision made during a development iteration."""

    decision: str
    reason: str
    alternatives_considered: list[str]

    _clean_decision = field_validator("decision", "reason")(_reject_blank)

    @field_validator("alternatives_considered")
    @classmethod
    def validate_alternatives(cls, values: list[str]) -> list[str]:
        """Reject empty alternative lists and blank values."""
        return validate_non_empty_string_list(values)


class Iteration(BaseModel):
    """Structured evidence for one development iteration."""

    id: str
    title: str
    goal: str
    context: str
    problem: str
    actions: list[str]
    decisions: list[Decision]
    trade_offs: list[str]
    result: str
    lessons: list[str]
    evidence: list[str]
    audience: str
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    _clean_strings = field_validator(
        "id",
        "title",
        "goal",
        "context",
        "problem",
        "result",
        "audience",
    )(_reject_blank)

    @field_validator("actions", "trade_offs", "lessons", "evidence")
    @classmethod
    def validate_lists(cls, values: list[str]) -> list[str]:
        """Reject empty lists and blank list entries."""
        return validate_non_empty_string_list(values)

    @field_validator("decisions")
    @classmethod
    def validate_decisions(cls, values: list[Decision]) -> list[Decision]:
        """Require at least one decision."""
        if not values:
            raise ValueError("must contain at least one decision")
        return values


class StoryPlan(BaseModel):
    """Structured output from the planner."""

    central_idea: str
    hook: str
    technical_points: list[str]
    decision_story: str
    reader_value: str
    ending: str

    model_config = ConfigDict(extra="forbid")

    _clean_strings = field_validator(
        "central_idea",
        "hook",
        "decision_story",
        "reader_value",
        "ending",
    )(_reject_blank)

    @field_validator("technical_points")
    @classmethod
    def validate_points(cls, values: list[str]) -> list[str]:
        """Reject blank technical points."""
        return validate_non_empty_string_list(values)


class Evaluation(BaseModel):
    """Structured draft evaluation."""

    technical_accuracy: int = Field(ge=1, le=10)
    specificity: int = Field(ge=1, le=10)
    readability: int = Field(ge=1, le=10)
    reader_value: int = Field(ge=1, le=10)
    evidence_coverage: int = Field(ge=1, le=10)
    unsupported_claims: list[str] = Field(default_factory=list)
    vague_sections: list[str] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)
    hard_failure: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("unsupported_claims", "vague_sections", "revision_instructions")
    @classmethod
    def validate_feedback(cls, values: list[str]) -> list[str]:
        """Allow empty feedback lists but reject blank entries."""
        for value in values:
            _reject_blank(value)
        return values


def validate_non_empty_string_list(values: list[str]) -> list[str]:
    """Validate a list contains at least one non-blank string."""
    if not values:
        raise ValueError("must contain at least one item")
    for value in values:
        _reject_blank(value)
    return values
