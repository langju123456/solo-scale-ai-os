import json
from pathlib import Path

import pytest

from soloscale.knowledge_models import ContentRole, RetrievalHit, SourceKind
from soloscale.local_ui import (
    UIActionResult,
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
