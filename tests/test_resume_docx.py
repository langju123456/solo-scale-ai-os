import hashlib
import io
import zipfile

import pytest

from soloscale.resume_docx import (
    ResumeTemplateError,
    extract_candidate_profile,
    read_template_paragraphs,
    tailor_resume_docx,
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
