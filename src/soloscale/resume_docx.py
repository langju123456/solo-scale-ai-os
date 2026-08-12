"""Safe, deterministic DOCX handling for the local resume user flow.

The uploaded resume is the truth boundary.  Tailoring may only reorder existing
paragraph blocks; it never rewrites candidate claims or calls an external service.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from soloscale.resume_models import CandidateProfile

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


class ResumeTemplateError(ValueError):
    """Raised when an uploaded file is not a bounded, readable DOCX resume."""


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
    paragraphs: list[TemplateParagraph], start_heading: str, end_heading: str | None
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
    end = len(paragraphs)
    if end_heading is not None:
        end = next(
            (
                index
                for index, paragraph in enumerate(paragraphs[start + 1 :], start=start + 1)
                if _heading(paragraph.text) == end_heading
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
    summary = _nonblank_text(_section_slice(paragraphs, "SUMMARY", "PROJECT HIGHLIGHTS"))
    projects = _section_slice(paragraphs, "PROJECT HIGHLIGHTS", "EDUCATION")
    education = _nonblank_text(_section_slice(paragraphs, "EDUCATION", "TECHNICAL SKILLS"))
    skills = _section_slice(paragraphs, "TECHNICAL SKILLS", "WORK EXPERIENCE")
    experience = _section_slice(paragraphs, "WORK EXPERIENCE", None)
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
