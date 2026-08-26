from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soloscale.editorial_models import EditorialProvenance
from soloscale.reference_intelligence import ContentPattern, ReferenceAsset


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    OBSERVED = "OBSERVED"
    HYPOTHESIS = "HYPOTHESIS"
    PLANNED = "PLANNED"


class ContentReviewDecision(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ContentClaim(_StrictModel):
    id: str = Field(pattern=r"^CLAIM-[0-9]{2}$")
    text: str = Field(min_length=1, max_length=220)
    status: ClaimStatus
    receipt: str | None = Field(default=None, max_length=2000)
    limits: str | None = Field(default=None, max_length=1200)

    @model_validator(mode="after")
    def evidence_backed_status_requires_receipt(self) -> ContentClaim:
        if self.status in {ClaimStatus.VERIFIED, ClaimStatus.OBSERVED} and not self.receipt:
            raise ValueError(f"{self.status.value} claims require a receipt")
        return self


class ContentBrief(_StrictModel):
    topic: str = Field(min_length=1, max_length=180)
    audience: str = Field(min_length=1, max_length=500)
    language: Literal["English", "中文"] = "English"
    call_to_action: str = Field(min_length=1, max_length=220)
    source_label: str = Field(min_length=1, max_length=500)
    claims: list[ContentClaim] = Field(min_length=1, max_length=8)
    evidence_bundle_id: str | None = None
    evidence_item_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)
    evidence_filters: dict[str, str] = Field(default_factory=dict)
    reference_asset: ReferenceAsset | None = None
    content_pattern: ContentPattern | None = None

    @model_validator(mode="after")
    def evidence_references_are_consistent(self) -> ContentBrief:
        if len(self.evidence_item_ids) != len(set(self.evidence_item_ids)):
            raise ValueError("evidence item ids must be unique")
        if self.evidence_item_ids and self.evidence_bundle_id is None:
            raise ValueError("evidence item ids require an evidence bundle id")
        if (self.reference_asset is None) != (self.content_pattern is None):
            raise ValueError("reference asset and content pattern must be supplied together")
        if (
            self.reference_asset is not None
            and self.content_pattern is not None
            and self.reference_asset.reference_id != self.content_pattern.reference_id
        ):
            raise ValueError("content pattern must belong to the selected reference")
        return self


class StoryboardScene(_StrictModel):
    id: str = Field(pattern=r"^SCENE-[0-9]{2}$")
    start_second: int = Field(ge=0, le=600)
    end_second: int = Field(gt=0, le=600)
    purpose: str = Field(min_length=1, max_length=120)
    visual: str = Field(min_length=1, max_length=500)
    voiceover: str = Field(min_length=1, max_length=600)
    on_screen_text: str = Field(min_length=1, max_length=240)
    claim_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> StoryboardScene:
        if self.end_second <= self.start_second:
            raise ValueError("scene end must follow scene start")
        return self


class ContentDrafts(_StrictModel):
    canonical_story: str = Field(default="", max_length=16_000)
    linkedin: str = Field(min_length=1, max_length=12_000)
    x_thread: list[str] = Field(min_length=1, max_length=12)
    x_post: str = Field(default="", max_length=280)
    blog: str = Field(default="", max_length=24_000)
    video_script: str = Field(min_length=1, max_length=16_000)
    storyboard: list[StoryboardScene] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def x_posts_fit_platform_limit(self) -> ContentDrafts:
        if any(len(post) > 280 for post in self.x_thread):
            raise ValueError("X thread posts must not exceed 280 characters")
        if len(self.x_post) > 280:
            raise ValueError("Standalone X post must not exceed 280 characters")
        return self


class ContentReviewReceipt(_StrictModel):
    run_id: str
    revision: int = Field(ge=1)
    decision: ContentReviewDecision
    created_at: str
    artifact_sha256: dict[str, str]
    reset_target: str | None = None
    publication_performed: Literal[False] = False


class ContentRun(_StrictModel):
    status: Literal["DRAFT_REQUIRES_HUMAN_APPROVAL"] = "DRAFT_REQUIRES_HUMAN_APPROVAL"
    run_id: str
    created_at: str
    brief: ContentBrief
    drafts: ContentDrafts
    artifact_paths: list[str]
    editorial_provenance: list[EditorialProvenance] = Field(default_factory=list)
    network_used: bool = False
    model_used: bool = False
    publication_performed: Literal[False] = False
    limitations: list[str]
