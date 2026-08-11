import json
import stat
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.resume_models import CandidateProfile, JobResearchSource, ResumeMode, ResumeRun
from soloscale.resume_workspace import run_resume_workspace

AI_ENGINEER_JD = """AI Engineer
- Required: Python and RAG systems
- Required: Docker deployment and CI/CD
- Preferred: Kubernetes operations
"""


def _hit(chunk_id: str, excerpt: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        document_id="doc-1",
        source_kind=SourceKind.CODEX_SESSION,
        external_id="thread-1",
        locator="private",
        title="Local",
        role=ContentRole.ASSISTANT,
        timestamp=None,
        excerpt=excerpt,
        chunk_sha256="a" * 64,
        document_sha256="b" * 64,
        score=1,
        channels=["fts"],
    )


def _hits() -> list[RetrievalHit]:
    return [_hit("chunk-rag", "Python RAG evidence"), _hit("chunk-ci", "Docker CI/CD verification")]


def test_local_workspace_writes_nine_private_artifacts_and_valid_graph(tmp_path: Path) -> None:
    run = run_resume_workspace(
        data_root=tmp_path / ".soloscale",
        job_description=AI_ENGINEER_JD,
        candidate_profile=CandidateProfile(
            summary="AI engineer",
            skills=["Python", "RAG"],
            experience_bullets=["Operator-supplied base-resume bullet."],
        ),
        evidence_hits=_hits(),
    )

    run_dir = tmp_path / ".soloscale" / "resume-runs" / run.run_id
    expected = {
        "00_input.json",
        "01_job_research.json",
        "02_requirements.json",
        "03_evidence_matches.json",
        "04_resume.md",
        "05_gaps.json",
        "06_graph.json",
        "07_verification.json",
        "run.json",
    }
    assert {path.name for path in run_dir.iterdir()} == expected
    assert stat.S_IMODE((tmp_path / ".soloscale" / "resume-runs").stat().st_mode) == 0o700
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run_dir.iterdir())
    graph = json.loads((run_dir / "06_graph.json").read_text(encoding="utf-8"))
    assert {node["kind"] for node in graph["nodes"]} >= {"JOB", "REQUIREMENT", "EVIDENCE", "GAP"}
    assert not ({"PROJECT", "CODE", "VERIFICATION"} & {node["kind"] for node in graph["nodes"]})
    assert all({"source", "target", "relation"} <= set(edge) for edge in graph["edges"])
    verification = json.loads((run_dir / "07_verification.json").read_text(encoding="utf-8"))
    assert verification["all_evidence_ids_exist"] is True
    assert verification["personal_bullets_supported"] is True
    assert verification["coverage"]["total"] == 4


def test_profile_and_evidence_truth_boundary_and_repeat_runs(tmp_path: Path) -> None:
    root = tmp_path / ".soloscale"
    profile = CandidateProfile(experience_bullets=["Only operator supplied this experience."])
    first = run_resume_workspace(
        data_root=root,
        job_description=AI_ENGINEER_JD,
        candidate_profile=profile,
        evidence_hits=_hits(),
    )
    second = run_resume_workspace(
        data_root=root,
        job_description=AI_ENGINEER_JD,
        candidate_profile=profile,
        evidence_hits=_hits(),
    )

    assert first.run_id != second.run_id
    first_resume = (root / "resume-runs" / first.run_id / "04_resume.md").read_text(
        encoding="utf-8"
    )
    assert "Only operator supplied this experience." in first_resume
    assert "Python RAG evidence" not in first_resume
    assert "evidence_ids: PROFILE-01" in first_resume
    assert not (root / "resume-runs" / first.run_id / "04_resume.md").samefile(
        root / "resume-runs" / second.run_id / "04_resume.md"
    )


def test_dual_writes_application_bundle_without_overwriting(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    library_root = tmp_path / "Resume Applications"
    profile = CandidateProfile(
        full_name="Lang Ju",
        skills=["Python", "RAG"],
        project_bullets=["Built an operator-supplied Python RAG workflow."],
    )

    def run_once() -> ResumeRun:
        return run_resume_workspace(
            data_root=data_root,
            job_description=AI_ENGINEER_JD,
            candidate_profile=profile,
            evidence_hits=_hits(),
            company_name="Faros",
            company_url="https://www.linkedin.com/jobs/view/4432211307/",
            job_title="AI-Native Builder",
            job_id="4432211307",
            application_library_root=library_root,
        )

    first = run_once()
    second = run_once()

    application_dirs = sorted((library_root / "applications").iterdir())
    assert len(application_dirs) == 2
    assert stat.S_IMODE(library_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((library_root / "applications").stat().st_mode) == 0o700
    assert application_dirs[0].name.startswith("20")
    assert application_dirs[0].name != application_dirs[1].name
    for application_dir in application_dirs:
        assert {path.name for path in application_dir.iterdir()} == {
            "JD.md",
            "Resume_Lang-Ju_Faros_AI-Native-Builder.md",
            "application.json",
        }
        assert stat.S_IMODE(application_dir.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in application_dir.iterdir())
        jd = (application_dir / "JD.md").read_text(encoding="utf-8")
        assert "AI-Native Builder" in jd
        assert "4432211307" in jd
        resume = (application_dir / "Resume_Lang-Ju_Faros_AI-Native-Builder.md").read_text(
            encoding="utf-8"
        )
        assert "Built an operator-supplied Python RAG workflow." in resume
    first_run = json.loads(
        (data_root / "resume-runs" / first.run_id / "run.json").read_text(encoding="utf-8")
    )
    second_run = json.loads(
        (data_root / "resume-runs" / second.run_id / "run.json").read_text(encoding="utf-8")
    )
    assert first_run["route"]["application_library_saved"] is True
    assert second_run["route"]["application_library_saved"] is True
    assert (
        first_run["route"]["application_library_path"]
        != second_run["route"]["application_library_path"]
    )


def test_hybrid_requires_explicit_provider_without_network(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit JobResearchProvider"):
        run_resume_workspace(
            data_root=tmp_path / ".soloscale",
            job_description=AI_ENGINEER_JD,
            candidate_profile=CandidateProfile(),
            evidence_hits=_hits(),
            mode=ResumeMode.HYBRID,
        )


def test_hybrid_route_records_network_boundary_when_provider_is_invoked(tmp_path: Path) -> None:
    class FakeProvider:
        def research(
            self, *, job_description: str, company_name: str | None, company_url: str | None
        ) -> list[JobResearchSource]:
            del job_description, company_name, company_url
            return [JobResearchSource(title="Imported research")]

    run = run_resume_workspace(
        data_root=tmp_path / ".soloscale",
        job_description="Required: Python",
        candidate_profile=CandidateProfile(),
        evidence_hits=[],
        mode=ResumeMode.HYBRID,
        research_provider=FakeProvider(),
    )
    payload = json.loads(
        (tmp_path / ".soloscale" / "resume-runs" / run.run_id / "run.json").read_text()
    )
    assert payload["route"]["network_allowed"] is True


def test_critical_generic_requirement_remains_gap_and_profile_ids_are_deterministic(
    tmp_path: Path,
) -> None:
    run = run_resume_workspace(
        data_root=tmp_path / ".soloscale",
        job_description="Required: leadership experience",
        candidate_profile=CandidateProfile(experience_bullets=["Led a local club."]),
        evidence_hits=[_hit("chunk", "leadership experience")],
    )
    run_dir = tmp_path / ".soloscale" / "resume-runs" / run.run_id
    verification = json.loads((run_dir / "07_verification.json").read_text(encoding="utf-8"))
    assert verification["coverage"]["unsupported"] == 1
    resume = (run_dir / "04_resume.md").read_text(encoding="utf-8")
    assert "Led a local club." in resume
    assert "evidence_ids: PROFILE-01" in resume
