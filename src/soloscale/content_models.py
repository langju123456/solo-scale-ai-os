from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    OBSERVED = "OBSERVED"
    HYPOTHESIS = "HYPOTHESIS"
    PLANNED = "PLANNED"


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


class StoryboardScene(_StrictModel):
    id: str = Field(pattern=r"^SCENE-[0-9]{2}$")
    start_second: int = Field(ge=0, le=600)
    end_second: int = Field(gt=0, le=600)
    purpose: str
    visual: str
    voiceover: str
    on_screen_text: str
    claim_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> StoryboardScene:
        if self.end_second <= self.start_second:
            raise ValueError("scene end must follow scene start")
        return self


class ContentDrafts(_StrictModel):
    linkedin: str
    x_thread: list[str] = Field(min_length=1, max_length=12)
    video_script: str
    storyboard: list[StoryboardScene] = Field(min_length=1, max_length=12)


class ContentRun(_StrictModel):
    status: Literal["DRAFT_REQUIRES_HUMAN_APPROVAL"] = "DRAFT_REQUIRES_HUMAN_APPROVAL"
    run_id: str
    created_at: str
    brief: ContentBrief
    drafts: ContentDrafts
    artifact_paths: list[str]
    network_used: Literal[False] = False
    model_used: Literal[False] = False
    publication_performed: Literal[False] = False
    limitations: list[str]
