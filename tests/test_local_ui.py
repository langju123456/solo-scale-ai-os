from pathlib import Path

from soloscale.local_ui import (
    UIActionResult,
    _build_jd_resume_command,
    _build_resume_sections,
    _run_action,
    _split_path_list,
)


def test_split_path_list_supports_comma_and_newline() -> None:
    assert _split_path_list("a, b\nc,, d") == ["a", "b", "c", "d"]


def test_run_action_knowledge_status_builds_expected_command(
    monkeypatch, tmp_path: Path
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
