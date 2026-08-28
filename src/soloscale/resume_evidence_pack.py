"""Compact verified evidence and deterministic JD positioning for Resume."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from soloscale.content_canon import StoryReadiness, load_month_one_canon
from soloscale.evidence_hub import EvidenceHub, EvidenceHubError, inspect_git_repository
from soloscale.github_connect import GitHubConnectionStore, is_resume_safe_commit_summary
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
    # The real Resume template keeps the project name in a non-bullet heading,
    # while CandidateProfile intentionally retains only approved bullets. Match
    # the first distinctive SoloScale project bullet without weakening the
    # general profile truth boundary.
    markers = (
        "evidence-grounded",
        "retrieval-backed resume",
        "conversation rag",
        "evidence agent",
        "resume workspace",
    )
    for index, text in enumerate(entries, start=1):
        folded = text.casefold()
        if sum(marker in folded for marker in markers) >= 2:
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


def _append_repository_facts(
    *,
    profile: CandidateProfile,
    atomic_facts: list[ResumeAtomicFact],
    sources: list[CandidateEvidenceSource],
    data_root: Path,
    repository_root: Path,
) -> None:
    target_id = _soloscale_target(profile)
    if target_id is None:
        return
    if not EvidenceHub.catalog_exists(data_root):
        raise EvidenceHubError("local project evidence has not been refreshed")
    current_source, _ = inspect_git_repository(repository_root)
    stored_snapshot = EvidenceHub(data_root).git_repository_snapshot(repository_root)
    if stored_snapshot is None:
        raise EvidenceHubError("local project evidence has not been refreshed")
    stored_source, stored_items = stored_snapshot
    if stored_source.content_sha256 != current_source.content_sha256:
        raise EvidenceHubError("local project evidence is stale")
    commit_items = sorted(
        (
            item
            for item in stored_items
            if item.evidence_type == "git_commit"
            and item.verification_status == "git_object_verified"
        ),
        key=lambda item: (item.source_at or item.captured_at, item.native_id),
        reverse=True,
    )[:12]
    if not commit_items:
        return
    insert_at = len(atomic_facts)
    evidence_id = "EVIDENCE-LOCAL-GIT"
    sources.insert(
        0,
        CandidateEvidenceSource(
            evidence_id=evidence_id,
            project=stored_source.project or repository_root.name,
            source_kind="REPOSITORY",
            source_sha256=stored_source.content_sha256,
            source_refs=[item.native_id for item in commit_items],
        ),
    )
    repository_facts: list[ResumeAtomicFact] = []
    safe_commit_items = [
        item
        for item in commit_items
        if is_resume_safe_commit_summary(
            item.public_safe_summary.removeprefix("Repository commit: ").strip()
        )
    ]
    for index, item in enumerate(safe_commit_items, start=1):
        subject = item.public_safe_summary.removeprefix("Repository commit: ").strip()
        fact_id = f"FACT-{evidence_id}-{index:02d}"
        text = f"Verified repository commit: {subject}"
        repository_facts.append(
            ResumeAtomicFact(
                fact_id=fact_id,
                profile_entry_id=target_id,
                evidence_id=evidence_id,
                source_kind="CANDIDATE_EVIDENCE",
                project=stored_source.project or repository_root.name,
                capability_tags=["repository", "verified-commit"],
                text=text,
                source_sha256=stored_source.content_sha256,
                fact_sha256=hashlib.sha256(
                    f"{fact_id}\0{target_id}\0{text}".encode()
                ).hexdigest(),
            )
        )
    atomic_facts[insert_at:insert_at] = repository_facts


def _append_github_commit_facts(
    *,
    profile: CandidateProfile,
    atomic_facts: list[ResumeAtomicFact],
    sources: list[CandidateEvidenceSource],
    data_root: Path,
) -> None:
    """Admit only bounded committed summaries from explicitly selected GitHub repos."""

    target_id = _soloscale_target(profile)
    if target_id is None or not EvidenceHub.catalog_exists(data_root):
        return
    try:
        connection = GitHubConnectionStore(data_root).load()
    except (OSError, ValueError):
        return
    if connection is None or connection.evidence_refreshed_at is None:
        return
    hub = EvidenceHub(data_root)
    candidates = []
    for repository in connection.selected_repositories:
        candidates.extend(
            hub.search_metadata(
                "GitHub commit:",
                limit=12,
                source_types=["selected_repository_snapshot"],
                project_id=repository.full_name,
            )
        )
    local_commit_ids = {
        reference
        for source in sources
        if source.evidence_id == "EVIDENCE-LOCAL-GIT"
        for reference in source.source_refs
    }
    commits = sorted(
        (
            item
            for item in candidates
            if item.evidence_type == "github_commit"
            and item.verification_status == "github_api_observed_not_role_verified"
            and item.native_id not in local_commit_ids
        ),
        key=lambda item: (item.source_at or item.captured_at, item.native_id),
        reverse=True,
    )
    commits = [
        item
        for item in commits
        if is_resume_safe_commit_summary(
            item.public_safe_summary.removeprefix("GitHub commit: ").strip()
        )
    ][:12]
    if not commits:
        return
    source_sha256 = hashlib.sha256(
        json.dumps(
            [
                {
                    "evidence_id": item.evidence_id,
                    "content_sha256": item.content_sha256,
                }
                for item in commits
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    evidence_id = "EVIDENCE-GITHUB-COMMITS"
    sources.append(
        CandidateEvidenceSource(
            evidence_id=evidence_id,
            project="Selected GitHub repositories",
            source_kind="REPOSITORY",
            source_sha256=source_sha256,
            source_refs=[item.native_id for item in commits],
        )
    )
    for index, item in enumerate(commits, start=1):
        subject = item.public_safe_summary.removeprefix("GitHub commit: ").strip()
        fact_id = f"FACT-{evidence_id}-{index:02d}"
        text = f"Committed repository summary: {subject}"
        atomic_facts.append(
            ResumeAtomicFact(
                fact_id=fact_id,
                profile_entry_id=target_id,
                evidence_id=evidence_id,
                source_kind="CANDIDATE_EVIDENCE",
                project=item.project,
                capability_tags=["repository", "committed-summary"],
                text=text,
                source_sha256=source_sha256,
                fact_sha256=hashlib.sha256(
                    f"{fact_id}\0{target_id}\0{text}".encode()
                ).hexdigest(),
            )
        )


def build_candidate_evidence_pack(
    profile: CandidateProfile,
    *,
    data_root: Path | None = None,
    repository_root: Path | None = None,
) -> CandidateEvidencePack:
    """Combine approved Resume facts with compact tracked READY evidence."""

    atomic_facts = build_resume_atomic_facts(profile)
    sources: list[CandidateEvidenceSource] = []
    if repository_root is not None and data_root is None:
        raise ValueError("data_root is required when repository_root is provided")
    if data_root is not None and repository_root is not None:
        _append_repository_facts(
            profile=profile,
            atomic_facts=atomic_facts,
            sources=sources,
            data_root=Path(data_root),
            repository_root=Path(repository_root),
        )
    if data_root is not None:
        _append_github_commit_facts(
            profile=profile,
            atomic_facts=atomic_facts,
            sources=sources,
            data_root=Path(data_root),
        )
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
