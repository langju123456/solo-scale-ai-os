import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from soloscale.evidence_hub import EvidenceHub, EvidenceHubError
from soloscale.resume_docx import (
    ResumeTemplateError,
    ResumeValidationRuleCode,
    _deterministic_hiring_signals,
    _remove_trailing_empty_paragraphs,
    _select_safe_rewrites,
    _validate_role_strategy,
    extract_candidate_profile,
    read_template_paragraphs,
    tailor_resume_docx,
)
from soloscale.resume_evidence_pack import (
    build_candidate_evidence_pack,
    build_jd_positioning_brief,
)
from soloscale.resume_models import (
    CandidateProfile,
    GroundedResumeBulletRewrite,
    GroundedResumeSummaryRewrite,
    ResumeClaimProvenance,
    ResumeClaimVerificationStatus,
    RoleStrategy,
    build_resume_atomic_facts,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _fact_ids(profile: CandidateProfile, *source_ids: str) -> list[str]:
    return [
        fact.fact_id
        for fact in build_resume_atomic_facts(profile)
        if fact.profile_entry_id in source_ids
    ]


def test_blank_page_guard_removes_only_body_final_empty_paragraphs() -> None:
    body = ElementTree.fromstring(
        f'<w:body xmlns:w="{W_NS}"><w:p><w:r><w:t>Keep</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="BodyText"/></w:pPr></w:p>'
        '<w:bookmarkEnd w:id="0"/><w:sectPr/></w:body>'
    )

    assert _remove_trailing_empty_paragraphs(body) == 1
    assert [node.text for node in body.iter(f"{{{W_NS}}}t")] == ["Keep"]


def _paragraph(text: str, *, bullet: bool = False) -> str:
    numbering = "<w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr></w:pPr>"
    return f"<w:p>{numbering if bullet else ''}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _template_docx() -> bytes:
    paragraphs = [
        _paragraph("LANG JU"),
        _paragraph("AI Engineer"),
        _paragraph("lang@example.com"),
        _paragraph("SUMMARY"),
        _paragraph("Evidence-grounded engineer."),
        _paragraph("PROJECT HIGHLIGHTS"),
        _paragraph("Search Project"),
        _paragraph("Built Python RAG retrieval.", bullet=True),
        _paragraph("Platform Project"),
        _paragraph("Shipped Docker and Kubernetes automation.", bullet=True),
        _paragraph("EDUCATION"),
        _paragraph("M.S. Information Systems"),
        _paragraph("TECHNICAL SKILLS"),
        _paragraph("Python, RAG", bullet=True),
        _paragraph("Docker, Kubernetes", bullet=True),
        _paragraph("WORK EXPERIENCE"),
        _paragraph("Example Company"),
        _paragraph("Delivered production systems.", bullet=True),
    ]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>"
    ).encode()
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"content-types")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", b"styles-preserve-exactly")
        archive.writestr("word/numbering.xml", b"numbering-preserve-exactly")
    return target.getvalue()


def test_extract_profile_and_tailor_preserve_every_candidate_claim() -> None:
    template = _template_docx()
    profile = extract_candidate_profile(template)

    assert profile.full_name == "LANG JU"
    assert profile.headline == "AI Engineer"
    assert profile.summary == "Evidence-grounded engineer."
    assert profile.project_bullets == [
        "Built Python RAG retrieval.",
        "Shipped Docker and Kubernetes automation.",
    ]
    assert profile.skills == ["Python, RAG", "Docker, Kubernetes"]
    assert profile.experience_bullets == ["Delivered production systems."]

    output = tailor_resume_docx(template, "Required: Docker and Kubernetes platform delivery")
    assert output.claims_preserved is True
    assert output.project_blocks_reordered == 2
    assert output.skill_bullets_reordered == 2
    assert output.template_sha256 == hashlib.sha256(template).hexdigest()
    assert output.output_sha256 == hashlib.sha256(output.content).hexdigest()

    before = [item.text for item in read_template_paragraphs(template) if item.text]
    after = [item.text for item in read_template_paragraphs(output.content) if item.text]
    assert sorted(after) == sorted(before)
    assert after.index("Platform Project") < after.index("Search Project")
    assert after.index("Docker, Kubernetes") < after.index("Python, RAG")

    with zipfile.ZipFile(io.BytesIO(template)) as source, zipfile.ZipFile(
        io.BytesIO(output.content)
    ) as tailored:
        for name in source.namelist():
            if name != "word/document.xml":
                assert tailored.read(name) == source.read(name)


def test_rejects_non_docx_upload() -> None:
    with pytest.raises(ResumeTemplateError, match="not a readable DOCX"):
        extract_candidate_profile(b"not-a-zip")


def test_hiring_signal_must_be_an_exact_jd_quote() -> None:
    source = "Built Python service."
    profile = CandidateProfile(skills=["Python"], project_bullets=[source])
    strategy = RoleStrategy(
        role_summary="Python application development",
        top_hiring_signals=["Python application development"],
        evidence_priority=["PROFILE-01"],
        skill_priority=["Python"],
        bullet_rewrites=[
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-01",
                text=source,
                source_fact_ids=_fact_ids(profile, "PROFILE-01"),
            )
        ],
        rewrite_guidance="Preserve the approved fact.",
    )

    with pytest.raises(ResumeTemplateError) as failure:
        _validate_role_strategy(
            strategy,
            profile=profile,
            job_description="Required: Python web application development.",
        )

    diagnostics = failure.value.validation_diagnostics
    assert diagnostics is not None
    assert diagnostics.as_dict() == {
        "validator_status": "rejected",
        "failure_count": 1,
        "failures": [
            {
                "rule_code": "HIRING_SIGNAL_NOT_SOURCE_GROUNDED",
                "json_path": "$.top_hiring_signals[0]",
                "claim_id": None,
            }
        ],
        "candidate_count": 1,
        "verified_count": 1,
        "supported_count": 0,
        "rejected_count": 0,
        "duplicate_count": 0,
        "source_span_failure_count": 1,
    }


def test_truth_validation_diagnostics_are_aggregated_and_body_free() -> None:
    source = "Built Python service for users."
    profile = CandidateProfile(skills=["Python"], project_bullets=[source])
    strategy = RoleStrategy(
        role_summary="Synthetic role",
        top_hiring_signals=["Python", "python"],
        evidence_priority=["PROFILE-01", "PROFILE-01"],
        skill_priority=["Python"],
        bullet_rewrites=[
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-01",
                text="Improved Python service by 25% with Django.",
                source_fact_ids=["FACT-PROFILE-99-01"],
            )
        ],
        unsupported_requirements=["Kubernetes private requirement"],
        rewrite_guidance="Synthetic guidance",
    )

    with pytest.raises(ResumeTemplateError) as failure:
        _validate_role_strategy(
            strategy,
            profile=profile,
            job_description="Required: Python.",
        )

    diagnostics = failure.value.validation_diagnostics
    assert diagnostics is not None
    payload = diagnostics.as_dict()
    failures = payload["failures"]
    assert isinstance(failures, list)
    codes = {
        item["rule_code"]
        for item in failures
        if isinstance(item, dict)
    }
    assert {
        ResumeValidationRuleCode.OUTPUT_DUPLICATE.value,
        ResumeValidationRuleCode.CLAIM_SOURCE_MISMATCH.value,
        ResumeValidationRuleCode.CLAIM_NO_EVIDENCE.value,
        ResumeValidationRuleCode.CLAIM_NEW_NUMBER.value,
        ResumeValidationRuleCode.REWRITE_FACT_MUTATION.value,
        ResumeValidationRuleCode.GAP_NOT_SOURCE_GROUNDED.value,
        ResumeValidationRuleCode.HIRING_SIGNAL_DUPLICATE.value,
    } <= codes
    assert payload["duplicate_count"] == 2
    assert payload["source_span_failure_count"] == 2
    assert payload["rejected_count"] == 1
    serialized = json.dumps(payload)
    assert source not in serialized
    assert "Kubernetes private requirement" not in serialized
    assert "Improved Python service" not in serialized


def test_hiring_signals_are_deterministic_exact_jd_spans() -> None:
    job_description = """Job Responsibilities:
• Build Python web applications.
• Design RAG pipelines.
Requirements:
• Use Git for version control.
• Test application quality.
"""

    signals = _deterministic_hiring_signals(job_description)

    assert signals == [
        "Build Python web applications.",
        "Design RAG pipelines.",
        "Use Git for version control.",
        "Test application quality.",
    ]
    assert all(signal in job_description for signal in signals)


def test_candidate_evidence_pack_adds_fresh_committed_project_facts(
    tmp_path: Path,
) -> None:
    profile = CandidateProfile(
        skills=["Python, RAG"],
        project_bullets=["Built SoloScale AI OS with evidence-grounded workflows."],
    )
    repository = tmp_path / "solo-scale-ai-os"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "feature.txt").write_text("background job", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "feature.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "feat: add non-blocking resume jobs",
        ],
        check=True,
    )
    data_root = tmp_path / "data"
    EvidenceHub(data_root).sync_git_repository(repository)
    assert b"background job" not in EvidenceHub(data_root).database_path.read_bytes()

    first = build_candidate_evidence_pack(
        profile,
        data_root=data_root,
        repository_root=repository,
    )
    second = build_candidate_evidence_pack(
        profile,
        data_root=data_root,
        repository_root=repository,
    )

    assert first.pack_sha256 == second.pack_sha256
    assert {source.evidence_id for source in first.sources} == {
        "EVIDENCE-LOCAL-GIT",
        "EVIDENCE-M1-13",
        "EVIDENCE-M1-14",
        "EVIDENCE-M1-15",
    }
    candidate_facts = [
        fact for fact in first.atomic_facts if fact.source_kind == "CANDIDATE_EVIDENCE"
    ]
    assert len(candidate_facts) >= 15
    assert {fact.profile_entry_id for fact in candidate_facts} == {"PROFILE-01"}
    assert any(
        fact.text == "Verified repository commit: feat: add non-blocking resume jobs"
        for fact in candidate_facts
    )

    brief = build_jd_positioning_brief(
        "AI Engineer\nBuild Python RAG pipelines and reliable background jobs.",
        first.atomic_facts,
    )
    assert brief.top_hiring_signals == [
        "AI Engineer",
        "Build Python RAG pipelines and reliable background jobs.",
    ]
    assert set(brief.priority_fact_ids) <= {
        fact.fact_id for fact in first.atomic_facts
    }

    (repository / "feature.txt").write_text("changed but not refreshed", encoding="utf-8")
    with pytest.raises(EvidenceHubError, match="stale"):
        build_candidate_evidence_pack(
            profile,
            data_root=data_root,
            repository_root=repository,
        )


def test_selective_rewrite_keeps_supported_claim_and_restores_rejected_claim() -> None:
    first_source = "Built Python service for users."
    second_source = "Delivered RAG system."
    profile = CandidateProfile(
        skills=["Python", "RAG"],
        project_bullets=[first_source, second_source],
    )
    strategy = RoleStrategy(
        role_summary="Python and RAG role",
        top_hiring_signals=["Required: Python and RAG."],
        evidence_priority=["PROFILE-01", "PROFILE-02"],
        skill_priority=["Python", "RAG"],
        bullet_rewrites=[
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-01",
                text="Engineered a reliable Python service supporting users.",
                source_fact_ids=_fact_ids(profile, "PROFILE-01"),
            ),
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-02",
                text="Built a Django RAG system.",
                source_fact_ids=_fact_ids(profile, "PROFILE-02"),
            ),
        ],
        rewrite_guidance="Prefer grounded wording.",
    )

    selected, _entries, diagnostics = _select_safe_rewrites(
        strategy,
        profile=profile,
        job_description="Required: Python and RAG.",
    )

    rewrites = {
        item.profile_entry_id: item.text for item in selected.bullet_rewrites
    }
    assert rewrites == {
        "PROFILE-01": "Engineered a reliable Python service supporting users.",
        "PROFILE-02": second_source,
    }
    assert diagnostics.validator_status == "selective_pass"
    assert diagnostics.supported_count == 1
    assert diagnostics.rejected_count == 1
    assert {
        failure.rule_code for failure in diagnostics.failures
    } >= {ResumeValidationRuleCode.CLAIM_TECHNOLOGY_INFLATION}


def test_multi_source_synthesis_uses_union_and_falls_back_only_unsafe_slots() -> None:
    first_source = "Built RAG retrieval."
    second_source = "Added FastAPI orchestration."
    third_source = "Validated citations."
    profile = CandidateProfile(
        summary="Evidence-grounded engineer.",
        skills=["RAG", "FastAPI"],
        project_bullets=[first_source, second_source, third_source],
    )
    strategy = RoleStrategy(
        role_summary="RAG and FastAPI role",
        top_hiring_signals=["Required: RAG and FastAPI."],
        evidence_priority=["PROFILE-01", "PROFILE-02", "PROFILE-03"],
        skill_priority=["RAG", "FastAPI"],
        bullet_rewrites=[
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-01",
                kind="SYNTHESIS",
                text="Built RAG retrieval with FastAPI orchestration.",
                source_profile_entry_ids=["PROFILE-01", "PROFILE-02"],
                source_fact_ids=_fact_ids(
                    profile, "PROFILE-01", "PROFILE-02"
                ),
            ),
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-02",
                text=second_source,
                source_fact_ids=_fact_ids(profile, "PROFILE-02"),
            ),
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-03",
                text=(
                    "Led Django citations for clients in production and increased "
                    "results by 40%."
                ),
                source_fact_ids=_fact_ids(profile, "PROFILE-03"),
            ),
        ],
        summary_rewrite=GroundedResumeSummaryRewrite(
            text="Built RAG retrieval with FastAPI orchestration for 40% more clients.",
            source_profile_entry_ids=["PROFILE-01", "PROFILE-02"],
            source_fact_ids=_fact_ids(profile, "PROFILE-01", "PROFILE-02"),
        ),
        rewrite_guidance="Synthesize only approved facts.",
    )

    selected, _entries, diagnostics = _select_safe_rewrites(
        strategy,
        profile=profile,
        job_description="Required: RAG and FastAPI.",
    )

    selected_by_id = {
        rewrite.profile_entry_id: rewrite for rewrite in selected.bullet_rewrites
    }
    assert selected_by_id["PROFILE-01"].kind == "SYNTHESIS"
    assert selected_by_id["PROFILE-01"].text == (
        "Built RAG retrieval with FastAPI orchestration."
    )
    assert selected_by_id["PROFILE-03"].text == third_source
    assert selected.summary_rewrite is None
    assert diagnostics.validator_status == "selective_pass"
    assert diagnostics.rejected_count == 2
    assert {
        failure.claim_id
        for failure in diagnostics.failures
        if failure.claim_id is not None
    } == {"PROFILE-03", "SUMMARY"}
    assert {
        failure.rule_code for failure in diagnostics.failures
    } >= {
        ResumeValidationRuleCode.CLAIM_ROLE_INFLATION,
        ResumeValidationRuleCode.CLAIM_CLIENT_INFLATION,
        ResumeValidationRuleCode.CLAIM_SCALE_INFLATION,
        ResumeValidationRuleCode.CLAIM_OUTCOME_INFLATION,
        ResumeValidationRuleCode.CLAIM_TECHNOLOGY_INFLATION,
        ResumeValidationRuleCode.CLAIM_NEW_NUMBER,
    }


def test_synthesis_provenance_rejects_misaligned_target_source_hash() -> None:
    final_text = "Built RAG retrieval with FastAPI orchestration."
    first_source_hash = hashlib.sha256(b"Built RAG retrieval.").hexdigest()
    second_source_hash = hashlib.sha256(b"Added FastAPI orchestration.").hexdigest()
    claim = ResumeClaimProvenance(
        claim_id="CLAIM-01",
        render_location="BULLET",
        final_text=final_text,
        final_text_sha256=hashlib.sha256(final_text.encode()).hexdigest(),
        profile_entry_id="PROFILE-01",
        approved_source_sha256=first_source_hash,
        evidence_ids=["PROFILE-01", "PROFILE-02"],
        approved_evidence_sha256s=[first_source_hash, second_source_hash],
        fact_ids=["FACT-PROFILE-01-01", "FACT-PROFILE-02-01"],
        source_fact_sha256s=[
            hashlib.sha256(
                b"FACT-PROFILE-01-01\0PROFILE-01\0Built RAG retrieval"
            ).hexdigest(),
            hashlib.sha256(
                b"FACT-PROFILE-02-01\0PROFILE-02\0FastAPI orchestration"
            ).hexdigest(),
        ],
        status=ResumeClaimVerificationStatus.SUPPORTED,
        verification_basis="DETERMINISTIC_MULTI_SOURCE_SYNTHESIS",
    )

    assert claim.evidence_ids == ["PROFILE-01", "PROFILE-02"]
    with pytest.raises(ValidationError, match="target source hash"):
        ResumeClaimProvenance.model_validate(
            {
                **claim.model_dump(mode="json"),
                "approved_evidence_sha256s": [
                    second_source_hash,
                    first_source_hash,
                ],
            }
        )
