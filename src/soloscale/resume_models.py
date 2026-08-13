"""Typed, local-only contracts for the Resume Intelligence Workspace."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from soloscale.models import ContractModel, utc_now


class ResumeMode(StrEnum):
    LOCAL_ONLY = "local-only"
    HYBRID = "hybrid"


class InterviewDefenseStatus(StrEnum):
    NEEDS_MAPPING = "NEEDS_MAPPING"
    MAPPED = "MAPPED"


class InterviewDefenseMapping(ContractModel):
    case_id: str
    learning_run_id: str
    mapping_basis: Literal["OPERATOR_CONFIRMED"] = "OPERATOR_CONFIRMED"
    repository: str
    branch: str
    commit: str
    anchor_pack: dict[str, object]


class InterviewDefenseRecord(ContractModel):
    bullet_id: str
    bullet_text: str
    bullet_sha256: str
    status: InterviewDefenseStatus = InterviewDefenseStatus.NEEDS_MAPPING
    mapping: InterviewDefenseMapping | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> InterviewDefenseRecord:
        if (self.status == InterviewDefenseStatus.MAPPED) is not (
            self.mapping is not None
        ):
            raise ValueError("interview defense mapping status must match its mapping")
        return self
class GraphNodeKind(StrEnum):
    JOB = "JOB"
    REQUIREMENT = "REQUIREMENT"
    SKILL = "SKILL"
    EVIDENCE = "EVIDENCE"
    PROJECT = "PROJECT"
    CODE = "CODE"
    VERIFICATION = "VERIFICATION"
    GAP = "GAP"
    LEARNING_TASK = "LEARNING_TASK"


class CandidateProfile(ContractModel):
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience_bullets: list[str] = Field(default_factory=list)
    project_bullets: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)

    @field_validator("skills", "experience_bullets", "project_bullets", "education")
    @classmethod
    def nonblank_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class JobResearchSource(ContractModel):
    title: str
    url: str | None = None
    summary: str | None = None


class JobRequirement(ContractModel):
    id: str
    text: str
    skills: list[str] = Field(default_factory=list)
    priority: Literal["critical", "preferred"] = "preferred"


class EvidenceLocator(ContractModel):
    document_id: str
    source_kind: str
    external_id: str
    source_locator: str
    title: str | None = None
    role: str
    timestamp: str | None = None
    chunk_sha256: str
    document_sha256: str
    searchable_metadata_sha256: str | None = None
    channels: list[str] = Field(min_length=1)
    repository: str | None = None
    branch: str | None = None
    commit: str | None = None
    file_path: str | None = None
    symbol: str | None = None
    line_range: str | None = None
    related_test: str | None = None
    verification_receipt: str | None = None


class EvidenceMatch(ContractModel):
    id: str
    requirement_id: str
    evidence_id: str
    excerpt: str
    match_quality: Literal["lexical_candidate_strong", "lexical_candidate_partial"]
    locator: EvidenceLocator


class ResumeBullet(ContractModel):
    text: str
    requirement_ids: list[str] = Field(default_factory=list)
    profile_entry_ids: list[str] = Field(min_length=1)
    support: Literal["candidate_profile"] = "candidate_profile"

    @field_validator("profile_entry_ids")
    @classmethod
    def profile_entry_ids_are_explicit(cls, values: list[str]) -> list[str]:
        if any(not value.startswith("PROFILE-") for value in values):
            raise ValueError("resume bullets must reference Candidate Profile entries")
        return values


class ResumeDraft(ContractModel):
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    bullets: list[ResumeBullet] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)


class SkillGap(ContractModel):
    requirement_id: str
    skill: str
    reason: str


class LearningTask(ContractModel):
    id: str
    requirement_id: str
    title: str
    acceptance_criterion: str


class EvidenceGraphNode(ContractModel):
    id: str
    kind: GraphNodeKind
    label: str
    detail: dict[str, str | list[str] | None] = Field(default_factory=dict)


class EvidenceGraphEdge(ContractModel):
    source: str
    target: str
    relation: str


class ResumeRun(ContractModel):
    run_id: str
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    mode: ResumeMode
    evidence_bundle_id: str | None = None
    status: Literal["CANDIDATE_REQUIRES_HUMAN_CONFIRMATION"] = (
        "CANDIDATE_REQUIRES_HUMAN_CONFIRMATION"
    )
    route: dict[str, str | int | bool] = Field(default_factory=dict)
    artifact_paths: list[str] = Field(default_factory=list)


class ResumeDeliveryReceipt(ContractModel):
    run_id: str
    state: Literal[
        "INTERNAL_READY",
        "APPLICATION_LIBRARY_PENDING",
        "APPLICATION_LIBRARY_SAVED",
        "APPLICATION_LIBRARY_PUBLISHED_DURABILITY_UNCERTAIN",
        "APPLICATION_LIBRARY_FAILED",
    ]
    application_library_path: str | None = None
    error_type: str | None = None
    retry_safe: bool = False
