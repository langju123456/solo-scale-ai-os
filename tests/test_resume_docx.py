import hashlib
import io
import json
import zipfile

import pytest

from soloscale.resume_docx import (
    ResumeTemplateError,
    ResumeValidationRuleCode,
    _deterministic_hiring_signals,
    _select_safe_rewrites,
    _validate_role_strategy,
    extract_candidate_profile,
    read_template_paragraphs,
    tailor_resume_docx,
)
from soloscale.resume_models import (
    CandidateProfile,
    GroundedResumeBulletRewrite,
    RoleStrategy,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


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
                source_facts=[source],
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
                source_facts=["private unsupported source fragment"],
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
    codes = {
        item["rule_code"]
        for item in payload["failures"]
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
    assert "private unsupported source fragment" not in serialized
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
                text="Built reliable Python service for users.",
                source_facts=["Python service for users"],
            ),
            GroundedResumeBulletRewrite(
                profile_entry_id="PROFILE-02",
                text="Built a RAG system.",
                source_facts=["Delivered RAG system"],
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
        "PROFILE-01": "Built reliable Python service for users.",
        "PROFILE-02": second_source,
    }
    assert diagnostics.validator_status == "selective_pass"
    assert diagnostics.supported_count == 1
    assert diagnostics.rejected_count == 1
    assert {
        failure.rule_code for failure in diagnostics.failures
    } == {ResumeValidationRuleCode.CLAIM_NO_EVIDENCE}
