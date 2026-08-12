import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.knowledge_store import InvalidKnowledgeQueryError
from soloscale.learning_traceability import run_learning_traceability
from soloscale.local_ui import (
    UIActionResult,
    UploadedFile,
    _create_resume_pdf_preview,
    _interview_defense_panel,
    _learning_page,
    _page,
    _parse_submission,
    _result_card,
    _resume_graph,
    _run_action,
    _run_user_resume,
    _search_job_evidence,
    _split_path_list,
    _user_page,
    _workspace_path,
    _write_private_bytes,
    _write_private_json,
)
from soloscale.resume_models import CandidateProfile
from soloscale.resume_workspace import (
    map_interview_defense_bullet,
    run_resume_workspace,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _expected_repository_ref() -> str:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branch:
        return branch
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    github_ref = os.environ.get("GITHUB_REF", "")
    assert github_ref.startswith("refs/")
    return github_ref.removeprefix("refs/")


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
    assert 'href="/content"' in page
    assert 'href="/learning"' in page
    assert "knowledge-sync" not in page
    assert 'name="model"' not in page
    assert 'name="source_kind"' not in page


def test_interview_defense_ui_requires_explicit_mapping_and_opens_exact_run(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    resume = run_resume_workspace(
        data_root=data_root,
        job_description="Required: Python retrieval",
        candidate_profile=CandidateProfile(
            experience_bullets=["Built an operator-supplied retrieval workflow."]
        ),
        evidence_hits=[],
    )
    before_learning = _interview_defense_panel(
        data_root=data_root,
        run_id=resume.run_id,
        repo_root=REPOSITORY_ROOT,
    )
    assert "NEEDS_MAPPING" in before_learning
    assert "Interview Defense →" not in before_learning

    selected = run_learning_traceability(
        data_root=data_root,
        repository_root=REPOSITORY_ROOT,
    )
    newer = run_learning_traceability(
        data_root=data_root,
        repository_root=REPOSITORY_ROOT,
    )
    before_mapping = _interview_defense_panel(
        data_root=data_root,
        run_id=resume.run_id,
        repo_root=REPOSITORY_ROOT,
    )
    assert "确认关联 Conversation RAG 锚点" in before_mapping
    assert max(selected.run_id, newer.run_id) in before_mapping
    assert _expected_repository_ref() in before_mapping
    assert "Interview Defense →" not in before_mapping

    map_interview_defense_bullet(
        data_root=data_root,
        repository_root=REPOSITORY_ROOT,
        resume_run_id=resume.run_id,
        bullet_id="PROFILE-01",
        learning_run_id=selected.run_id,
    )
    mapped_panel = _interview_defense_panel(
        data_root=data_root,
        run_id=resume.run_id,
        repo_root=REPOSITORY_ROOT,
    )
    assert "Interview Defense →" in mapped_panel
    assert selected.run_id in mapped_panel
    assert "#interview-defense" in mapped_panel

    run_count = len(list((data_root / "learning-runs").iterdir()))
    page = _learning_page(
        data_root,
        REPOSITORY_ROOT,
        {
            "run_id": selected.run_id,
            "resume_run_id": resume.run_id,
            "bullet_id": "PROFILE-01",
        },
    )
    assert 'id="interview-defense"' in page
    assert f"Exact Learning run: <code>{selected.run_id}</code>" in page
    assert "Reasoning anchors" in page
    assert "Code anchors" in page
    assert "Test anchors" in page
    assert "src/soloscale/knowledge_store.py" in page
    assert "复制锚点包" in page
    assert "do not prove authorship" in page
    assert len(list((data_root / "learning-runs").iterdir())) == run_count

    anchors_path = (
        data_root / "learning-runs" / selected.run_id / "03_code_anchors.json"
    )
    anchors = json.loads(anchors_path.read_text(encoding="utf-8"))
    anchors["code_anchors"][0]["file_sha256"] = "0" * 64
    anchors_path.write_text(json.dumps(anchors), encoding="utf-8")
    stale_panel = _interview_defense_panel(
        data_root=data_root,
        run_id=resume.run_id,
        repo_root=REPOSITORY_ROOT,
    )
    assert "NEEDS_MAPPING" in stale_panel
    assert "Interview Defense →" not in stale_panel
    assert "映射已失效" in stale_panel

    invalid = _learning_page(
        data_root,
        REPOSITORY_ROOT,
        {"run_id": "not-a-learning-run"},
    )
    assert "Interactive evidence graph" not in invalid


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


def test_job_evidence_search_processes_every_normal_jd_term(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    accepted_queries: list[str] = []

    class FakeStore:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def search(self, query: str, limit: int) -> list[RetrievalHit]:
            assert limit == 3
            if len(query.split()) > 4:
                raise InvalidKnowledgeQueryError("query contains too many searchable terms")
            accepted_queries.append(query)
            return [
                RetrievalHit(
                    chunk_id=f"chunk-{len(accepted_queries)}",
                    document_id="doc",
                    source_kind=SourceKind.CODEX_SESSION,
                    external_id="thread",
                    locator="private",
                    title="local evidence",
                    role=ContentRole.ASSISTANT,
                    timestamp=None,
                    excerpt=query,
                    chunk_sha256="a" * 64,
                    document_sha256="b" * 64,
                    score=1,
                    channels=["fts"],
                )
            ]

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", FakeStore)
    terms = [f"requirement{index}" for index in range(40)]
    job_description = " ".join(terms) + "\n" + "\n".join(
        f"line{index} python" for index in range(30)
    )

    hits = _search_job_evidence(job_description, tmp_path)

    accepted_terms = {term for query in accepted_queries for term in query.split()}
    assert set(terms).issubset(accepted_terms)
    assert "line29" in accepted_terms
    assert all(len(query.split()) <= 4 for query in accepted_queries)
    assert len(hits) == len(accepted_queries)


def test_create_resume_pdf_preview_uses_isolated_local_renderer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "resume.docx"
    target = tmp_path / "preview.pdf"
    source.write_bytes(_uploaded_resume_docx())

    def fake_run(
        command: list[str], *, capture_output: bool, check: bool, timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output is True
        assert check is False
        assert timeout == 30
        assert any(item.startswith("-env:UserInstallation=file:") for item in command)
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "resume.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("soloscale.local_ui._find_soffice", lambda: "/local/soffice")
    monkeypatch.setattr("soloscale.local_ui.subprocess.run", fake_run)

    assert _create_resume_pdf_preview(source, target) is True
    assert target.read_bytes().startswith(b"%PDF-")
    assert target.stat().st_mode & 0o777 == 0o600


def test_ui_private_artifact_writers_are_atomic_and_reject_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "run.json"
    target.write_text("old", encoding="utf-8")
    original_fsync = os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated UI receipt durability failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="durability"):
        _write_private_json(target, {"state": "new"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "new"}

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    link = tmp_path / "preview.pdf"
    link.symlink_to(outside)
    with pytest.raises(OSError, match="regular file"):
        _write_private_bytes(link, b"%PDF-new")
    assert outside.read_bytes() == b"outside"


def test_user_resume_rejects_symlinked_data_root_before_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_root = tmp_path / ".soloscale"
    data_root.symlink_to(outside, target_is_directory=True)

    class ForbiddenStore:
        def __init__(self, root: Path) -> None:
            raise AssertionError(f"KnowledgeStore must not open symlinked root: {root}")

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", ForbiddenStore)
    result = _run_user_resume(
        {
            "job_description": "Required: Python",
            "resume_library_root": str(tmp_path / "Resume Applications"),
        },
        {
            "resume_template": UploadedFile(
                filename="synthetic.docx",
                content_type="application/octet-stream",
                content=_uploaded_resume_docx(),
            )
        },
        data_root,
        tmp_path / "repo",
    )

    assert result.return_code == 1
    assert "symlink" in result.stderr
    assert list(outside.iterdir()) == []


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

    def fake_preview(source: Path, target: Path) -> bool:
        assert source.name == "08_resume.docx"
        target.write_bytes(b"%PDF-1.7\n%%EOF\n")
        target.chmod(0o600)
        return True

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", FakeStore)
    monkeypatch.setattr("soloscale.local_ui._run_command", no_subprocess)
    monkeypatch.setattr("soloscale.local_ui._create_resume_pdf_preview", fake_preview)
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
        tmp_path / "repo",
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
    assert metadata["preview_url"].endswith("/resume.pdf")
    assert (run_dir / "10_resume_preview.pdf").is_file()
    run_payload = json.loads((run_dir / "run.json").read_text())
    assert {"08_resume.docx", "09_user_ui.json", "10_resume_preview.pdf"} <= set(
        run_payload["artifact_paths"]
    )
    application_metadata = json.loads((external_docx.parent / "application.json").read_text())
    assert application_metadata["resume_docx_filename"] == external_docx.name
    rendered = _user_page(result, tmp_path / ".soloscale", {})
    assert "针对性简历已生成" in rendered
    assert metadata["download_url"] in rendered
    assert metadata["preview_url"] in rendered
    assert 'class="resume-pdf-preview"' in rendered
    assert "在新窗口打开" in rendered
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


def test_resume_workspace_rejects_unknown_mode_without_crashing(tmp_path: Path) -> None:
    result = _run_action(
        {
            "action": "resume-workspace",
            "job_description": "Required: Python",
            "resume_mode": "unknown",
        },
        tmp_path / ".soloscale",
        tmp_path,
    )

    assert result is not None
    assert result.return_code == 2
    assert "Resume mode 无效" in result.stderr
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
        tmp_path / "repo",
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


def test_old_jd_resume_action_is_disabled_and_not_rendered(tmp_path: Path) -> None:
    result = _run_action(
        {"action": "jd-resume-draft", "job_description": "Required: Python"},
        tmp_path / ".soloscale",
        tmp_path,
    )
    assert result is not None
    assert result.return_code == 2
    assert "Evidence Agent" in result.stderr
    page = _page(None, tmp_path / ".soloscale", {})
    assert 'name="action" value="jd-resume-draft"' not in page
    assert "Evidence discovery（旧 JD 简历入口已停用）" in page


def test_resume_workspace_rejects_symlinked_application_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeStore:
        def __init__(self, root: Path) -> None:
            del root

        def search(self, query: str, limit: int) -> list[RetrievalHit]:
            del query, limit
            return []

    monkeypatch.setattr("soloscale.local_ui.KnowledgeStore", FakeStore)
    outside = tmp_path / "outside"
    outside.mkdir()
    library = tmp_path / "Resume Applications"
    library.symlink_to(outside, target_is_directory=True)
    result = _run_action(
        {
            "action": "resume-workspace",
            "job_description": "Required: Python",
            "resume_library_root": str(library),
        },
        tmp_path / ".soloscale",
        tmp_path / "repo",
    )
    assert result is not None
    assert result.return_code == 1
    assert result.stderr == "application library save failed; inspect delivery.json"
    assert list(outside.iterdir()) == []
