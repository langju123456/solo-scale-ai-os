import json
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.local_ui import (
    UIActionResult,
    _build_jd_resume_command,
    _build_resume_sections,
    _page,
    _result_card,
    _resume_graph,
    _run_action,
    _split_path_list,
)


def test_split_path_list_supports_comma_and_newline() -> None:
    assert _split_path_list("a, b\nc,, d") == ["a", "b", "c", "d"]


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
