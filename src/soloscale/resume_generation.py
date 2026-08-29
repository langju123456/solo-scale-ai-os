"""Canonical Resume generation migration over the evidence-intelligence pipeline.

Real JD → requirement normalization → high-recall retrieval (CP2) → graded claim truth
(CP3) → role strategy → bounded model generation (CP4) → deterministic post-generation
validation → coverage/gap report → traceable Application Resume draft.

The old narrow approved-template-facts path is no longer the canonical generation
surface. The model may rephrase, compress, reorder, and combine authorized claims; it
may never add technology, metrics, outcomes, ownership, scale, or deployment.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel

from soloscale.deepseek_provider import DeepSeekCallReceipt
from soloscale.resume_claim_truth import build_claim_truth_result
from soloscale.resume_models import (
    ApplicationClaim,
    ApplicationResumeDraft,
    ClaimClass,
    ClaimStrength,
    ClaimTruthResult,
    ContributionMode,
    CoverageReport,
    CoverageSummary,
    EvidenceAuthority,
    EvidenceOwnership,
    GeneratedResumeBullet,
    GenerationReceipt,
    GenerationViolation,
    GenerationViolationCode,
    ResumeEvidenceCoverageMap,
    ResumeGenerationContract,
    ResumeGenerationResult,
    ResumeRoleStrategy,
    ValidationReport,
)
from soloscale.resume_retrieval import _ALL_LEXICON_TERMS, capability_expansion

_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?\+?[kKmM]?(?!\w)")
_STRONG_VERBS = {
    "implemented",
    "built",
    "designed",
    "developed",
    "engineered",
    "architected",
    "led",
    "shipped",
}
_WEAKENING_VERBS = {
    "familiar",
    "exposed",
    "worked with",
    "studied",
    "used",
    "explored",
    "experimented",
}
_SCALE_TERMS = {
    "production",
    "productionized",
    "enterprise",
    "millions",
    "users",
    "customers",
    "serving",
    "scale",
}
_OUTCOME_TERMS = {"reduced", "improved", "increased", "accelerated", "boosted", "grew"}
_DEPLOYMENT_TERMS = {"deployed", "deployment", "cloud run", "gke", "vertex"}
_MODEL_RULES = (
    ("Rephrase, compress, reorder, and combine only the provided authorized claims."),
    (
        "Never add a technology, metric, outcome, ownership, deployment, or scale "
        "not present in the authorized claims."
    ),
    (
        "Keep bullets specific and short; avoid filler such as leveraged, utilized, "
        "passionate, dynamic, or results-driven."
    ),
    ("Assign every bullet exactly the claim ids and contribution mode provided."),
    ("Do not mention excluded implications."),
)


class _GeneratedBulletPayload(BaseModel):
    section: Literal["EXPERIENCE", "PROJECTS", "INDEPENDENT_ENGINEERING", "EDUCATION"]
    text: str
    source_claim_ids: list[str]
    project_identity: str | None = None
    contribution_mode: ContributionMode


class _GeneratedResumePayload(BaseModel):
    headline: str
    summary: str
    skills: list[str]
    bullets: list[_GeneratedBulletPayload]


class ResumeGenerationGateway(Protocol):
    def complete(
        self,
        schema: type[_GeneratedResumePayload],
        *,
        system: str,
        user: str,
        reasoning_effort: object | None = None,
        thinking_enabled: bool | None = None,
    ) -> _GeneratedResumePayload: ...


class GenerationProviderError(RuntimeError):
    """The selected provider cannot generate without an explicit fallback."""


class _DeepSeekGenerationGateway:
    def __init__(self, gateway: object) -> None:
        self._gateway = gateway

    def complete(
        self,
        schema: type[_GeneratedResumePayload],
        *,
        system: str,
        user: str,
        reasoning_effort: object | None = None,
        thinking_enabled: bool | None = None,
    ) -> _GeneratedResumePayload:
        return cast(
            _GeneratedResumePayload,
            self._gateway.complete(  # type: ignore[attr-defined]
                schema,
                system=system,
                user=user,
                reasoning_effort=reasoning_effort,
                thinking_enabled=thinking_enabled,
            ),
        )

    @property
    def last_receipt(self) -> DeepSeekCallReceipt | None:
        return getattr(self._gateway, "last_receipt", None)


def _claim_priority(claim: ApplicationClaim) -> int:
    return (
        (2 if claim.claim_class is ClaimClass.VERIFIED else 1)
        + (2 if claim.authority is EvidenceAuthority.HIGH else 1)
        + min(len(claim.evidence_ids), 5)
    )


def select_allowed_claims(
    claim_truth: ClaimTruthResult, *, max_claims: int = 10
) -> list[ApplicationClaim]:
    """Claim budget: strongest, least redundant claims first."""

    ranked = sorted(
        claim_truth.application_claims,
        key=lambda claim: (-_claim_priority(claim), claim.claim_id),
    )
    selected: list[ApplicationClaim] = []
    covered_terms: set[str] = set()
    for claim in ranked:
        vocabulary = set(claim.technology_vocabulary)
        overlap = vocabulary & covered_terms
        if selected and vocabulary and len(overlap) / len(vocabulary) >= 0.75:
            continue
        selected.append(claim)
        covered_terms.update(vocabulary)
        if len(selected) >= max_claims:
            break
    return selected


def build_role_strategy(
    *,
    coverage_map: ResumeEvidenceCoverageMap,
    claim_truth: ClaimTruthResult,
) -> ResumeRoleStrategy:
    """JD-conditioned positioning derived from the graded claim map."""

    claims_by_requirement: dict[str, list[ApplicationClaim]] = {}
    for claim in claim_truth.application_claims:
        claims_by_requirement.setdefault(claim.requirement_id, []).append(claim)
    prioritized = sorted(
        claim_truth.requirement_maps,
        key=lambda item: (
            -sum(
                _claim_priority(claim)
                for claim in claims_by_requirement.get(item.requirement_id, [])
            ),
            item.requirement_id,
        ),
    )
    prioritized_ids = [item.requirement_id for item in prioritized[:8]]
    jd_terms = sorted(
        {
            term
            for item in prioritized[:8]
            for term in capability_expansion(item.requirement_text).technology_terms
        }
    )[:10]
    requirements_by_id = {
        item.requirement_id: item for item in coverage_map.requirements
    }
    evidence_ordered: list[str] = []
    for claim in sorted(
        claim_truth.application_claims,
        key=lambda item: (-_claim_priority(item), item.claim_id),
    ):
        requirement = requirements_by_id[claim.requirement_id]
        excluded: set[str] = set()
        for implication in claim.excluded_implications:
            excluded.add(implication.casefold())
            excluded.update(
                capability_expansion(implication).technology_terms
            )
        for term in claim.technology_vocabulary:
            if (
                term in _ALL_LEXICON_TERMS
                and term not in requirement.technology_terms
                and term not in excluded
                and term not in evidence_ordered
            ):
                evidence_ordered.append(term)
    emphasized = list(dict.fromkeys((*jd_terms, *evidence_ordered)))[:20]
    unsupported = sorted(
        {
            implication
            for claim in claim_truth.application_claims
            for implication in claim.excluded_implications
        }
    )[:20]
    project_ranking: dict[str, int] = {}
    for candidate in coverage_map.candidates:
        if not candidate.source_identity.strip():
            continue
        if candidate.source_kind.value not in {"local_git", "evidence_hub", "github"}:
            continue
        project_name = (
            Path(candidate.source_identity).name
            if candidate.source_kind.value == "local_git"
            else candidate.source_identity
        )
        project_ranking[project_name] = (
            project_ranking.get(project_name, 0) + 1
        )
    selected_projects = [
        name
        for name, _count in sorted(
            project_ranking.items(), key=lambda item: (-item[1], item[0])
        )[:6]
    ]
    first_text = (
        coverage_map.requirements[0].text
        if coverage_map.requirements
        else "AI Engineer"
    )
    role_title = first_text.strip(" \t#•-")
    if not role_title:
        role_title = "AI Engineer"
    if "faiss" in evidence_ordered and (
        "chroma" in evidence_ordered or "chromadb" in evidence_ordered
    ):
        headline = f"{role_title} | RAG & vector retrieval — FAISS, Chroma"
    elif evidence_ordered:
        headline = f"{role_title} | {', '.join(evidence_ordered[:3])}"
    else:
        headline = f"{role_title} | {', '.join(jd_terms[:3]) or 'AI engineering'}"
    claim_allocations = {
        requirement_id: [claim.claim_id for claim in claims]
        for requirement_id, claims in claims_by_requirement.items()
    }
    if "faiss" in evidence_ordered and (
        "chroma" in evidence_ordered or "chromadb" in evidence_ordered
    ):
        positioning = (
            f"{role_title} with evidence-backed strengths in "
            f"{', '.join(jd_terms[:4]) or 'software engineering'}, plus "
            "RAG & vector retrieval built on FAISS and Chroma."
        )
    else:
        positioning = (
            f"{role_title} with evidence-backed strengths in "
            f"{', '.join(emphasized[:6]) or 'software engineering'}."
        )
    return ResumeRoleStrategy(
        role_title=role_title,
        positioning=positioning,
        headline=headline,
        prioritized_requirement_ids=prioritized_ids,
        emphasized_terms=emphasized,
        unsupported_terms=unsupported,
        selected_projects=selected_projects,
        claim_allocations=claim_allocations,
        recruiter_scan_priorities=[item.requirement_text[:160] for item in prioritized[:5]],
    )


def build_generation_contract(
    *,
    job_description: str,
    strategy: ResumeRoleStrategy,
    claims: Sequence[ApplicationClaim],
) -> ResumeGenerationContract:
    """Bounded generation context: strategy, allowed claims, and hard rules."""

    vocabulary = sorted(
        {
            term
            for claim in claims
            for term in claim.technology_vocabulary
            if term in _ALL_LEXICON_TERMS
        }
    )
    emphasized = [term for term in strategy.emphasized_terms if term in vocabulary]
    skills = (emphasized + [term for term in vocabulary if term not in emphasized])[:24]
    excluded = sorted(
        {
            implication
            for claim in claims
            for implication in claim.excluded_implications
        }
    )
    return ResumeGenerationContract(
        job_description_sha256=hashlib.sha256(job_description.encode()).hexdigest(),
        role_strategy=strategy,
        allowed_claims=list(claims),
        skills=skills,
        projects=strategy.selected_projects,
        excluded_implications=excluded,
        required_sections=["EXPERIENCE", "PROJECTS", "INDEPENDENT_ENGINEERING", "EDUCATION"],
        model_rules=list(_MODEL_RULES),
    )


def _system_prompt(contract: ResumeGenerationContract) -> str:
    rules = "\n".join(f"- {rule}" for rule in contract.model_rules)
    excluded = ", ".join(contract.excluded_implications) or "none"
    return (
        "You are drafting a truthful submit-ready Application Resume from an authorized "
        "claim set. Use only the provided claims, skills, and projects.\n"
        f"{rules}\n"
        f"Excluded implications (do not state): {excluded}\n"
        "Return the structured schema exactly. Keep bullets under 24 words where possible."
    )


def _contract_user_text(contract: ResumeGenerationContract) -> str:
    return contract.model_dump_json()


def _draft_from_payload(
    *,
    payload: _GeneratedResumePayload,
    job_description: str,
    claims_by_id: dict[str, ApplicationClaim],
) -> ApplicationResumeDraft:
    bullets: list[GeneratedResumeBullet] = []
    for index, item in enumerate(payload.bullets, start=1):
        source_claims = [
            claim
            for claim_id in item.source_claim_ids
            if (claim := claims_by_id.get(claim_id)) is not None
        ]
        boundary = (
            source_claims[0].claim_class.value
            if source_claims
            else "unverified"
        )
        bullets.append(
            GeneratedResumeBullet(
                bullet_id=f"BULLET-{index:02d}",
                section=item.section,
                text=item.text,
                source_claim_ids=item.source_claim_ids,
                project_identity=item.project_identity,
                contribution_mode=item.contribution_mode,
                truth_boundary=boundary,
            )
        )
    return ApplicationResumeDraft(
        job_description_sha256=hashlib.sha256(job_description.encode()).hexdigest(),
        headline=payload.headline,
        summary=payload.summary,
        skills=payload.skills,
        bullets=bullets,
    )


def _text_technology_violation(text: str, allowed: set[str]) -> bool:
    for term in capability_expansion(text).technology_terms:
        if " " not in term:
            if term not in allowed:
                return True
            continue
        for word in term.split():
            if word in _ALL_LEXICON_TERMS and word not in allowed:
                return True
    return False


def _bullet_violations(
    bullet: GeneratedResumeBullet,
    claims_by_id: dict[str, ApplicationClaim],
) -> list[GenerationViolationCode]:
    source = [
        claim
        for claim_id in bullet.source_claim_ids
        if (claim := claims_by_id.get(claim_id)) is not None
    ]
    violations: list[GenerationViolationCode] = []
    if len(source) != len(bullet.source_claim_ids) or not source:
        violations.append(GenerationViolationCode.UNKNOWN_SOURCE_CLAIM)
        return violations
    allowed = {term for claim in source for term in claim.technology_vocabulary}
    if _text_technology_violation(bullet.text, allowed):
        violations.append(GenerationViolationCode.UNAUTHORIZED_TECHNOLOGY)
    if _NUMBER_RE.search(bullet.text) is not None:
        violations.append(GenerationViolationCode.INVENTED_METRIC)
    folded = bullet.text.casefold()
    source_text = " ".join(claim.proposed_text for claim in source).casefold()
    if any(term in folded for term in _SCALE_TERMS) and not any(
        term in source_text for term in _SCALE_TERMS
    ):
        violations.append(GenerationViolationCode.SCALE_INFLATION)
    if any(term in folded for term in _OUTCOME_TERMS) and not any(
        term in source_text for term in _OUTCOME_TERMS
    ):
        violations.append(GenerationViolationCode.INVENTED_OUTCOME)
    if any(term in folded for term in _DEPLOYMENT_TERMS) and not any(
        term in source_text for term in _DEPLOYMENT_TERMS
    ):
        violations.append(GenerationViolationCode.DEPLOYMENT_INFLATION)
    if (
        any(verb in folded for verb in _STRONG_VERBS)
        and any(claim.ownership is EvidenceOwnership.UNPROVEN for claim in source)
    ):
        violations.append(GenerationViolationCode.OWNERSHIP_INFLATION)
    if (
        bullet.contribution_mode is ContributionMode.EXTERNAL_UNKNOWN
        and any(verb in folded for verb in _STRONG_VERBS)
    ):
        violations.append(GenerationViolationCode.CONTRIBUTION_MODE_INFLATION)
    strongest = max(
        source,
        key=lambda claim: (
            1 if claim.strength is ClaimStrength.STRONG else 0,
            claim.claim_id,
        ),
    )
    if (
        any(verb in folded for verb in _WEAKENING_VERBS)
        and strongest.strength in {ClaimStrength.STRONG, ClaimStrength.MODERATE}
    ):
        violations.append(GenerationViolationCode.WEAKENED_CLAIM)
    return violations


def _strongest_claim_text(
    bullet: GeneratedResumeBullet, claims_by_id: dict[str, ApplicationClaim]
) -> str:
    source = [
        claim
        for claim_id in bullet.source_claim_ids
        if (claim := claims_by_id.get(claim_id)) is not None
    ]
    if not source:
        return bullet.text
    return max(
        source,
        key=lambda claim: (
            1 if claim.strength is ClaimStrength.STRONG else 0,
            1 if claim.claim_class is ClaimClass.VERIFIED else 0,
            claim.claim_id,
        ),
    ).proposed_text


def validate_application_draft(
    draft: ApplicationResumeDraft,
    claim_truth: ClaimTruthResult,
) -> tuple[ApplicationResumeDraft, ValidationReport]:
    """Deterministic post-generation truth gate with repair-or-reject semantics."""

    claims_by_id = {claim.claim_id: claim for claim in claim_truth.application_claims}
    violations: list[GenerationViolation] = []
    repaired_count = 0
    rejected_count = 0
    final_bullets: list[GeneratedResumeBullet] = []
    for bullet in draft.bullets:
        codes = _bullet_violations(bullet, claims_by_id)
        if not codes:
            final_bullets.append(bullet)
            continue
        violations.extend(
            GenerationViolation(
                bullet_id=bullet.bullet_id,
                rule_code=code,
                detail=f"{code.value} in generated bullet",
            )
            for code in codes
        )
        if GenerationViolationCode.UNKNOWN_SOURCE_CLAIM in codes:
            rejected_count += 1
            continue
        repaired = bullet.model_copy(
            update={
                "text": _strongest_claim_text(bullet, claims_by_id),
                "generation_status": "DETERMINISTIC_REPAIR",
            }
        )
        if _bullet_violations(repaired, claims_by_id):
            rejected_count += 1
            continue
        repaired_count += 1
        final_bullets.append(repaired)
    valid = bool(final_bullets)
    allowed_skill_terms: set[str] = set()
    for claim in claim_truth.application_claims:
        excluded_terms: set[str] = set()
        for implication in claim.excluded_implications:
            excluded_terms.add(implication.casefold())
            excluded_terms.update(
                capability_expansion(implication).technology_terms
            )
        allowed_skill_terms |= set(claim.technology_vocabulary) - excluded_terms
    kept_skills = [
        skill
        for skill in draft.skills
        if _skill_supported(skill, allowed_skill_terms)
    ]
    rejected_skills = [
        skill for skill in draft.skills if skill not in kept_skills
    ]
    repaired_draft = draft.model_copy(
        update={"bullets": final_bullets, "skills": kept_skills}
    )
    return repaired_draft, ValidationReport(
        checked_count=len(draft.bullets),
        violations=violations,
        repaired_count=repaired_count,
        rejected_count=rejected_count,
        rejected_skills=rejected_skills,
        valid=valid,
    )


def _skill_supported(skill: str, allowed: set[str]) -> bool:
    technology = set(capability_expansion(skill).technology_terms)
    return not technology or technology <= allowed


def apply_editorial_review(
    draft: ApplicationResumeDraft,
    claim_truth: ClaimTruthResult,
    *,
    editorial: Callable[[str], str] | None = None,
) -> tuple[ApplicationResumeDraft, ValidationReport]:
    """Stylistic review may improve wording but can never bypass the truth gate."""

    if editorial is None:
        polished = draft
    else:
        polished = draft.model_copy(
            update={
                "bullets": [
                    bullet.model_copy(update={"text": editorial(bullet.text)})
                    for bullet in draft.bullets
                ]
            }
        )
    final, report = validate_application_draft(polished, claim_truth)
    report = report.model_copy(update={"post_editorial_validated": True})
    return final, report


def build_coverage_report(
    *,
    claim_truth: ClaimTruthResult,
    draft: ApplicationResumeDraft,
) -> CoverageReport:
    claim_requirement_ids = {
        claim.claim_id: claim.requirement_id
        for claim in claim_truth.application_claims
    }
    used = {
        claim_requirement_ids.get(claim_id, "")
        for bullet in draft.bullets
        for claim_id in bullet.source_claim_ids
    }
    used.discard("")
    gap_requirements = {gap.requirement_id for gap in claim_truth.target_gaps}
    strongly: list[str] = []
    partially: list[str] = []
    gaps: list[str] = []
    omitted: list[str] = []
    summaries: list[CoverageSummary] = []
    for requirement in claim_truth.requirement_maps:
        verified_claims = [
            claim
            for claim in claim_truth.application_claims
            if claim.requirement_id == requirement.requirement_id
            and claim.claim_class is ClaimClass.VERIFIED
        ]
        supported_claims = [
            claim
            for claim in claim_truth.application_claims
            if claim.requirement_id == requirement.requirement_id
            and claim.claim_class is ClaimClass.SUPPORTED_DERIVATION
        ]
        if requirement.requirement_id in used and verified_claims:
            status: Literal[
                "STRONGLY_REPRESENTED",
                "PARTIALLY_REPRESENTED",
                "HIGH_VALUE_GAP",
                "INTENTIONALLY_OMITTED",
                "NO_EVIDENCE",
            ] = "STRONGLY_REPRESENTED"
            strongly.append(requirement.requirement_id)
        elif requirement.requirement_id in used and supported_claims:
            status = "PARTIALLY_REPRESENTED"
            partially.append(requirement.requirement_id)
        elif verified_claims or supported_claims:
            status = "INTENTIONALLY_OMITTED"
            omitted.append(requirement.requirement_id)
        else:
            status = "NO_EVIDENCE"
        if requirement.requirement_id in gap_requirements:
            gaps.append(requirement.requirement_id)
        summaries.append(
            CoverageSummary(
                requirement_id=requirement.requirement_id,
                status=status,
                detail=requirement.requirement_text[:200],
            )
        )
    return CoverageReport(
        strongly_represented=strongly,
        partially_represented=partially,
        high_value_gaps=sorted(set(gaps)),
        intentionally_omitted=omitted,
        summaries=summaries,
    )


def generate_application_resume(
    *,
    job_description: str,
    coverage_map: ResumeEvidenceCoverageMap,
    gateway: ResumeGenerationGateway,
    owned_project_markers: Sequence[str] = (),
    max_claims: int = 10,
    reasoning_effort: str = "high",
    thinking_enabled: bool = True,
    real_call: bool = False,
) -> ResumeGenerationResult:
    """Run the full canonical generation path through the model boundary."""

    claim_truth = build_claim_truth_result(
        job_description=job_description,
        coverage_map=coverage_map,
        owned_project_markers=owned_project_markers,
    )
    strategy = build_role_strategy(
        coverage_map=coverage_map, claim_truth=claim_truth
    )
    claims = select_allowed_claims(claim_truth, max_claims=max_claims)
    contract = build_generation_contract(
        job_description=job_description, strategy=strategy, claims=claims
    )
    claims_by_id = {claim.claim_id: claim for claim in claims}
    try:
        payload = gateway.complete(
            _GeneratedResumePayload,
            system=_system_prompt(contract),
            user=_contract_user_text(contract),
            reasoning_effort=reasoning_effort,
            thinking_enabled=thinking_enabled,
        )
    except GenerationProviderError:
        raise
    except Exception as exc:
        raise GenerationProviderError(
            "the selected provider could not complete generation"
        ) from exc
    draft = _draft_from_payload(
        payload=payload,
        job_description=job_description,
        claims_by_id=claims_by_id,
    )
    validated_draft, validation_report = validate_application_draft(
        draft, claim_truth
    )
    receipt = _receipt_from_gateway(
        gateway,
        reasoning_effort=reasoning_effort,
        thinking_enabled=thinking_enabled,
        real_call=real_call,
    )
    coverage = build_coverage_report(
        claim_truth=claim_truth, draft=validated_draft
    )
    return ResumeGenerationResult(
        job_description_sha256=hashlib.sha256(job_description.encode()).hexdigest(),
        role_strategy=strategy,
        application_draft=validated_draft,
        coverage_report=coverage,
        validation_report=validation_report,
        generation_receipt=receipt,
        target_gaps=claim_truth.target_gaps,
    )


def _receipt_from_gateway(
    gateway: ResumeGenerationGateway,
    *,
    reasoning_effort: str,
    thinking_enabled: bool,
    real_call: bool,
) -> GenerationReceipt:
    deepseek_receipt = getattr(gateway, "last_receipt", None)
    if isinstance(deepseek_receipt, DeepSeekCallReceipt):
        return GenerationReceipt(
            provider=deepseek_receipt.provider,
            model=deepseek_receipt.model,
            reasoning_effort=deepseek_receipt.reasoning_effort.value,
            thinking_enabled=deepseek_receipt.thinking_enabled,
            model_calls=1,
            latency_ms=deepseek_receipt.latency_ms,
            input_tokens=deepseek_receipt.input_tokens,
            output_tokens=deepseek_receipt.output_tokens,
            cache_tokens=deepseek_receipt.cache_tokens,
            status=deepseek_receipt.status,
            error_category=(
                deepseek_receipt.error_category.value
                if deepseek_receipt.error_category is not None
                else None
            ),
            real_call=real_call,
        )
    return GenerationReceipt(
        provider="unknown",
        model="unknown",
        reasoning_effort=reasoning_effort,
        thinking_enabled=thinking_enabled,
        model_calls=1,
        latency_ms=0,
        status="SUCCEEDED",
        real_call=real_call,
    )


def deepseek_generation_gateway(gateway: object) -> ResumeGenerationGateway:
    """Wrap the canonical DeepSeek gateway for structured Resume generation."""

    return _DeepSeekGenerationGateway(gateway)
