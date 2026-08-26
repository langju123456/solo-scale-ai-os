"""Typed, local-only contracts for the Resume Intelligence Workspace."""

from __future__ import annotations

import hashlib
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


class GroundedResumeBulletRewrite(ContractModel):
    """One model-authored wording change anchored to one approved profile entry."""

    profile_entry_id: str = Field(pattern=r"^PROFILE-\d{2}$")
    text: str = Field(min_length=1, max_length=600)
    source_facts: list[str] = Field(min_length=1, max_length=8)


class RoleStrategy(ContractModel):
    """Small structured plan that makes one resume run explicitly JD-conditioned."""

    role_summary: str = Field(min_length=1, max_length=500)
    top_hiring_signals: list[str] = Field(min_length=1, max_length=8)
    evidence_priority: list[str] = Field(min_length=1, max_length=80)
    skill_priority: list[str] = Field(default_factory=list, max_length=40)
    bullet_rewrites: list[GroundedResumeBulletRewrite] = Field(min_length=1, max_length=80)
    unsupported_requirements: list[str] = Field(default_factory=list, max_length=16)
    rewrite_guidance: str = Field(min_length=1, max_length=800)


class ResumeClaimVerificationStatus(StrEnum):
    """Deterministic export status for one final resume claim."""

    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"


class ResumeHiringSignalReceipt(ContractModel):
    """Body-free identity for one exact JD hiring signal."""

    signal_id: str = Field(pattern=r"^SIGNAL-\d{2}$")
    signal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exact_jd_quote: Literal[True] = True


class ResumeClaimProvenance(ContractModel):
    """One exported bullet and the approved Candidate Profile fact that supports it."""

    claim_id: str = Field(pattern=r"^CLAIM-\d{2}$")
    final_text: str = Field(min_length=1, max_length=600)
    final_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_entry_id: str = Field(pattern=r"^PROFILE-\d{2}$")
    approved_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_ids: list[str] = Field(min_length=1, max_length=1)
    source_fact_sha256s: list[str] = Field(default_factory=list, max_length=8)
    hiring_signal_ids: list[str] = Field(default_factory=list, max_length=8)
    status: ResumeClaimVerificationStatus
    verification_basis: Literal[
        "EXACT_OPERATOR_APPROVED_PROFILE_ENTRY",
        "DETERMINISTIC_EVIDENCE_PRESERVING_REWRITE",
    ]

    @model_validator(mode="after")
    def validate_exported_claim(self) -> ResumeClaimProvenance:
        if self.final_text_sha256 != hashlib.sha256(
            self.final_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("final_text_sha256 must match the exported claim")
        if self.evidence_ids != [self.profile_entry_id]:
            raise ValueError("resume claim evidence must be its approved profile entry")
        if len(self.source_fact_sha256s) != len(set(self.source_fact_sha256s)):
            raise ValueError("source fact hashes must be unique")
        if len(self.hiring_signal_ids) != len(set(self.hiring_signal_ids)):
            raise ValueError("hiring signal identities must be unique")
        if self.status not in {
            ResumeClaimVerificationStatus.VERIFIED,
            ResumeClaimVerificationStatus.SUPPORTED,
        }:
            raise ValueError("unverified or contradicted claims cannot be exported")
        if (
            self.status == ResumeClaimVerificationStatus.VERIFIED
            and self.final_text_sha256 != self.approved_source_sha256
        ):
            raise ValueError("VERIFIED claims must preserve the approved text exactly")
        if (
            self.status == ResumeClaimVerificationStatus.SUPPORTED
            and not self.source_fact_sha256s
        ):
            raise ValueError("SUPPORTED rewrites require hashed source facts")
        return self


class ResumeProvenanceReceipt(ContractModel):
    """Private claim-level receipt for one generated resume artifact."""

    run_id: str
    resume_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_description_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation_mode: str
    source_inputs_retained: Literal[False] = False
    contains_source_bodies: Literal[False] = False
    hiring_signals: list[ResumeHiringSignalReceipt] = Field(
        default_factory=list, max_length=8
    )
    claims: list[ResumeClaimProvenance] = Field(min_length=1, max_length=80)
    unsupported_requirement_sha256s: list[str] = Field(default_factory=list, max_length=16)
    all_exported_claims_supported: Literal[True] = True
    final_human_review_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_receipt_links(self) -> ResumeProvenanceReceipt:
        signal_ids = {item.signal_id for item in self.hiring_signals}
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("resume claim identities must be unique")
        if any(not set(item.hiring_signal_ids) <= signal_ids for item in self.claims):
            raise ValueError("resume claim references an unknown hiring signal")
        if len(self.unsupported_requirement_sha256s) != len(
            set(self.unsupported_requirement_sha256s)
        ):
            raise ValueError("unsupported requirement hashes must be unique")
        return self


class ResumeExpertReviewPatch(ContractModel):
    """One evidence-preserving wording patch from the optional expert reviewer."""

    profile_entry_id: str = Field(pattern=r"^PROFILE-\d{2}$")
    before_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    after: str = Field(min_length=1, max_length=600)
    new_factual_claims: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=1, max_length=500)


class ResumeExpertReviewResult(ContractModel):
    """Patch-only expert review; deterministic code owns acceptance."""

    summary: str = Field(min_length=1, max_length=800)
    patches: list[ResumeExpertReviewPatch] = Field(default_factory=list, max_length=80)
    omitted_high_value_profile_entry_ids: list[str] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_patch_identity(self) -> ResumeExpertReviewResult:
        patch_ids = [patch.profile_entry_id for patch in self.patches]
        if len(patch_ids) != len(set(patch_ids)):
            raise ValueError("expert review patches must have unique profile identities")
        if len(self.omitted_high_value_profile_entry_ids) != len(
            set(self.omitted_high_value_profile_entry_ids)
        ):
            raise ValueError("omitted profile identities must be unique")
        return self


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
