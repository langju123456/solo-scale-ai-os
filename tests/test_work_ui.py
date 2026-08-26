from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
    assert "我的工作资料" in page
    assert "soloscale://choose-work-repository" in page
    assert "soloscale://choose-chatgpt-export" in page
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
    (project / ".git").mkdir(parents=True)

    result = import_codex_history(data_root, codex_home=codex_home)
    snapshot = load_work_context(
        data_root,
        workspace_root=project,
        home=tmp_path / "home",
    )

    assert result.imported == 1
    assert snapshot.codex_sessions == 1
    assert snapshot.project_connected is True
    assert snapshot.codex_folder_available is True
    assert session.read_bytes() == before

    page = work_page(
        data_root=data_root,
        workspace_root=project,
        desktop_mode=True,
        notice="本地 Git 项目已连接。",
    )
    assert "本地 Git 项目已连接" in page
    assert "下一步" in page
    assert "准备这个项目" in page
    assert "正在准备项目快照" in page
    assert "不会上传项目代码" in page
    assert 'action="/work/refresh"' in page
    assert str(project) not in page
