"""Compact verified evidence and deterministic JD positioning for Resume."""

from __future__ import annotations

import hashlib
import json
import re

from soloscale.content_canon import StoryReadiness, load_month_one_canon
from soloscale.resume_models import (
    CandidateEvidencePack,
    CandidateEvidenceSource,
    CandidateProfile,
    JDPositioningBrief,
    ResumeAtomicFact,
    build_resume_atomic_facts,
)

_HEADING_LINES = {
    "about the job",
    "job description",
    "job responsibilities",
    "preferred qualifications",
    "qualifications",
    "requirements",
    "responsibilities",
}
_THEMES = (
    "Python",
    "RAG",
    "retrieval",
    "reranking",
    "embeddings",
    "structured outputs",
    "agentic workflows",
    "evaluation",
    "FastAPI",
    "Flask",
    "Django",
    "Vertex AI",
    "GCP",
    "AWS",
    "PostgreSQL",
    "CI/CD",
    "observability",
)
_STORY_IDS = ("M1-13", "M1-14", "M1-15")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{2,}")


def deterministic_hiring_signals(job_description: str) -> list[str]:
    """Return bounded exact JD spans without asking a model to restate them."""

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
    candidates = [
        line
        for line in lines
        if line.casefold().rstrip(":") not in _HEADING_LINES
        and 3 <= len(line) <= 360
    ]
    return list(dict.fromkeys(candidates[:8])) or [
        job_description.strip()[:360]
    ]


def _entry_terms(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value)}


def _soloscale_target(profile: CandidateProfile) -> str | None:
    entries = profile.experience_bullets + profile.project_bullets
    for index, text in enumerate(entries, start=1):
        folded = text.casefold()
        if "soloscale" in folded or "solo scale" in folded:
            return f"PROFILE-{index:02d}"
    return None


def _story_fact_texts(story_id: str) -> list[tuple[str, str | None]]:
    story = next(
        story for story in load_month_one_canon().stories if story.story_id == story_id
    )
    facts: list[tuple[str, str | None]] = [
        (story.fact, None),
        (story.architecture, None),
        (story.decision, None),
        (story.implementation, None),
        (story.higher_level_insight, None),
    ]
    facts.extend((metric, metric) for metric in story.verified_metrics)
    return facts[:8]


def build_candidate_evidence_pack(profile: CandidateProfile) -> CandidateEvidencePack:
    """Combine approved Resume facts with compact tracked READY evidence."""

    atomic_facts = build_resume_atomic_facts(profile)
    sources: list[CandidateEvidenceSource] = []
    target_id = _soloscale_target(profile)
    if target_id is not None:
        stories = {story.story_id: story for story in load_month_one_canon().stories}
        for story_id in _STORY_IDS:
            story = stories[story_id]
            if story.status != StoryReadiness.READY_FOR_PRODUCTION:
                continue
            evidence_id = f"EVIDENCE-{story_id}"
            source_payload = story.model_dump_json()
            source_sha256 = hashlib.sha256(source_payload.encode()).hexdigest()
            sources.append(
                CandidateEvidenceSource(
                    evidence_id=evidence_id,
                    project="SoloScale AI OS",
                    source_kind="TRACKED_CANON",
                    source_sha256=source_sha256,
                    source_refs=story.evidence_candidates,
                )
            )
            for index, (text, metric) in enumerate(
                _story_fact_texts(story_id), start=1
            ):
                fact_id = f"FACT-{evidence_id}-{index:02d}"
                atomic_facts.append(
                    ResumeAtomicFact(
                        fact_id=fact_id,
                        profile_entry_id=target_id,
                        evidence_id=evidence_id,
                        source_kind="CANDIDATE_EVIDENCE",
                        project="SoloScale AI OS",
                        capability_tags=[
                            "desktop-ai",
                            "background-jobs",
                            "performance",
                            "evidence-grounding",
                        ],
                        metric=metric,
                        text=text,
                        source_sha256=source_sha256,
                        fact_sha256=hashlib.sha256(
                            f"{fact_id}\0{target_id}\0{text}".encode()
                        ).hexdigest(),
                    )
                )
    canonical = json.dumps(
        {
            "sources": [source.model_dump(mode="json") for source in sources],
            "atomic_facts": [fact.model_dump(mode="json") for fact in atomic_facts],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CandidateEvidencePack(
        sources=sources,
        atomic_facts=atomic_facts,
        pack_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def build_jd_positioning_brief(
    job_description: str,
    facts: list[ResumeAtomicFact],
) -> JDPositioningBrief:
    """Rank verified facts against exact JD language without model inference."""

    signals = deterministic_hiring_signals(job_description)
    jd_terms = _entry_terms(job_description)
    ranked = sorted(
        facts,
        key=lambda fact: (
            -len(_entry_terms(fact.text) & jd_terms),
            fact.fact_id,
        ),
    )
    technical_themes = [
        theme for theme in _THEMES if theme.casefold() in job_description.casefold()
    ]
    projects = [fact.project for fact in ranked if fact.project]
    lines = [line.strip() for line in job_description.splitlines() if line.strip()]
    role_title = next(
        (
            line.strip(" -•\t")
            for line in lines
            if line.casefold().strip(" :") not in _HEADING_LINES
            and len(line.strip(" -•\t")) <= 120
            and not line.lstrip().startswith(("●", "-", "•"))
        ),
        "Target role",
    )
    return JDPositioningBrief(
        job_description_sha256=hashlib.sha256(job_description.encode()).hexdigest(),
        role_title=role_title,
        top_hiring_signals=signals,
        technical_themes=technical_themes[:12],
        priority_fact_ids=[fact.fact_id for fact in ranked[:48]],
        first_resume_focus=list(dict.fromkeys(projects))[:8],
    )


__all__ = [
    "build_candidate_evidence_pack",
    "build_jd_positioning_brief",
    "deterministic_hiring_signals",
]
