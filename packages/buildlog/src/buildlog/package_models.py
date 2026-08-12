"""Validated contracts for local publishing packages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_SOURCE_FIELDS = {
    "title",
    "goal",
    "context",
    "problem",
    "actions",
    "decisions",
    "trade_offs",
    "result",
    "lessons",
    "evidence",
    "audience",
    "metadata",
}


class BaseCardSpec(BaseModel):
    """Fields shared by every grounded card specification."""

    title: str = Field(min_length=1, max_length=80)
    source_fields: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        """Normalize surrounding whitespace in a card title."""
        return value.strip()

    @field_validator("source_fields")
    @classmethod
    def validate_source_fields(cls, values: list[str]) -> list[str]:
        """Require unique references to known Iteration fields."""
        cleaned = [value.strip() for value in values]
        unknown = sorted(set(cleaned) - ALLOWED_SOURCE_FIELDS)
        if unknown:
            raise ValueError(f"unknown source fields: {', '.join(unknown)}")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("source fields must be unique")
        return cleaned


class TitleCardSpec(BaseCardSpec):
    """Opening card for one package."""

    type: Literal["title"]
    subtitle: str = Field(min_length=1, max_length=220)


class ArchitectureCardSpec(BaseCardSpec):
    """Grounded flow or system-boundary card."""

    type: Literal["architecture"]
    steps: list[str] = Field(min_length=3, max_length=8)
    summary: str = Field(min_length=1, max_length=220)

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, values: list[str]) -> list[str]:
        """Keep architecture steps concise enough to render."""
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 90 for value in cleaned):
            raise ValueError("architecture steps must contain 1-90 characters")
        return cleaned


class TradeoffCardSpec(BaseCardSpec):
    """Decision card with one explicit benefit and cost."""

    type: Literal["tradeoff"]
    decision: str = Field(min_length=1, max_length=180)
    benefit: str = Field(min_length=1, max_length=240)
    cost: str = Field(min_length=1, max_length=240)


class TakeawayCardSpec(BaseCardSpec):
    """Closing card with supported lessons."""

    type: Literal["takeaway"]
    items: list[str] = Field(min_length=2, max_length=4)

    @field_validator("items")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        """Keep takeaway items non-blank and renderable."""
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 220 for value in cleaned):
            raise ValueError("takeaway items must contain 1-220 characters")
        return cleaned


CardSpec = Annotated[
    TitleCardSpec | ArchitectureCardSpec | TradeoffCardSpec | TakeawayCardSpec,
    Field(discriminator="type"),
]


class AssetPlan(BaseModel):
    """Validated selection and content for one small package."""

    cards: list[CardSpec] = Field(min_length=3, max_length=4)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_composition(self) -> AssetPlan:
        """Normalize dense flows and require one coherent composition."""
        normalized_cards: list[CardSpec] = []
        for card in self.cards:
            if isinstance(card, ArchitectureCardSpec) and len(card.steps) > 5:
                card = card.model_copy(update={"steps": _reduce_steps(card.steps)})
            normalized_cards.append(card)
        self.cards = normalized_cards
        card_types = [card.type for card in self.cards]
        if card_types[0] != "title":
            raise ValueError("the first card must be a title card")
        if card_types[-1] != "takeaway":
            raise ValueError("the final card must be a takeaway card")
        if len(card_types) != len(set(card_types)):
            raise ValueError("card types must not repeat")
        return self


def _reduce_steps(values: list[str]) -> list[str]:
    """Keep a five-step overview while preserving the first and final states."""
    final_index = len(values) - 1
    indices = [round(position * final_index / 4) for position in range(5)]
    return [values[index] for index in indices]


class PlannerProvenance(BaseModel):
    """Model and prompt identity used to create an AssetPlan."""

    model: str
    model_digest: str | None
    prompt_version: str
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class PackageSource(BaseModel):
    """Immutable source lineage for one package."""

    run_id: str
    iteration_id: str
    input_artifact_id: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption_artifact_id: str
    caption_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class PackageCaption(BaseModel):
    """Caption file entry in a package manifest."""

    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class PackageAsset(BaseModel):
    """One rendered card and its grounding information."""

    position: int = Field(ge=1)
    type: str
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    alt_text: str = Field(min_length=1, max_length=300)
    spec: CardSpec

    model_config = ConfigDict(extra="forbid")


class PublishingPackageManifest(BaseModel):
    """Single metadata contract for one target-aware package."""

    schema_version: Literal["1"]
    package_id: str
    target: Literal["linkedin"]
    review_status: Literal["pending"]
    source: PackageSource
    planner: PlannerProvenance
    caption: PackageCaption
    assets: list[PackageAsset] = Field(min_length=3, max_length=4)
    template_version: Literal["v1"]
    created_at: datetime

    model_config = ConfigDict(extra="forbid")
