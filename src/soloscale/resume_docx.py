"""Safe, deterministic DOCX handling for the local resume user flow.

The uploaded resume is the truth boundary.  Tailoring may only reorder existing
paragraph blocks; it never rewrites candidate claims or calls an external service.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any
from xml.etree import ElementTree

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    model_validator,
)

from soloscale.model_gateway import ModelCallProfile, ModelGateway
from soloscale.resume_gateway_boundary import (
    ExtractedResumeUpload,
    ResumeTemplateMetadata,
    prepare_resume_gateway_payload,
    restore_role_strategy,
    validate_role_strategy_placeholders,
)
from soloscale.resume_models import (
    CandidateProfile,
    GroundedResumeBulletRewrite,
    ResumeExpertReviewResult,
    RoleStrategy,
)

_MAX_DOCX_BYTES = 10 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_DOCUMENT_PART = "word/document.xml"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"

_NAMESPACES = {
    "w": _W_NS,
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "mo": "http://schemas.microsoft.com/office/mac/office/2008/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16cex": "http://schemas.microsoft.com/office/word/2018/wordml/cex",
    "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
    "w16": "http://schemas.microsoft.com/office/word/2018/wordml",
    "w16du": "http://schemas.microsoft.com/office/word/2023/wordml/word16du",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}
for _prefix, _uri in _NAMESPACES.items():
    ElementTree.register_namespace(_prefix, _uri)

_SECTION_HEADINGS = {
    "SUMMARY",
    "PROJECT HIGHLIGHTS",
    "EDUCATION",
    "TECHNICAL SKILLS",
    "WORK EXPERIENCE",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{2,}")
_STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "have",
    "job",
    "must",
    "our",
    "preferred",
    "required",
    "role",
    "that",
    "the",
    "this",
    "with",
    "you",
    "your",
}


class ResumeValidationRuleCode(StrEnum):
    """Stable, body-free rejection categories for Resume model evaluation."""

    SCHEMA_PROVIDER_REJECTED = "SCHEMA_PROVIDER_REJECTED"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    HIRING_SIGNAL_NOT_SOURCE_GROUNDED = "HIRING_SIGNAL_NOT_SOURCE_GROUNDED"
    HIRING_SIGNAL_DUPLICATE = "HIRING_SIGNAL_DUPLICATE"
    GAP_NOT_SOURCE_GROUNDED = "GAP_NOT_SOURCE_GROUNDED"
    CLAIM_NO_EVIDENCE = "CLAIM_NO_EVIDENCE"
    CLAIM_SOURCE_MISMATCH = "CLAIM_SOURCE_MISMATCH"
    CLAIM_ROLE_INFLATION = "CLAIM_ROLE_INFLATION"
    CLAIM_NEW_NUMBER = "CLAIM_NEW_NUMBER"
    CLAIM_CONTRADICTED = "CLAIM_CONTRADICTED"
    REWRITE_NOT_MATERIAL = "REWRITE_NOT_MATERIAL"
    REWRITE_FACT_MUTATION = "REWRITE_FACT_MUTATION"
    OUTPUT_DUPLICATE = "OUTPUT_DUPLICATE"


@dataclass(frozen=True)
class ResumeValidationFailure:
    rule_code: ResumeValidationRuleCode
    json_path: str
    claim_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule_code": self.rule_code.value,
            "json_path": self.json_path,
            "claim_id": self.claim_id,
        }


@dataclass(frozen=True)
class ResumeValidationDiagnostics:
    failures: tuple[ResumeValidationFailure, ...]
    candidate_count: int
    verified_count: int
    supported_count: int
    rejected_count: int
    duplicate_count: int
    source_span_failure_count: int
    validator_status: str = "rejected"

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def as_dict(self) -> dict[str, object]:
        return {
            "validator_status": self.validator_status,
            "failure_count": self.failure_count,
            "failures": [failure.as_dict() for failure in self.failures],
            "candidate_count": self.candidate_count,
            "verified_count": self.verified_count,
            "supported_count": self.supported_count,
            "rejected_count": self.rejected_count,
            "duplicate_count": self.duplicate_count,
            "source_span_failure_count": self.source_span_failure_count,
        }


class ResumeTemplateError(ValueError):
    """Raised when Resume input or generated output violates its contract."""

    def __init__(
        self,
        message: str,
        *,
        validation_diagnostics: ResumeValidationDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_diagnostics = validation_diagnostics


@dataclass(frozen=True)
class TemplateParagraph:
    text: str
    is_bullet: bool


@dataclass(frozen=True)
class TailoredDocx:
    content: bytes
    template_sha256: str
    output_sha256: str
    project_blocks_reordered: int
    skill_bullets_reordered: int
    source_paragraph_count: int
    claims_preserved: bool
    grounded_rewrites: int = 0
    rejected_rewrites: int = 0
    generation_mode: str = "template"
    provider: str | None = None
    model: str | None = None
    model_call_profile: dict[str, object] | None = None
    validation_diagnostics: ResumeValidationDiagnostics | None = None
    role_strategy: RoleStrategy | None = None
    expert_review: ResumeExpertReviewResult | None = None
    expert_rewrites: int = 0
    expert_provider: str | None = None
    expert_model: str | None = None


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{_W}t")).strip()


def _is_bullet(paragraph: ElementTree.Element) -> bool:
    properties = paragraph.find(f"{_W}pPr")
    return properties is not None and properties.find(f"{_W}numPr") is not None


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = PurePosixPath(info.filename)
    if name.is_absolute() or ".." in name.parts or not info.filename:
        raise ResumeTemplateError("DOCX contains an unsafe package path")
    if info.flag_bits & 0x1:
        raise ResumeTemplateError("Encrypted DOCX files are not supported")


def _read_package(template: bytes) -> tuple[list[tuple[zipfile.ZipInfo, bytes]], bytes]:
    if not template:
        raise ResumeTemplateError("Please upload a DOCX resume template")
    if len(template) > _MAX_DOCX_BYTES:
        raise ResumeTemplateError("DOCX template must be 10 MB or smaller")
    try:
        with zipfile.ZipFile(io.BytesIO(template)) as archive:
            names: set[str] = set()
            total_size = 0
            members: list[tuple[zipfile.ZipInfo, bytes]] = []
            for info in archive.infolist():
                _validate_member(info)
                if info.filename in names:
                    raise ResumeTemplateError("DOCX contains duplicate package entries")
                names.add(info.filename)
                total_size += info.file_size
                if total_size > _MAX_UNCOMPRESSED_BYTES:
                    raise ResumeTemplateError("DOCX expands beyond the 50 MB safety limit")
                members.append((info, archive.read(info)))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ResumeTemplateError("The uploaded file is not a readable DOCX") from exc
    document = next((content for info, content in members if info.filename == _DOCUMENT_PART), None)
    if document is None:
        raise ResumeTemplateError("DOCX is missing word/document.xml")
    return members, document


def _parse_document(document: bytes) -> tuple[ElementTree.Element, ElementTree.Element]:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ResumeTemplateError("DOCX document XML is malformed") from exc
    body = root.find(f"{_W}body")
    if body is None:
        raise ResumeTemplateError("DOCX does not contain a Word document body")
    return root, body


def read_template_paragraphs(template: bytes) -> list[TemplateParagraph]:
    """Extract visible body paragraphs without macros, relationships, or external calls."""
    _, document = _read_package(template)
    _, body = _parse_document(document)
    return [
        TemplateParagraph(text=_paragraph_text(child), is_bullet=_is_bullet(child))
        for child in body
        if child.tag == f"{_W}p"
    ]


def _heading(value: str) -> str:
    return " ".join(value.upper().split())


def _section_slice(
    paragraphs: list[TemplateParagraph], start_heading: str
) -> list[TemplateParagraph]:
    start = next(
        (
            index
            for index, paragraph in enumerate(paragraphs)
            if _heading(paragraph.text) == start_heading
        ),
        None,
    )
    if start is None:
        return []
    end = next(
        (
            index
            for index, paragraph in enumerate(paragraphs[start + 1 :], start=start + 1)
            if _heading(paragraph.text) in _SECTION_HEADINGS
        ),
        len(paragraphs),
    )
    return paragraphs[start + 1 : end]


def _nonblank_text(paragraphs: Iterable[TemplateParagraph]) -> list[str]:
    return [paragraph.text for paragraph in paragraphs if paragraph.text]


def extract_candidate_profile(template: bytes) -> CandidateProfile:
    """Build operator-supplied profile facts from the uploaded resume only."""
    paragraphs = read_template_paragraphs(template)
    summary_index = next(
        (
            index
            for index, paragraph in enumerate(paragraphs)
            if _heading(paragraph.text) == "SUMMARY"
        ),
        len(paragraphs),
    )
    identity = [paragraph.text for paragraph in paragraphs[:summary_index] if paragraph.text]
    summary = _nonblank_text(_section_slice(paragraphs, "SUMMARY"))
    projects = _section_slice(paragraphs, "PROJECT HIGHLIGHTS")
    education = _nonblank_text(_section_slice(paragraphs, "EDUCATION"))
    skills = _section_slice(paragraphs, "TECHNICAL SKILLS")
    experience = _section_slice(paragraphs, "WORK EXPERIENCE")
    return CandidateProfile(
        full_name=identity[0] if identity else None,
        headline=identity[1] if len(identity) > 1 else None,
        summary=summary[0] if summary else None,
        skills=[paragraph.text for paragraph in skills if paragraph.text and paragraph.is_bullet],
        project_bullets=[
            paragraph.text for paragraph in projects if paragraph.text and paragraph.is_bullet
        ],
        experience_bullets=[
            paragraph.text for paragraph in experience if paragraph.text and paragraph.is_bullet
        ],
        education=education,
    )


def _terms(text: str) -> set[str]:
    return {
        token.casefold().strip("./-")
        for token in _TOKEN_RE.findall(text)
        if token.casefold().strip("./-") not in _STOP_WORDS
    }


def _score(text: str, job_terms: set[str]) -> int:
    paragraph_terms = _terms(text)
    return sum(term in paragraph_terms for term in job_terms)


def _section_bounds(
    children: list[ElementTree.Element], start_heading: str, end_heading: str
) -> tuple[int, int] | None:
    start = next(
        (
            index
            for index, child in enumerate(children)
            if child.tag == f"{_W}p" and _heading(_paragraph_text(child)) == start_heading
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index, child in enumerate(children[start + 1 :], start=start + 1)
            if child.tag == f"{_W}p" and _heading(_paragraph_text(child)) == end_heading
        ),
        None,
    )
    return (start + 1, end) if end is not None else None


def _replace_range(
    body: ElementTree.Element,
    start: int,
    end: int,
    replacement: list[ElementTree.Element],
) -> None:
    current = list(body)[start:end]
    for child in current:
        body.remove(child)
    for offset, child in enumerate(replacement):
        body.insert(start + offset, child)


def _reorder_project_blocks(
    body: ElementTree.Element, children: list[ElementTree.Element], job_terms: set[str]
) -> int:
    bounds = _section_bounds(children, "PROJECT HIGHLIGHTS", "EDUCATION")
    if bounds is None:
        return 0
    start, end = bounds
    section = children[start:end]
    prefix: list[ElementTree.Element] = []
    blocks: list[list[ElementTree.Element]] = []
    current: list[ElementTree.Element] = []
    for child in section:
        text = _paragraph_text(child) if child.tag == f"{_W}p" else ""
        if text and not _is_bullet(child):
            if current:
                blocks.append(current)
            current = [child]
        elif current:
            current.append(child)
        else:
            prefix.append(child)
    if current:
        blocks.append(current)
    if len(blocks) < 2:
        return 0
    ranked = sorted(
        enumerate(blocks),
        key=lambda item: (
            -_score(" ".join(_paragraph_text(child) for child in item[1]), job_terms),
            item[0],
        ),
    )
    reordered_count = sum(index != original for index, (original, _) in enumerate(ranked))
    if reordered_count:
        replacement = prefix + [child for _, block in ranked for child in block]
        _replace_range(body, start, end, replacement)
    return reordered_count


def _reorder_skill_bullets(
    body: ElementTree.Element, children: list[ElementTree.Element], job_terms: set[str]
) -> int:
    bounds = _section_bounds(children, "TECHNICAL SKILLS", "WORK EXPERIENCE")
    if bounds is None:
        return 0
    start, end = bounds
    section = children[start:end]
    bullet_positions = [index for index, child in enumerate(section) if _is_bullet(child)]
    if len(bullet_positions) < 2:
        return 0
    bullets = [section[index] for index in bullet_positions]
    ranked = sorted(
        enumerate(bullets),
        key=lambda item: (-_score(_paragraph_text(item[1]), job_terms), item[0]),
    )
    reordered_count = sum(index != original for index, (original, _) in enumerate(ranked))
    if reordered_count:
        replacement = list(section)
        for position, (_, bullet) in zip(bullet_positions, ranked, strict=True):
            replacement[position] = bullet
        _replace_range(body, start, end, replacement)
    return reordered_count


def _profile_entries(profile: CandidateProfile) -> dict[str, str]:
    claims = profile.experience_bullets + profile.project_bullets
    if len(set(claims)) != len(claims):
        raise ResumeTemplateError(
            "AI tailoring requires each approved resume bullet to have unique text"
        )
    return {f"PROFILE-{index:02d}": text for index, text in enumerate(claims, start=1)}


def _profile_skills(profile: CandidateProfile) -> dict[str, str]:
    if len(set(profile.skills)) != len(profile.skills):
        raise ResumeTemplateError(
            "AI tailoring requires each approved skill line to have unique text"
        )
    return {
        f"SKILL-{index:02d}": text
        for index, text in enumerate(profile.skills, start=1)
    }


def _exact_priority_validator(
    allowed_values: tuple[str, ...], *, field_name: str
) -> Callable[[list[str]], list[str]]:
    allowed = frozenset(allowed_values)

    def validate(values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError(f"{field_name} values must be unique")
        if set(values) != allowed:
            raise ValueError(f"{field_name} must contain every allowed value exactly once")
        return values

    return validate


class _ExactRoleStrategyResponse(BaseModel):
    """Current wire contract with one ignored legacy model-authored field."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_hiring_signals(cls, value: object) -> object:
        if isinstance(value, dict) and "top_hiring_signals" in value:
            cleaned = dict(value)
            cleaned.pop("top_hiring_signals", None)
            return cleaned
        return value


def _exact_role_strategy_model(
    entry_ids: list[str], *, skill_ids: list[str]
) -> type[BaseModel]:
    """Build a request-specific schema whose rewrite keys cannot be omitted."""

    evidence_schema_extra: dict[str, Any] = {
        "items": {"enum": entry_ids, "type": "string"},
        "uniqueItems": True,
    }
    skill_items: dict[str, Any] = {"type": "string"}
    if skill_ids:
        skill_items["enum"] = skill_ids
    skill_schema_extra: dict[str, Any] = {
        "items": skill_items,
        "uniqueItems": True,
    }

    rewrite_body = create_model(
        "GroundedRewriteBody",
        __config__=ConfigDict(extra="forbid"),
        text=(str, Field(min_length=1, max_length=600)),
        source_facts=(list[str], Field(min_length=1, max_length=8)),
    )
    rewrite_fields: dict[str, Any] = {
        entry_id: (rewrite_body, ...) for entry_id in entry_ids
    }
    rewrite_map = create_model(
        "ExactBulletRewrites",
        __config__=ConfigDict(extra="forbid"),
        **rewrite_fields,
    )
    return create_model(
        "ExactRoleStrategy",
        __base__=_ExactRoleStrategyResponse,
        role_summary=(str, Field(min_length=1, max_length=500)),
        evidence_priority=(
            Annotated[
                list[str],
                AfterValidator(
                    _exact_priority_validator(
                        tuple(entry_ids), field_name="evidence_priority"
                    )
                ),
            ],
            Field(
                min_length=len(entry_ids),
                max_length=len(entry_ids),
                json_schema_extra=evidence_schema_extra,
            ),
        ),
        skill_priority=(
            Annotated[
                list[str],
                AfterValidator(
                    _exact_priority_validator(
                        tuple(skill_ids), field_name="skill_priority"
                    )
                ),
            ],
            Field(
                min_length=len(skill_ids),
                max_length=len(skill_ids),
                json_schema_extra=skill_schema_extra,
            ),
        ),
        bullet_rewrites=(rewrite_map, ...),
        unsupported_requirements=(
            list[str],
            Field(default_factory=list, max_length=16),
        ),
        rewrite_guidance=(str, Field(min_length=1, max_length=800)),
    )


def _role_strategy_from_exact(
    response: BaseModel,
    *,
    entry_ids: list[str],
    sanitized_skill_by_id: dict[str, str],
    top_hiring_signals: list[str],
) -> RoleStrategy:
    payload = response.model_dump(mode="json")
    payload["top_hiring_signals"] = top_hiring_signals
    rewrite_map = payload.pop("bullet_rewrites", None)
    if not isinstance(rewrite_map, dict):
        raise ResumeTemplateError("AI role strategy returned an invalid rewrite map")
    rewrites: list[dict[str, object]] = []
    for entry_id in entry_ids:
        body = rewrite_map.get(entry_id)
        if not isinstance(body, dict):
            raise ResumeTemplateError(
                "AI role strategy omitted an approved profile entry"
            )
        rewrites.append({"profile_entry_id": entry_id, **body})
    payload["bullet_rewrites"] = rewrites
    skill_priority = payload.get("skill_priority")
    if not isinstance(skill_priority, list) or any(
        not isinstance(value, str) for value in skill_priority
    ):
        raise ResumeTemplateError("AI role strategy returned an invalid skill priority")
    try:
        payload["skill_priority"] = [
            sanitized_skill_by_id[skill_id] for skill_id in skill_priority
        ]
    except KeyError as error:
        raise ResumeTemplateError(
            "AI role strategy referenced an unknown approved skill"
        ) from error
    return RoleStrategy.model_validate(payload)


_HIRING_SIGNAL_HEADINGS = {
    "about the job",
    "job description",
    "job responsibilities",
    "preferred qualifications",
    "qualifications",
    "requirements",
    "responsibilities",
}


def _deterministic_hiring_signals(job_description: str) -> list[str]:
    """Select bounded exact JD spans without asking the model to restate them."""

    lines = [
        line.strip(" -•\t")
        for line in job_description.splitlines()
        if line.strip(" -•\t")
    ]
    if len(lines) == 1:
        lines = [
            fragment.strip()
            for fragment in re.split(r"[.;；]", lines[0])
            if fragment.strip()
        ]
    signals: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line.casefold().rstrip(":") in _HIRING_SIGNAL_HEADINGS:
            continue
        signal = line
        if len(signal) > 240:
            signal = signal[:240].rsplit(" ", 1)[0].rstrip(" ,;:")
        if not signal:
            continue
        folded = signal.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        signals.append(signal)
        if len(signals) == 8:
            break
    if not signals:
        signal = job_description.strip()[:240].rstrip()
        if not signal:
            raise ResumeTemplateError("Job description must not be empty")
        signals.append(signal)
    return signals


_GENERIC_JD_TERMS = {
    "ability",
    "build",
    "building",
    "candidate",
    "development",
    "engineer",
    "engineering",
    "experience",
    "responsibilities",
    "skills",
    "strong",
    "team",
    "work",
}
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?\+?(?!\w)")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")


def _protected_facts(text: str) -> set[str]:
    """Collect numbers and name/technology-like tokens that rewrites cannot invent."""
    protected = {match.group(0).casefold() for match in _NUMBER_RE.finditer(text)}
    words = list(_WORD_RE.finditer(text))
    for index, match in enumerate(words):
        token = match.group(0).strip("./-")
        if not token:
            continue
        if (
            any(character in token for character in "+#./")
            or any(character.isdigit() for character in token)
            or (len(token) > 1 and token.isupper())
            or any(character.isupper() for character in token[1:])
            or (index > 0 and token[0].isupper())
        ):
            protected.add(token.casefold())
    return protected


def _duplicate_indices(values: list[str], *, casefold: bool = False) -> list[int]:
    seen: set[str] = set()
    duplicates: list[int] = []
    for index, value in enumerate(values):
        key = value.casefold() if casefold else value
        if key in seen:
            duplicates.append(index)
        else:
            seen.add(key)
    return duplicates


def _validate_role_strategy(
    strategy: RoleStrategy,
    *,
    profile: CandidateProfile,
    job_description: str,
) -> dict[str, str]:
    entries = _profile_entries(profile)
    expected_ids = set(entries)
    failures: list[ResumeValidationFailure] = []
    claim_failure_ids: set[str] = set()
    duplicate_count = 0
    source_span_failure_count = 0

    def reject(
        rule_code: ResumeValidationRuleCode,
        json_path: str,
        *,
        claim_id: str | None = None,
        duplicate: bool = False,
        source_span: bool = False,
    ) -> None:
        nonlocal duplicate_count, source_span_failure_count
        failures.append(
            ResumeValidationFailure(
                rule_code=rule_code,
                json_path=json_path,
                claim_id=claim_id,
            )
        )
        if claim_id in expected_ids:
            claim_failure_ids.add(claim_id)
        if duplicate:
            duplicate_count += 1
        if source_span:
            source_span_failure_count += 1

    for index in _duplicate_indices(strategy.evidence_priority):
        evidence_id = strategy.evidence_priority[index]
        reject(
            ResumeValidationRuleCode.OUTPUT_DUPLICATE,
            f"$.evidence_priority[{index}]",
            claim_id=evidence_id if evidence_id in expected_ids else None,
            duplicate=True,
        )
    if set(strategy.evidence_priority) != expected_ids:
        reject(
            ResumeValidationRuleCode.OUTPUT_SCHEMA_INVALID,
            "$.evidence_priority",
        )
        claim_failure_ids.update(expected_ids - set(strategy.evidence_priority))

    expected_skills = set(profile.skills)
    for index in _duplicate_indices(strategy.skill_priority):
        reject(
            ResumeValidationRuleCode.OUTPUT_DUPLICATE,
            f"$.skill_priority[{index}]",
            duplicate=True,
        )
    if set(strategy.skill_priority) != expected_skills:
        reject(
            ResumeValidationRuleCode.OUTPUT_SCHEMA_INVALID,
            "$.skill_priority",
        )

    rewrite_ids = [item.profile_entry_id for item in strategy.bullet_rewrites]
    for index in _duplicate_indices(rewrite_ids):
        reject(
            ResumeValidationRuleCode.OUTPUT_DUPLICATE,
            f"$.bullet_rewrites[{index}].profile_entry_id",
            claim_id=rewrite_ids[index],
            duplicate=True,
        )
    rewrites = {item.profile_entry_id: item for item in strategy.bullet_rewrites}
    if set(rewrites) != expected_ids:
        reject(
            ResumeValidationRuleCode.OUTPUT_SCHEMA_INVALID,
            "$.bullet_rewrites",
        )
        claim_failure_ids.update(expected_ids - set(rewrites))

    jd_terms = _terms(job_description) - _GENERIC_JD_TERMS
    for index, rewrite in enumerate(strategy.bullet_rewrites):
        entry_id = rewrite.profile_entry_id
        if entry_id not in entries:
            continue
        source = entries[entry_id]
        source_folded = source.casefold()
        if any(fact.casefold() not in source_folded for fact in rewrite.source_facts):
            reject(
                ResumeValidationRuleCode.CLAIM_SOURCE_MISMATCH,
                f"$.bullet_rewrites[{index}].source_facts",
                claim_id=entry_id,
                source_span=True,
            )
        if not any(fact.casefold() in rewrite.text.casefold() for fact in rewrite.source_facts):
            reject(
                ResumeValidationRuleCode.CLAIM_NO_EVIDENCE,
                f"$.bullet_rewrites[{index}].text",
                claim_id=entry_id,
            )
        invented_protected = _protected_facts(rewrite.text) - _protected_facts(source)
        invented_numbers = {
            match.group(0).casefold() for match in _NUMBER_RE.finditer(rewrite.text)
        } - {match.group(0).casefold() for match in _NUMBER_RE.finditer(source)}
        invented_jd_terms = ((_terms(rewrite.text) & jd_terms) - _terms(source))
        if invented_numbers:
            reject(
                ResumeValidationRuleCode.CLAIM_NEW_NUMBER,
                f"$.bullet_rewrites[{index}].text",
                claim_id=entry_id,
            )
        if invented_protected - invented_numbers:
            reject(
                ResumeValidationRuleCode.REWRITE_FACT_MUTATION,
                f"$.bullet_rewrites[{index}].text",
                claim_id=entry_id,
            )
        if invented_jd_terms:
            reject(
                ResumeValidationRuleCode.CLAIM_ROLE_INFLATION,
                f"$.bullet_rewrites[{index}].text",
                claim_id=entry_id,
            )

    job_description_folded = job_description.casefold()
    for index, item in enumerate(strategy.unsupported_requirements):
        if item.casefold() not in job_description_folded:
            reject(
                ResumeValidationRuleCode.GAP_NOT_SOURCE_GROUNDED,
                f"$.unsupported_requirements[{index}]",
                source_span=True,
            )
    for index in _duplicate_indices(strategy.top_hiring_signals, casefold=True):
        reject(
            ResumeValidationRuleCode.HIRING_SIGNAL_DUPLICATE,
            f"$.top_hiring_signals[{index}]",
            duplicate=True,
        )
    for index, item in enumerate(strategy.top_hiring_signals):
        if item.casefold() not in job_description_folded:
            reject(
                ResumeValidationRuleCode.HIRING_SIGNAL_NOT_SOURCE_GROUNDED,
                f"$.top_hiring_signals[{index}]",
                source_span=True,
            )

    if failures:
        verified_count = 0
        supported_count = 0
        for entry_id in expected_ids - claim_failure_ids:
            selected_rewrite = rewrites.get(entry_id)
            if selected_rewrite is None:
                continue
            if selected_rewrite.text == entries[entry_id]:
                verified_count += 1
            else:
                supported_count += 1
        raise ResumeTemplateError(
            "AI role strategy failed deterministic truth validation",
            validation_diagnostics=ResumeValidationDiagnostics(
                failures=tuple(failures),
                candidate_count=len(entries),
                verified_count=verified_count,
                supported_count=supported_count,
                rejected_count=len(claim_failure_ids),
                duplicate_count=duplicate_count,
                source_span_failure_count=source_span_failure_count,
            ),
        )
    return entries


_SELECTIVE_REWRITE_FAILURES = {
    ResumeValidationRuleCode.CLAIM_NO_EVIDENCE,
    ResumeValidationRuleCode.CLAIM_SOURCE_MISMATCH,
    ResumeValidationRuleCode.CLAIM_ROLE_INFLATION,
    ResumeValidationRuleCode.CLAIM_NEW_NUMBER,
    ResumeValidationRuleCode.CLAIM_CONTRADICTED,
    ResumeValidationRuleCode.REWRITE_NOT_MATERIAL,
    ResumeValidationRuleCode.REWRITE_FACT_MUTATION,
}


def _select_safe_rewrites(
    strategy: RoleStrategy,
    *,
    profile: CandidateProfile,
    job_description: str,
) -> tuple[RoleStrategy, dict[str, str], ResumeValidationDiagnostics]:
    """Keep each supported rewrite and restore only rejected claims to source."""

    entries = _profile_entries(profile)
    try:
        _validate_role_strategy(
            strategy,
            profile=profile,
            job_description=job_description,
        )
    except ResumeTemplateError as error:
        diagnostics = error.validation_diagnostics
        if diagnostics is None or any(
            failure.rule_code not in _SELECTIVE_REWRITE_FAILURES
            or failure.claim_id is None
            for failure in diagnostics.failures
        ):
            raise
        rejected_ids = {
            failure.claim_id
            for failure in diagnostics.failures
            if failure.claim_id is not None
        }
        selected_rewrites = [
            GroundedResumeBulletRewrite(
                profile_entry_id=rewrite.profile_entry_id,
                text=entries[rewrite.profile_entry_id],
                source_facts=[entries[rewrite.profile_entry_id]],
            )
            if rewrite.profile_entry_id in rejected_ids
            else rewrite
            for rewrite in strategy.bullet_rewrites
        ]
        selected = strategy.model_copy(
            update={"bullet_rewrites": selected_rewrites}
        )
        _validate_role_strategy(
            selected,
            profile=profile,
            job_description=job_description,
        )
        return (
            selected,
            entries,
            replace(diagnostics, validator_status="selective_pass"),
        )

    verified_count = sum(
        rewrite.text == entries[rewrite.profile_entry_id]
        for rewrite in strategy.bullet_rewrites
    )
    return (
        strategy,
        entries,
        ResumeValidationDiagnostics(
            failures=(),
            candidate_count=len(entries),
            verified_count=verified_count,
            supported_count=len(entries) - verified_count,
            rejected_count=0,
            duplicate_count=0,
            source_span_failure_count=0,
            validator_status="accepted",
        ),
    )


def _reorder_project_blocks_by_priority(
    body: ElementTree.Element,
    children: list[ElementTree.Element],
    priority_by_text: dict[str, int],
) -> int:
    bounds = _section_bounds(children, "PROJECT HIGHLIGHTS", "EDUCATION")
    if bounds is None:
        return 0
    start, end = bounds
    section = children[start:end]
    prefix: list[ElementTree.Element] = []
    blocks: list[list[ElementTree.Element]] = []
    current: list[ElementTree.Element] = []
    for child in section:
        text = _paragraph_text(child) if child.tag == f"{_W}p" else ""
        if text and not _is_bullet(child):
            if current:
                blocks.append(current)
            current = [child]
        elif current:
            current.append(child)
        else:
            prefix.append(child)
    if current:
        blocks.append(current)
    ranked = sorted(
        enumerate(blocks),
        key=lambda item: (
            min(
                (
                    priority_by_text.get(_paragraph_text(child), len(priority_by_text))
                    for child in item[1]
                    if child.tag == f"{_W}p" and _is_bullet(child)
                ),
                default=len(priority_by_text),
            ),
            item[0],
        ),
    )
    reordered_count = sum(index != original for index, (original, _) in enumerate(ranked))
    if reordered_count:
        _replace_range(body, start, end, prefix + [child for _, block in ranked for child in block])
    return reordered_count


def _reorder_skill_bullets_by_priority(
    body: ElementTree.Element,
    children: list[ElementTree.Element],
    skill_priority: list[str],
) -> int:
    bounds = _section_bounds(children, "TECHNICAL SKILLS", "WORK EXPERIENCE")
    if bounds is None:
        return 0
    start, end = bounds
    section = children[start:end]
    bullet_positions = [index for index, child in enumerate(section) if _is_bullet(child)]
    bullets_by_text = {
        _paragraph_text(section[index]): section[index] for index in bullet_positions
    }
    if set(bullets_by_text) != set(skill_priority):
        raise ResumeTemplateError("Approved skill lines do not map cleanly to the DOCX")
    reordered = [bullets_by_text[text] for text in skill_priority]
    reordered_count = sum(
        section[position] is not bullet
        for position, bullet in zip(bullet_positions, reordered, strict=True)
    )
    if reordered_count:
        replacement = list(section)
        for position, bullet in zip(bullet_positions, reordered, strict=True):
            replacement[position] = bullet
        _replace_range(body, start, end, replacement)
    return reordered_count


def _replace_bullet_text(
    body: ElementTree.Element,
    *,
    source_text: str,
    replacement_text: str,
) -> None:
    matches = [
        child
        for child in body
        if child.tag == f"{_W}p" and _is_bullet(child) and _paragraph_text(child) == source_text
    ]
    if len(matches) != 1:
        raise ResumeTemplateError("Approved profile entry does not map uniquely to the DOCX")
    text_nodes = list(matches[0].iter(f"{_W}t"))
    if not text_nodes:
        raise ResumeTemplateError("Approved DOCX bullet has no writable text")
    text_nodes[0].text = replacement_text
    for node in text_nodes[1:]:
        node.text = ""


def _structured_candidate_artifact(
    strategy: RoleStrategy,
    *,
    status: str,
    diagnostics: ResumeValidationDiagnostics | None,
) -> dict[str, object]:
    """Keep the raw model candidate inspectable but never submission-eligible."""

    return {
        "schema_version": "1.0",
        "artifact_type": "RESUME_STRUCTURED_CANDIDATE",
        "status": status,
        "submission_status": "NOT_FOR_SUBMISSION",
        "label": (
            "REJECTED / NOT FOR SUBMISSION"
            if status == "REJECTED"
            else "NOT FOR SUBMISSION"
        ),
        "structured_candidate": strategy.model_dump(mode="json"),
        "validation_diagnostics": (
            diagnostics.as_dict() if diagnostics is not None else None
        ),
    }


def tailor_resume_docx_with_gateway(
    template: bytes,
    job_description: str,
    *,
    gateway: ModelGateway,
    tailoring_instructions: str = "",
    template_metadata: ResumeTemplateMetadata | None = None,
    support_upload: ExtractedResumeUpload | None = None,
    candidate_recorder: Callable[[dict[str, object]], None] | None = None,
) -> TailoredDocx:
    """Use one explicit provider to rank and ground rewrites against approved bullets."""
    if not job_description.strip():
        raise ResumeTemplateError("Job description must not be empty")
    profile = extract_candidate_profile(template)
    entries = _profile_entries(profile)
    skills = _profile_skills(profile)
    entry_ids = list(entries)
    skill_ids = list(skills)
    response_schema = _exact_role_strategy_model(
        entry_ids, skill_ids=skill_ids
    )
    prepared = prepare_resume_gateway_payload(
        profile=profile,
        job_description=job_description,
        tailoring_instructions=tailoring_instructions,
        template_metadata=template_metadata
        or ResumeTemplateMetadata(source_format="docx"),
        support_upload=support_upload,
        output_schema=response_schema,
    )
    sanitized_skill_by_id = dict(
        zip(
            skill_ids,
            prepared.payload.candidate_profile.skills,
            strict=True,
        )
    )
    deterministic_signals = _deterministic_hiring_signals(
        prepared.payload.job_description
    )
    model_user = prepared.payload.model_dump_json()
    model_system = (
        "You tailor a resume only from approved Candidate Profile facts. Return the "
        "required JSON. Rank every profile entry and every exact skill line once. "
        "skill_priority must contain every request-specific SKILL key exactly once; "
        "SKILL-01 maps to the first candidate_profile.skills item, and so on. "
        "bullet_rewrites is an object whose required PROFILE keys are fixed by the "
        "schema; provide one grounded rewrite body for every key. Rewrite every bullet "
        "for this JD, but retain at least one exact source fact fragment and never add "
        "a technology, company, metric, scope, or outcome absent from that source "
        "bullet. Use the deterministic hiring signals below as read-only targeting "
        "context; do not return or restate them. Unsupported requirements must be exact "
        "quotes from the JD. Preserve every __SS_PRIVATE_*__ placeholder exactly. "
        "Deterministic hiring signals: "
        + json.dumps(deterministic_signals, ensure_ascii=False)
    )
    model_started = time.perf_counter()
    exact_strategy = gateway.complete(
        response_schema,
        system=model_system,
        user=model_user,
        reasoning_effort="none",
    )
    strategy = _role_strategy_from_exact(
        exact_strategy,
        entry_ids=entry_ids,
        sanitized_skill_by_id=sanitized_skill_by_id,
        top_hiring_signals=deterministic_signals,
    )
    model_call_profile: dict[str, object] = {
        "schema_version": "1.0",
        "model_call_count": 1,
        "output_contract": "selective_rewrite_v0.1",
        "reasoning_effort": "none",
        "gateway_wall_ms": int((time.perf_counter() - model_started) * 1000),
        "system_chars": len(model_system),
        "user_chars": len(model_user),
        "schema_chars": len(
            json.dumps(
                response_schema.model_json_schema(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }
    provider_profile = getattr(gateway, "last_call_profile", None)
    if isinstance(provider_profile, ModelCallProfile):
        model_call_profile["provider_metrics"] = provider_profile.model_dump(
            mode="json"
        )
    validate_role_strategy_placeholders(strategy, prepared)
    strategy = restore_role_strategy(strategy, prepared.private_replacements)
    raw_strategy = strategy
    if candidate_recorder is not None:
        candidate_recorder(
            _structured_candidate_artifact(
                raw_strategy,
                status="PENDING_VALIDATION",
                diagnostics=None,
            )
        )
    try:
        strategy, validated_entries, validation_diagnostics = _select_safe_rewrites(
            raw_strategy, profile=profile, job_description=job_description
        )
    except ResumeTemplateError as error:
        if candidate_recorder is not None:
            candidate_recorder(
                _structured_candidate_artifact(
                    raw_strategy,
                    status="REJECTED",
                    diagnostics=error.validation_diagnostics,
                )
            )
        raise
    if candidate_recorder is not None:
        candidate_recorder(
            _structured_candidate_artifact(
                raw_strategy,
                status=(
                    "REJECTED"
                    if validation_diagnostics.rejected_count
                    else "VALIDATED"
                ),
                diagnostics=validation_diagnostics,
            )
        )
    members, document = _read_package(template)
    root, body = _parse_document(document)
    source_paragraphs = [
        _paragraph_text(child) for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    ]
    rank_by_id = {entry_id: index for index, entry_id in enumerate(strategy.evidence_priority)}
    priority_by_text = {
        validated_entries[entry_id]: rank for entry_id, rank in rank_by_id.items()
    }
    project_count = _reorder_project_blocks_by_priority(body, list(body), priority_by_text)
    skill_count = _reorder_skill_bullets_by_priority(
        body, list(body), strategy.skill_priority
    )
    rewrite_by_id = {item.profile_entry_id: item.text for item in strategy.bullet_rewrites}
    for entry_id, source_text in validated_entries.items():
        _replace_bullet_text(
            body,
            source_text=source_text,
            replacement_text=rewrite_by_id[entry_id],
        )
    if len(source_paragraphs) != sum(
        1 for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    ):
        raise ResumeTemplateError("AI tailoring changed the DOCX paragraph structure")
    tailored_document = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for info, content in members:
            archive.writestr(
                info, tailored_document if info.filename == _DOCUMENT_PART else content
            )
    output = target.getvalue()
    return TailoredDocx(
        content=output,
        template_sha256=hashlib.sha256(template).hexdigest(),
        output_sha256=hashlib.sha256(output).hexdigest(),
        project_blocks_reordered=project_count,
        skill_bullets_reordered=skill_count,
        source_paragraph_count=len(source_paragraphs),
        claims_preserved=True,
        grounded_rewrites=validation_diagnostics.supported_count,
        rejected_rewrites=validation_diagnostics.rejected_count,
        generation_mode="ai",
        provider=gateway.descriptor.provider.value,
        model=gateway.descriptor.model,
        model_call_profile=model_call_profile,
        validation_diagnostics=validation_diagnostics,
        role_strategy=strategy,
    )


def request_resume_expert_review(
    tailored: TailoredDocx,
    *,
    profile: CandidateProfile,
    gateway: ModelGateway,
) -> ResumeExpertReviewResult:
    """Send only hiring signals, source fragments, and the current draft."""

    strategy = tailored.role_strategy
    if strategy is None:
        raise ResumeTemplateError(
            "Expert review requires an evidence-grounded AI draft"
        )
    approved_entries = _profile_entries(profile)
    rewrite_by_id = {
        rewrite.profile_entry_id: rewrite for rewrite in strategy.bullet_rewrites
    }
    if set(rewrite_by_id) != set(approved_entries):
        raise ResumeTemplateError("Expert review cannot resolve the approved draft")
    return gateway.complete(
        ResumeExpertReviewResult,
        system=(
            "Review a resume as a hiring manager. Return patches only. Improve clarity, "
            "technical accuracy, ATS alignment, ordering emphasis, and one-page density "
            "without adding scope, ownership, technologies, metrics, or outcomes. Every "
            "patch must identify the exact PROFILE key and SHA-256 of its current text. "
            "List every proposed new factual claim explicitly in new_factual_claims; the "
            "application will reject any non-empty list. Do not request or infer other "
            "local evidence."
        ),
        user=json.dumps(
            {
                "hiring_signals": strategy.top_hiring_signals,
                "evidence_bundle": {
                    entry_id: {
                        "source_facts": rewrite_by_id[entry_id].source_facts,
                        "approved_source_sha256": hashlib.sha256(
                            approved_entries[entry_id].encode("utf-8")
                        ).hexdigest(),
                    }
                    for entry_id in strategy.evidence_priority
                },
                "draft_resume_bullets": {
                    entry_id: rewrite_by_id[entry_id].text
                    for entry_id in strategy.evidence_priority
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        reasoning_effort="low",
    )


def apply_resume_expert_review(
    tailored: TailoredDocx,
    *,
    profile: CandidateProfile,
    job_description: str,
    review: ResumeExpertReviewResult,
    expert_provider: str,
    expert_model: str | None,
) -> TailoredDocx:
    """Apply a patch-only expert review and re-run the original fact validator."""

    strategy = tailored.role_strategy
    if strategy is None:
        raise ResumeTemplateError(
            "Expert review requires an evidence-grounded AI draft"
        )
    approved_entries = _profile_entries(profile)
    rewrite_by_id = {
        rewrite.profile_entry_id: rewrite for rewrite in strategy.bullet_rewrites
    }
    if set(rewrite_by_id) != set(approved_entries):
        raise ResumeTemplateError("Expert review cannot resolve the approved draft")
    allowed_ids = set(approved_entries)
    if any(
        patch.profile_entry_id not in allowed_ids
        or patch.before_sha256
        != hashlib.sha256(
            rewrite_by_id[patch.profile_entry_id].text.encode("utf-8")
        ).hexdigest()
        for patch in review.patches
    ):
        raise ResumeTemplateError("Expert review patch does not match the approved draft")
    if any(patch.new_factual_claims for patch in review.patches):
        raise ResumeTemplateError("Expert review proposed unsupported factual claims")
    if any(
        entry_id not in allowed_ids
        for entry_id in review.omitted_high_value_profile_entry_ids
    ):
        raise ResumeTemplateError("Expert review referenced an unknown approved fact")

    patch_by_id = {patch.profile_entry_id: patch for patch in review.patches}
    updated_rewrites = [
        rewrite.model_copy(
            update={"text": patch_by_id[rewrite.profile_entry_id].after}
        )
        if rewrite.profile_entry_id in patch_by_id
        else rewrite
        for rewrite in strategy.bullet_rewrites
    ]
    reviewed_strategy = strategy.model_copy(update={"bullet_rewrites": updated_rewrites})
    _validate_role_strategy(
        reviewed_strategy,
        profile=profile,
        job_description=job_description,
    )

    members, document = _read_package(tailored.content)
    root, body = _parse_document(document)
    source_paragraph_count = sum(
        1 for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    )
    for entry_id, patch in patch_by_id.items():
        _replace_bullet_text(
            body,
            source_text=rewrite_by_id[entry_id].text,
            replacement_text=patch.after,
        )
    if source_paragraph_count != sum(
        1 for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    ):
        raise ResumeTemplateError("Expert review changed the DOCX paragraph structure")
    reviewed_document = ElementTree.tostring(
        root, encoding="utf-8", xml_declaration=True
    )
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for info, content in members:
            archive.writestr(
                info,
                reviewed_document if info.filename == _DOCUMENT_PART else content,
            )
    output = target.getvalue()
    return replace(
        tailored,
        content=output,
        output_sha256=hashlib.sha256(output).hexdigest(),
        grounded_rewrites=len(reviewed_strategy.bullet_rewrites),
        role_strategy=reviewed_strategy,
        expert_review=review,
        expert_rewrites=len(review.patches),
        expert_provider=expert_provider,
        expert_model=expert_model,
    )


def tailor_resume_docx(template: bytes, job_description: str) -> TailoredDocx:
    """Return a style-preserving DOCX whose source claims are only reordered."""
    if not job_description.strip():
        raise ResumeTemplateError("Job description must not be empty")
    members, document = _read_package(template)
    root, body = _parse_document(document)
    source_paragraphs = [
        _paragraph_text(child) for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    ]
    job_terms = _terms(job_description)
    children = list(body)
    project_count = _reorder_project_blocks(body, children, job_terms)
    children = list(body)
    skill_count = _reorder_skill_bullets(body, children, job_terms)
    output_paragraphs = [
        _paragraph_text(child) for child in body if child.tag == f"{_W}p" and _paragraph_text(child)
    ]
    claims_preserved = sorted(source_paragraphs) == sorted(output_paragraphs)
    if not claims_preserved:
        raise ResumeTemplateError("Tailoring changed candidate text; output was rejected")
    tailored_document = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for info, content in members:
            output_content = tailored_document if info.filename == _DOCUMENT_PART else content
            archive.writestr(info, output_content)
    output = target.getvalue()
    return TailoredDocx(
        content=output,
        template_sha256=hashlib.sha256(template).hexdigest(),
        output_sha256=hashlib.sha256(output).hexdigest(),
        project_blocks_reordered=project_count,
        skill_bullets_reordered=skill_count,
        source_paragraph_count=len(source_paragraphs),
        claims_preserved=claims_preserved,
    )
