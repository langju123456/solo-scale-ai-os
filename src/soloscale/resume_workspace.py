# ruff: noqa: E501
"""Deterministic private Resume Intelligence Workspace workflow.

The module deliberately does not call a network service.  Hybrid research is only a
provider boundary; callers must explicitly supply a provider and its research packet.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from soloscale.knowledge_models import RetrievalHit
from soloscale.resume_models import (
    CandidateProfile,
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceLocator,
    EvidenceMatch,
    GraphNodeKind,
    JobRequirement,
    JobResearchSource,
    LearningTask,
    ResumeBullet,
    ResumeDraft,
    ResumeMode,
    ResumeRun,
    SkillGap,
)

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_SKILL_RE = re.compile(
    r"\b(?:python|rag|llm|docker|kubernetes|k8s|aws|gcp|azure|sql|git|ci/?cd|fastapi)\b", re.I
)


class JobResearchProvider(Protocol):
    """Optional boundary; providers must never receive local evidence or profiles."""

    def research(
        self, *, job_description: str, company_name: str | None, company_url: str | None
    ) -> list[JobResearchSource]: ...


class DisabledJobResearchProvider:
    def research(
        self, *, job_description: str, company_name: str | None, company_url: str | None
    ) -> list[JobResearchSource]:
        del job_description, company_name, company_url
        return []


def _safe_text_list(value: object) -> list[str]:
    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _skills(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).lower() for match in _SKILL_RE.finditer(text)))


def parse_requirements(job_description: str) -> list[JobRequirement]:
    """A transparent line-first parser suitable for a bounded local v0.1 workflow."""
    fragments = [line.strip(" -•\t") for line in job_description.splitlines() if line.strip()]
    if len(fragments) == 1:
        fragments = [part.strip() for part in re.split(r"[.;；]", fragments[0]) if part.strip()]
    requirements: list[JobRequirement] = []
    for index, text in enumerate(fragments[:24], start=1):
        lower = text.casefold()
        priority: Literal["critical", "preferred"] = (
            "critical"
            if any(token in lower for token in ("required", "must", "必要", "要求"))
            else "preferred"
        )
        requirements.append(
            JobRequirement(
                id=f"REQ-{index:02d}", text=text, skills=_skills(text), priority=priority
            )
        )
    return requirements


def _locator(hit: RetrievalHit) -> EvidenceLocator:
    # Existing Conversation RAG hits have no code locator; retain that absence explicitly.
    del hit
    return EvidenceLocator()


def build_matches(
    requirements: list[JobRequirement], hits: list[RetrievalHit]
) -> list[EvidenceMatch]:
    matches: list[EvidenceMatch] = []
    for requirement in requirements:
        terms = set(_skills(requirement.text))
        if not terms:
            continue
        for hit in hits:
            overlap = sum(term in hit.excerpt.casefold() for term in terms)
            if not overlap and terms:
                continue
            matches.append(
                EvidenceMatch(
                    id=f"MATCH-{requirement.id}-{hit.chunk_id}",
                    requirement_id=requirement.id,
                    evidence_id=hit.chunk_id,
                    excerpt=hit.excerpt,
                    strength="strong" if overlap > 1 else "partial",
                    locator=_locator(hit),
                )
            )
    return matches


def build_resume(profile: CandidateProfile, requirements: list[JobRequirement]) -> ResumeDraft:
    bullets: list[ResumeBullet] = []
    for index, text in enumerate(profile.experience_bullets + profile.project_bullets, start=1):
        profile_terms = set(_skills(text)) | {item.casefold() for item in profile.skills}
        requirement_ids = [
            requirement.id
            for requirement in requirements
            if profile_terms & set(_skills(requirement.text))
        ]
        bullets.append(
            ResumeBullet(
                text=text,
                evidence_ids=[f"PROFILE-{index:02d}"],
                requirement_ids=requirement_ids,
                support="candidate_profile",
            )
        )
    return ResumeDraft(
        summary=profile.summary or profile.headline,
        skills=profile.skills,
        bullets=[bullet for bullet in bullets if bullet.text][:6],
        education=profile.education,
    )


def _gaps(
    requirements: list[JobRequirement], matches: list[EvidenceMatch]
) -> tuple[list[SkillGap], list[LearningTask]]:
    covered = {match.requirement_id for match in matches}
    gaps: list[SkillGap] = []
    tasks: list[LearningTask] = []
    for requirement in requirements:
        if requirement.id in covered:
            continue
        skill = requirement.skills[0] if requirement.skills else requirement.text[:80]
        gaps.append(
            SkillGap(
                requirement_id=requirement.id,
                skill=skill,
                reason="No verified local evidence match.",
            )
        )
        tasks.append(
            LearningTask(
                id=f"LEARN-{requirement.id}",
                requirement_id=requirement.id,
                title=f"Build evidence for {skill}",
                acceptance_criterion="Create a reviewed local artifact and verification receipt.",
            )
        )
    return gaps, tasks


def build_graph(
    job_title: str,
    requirements: list[JobRequirement],
    matches: list[EvidenceMatch],
    gaps: list[SkillGap],
    tasks: list[LearningTask],
) -> tuple[list[EvidenceGraphNode], list[EvidenceGraphEdge]]:
    nodes = [
        EvidenceGraphNode(
            id="JOB-01", kind=GraphNodeKind.JOB, label=job_title, detail={"title": job_title}
        )
    ]
    edges: list[EvidenceGraphEdge] = []
    for requirement in requirements:
        nodes.append(
            EvidenceGraphNode(
                id=requirement.id,
                kind=GraphNodeKind.REQUIREMENT,
                label=requirement.text,
                detail={"priority": requirement.priority, "skills": requirement.skills},
            )
        )
        edges.append(EvidenceGraphEdge(source="JOB-01", target=requirement.id, relation="requires"))
        for skill in requirement.skills:
            skill_id = f"SKILL-{skill.upper()}"
            if not any(node.id == skill_id for node in nodes):
                nodes.append(
                    EvidenceGraphNode(id=skill_id, kind=GraphNodeKind.SKILL, label=skill, detail={})
                )
            edges.append(
                EvidenceGraphEdge(source=requirement.id, target=skill_id, relation="needs")
            )
    for match in matches:
        evidence_id = f"EV-{match.evidence_id}"
        if not any(node.id == evidence_id for node in nodes):
            nodes.append(
                EvidenceGraphNode(
                    id=evidence_id,
                    kind=GraphNodeKind.EVIDENCE,
                    label=match.evidence_id,
                    detail={"excerpt": match.excerpt, **match.locator.model_dump()},
                )
            )
        edges.append(
            EvidenceGraphEdge(
                source=match.requirement_id, target=evidence_id, relation="demonstrated_by"
            )
        )
    for gap, task in zip(gaps, tasks, strict=True):
        gap_id = f"GAP-{gap.requirement_id}"
        nodes.append(
            EvidenceGraphNode(
                id=gap_id, kind=GraphNodeKind.GAP, label=gap.skill, detail={"reason": gap.reason}
            )
        )
        nodes.append(
            EvidenceGraphNode(
                id=task.id,
                kind=GraphNodeKind.LEARNING_TASK,
                label=task.title,
                detail={"acceptance_criterion": task.acceptance_criterion},
            )
        )
        edges.extend(
            [
                EvidenceGraphEdge(source=gap.requirement_id, target=gap_id, relation="missing"),
                EvidenceGraphEdge(source=gap_id, target=task.id, relation="resolved_by"),
            ]
        )
    return nodes, edges


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.chmod(path, _FILE_MODE)


def _resume_markdown(draft: ResumeDraft) -> str:
    lines = ["# Resume Draft"]
    if draft.summary:
        lines += ["", "## Summary", draft.summary]
    if draft.skills:
        lines += ["", "## Skills", ", ".join(draft.skills)]
    if draft.bullets:
        lines += ["", "## Experience & Projects"]
        for bullet in draft.bullets:
            lines.append(f"- {bullet.text}")
            lines.append(
                f"  <!-- evidence_ids: {', '.join(bullet.evidence_ids)}; "
                f"requirement_ids: {', '.join(bullet.requirement_ids)} -->"
            )
    if draft.education:
        lines += ["", "## Education"] + [f"- {item}" for item in draft.education]
    return "\n".join(lines) + "\n"


def _safe_filename_component(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", (value or "").strip(), flags=re.UNICODE)
    return cleaned.strip("-._")[:80] or fallback


def _job_id_from_url(company_url: str | None) -> str | None:
    matches = re.findall(r"\d{6,}", company_url or "")
    return matches[-1] if matches else None


def _application_bundle(
    *,
    library_root: Path,
    run_id: str,
    job_description: str,
    resume_markdown: str,
    candidate_profile: CandidateProfile,
    company_name: str | None,
    company_url: str | None,
    job_title: str | None,
    job_id: str | None,
) -> Path:
    resolved_title = job_title or next(
        (line.strip() for line in job_description.splitlines() if line.strip()),
        "Job",
    )
    resolved_job_id = job_id or _job_id_from_url(company_url)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    directory_name = "_".join(
        [
            date,
            _safe_filename_component(company_name, "Unknown-Company"),
            _safe_filename_component(resolved_title, "Job"),
            _safe_filename_component(resolved_job_id, "No-Job-ID"),
        ]
    )
    library_root.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    applications_root = library_root / "applications"
    applications_root.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    application_dir = applications_root / directory_name
    try:
        application_dir.mkdir(mode=_DIR_MODE)
    except FileExistsError:
        application_dir = applications_root / f"{directory_name}__{run_id}"
        application_dir.mkdir(mode=_DIR_MODE)
    os.chmod(application_dir, _DIR_MODE)

    candidate = _safe_filename_component(candidate_profile.full_name, "Candidate")
    company = _safe_filename_component(company_name, "Company")
    role = _safe_filename_component(resolved_title, "Role")
    resume_filename = f"Resume_{candidate}_{company}_{role}.md"
    jd_lines = [f"# {resolved_title}", ""]
    if company_name:
        jd_lines.append(f"- Company: {company_name}")
    if resolved_job_id:
        jd_lines.append(f"- Job ID: `{resolved_job_id}`")
    if company_url:
        jd_lines.append(f"- Source: <{company_url}>")
    jd_lines += [f"- SoloScale run: `{run_id}`", "", "## Job Description", "", job_description, ""]
    jd_path = application_dir / "JD.md"
    jd_path.write_text("\n".join(jd_lines), encoding="utf-8")
    os.chmod(jd_path, _FILE_MODE)
    resume_path = application_dir / resume_filename
    resume_path.write_text(resume_markdown, encoding="utf-8")
    os.chmod(resume_path, _FILE_MODE)
    metadata = {
        "schema_version": "1.0",
        "company": company_name,
        "role": resolved_title,
        "job_id": resolved_job_id,
        "source_url": company_url,
        "captured_at": date,
        "jd_filename": "JD.md",
        "resume_filename": resume_filename,
        "soloscale_run_id": run_id,
        "status": "DRAFT_REQUIRES_HUMAN_REVIEW",
    }
    metadata_path = application_dir / "application.json"
    _write_json(metadata_path, metadata)
    return application_dir


def run_resume_workspace(
    *,
    data_root: Path,
    job_description: str,
    candidate_profile: CandidateProfile,
    evidence_hits: list[RetrievalHit],
    company_name: str | None = None,
    company_url: str | None = None,
    job_title: str | None = None,
    job_id: str | None = None,
    application_library_root: Path | None = None,
    mode: ResumeMode = ResumeMode.LOCAL_ONLY,
    research_provider: JobResearchProvider | None = None,
) -> ResumeRun:
    if not job_description.strip():
        raise ValueError("job_description must not be empty")
    if mode is ResumeMode.HYBRID and research_provider is None:
        raise ValueError("hybrid mode requires an explicit JobResearchProvider")
    if mode is ResumeMode.LOCAL_ONLY:
        research: list[JobResearchSource] = []
    else:
        assert research_provider is not None
        research = research_provider.research(
            job_description=job_description, company_name=company_name, company_url=company_url
        )
    run_id = f"resume-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:10]}"
    runs_root = data_root / "resume-runs"
    runs_root.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    os.chmod(runs_root, _DIR_MODE)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, mode=_DIR_MODE)
    os.chmod(run_dir, _DIR_MODE)
    requirements = parse_requirements(job_description)
    matches = build_matches(requirements, evidence_hits)
    draft = build_resume(candidate_profile, requirements)
    gaps, tasks = _gaps(requirements, matches)
    nodes, edges = build_graph(
        company_name or "Job Description", requirements, matches, gaps, tasks
    )
    hit_ids = {hit.chunk_id for hit in evidence_hits}
    strong = {match.requirement_id for match in matches if match.strength == "strong"}
    partial = {match.requirement_id for match in matches if match.strength == "partial"} - strong
    critical = {item.id for item in requirements if item.priority == "critical"}
    coverage = {
        "total": len(requirements),
        "strong": len(strong),
        "partial": len(partial),
        "unsupported": len(requirements) - len(strong | partial),
        "critical_covered": len(critical & (strong | partial)),
        "critical_total": len(critical),
    }
    verification = {
        "all_evidence_ids_exist": all(match.evidence_id in hit_ids for match in matches),
        "personal_bullets_supported": all(bool(bullet.evidence_ids) for bullet in draft.bullets),
        "coverage": coverage,
    }
    artifacts: list[tuple[str, object]] = [
        (
            "00_input.json",
            {
                "job_description": job_description,
                "company_name": company_name,
                "company_url": company_url,
                "candidate_profile": candidate_profile.model_dump(mode="json"),
                "mode": mode,
            },
        ),
        (
            "01_job_research.json",
            {"mode": mode, "sources": [source.model_dump(mode="json") for source in research]},
        ),
        ("02_requirements.json", [item.model_dump(mode="json") for item in requirements]),
        ("03_evidence_matches.json", [item.model_dump(mode="json") for item in matches]),
        (
            "05_gaps.json",
            {
                "gaps": [item.model_dump(mode="json") for item in gaps],
                "learning_tasks": [item.model_dump(mode="json") for item in tasks],
            },
        ),
        (
            "06_graph.json",
            {
                "nodes": [item.model_dump(mode="json") for item in nodes],
                "edges": [item.model_dump(mode="json") for item in edges],
            },
        ),
        ("07_verification.json", verification),
    ]
    for name, value in artifacts:
        _write_json(run_dir / name, value)
    resume_markdown = _resume_markdown(draft)
    resume_path = run_dir / "04_resume.md"
    resume_path.write_text(resume_markdown, encoding="utf-8")
    os.chmod(resume_path, _FILE_MODE)
    route: dict[str, str | int | bool] = {
        "network_allowed": mode is ResumeMode.HYBRID,
        "max_research_sources": 0 if mode is ResumeMode.LOCAL_ONLY else len(research),
        "evidence_source": "direct local KnowledgeStore search",
        "application_library_saved": False,
    }
    if application_library_root is not None:
        try:
            application_dir = _application_bundle(
                library_root=application_library_root,
                run_id=run_id,
                job_description=job_description,
                resume_markdown=resume_markdown,
                candidate_profile=candidate_profile,
                company_name=company_name,
                company_url=company_url,
                job_title=job_title,
                job_id=job_id,
            )
        except OSError:
            failed_run = ResumeRun(
                run_id=run_id,
                mode=mode,
                route=route,
                artifact_paths=[name for name, _ in artifacts] + ["04_resume.md", "run.json"],
            )
            _write_json(run_dir / "run.json", failed_run.model_dump(mode="json"))
            raise
        route["application_library_saved"] = True
        route["application_library_path"] = str(application_dir)
    run = ResumeRun(
        run_id=run_id,
        mode=mode,
        route=route,
        artifact_paths=[name for name, _ in artifacts] + ["04_resume.md", "run.json"],
    )
    _write_json(run_dir / "run.json", run.model_dump(mode="json"))
    return run
