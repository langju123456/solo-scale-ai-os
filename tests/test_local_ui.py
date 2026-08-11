import io
import json
import zipfile
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.local_ui import (
    UIActionResult,
    UploadedFile,
    _build_jd_resume_command,
    _build_resume_sections,
    _page,
    _parse_submission,
    _result_card,
    _resume_graph,
    _run_action,
    _run_user_resume,
    _split_path_list,
    _user_page,
    _workspace_path,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _uploaded_resume_docx() -> bytes:
    def paragraph(text: str, *, bullet: bool = False) -> str:
        numbering = (
            '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
            "</w:numPr></w:pPr>"
        )
        return f"<w:p>{numbering if bullet else ''}<w:r><w:t>{text}</w:t></w:r></w:p>"

    values = [
        paragraph("LANG JU"),
        paragraph("AI Engineer"),
        paragraph("lang@example.com"),
        paragraph("SUMMARY"),
        paragraph("Evidence-grounded engineer."),
        paragraph("PROJECT HIGHLIGHTS"),
        paragraph("RAG Project"),
        paragraph("Built Python RAG retrieval.", bullet=True),
        paragraph("EDUCATION"),
        paragraph("M.S. Information Systems"),
        paragraph("TECHNICAL SKILLS"),
        paragraph("Python, RAG", bullet=True),
        paragraph("WORK EXPERIENCE"),
        paragraph("Example Company"),
        paragraph("Delivered production systems.", bullet=True),
    ]
    document = (
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(values)
        + "<w:sectPr/></w:body></w:document>"
    ).encode()
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"content-types")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", b"styles")
    return target.getvalue()


def test_split_path_list_supports_comma_and_newline() -> None:
    assert _split_path_list("a, b\nc,, d") == ["a", "b", "c", "d"]


def test_user_page_is_resume_first_and_keeps_developer_tools_under_advanced(
    tmp_path: Path,
) -> None:
    page = _user_page(None, tmp_path / ".soloscale", {})
    assert 'action="/generate"' in page
    assert 'name="resume_template"' in page
    assert 'name="job_description"' in page
    assert "生成针对性简历" in page
    assert 'href="/advanced"' in page
    assert "knowledge-sync" not in page
    assert 'name="model"' not in page
    assert 'name="source_kind"' not in page


def test_parse_submission_reads_text_and_docx_upload() -> None:
    boundary = "SoloScaleBoundary"
    template = _uploaded_resume_docx()
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="job_description"\r\n\r\n'
        "Required: Python RAG\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="resume_template"; filename="resume.docx"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + template + f"\r\n--{boundary}--\r\n".encode()

    submission = _parse_submission(body, f"multipart/form-data; boundary={boundary}")

    assert submission.fields["job_description"] == "Required: Python RAG"
    assert submission.files["resume_template"].filename == "resume.docx"
    assert submission.files["resume_template"].content == template


def test_user_resume_flow_generates_matching_private_and_application_docx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def search(self, query: str, limit: int) -> list[RetrievalHit]:
            del query, limit
            return [
                RetrievalHit(
                    chunk_id="chunk-python-rag",
                    document_id="doc",
                    source_kind=SourceKind.CODEX_SESSION,
                    external_id="thread",
                    locator="private",
                    title="local evidence",
                    role=ContentRole.ASSISTANT,
                    timestamp=None,
                    excerpt="Built and verified Python RAG retrieval.",
                    chunk_sha256="a" * 64,
                    document_sha256="b" * 64,
                    score=1,
                    channels=["fts"],
                )
            ]

    def no_subprocess(command: list[str], cwd: Path) -> UIActionResult:
        raise AssertionError(f"user resume flow must not run subprocess: {command} {cwd}")

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", FakeStore)
    monkeypatch.setattr("soloscale.local_ui._run_command", no_subprocess)
    library_root = tmp_path / "Resume Applications"
    result = _run_user_resume(
        {
            "job_description": "AI Engineer\nRequired: Python and RAG",
            "company_name": "Example AI",
            "job_title": "GenAI Engineer",
            "job_id": "1234567",
            "resume_library_root": str(library_root),
        },
        {
            "resume_template": UploadedFile(
                filename="Lang_Ju_Resume.docx",
                content_type="application/octet-stream",
                content=_uploaded_resume_docx(),
            )
        },
        tmp_path / ".soloscale",
        tmp_path,
    )

    assert result.return_code == 0, result.stderr
    run_dir = _workspace_path(result.stdout)
    assert run_dir is not None
    private_docx = run_dir / "08_resume.docx"
    metadata = json.loads((run_dir / "09_user_ui.json").read_text())
    external_docx = Path(metadata["external_docx"])
    assert private_docx.is_file()
    assert external_docx.is_file()
    assert private_docx.read_bytes() == external_docx.read_bytes()
    assert metadata["claims_preserved"] is True
    assert metadata["network_used"] is False
    assert metadata["download_url"].endswith("/resume.docx")
    run_payload = json.loads((run_dir / "run.json").read_text())
    assert {"08_resume.docx", "09_user_ui.json"} <= set(run_payload["artifact_paths"])
    application_metadata = json.loads((external_docx.parent / "application.json").read_text())
    assert application_metadata["resume_docx_filename"] == external_docx.name
    rendered = _user_page(result, tmp_path / ".soloscale", {})
    assert "针对性简历已生成" in rendered
    assert metadata["download_url"] in rendered
    assert "没有网络调用" in rendered


def test_run_action_knowledge_status_builds_expected_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], cwd: Path) -> UIActionResult:
        calls.append(command)
        return UIActionResult(
            name=command[0],
            command=" ".join(command),
            return_code=0,
            stdout="ok",
            stderr="",
            elapsed_ms=1,
        )

    monkeypatch.setattr("soloscale.local_ui._run_command", fake_run_command)

    result = _run_action({"action": "knowledge-status"}, Path(".soloscale"), tmp_path)
    assert result is not None
    assert result.return_code == 0
    assert calls == [["knowledge-status", "--data-root", ".soloscale"]]


def test_build_resume_sections_with_claims_and_refs() -> None:
    payload = {
        "claims": [
            {
                "text": "实现了基于 evidence 的结构化输出链路。",
                "evidence_chunk_ids": ["c1", "c2"],
            },
            {
                "text": "补齐了工程恢复流程。",
                "evidence_chunk_ids": ["c2"],
            },
        ],
        "refs": [
            {
                "chunk_id": "c1",
                "title": "Run 2026 evidence",
                "source_kind": "chatgpt_conversation",
                "external_id": "ext-1",
                "excerpt": "run evidence excerpt",
            },
            {
                "chunk_id": "c2",
                "title": "BuildLog log",
                "source_kind": "buildlog_run",
                "external_id": "ext-2",
                "excerpt": "another evidence snippet",
            },
        ],
        "unsupported": ["缺失真实证据字段说明"],
        "open_questions": ["需要确认产品规模化指标"],
    }
    output = _build_resume_sections(payload, job_title_hint="AI Engineer JD")

    assert "# AI Engineer JD" in output
    assert "项目经历 1" in output
    assert "证据锚点" in output
    assert "c1（chatgpt_conversation｜Run 2026 evidence）" in output
    assert "未被证据覆盖 / 需人工补证" in output
    assert "待补充问题" in output


def test_build_jd_resume_command_requires_jd() -> None:
    command, prompt = _build_jd_resume_command({"job_description": ""}, Path(".soloscale"))
    assert command == []
    assert prompt is None


def test_build_jd_resume_command_uses_expected_defaults() -> None:
    command, prompt = _build_jd_resume_command(
        {"job_description": "AI 工程师", "resume_max_rounds": "2"},
        Path(".soloscale"),
    )
    assert prompt == "AI 工程师"
    assert command[0] == "evidence-agent"
    assert "--max-rounds" in command


def test_resume_graph_renders_clickable_native_svg(tmp_path: Path) -> None:
    run_dir = tmp_path / "resume-run"
    run_dir.mkdir()
    nodes = [
        {"id": f"N-{index}", "kind": "EVIDENCE", "label": str(index), "detail": {}}
        for index in range(30)
    ]
    (run_dir / "06_graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": []}), encoding="utf-8"
    )
    output = _resume_graph(f"Resume workspace: {run_dir}")
    assert 'id="resume-graph"' in output
    assert "onclick" in output
    assert "ondblclick" in output
    assert "height=Math.max" in output
    assert "overflow:auto" in output


def test_resume_workspace_defaults_to_documents_application_library(tmp_path: Path) -> None:
    page = _page(None, tmp_path / ".soloscale", {})
    expected = Path.home() / "Documents" / "Resume Applications"
    assert f'value="{expected}"' in page


def test_resume_workspace_is_local_only_and_renders_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def search(self, query: str, limit: int) -> list[RetrievalHit]:
            del query, limit
            return [
                RetrievalHit(
                    chunk_id="chunk-python",
                    document_id="doc",
                    source_kind=SourceKind.CODEX_SESSION,
                    external_id="thread",
                    locator="private",
                    title="local",
                    role=ContentRole.ASSISTANT,
                    timestamp=None,
                    excerpt="Python RAG implementation",
                    chunk_sha256="a" * 64,
                    document_sha256="b" * 64,
                    score=1,
                    channels=["fts"],
                )
            ]

    def no_subprocess(command: list[str], cwd: Path) -> UIActionResult:
        raise AssertionError(f"workspace must not run subprocess: {command} {cwd}")

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", FakeStore)
    monkeypatch.setattr("soloscale.local_ui._run_command", no_subprocess)
    result = _run_action(
        {
            "action": "resume-workspace",
            "job_description": "Required: Python and RAG",
            "candidate_base_resume": "Operator supplied Python work.",
            "candidate_skills": "Python, RAG",
            "company_name": "Faros",
            "job_title": "AI-Native Builder",
            "job_id": "4432211307",
            "resume_library_root": str(tmp_path / "Resume Applications"),
        },
        tmp_path / ".soloscale",
        tmp_path,
    )
    assert result is not None and result.return_code == 0
    page = _result_card(result)
    assert "One-page resume preview" in page
    assert "Requirements:" in page
    assert "Private artifacts:" in page
    assert "Application library:" in page
    assert "Resume Applications" in page
    assert 'id="resume-graph"' in page
    application_dirs = list((tmp_path / "Resume Applications" / "applications").iterdir())
    assert len(application_dirs) == 1
    assert (application_dirs[0] / "JD.md").is_file()
    assert (application_dirs[0] / "application.json").is_file()
