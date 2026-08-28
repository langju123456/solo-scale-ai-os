from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from soloscale.evidence_hub import EvidenceHub
from soloscale.ui_shell import SourceState, render_source_state
from soloscale.work_ui import (
    import_chatgpt_export,
    import_codex_history,
    load_work_context,
    work_page,
)


def _chatgpt_export(private_text: str) -> list[object]:
    return [
        {
            "id": "conversation-work-1",
            "title": "Private work conversation",
            "current_node": "assistant",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["user"],
                    "message": None,
                },
                "user": {
                    "id": "user",
                    "parent": "root",
                    "children": ["assistant"],
                    "message": {
                        "id": "user-message",
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": [private_text]},
                    },
                },
                "assistant": {
                    "id": "assistant",
                    "parent": "user",
                    "children": [],
                    "message": {
                        "id": "assistant-message",
                        "author": {"role": "assistant"},
                        "content": {
                            "content_type": "text",
                            "parts": ["Use a bounded evidence contract."],
                        },
                    },
                },
            },
        }
    ]


def _codex_session(private_text: str) -> str:
    records = [
        {
            "timestamp": "2026-08-16T01:00:00Z",
            "type": "session_meta",
            "payload": {"id": "work-session-1"},
        },
        {
            "timestamp": "2026-08-16T01:01:00Z",
            "type": "response_item",
            "payload": {
                "id": "work-message-1",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": private_text}],
            },
        },
    ]
    return "".join(json.dumps(record) + "\n" for record in records)


def test_work_page_is_read_only_and_hides_private_implementation_details(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"

    page = work_page(
        data_root=data_root,
        workspace_root=None,
        desktop_mode=True,
    )

    assert not data_root.exists()
    assert "你的工作资料" in page
    assert "选择 SoloScale 可以使用的资料。以后随时可以修改。" in page
    assert "只读取你明确选择的资料。本地优先，不扫描整台 Mac。" in page
    assert "了解数据边界" in page
    assert "已连接" in page
    assert "添加更多" in page
    assert "暂不可用" in page
    assert 'data-source-state="NOT_CONNECTED"' in page
    assert 'data-source-state="UNAVAILABLE"' in page
    assert 'id="work-processing"' in page
    assert 'data-source-state="PROCESSING"' in page
    assert "soloscale://choose-work-repository" in page
    assert "soloscale://choose-chatgpt-export" in page
    assert "完成并继续" in page
    assert 'href="/?lang=zh-CN"' in page
    assert "EvidenceHub" not in page
    assert "FTS" not in page
    assert "provenance_locator" not in page
    assert str(tmp_path) not in page


def test_chatgpt_import_is_explicit_source_preserving_and_body_free_in_ui(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    private_text = "PRIVATE INTERVIEW PROJECT DETAIL"
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps(_chatgpt_export(private_text)), encoding="utf-8")
    before = hashlib.sha256(export.read_bytes()).hexdigest()

    result = import_chatgpt_export(data_root, export)
    snapshot = load_work_context(data_root)
    page = work_page(
        data_root=data_root,
        workspace_root=None,
        desktop_mode=False,
    )

    assert result.imported == 1
    assert snapshot.chatgpt_exports == 1
    assert snapshot.knowledge_documents == 1
    assert hashlib.sha256(export.read_bytes()).hexdigest() == before
    assert private_text not in page
    assert export.name not in page
    assert str(tmp_path) not in page
    assert 'data-source="chatgpt-export"' in page
    assert 'data-source-state="READY"' in page
    assert "已导入 1 个对话" in page


def test_codex_import_and_selected_git_project_reuse_existing_intake(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    private_text = "PRIVATE CODEX IMPLEMENTATION NOTE"
    codex_home = tmp_path / "home" / ".codex"
    session = codex_home / "sessions" / "2026" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(_codex_session(private_text), encoding="utf-8")
    before = session.read_bytes()
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "feature.txt").write_text("verified feature", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "feature.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "feat: add verified local project evidence",
        ],
        check=True,
    )

    result = import_codex_history(data_root, codex_home=codex_home)
    snapshot = load_work_context(
        data_root,
        workspace_root=project,
        home=tmp_path / "home",
    )

    assert result.imported == 1
    assert snapshot.codex_sessions == 1
    assert snapshot.project_connected is True
    assert snapshot.project_state == "STALE"
    assert snapshot.codex_folder_available is True
    assert session.read_bytes() == before

    page = work_page(
        data_root=data_root,
        workspace_root=project,
        desktop_mode=True,
        notice="本地 Git 项目已连接。",
    )
    assert "本地 Git 项目已连接" in page
    assert "project · 工程证据已过期" in page
    assert "更换项目" in page
    assert "刷新工程证据" in page
    assert 'data-processing-source="Local Git"' in page
    assert "正在准备本地项目快照" in page
    assert 'data-processing-source="Codex"' in page
    assert "正在建立本地索引" in page
    assert "不保存或上传项目源码" in page
    assert 'action="/work/refresh"' in page
    assert str(project) not in page

    EvidenceHub(data_root).sync_git_repository(project)
    ready = load_work_context(data_root, workspace_root=project)
    assert ready.project_state == "READY"
    assert ready.project_fact_count == 1
    assert ready.project_new_commits == 0


def test_work_page_english_copy_actions_and_states_are_truthful(tmp_path: Path) -> None:
    page = work_page(
        data_root=tmp_path / "data",
        workspace_root=None,
        locale="en",
        desktop_mode=True,
        chatgpt_export_selected=True,
    )

    assert "Your Work Context" in page
    assert "Choose the work sources SoloScale can use. You can change them anytime." in page
    assert "Only work you explicitly choose is read. Local first; no whole-Mac scanning." in page
    assert "Connected" in page
    assert "Add More" in page
    assert "Not Available Yet" in page
    assert "File selected; waiting for your import approval" in page
    assert 'action="/work/import-chatgpt"' in page
    assert "Continue" in page
    assert "Connect GitHub" not in page


def test_shared_source_states_have_stable_semantics() -> None:
    expected: dict[SourceState, str] = {
        "READY": "✓",
        "STALE": "!",
        "PROCESSING": "●",
        "AVAILABLE": "＋",
        "NOT_CONNECTED": "○",
        "UNAVAILABLE": "—",
        "NEEDS_ATTENTION": "!",
    }

    for state, symbol in expected.items():
        rendered = render_source_state(state, "en")
        assert f'data-source-state="{state}"' in rendered
        assert symbol in rendered
