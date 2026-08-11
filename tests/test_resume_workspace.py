import json
import stat
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.resume_models import CandidateProfile, JobResearchSource, ResumeMode, ResumeRun
from soloscale.resume_workspace import ResumeWorkspaceStorageError, run_resume_workspace

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


def test_local_workspace_writes_private_artifacts_and_replayable_lineage(tmp_path: Path) -> None:
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
        "delivery.json",
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
    assert verification["candidate_lineage_replayable"] is True
    assert verification["resume_claims_reference_profile_entries"] is True
    assert verification["semantic_requirement_coverage_verified"] is False
    assert verification["coverage"]["total"] == 4
    matches = json.loads((run_dir / "03_evidence_matches.json").read_text(encoding="utf-8"))
    locator = matches[0]["locator"]
    assert locator["document_id"] == "doc-1"
    assert locator["source_kind"] == "codex_session"
    assert locator["external_id"] == "thread-1"
    assert locator["source_locator"] == "private"
    assert locator["chunk_sha256"] == "a" * 64
    assert locator["document_sha256"] == "b" * 64
    assert matches[0]["match_quality"].startswith("lexical_candidate_")


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
    assert "profile_entry_ids: PROFILE-01" in first_resume
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
    assert verification["coverage"]["no_lexical_candidate"] == 1
    resume = (run_dir / "04_resume.md").read_text(encoding="utf-8")
    assert "Led a local club." in resume
    assert "profile_entry_ids: PROFILE-01" in resume


def test_rejects_managed_symlink_and_tightens_existing_private_roots(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_root = tmp_path / ".soloscale"
    data_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ResumeWorkspaceStorageError, match="symlink"):
        run_resume_workspace(
            data_root=data_root,
            job_description="Required: Python",
            candidate_profile=CandidateProfile(),
            evidence_hits=[],
        )
    assert list(outside.iterdir()) == []

    data_root.unlink()
    data_root.mkdir(mode=0o755)
    library_root = tmp_path / "Resume Applications"
    library_root.mkdir(mode=0o755)
    run_resume_workspace(
        data_root=data_root,
        job_description="Required: Python",
        candidate_profile=CandidateProfile(),
        evidence_hits=[],
        application_library_root=library_root,
    )
    assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(library_root.stat().st_mode) == 0o700


def test_external_failure_keeps_recovery_receipt_and_no_partial_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from soloscale import resume_workspace

    original = resume_workspace._atomic_private_write

    def fail_resume_file(path: Path, text: str) -> None:
        if path.parent.name.endswith(".staging") and path.name.startswith("Resume_"):
            raise OSError("private detail must not escape")
        original(path, text)

    monkeypatch.setattr(resume_workspace, "_atomic_private_write", fail_resume_file)
    data_root = tmp_path / ".soloscale"
    library_root = tmp_path / "Resume Applications"
    with pytest.raises(ResumeWorkspaceStorageError, match="inspect delivery.json"):
        run_resume_workspace(
            data_root=data_root,
            job_description="Required: Python",
            candidate_profile=CandidateProfile(full_name="Candidate"),
            evidence_hits=[],
            application_library_root=library_root,
        )

    run_dir = next((data_root / "resume-runs").iterdir())
    delivery = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert delivery["state"] == "APPLICATION_LIBRARY_FAILED"
    assert delivery["error_type"] == "OSError"
    assert delivery["retry_safe"] is False
    assert not (run_dir / "run.json").exists()
    assert list((library_root / "applications").iterdir()) == []


def test_final_internal_failure_leaves_saved_delivery_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from soloscale import resume_workspace

    original = resume_workspace._write_json

    def fail_run_json(path: Path, value: object) -> None:
        if path.name == "run.json":
            raise OSError("simulated final receipt failure")
        original(path, value)

    monkeypatch.setattr(resume_workspace, "_write_json", fail_run_json)
    data_root = tmp_path / ".soloscale"
    library_root = tmp_path / "Resume Applications"
    with pytest.raises(OSError, match="simulated final receipt failure"):
        run_resume_workspace(
            data_root=data_root,
            job_description="Required: Python",
            candidate_profile=CandidateProfile(full_name="Candidate"),
            evidence_hits=[],
            application_library_root=library_root,
        )

    run_dir = next((data_root / "resume-runs").iterdir())
    delivery = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert delivery["state"] == "APPLICATION_LIBRARY_SAVED"
    assert Path(delivery["application_library_path"]).is_dir()
    assert not (run_dir / "run.json").exists()


def test_rejects_application_library_inside_repository_before_writing(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    data_root = repository_root / ".soloscale"
    with pytest.raises(ValueError, match="outside every Git repository"):
        run_resume_workspace(
            data_root=data_root,
            job_description="Required: Python",
            candidate_profile=CandidateProfile(),
            evidence_hits=[],
            application_library_root=repository_root / "resume-data",
            repository_root=repository_root,
        )
    assert not data_root.exists()
