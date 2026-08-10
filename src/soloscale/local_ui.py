from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


COMMAND_TIMEOUT_SECONDS = 120


@dataclass
class UIActionResult:
    name: str
    command: str
    return_code: int
    stdout: str
    stderr: str
    elapsed_ms: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_soloscale_command() -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    cli = shutil.which("soloscale")
    if cli is not None:
        return [cli], env

    env["PYTHONPATH"] = os.pathsep.join(
        [str(_repo_root() / "src"), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    return [sys.executable, "-m", "soloscale.cli"], env


def _run_command(command: list[str], cwd: Path) -> UIActionResult:
    start = time.perf_counter()
    cli_command, cli_env = _resolve_soloscale_command()
    full_command = cli_command + command
    try:
        completed = subprocess.run(
            full_command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return UIActionResult(
            name=command[0],
            command=" ".join(full_command),
            return_code=1,
            stdout="",
            stderr=str(exc),
            elapsed_ms=elapsed_ms,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return UIActionResult(
            name=command[0],
            command=" ".join(full_command),
            return_code=124,
            stdout="",
            stderr=f"command timed out after {COMMAND_TIMEOUT_SECONDS}s",
            elapsed_ms=elapsed_ms,
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return UIActionResult(
        name=command[0],
        command=" ".join(full_command),
        return_code=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        elapsed_ms=elapsed_ms,
    )


def _split_path_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _normalize_for_resume(value: str, *, limit: int = 260) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _extract_private_result_path(raw_stdout: str) -> Path | None:
    prefix = "Private result: "
    for line in raw_stdout.splitlines():
        if line.startswith(prefix):
            candidate = line[len(prefix) :].strip()
            if candidate:
                return Path(candidate).expanduser()
    return None


def _load_json_file(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _build_resume_sections(payload: dict, *, job_title_hint: str | None) -> str:
    claims = payload.get("claims", [])
    refs = payload.get("refs", [])
    unsupported = payload.get("unsupported", [])
    open_questions = payload.get("open_questions", [])
    if not isinstance(claims, list):
        claims = []
    if not isinstance(refs, list):
        refs = []

    refs_by_id = {}
    for item in refs:
        if isinstance(item, dict):
            chunk_id = item.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id:
                refs_by_id[chunk_id] = item

    title = (job_title_hint or "JD 简历草稿").strip() or "JD 简历草稿"
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("## 项目经历（可追溯）")

    if claims:
        for index, item in enumerate(claims, start=1):
            text = str(item.get("text", "") if isinstance(item, dict) else "")
            evidence_ids = [
                str(evidence_id)
                for evidence_id in (item.get("evidence_chunk_ids", []) if isinstance(item, dict) else [])
                if isinstance(evidence_id, str) and evidence_id.strip()
            ]
            lines.append(f"- 项目经历 {index}：{_normalize_for_resume(text)}")
            if evidence_ids:
                lines.append("  - 证据锚点：")
                for evidence_id in evidence_ids:
                    ref = refs_by_id.get(evidence_id, {})
                    source_kind = str(ref.get("source_kind", "unknown"))
                    title_text = _normalize_for_resume(str(ref.get("title", "") or ""), limit=80)
                    tag = f"{source_kind}" + (f"｜{title_text}" if title_text else "")
                    lines.append(f"    - {evidence_id}（{tag}）")
                lines.append("  - 可追溯说明：")
                for evidence_id in evidence_ids:
                    ref = refs_by_id.get(evidence_id, {})
                    excerpt = _normalize_for_resume(
                        str(ref.get("excerpt", "") or ""), limit=220
                    )
                    external_id = str(ref.get("external_id", "") or "")
                    prefix = " / ".join(
                        part
                        for part in [
                            str(ref.get("title", "") or ""),
                            external_id,
                        ]
                        if part
                    )
                    if prefix:
                        lines.append(f"    - {evidence_id}（{prefix}）：{excerpt}")
                    else:
                        lines.append(f"    - {evidence_id}：{excerpt}")
            else:
                lines.append("  - 证据锚点：未命中（请重试）")
    else:
        lines.append("- 尚未生成候选经历，请增加 JD 关键词并重试。")

    if unsupported:
        lines.append("")
        lines.append("## 未被证据覆盖 / 需人工补证")
        for item in unsupported:
            lines.append(f"- {_normalize_for_resume(str(item))}")

    if open_questions:
        lines.append("")
        lines.append("## 待补充问题")
        for item in open_questions:
            lines.append(f"- {_normalize_for_resume(str(item))}")

    lines.append("")
    lines.append("## 说明")
    lines.append(
        "- 所有“项目经历”条目均来自当前本地证据并保留 chunk_id。\n"
        "- 本次输出为本地草稿，建议人工改写为与你经历一致的表达。"
    )
    return "\n".join(lines).strip() + "\n"


def _build_jd_resume_command(form: dict[str, str], data_root: Path) -> tuple[list[str], str | None]:
    jd = form.get("job_description", "").strip()
    if not jd:
        return [], None

    model = form.get("resume_model", "qwen3:8b").strip() or "qwen3:8b"
    ollama_url = form.get("resume_ollama_url", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
    source_kind = form.get("resume_source_kind", "").strip()
    raw_rounds = form.get("resume_max_rounds", "2").strip()
    safe_rounds = max(1, min(3, int(raw_rounds) if raw_rounds.isdigit() else 2))

    instruction = (
        "请根据以下 JD，为个人 AI Engineer 简历输出结构化简历草稿：\n"
        "1) 项目经历（1-6条）；\n"
        "2) 每条都要关联 evidence chunk_id；\n"
        "3) 每条给出一句可追溯说明。\n"
        "4) 不允许发明未检索到的内容；缺证据请直说。"
        "\n\nJD：\n"
        f"{jd}"
    )

    command = [
        "evidence-agent",
        instruction,
        "--data-root",
        str(data_root),
        "--model",
        model,
        "--ollama-url",
        ollama_url,
        "--max-rounds",
        str(safe_rounds),
    ]
    if source_kind:
        command += ["--source-kind", source_kind]
    return command, jd


def _run_jd_resume_draft(form: dict[str, str], data_root: Path, repo_root: Path) -> UIActionResult | None:
    command, prompt_hint = _build_jd_resume_command(form, data_root)
    if not command:
        return UIActionResult(
            name="jd-resume-draft",
            command="jd-resume-draft",
            return_code=2,
            stdout="",
            stderr="JD 不能为空。",
            elapsed_ms=0,
        )

    base = _run_command(command, repo_root)
    if base.return_code != 0:
        return base

    result_path = _extract_private_result_path(base.stdout)
    if result_path is None or not result_path.is_file():
        return UIActionResult(
            name="jd-resume-draft",
            command=base.command,
            return_code=1,
            stdout=base.stdout,
            stderr="未找到 evidence-agent 产出的 04_result.json，无法生成结构化简历。",
            elapsed_ms=base.elapsed_ms,
        )

    payload = _load_json_file(result_path)
    if payload is None:
        return UIActionResult(
            name="jd-resume-draft",
            command=base.command,
            return_code=1,
            stdout=base.stdout,
            stderr="无法解析 evidence-agent 的 JSON 结果。",
            elapsed_ms=base.elapsed_ms,
        )

    structured = _build_resume_sections(payload, job_title_hint=prompt_hint)
    return UIActionResult(
        name="jd-resume-draft",
        command=base.command,
        return_code=0,
        stdout=structured,
        stderr="",
        elapsed_ms=base.elapsed_ms,
    )


def _escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def _parse_form(raw: bytes) -> dict[str, str]:
    return {
        key: values[0] if values else ""
        for key, values in urllib.parse.parse_qs(raw.decode("utf-8")).items()
    }


def _build_control_tower_path(data_root: Path) -> Path:
    return (data_root / "control-tower" / "index.html").resolve()


def _read_control_tower(data_root: Path) -> tuple[bool, str]:
    target = _build_control_tower_path(data_root)
    if not target.is_file():
        return False, ""
    return True, target.read_text(encoding="utf-8")


def _run_action(form: dict[str, str], data_root: Path, repo_root: Path) -> UIActionResult | None:
    action = form.get("action")
    if action == "knowledge-status":
        return _run_command(["knowledge-status", "--data-root", str(data_root)], repo_root)
    if action == "control-tower-build":
        return _run_command(["control-tower-build", "--data-root", str(data_root)], repo_root)
    if action == "knowledge-search":
        query = form.get("query", "").strip()
        if not query:
            return UIActionResult(
                name=action,
                command="knowledge-search",
                return_code=2,
                stdout="",
                stderr="Query 不能为空。",
                elapsed_ms=0,
            )
        source_kind = form.get("source_kind", "").strip()
        command = ["knowledge-search", query, "--data-root", str(data_root)]
        if source_kind:
            command += ["--source-kind", source_kind]
        return _run_command(command, repo_root)
    if action == "knowledge-sync":
        include_codex = form.get("include_codex") == "on"
        codex_home = form.get("codex_home", "").strip()
        chatgpt_exports = _split_path_list(form.get("chatgpt_exports", ""))
        buildlog_roots = _split_path_list(form.get("buildlog_roots", ""))

        command = ["knowledge-sync", "--data-root", str(data_root)]
        if not include_codex:
            command.append("--no-codex")
        if codex_home:
            command += ["--codex-home", codex_home]
        for export_path in chatgpt_exports:
            command += ["--chatgpt-export", export_path]
        for buildlog_root in buildlog_roots:
            command += ["--buildlog-root", buildlog_root]
        return _run_command(command, repo_root)
    if action == "evidence-agent":
        question = form.get("question", "").strip()
        if not question:
            return UIActionResult(
                name=action,
                command="evidence-agent",
                return_code=2,
                stdout="",
                stderr="Question 不能为空。",
                elapsed_ms=0,
            )
        model = form.get("model", "qwen3:8b").strip() or "qwen3:8b"
        ollama_url = form.get("ollama_url", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
        source_kind = form.get("agent_source_kind", "").strip()

        command = [
            "evidence-agent",
            question,
            "--data-root",
            str(data_root),
            "--model",
            model,
            "--ollama-url",
            ollama_url,
        ]
        if source_kind:
            command += ["--source-kind", source_kind]
        return _run_command(command, repo_root)
    if action == "jd-resume-draft":
        return _run_jd_resume_draft(form, data_root, repo_root)

    return None


def _result_card(result: UIActionResult | None) -> str:
    if result is None:
        return "<p>未匹配到动作。</p>"
    if result.return_code == 0:
        status = "✅ 成功"
        banner = "success"
    else:
        status = f"⚠️ 失败（Code {result.return_code}）"
        banner = "error"
    body = result.stdout if result.stdout else result.stderr
    if not body:
        body = "无输出"
    return f"""
<section class="card">
  <h2>{_escape(result.name)} · {status}</h2>
  <p>执行耗时：{result.elapsed_ms}ms</p>
  <p>命令：<code>{_escape(result.command)}</code></p>
  <pre class="{banner}">{_escape(body)}</pre>
</section>
"""


def _control_tower_section(data_root: Path) -> str:
    exists, _ = _read_control_tower(data_root)
    if not exists:
        return (
            "<section class=\"card\"><h2>Control Tower</h2>"
            "<p>还未生成。请先执行下方 <strong>Build Control Tower</strong>。</p></section>"
        )
    return (
        "<section class=\"card\"><h2>Control Tower</h2>"
        "<p><a href=\"/control-tower\" target=\"_blank\" rel=\"noopener\">打开 Control Tower</a></p></section>"
    )


def _page(action_result: UIActionResult | None, data_root: Path, form: dict[str, str]) -> str:
    includes = form.get("include_codex") == "on"
    query = _escape(form.get("query", ""))
    question = _escape(form.get("question", ""))
    source_kind = form.get("source_kind", "")
    model = _escape(form.get("model", "qwen3:8b"))
    ollama_url = _escape(form.get("ollama_url", "http://127.0.0.1:11434"))
    agent_source_kind = form.get("agent_source_kind", "")
    resume_job_description = _escape(form.get("job_description", ""))
    resume_model = _escape(form.get("resume_model", "qwen3:8b"))
    resume_ollama_url = _escape(form.get("resume_ollama_url", "http://127.0.0.1:11434"))
    resume_source_kind = form.get("resume_source_kind", "")
    resume_max_rounds = _escape(form.get("resume_max_rounds", "2"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>SoloScale Local UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; margin: 0; background: #0f172a; color: #e2e8f0; padding: 20px; }}
    .container {{ max-width: 1120px; margin: 0 auto; display: grid; gap: 16px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 16px; }}
    .full {{ grid-column: 1 / -1; }}
    h1, h2 {{ margin: 0 0 10px; }}
    form {{ display: grid; gap: 8px; margin-top: 8px; }}
    input, textarea, select {{ width: 100%; box-sizing: border-box; background: #0f172a; color: #e2e8f0; border: 1px solid #475569; border-radius: 8px; padding: 8px; }}
    button {{ cursor: pointer; border-radius: 8px; border: 1px solid #475569; padding: 10px 12px; background: #1d4ed8; color: #fff; font-weight: 600; }}
    pre {{ background: #0b1120; border: 1px solid #334155; padding: 12px; border-radius: 8px; white-space: pre-wrap; max-height: 280px; overflow: auto; }}
    .success {{ border-color: #10b981; color: #a7f3d0; }}
    .error {{ border-color: #ef4444; color: #fecaca; }}
    .small {{ color: #94a3b8; font-size: 0.9rem; }}
    label {{ display: grid; gap: 4px; }}
    .row {{ display: grid; gap: 4px; grid-template-columns: 1fr auto; align-items: end; }}
    .muted {{ color: #94a3b8; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>SoloScale 本地端（简化版）</h1>
  <p class="small">这是个人使用最小界面：用于触发 CLI 流程并读取结果。它不会自动更新简历、Casebook 或发布内容。</p>
  <div class="container">
    <section class="card">
      <h2>1）Knowledge 状态</h2>
      <p class="muted">当前数据根目录：<code>{_escape(str(data_root))}</code></p>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="knowledge-status" />
        <button type="submit">Run knowledge-status</button>
      </form>
    </section>

    <section class="card">
      <h2>2）Build Control Tower</h2>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="control-tower-build" />
        <button type="submit">Build Control Tower</button>
      </form>
      {_control_tower_section(data_root)}
    </section>

    <section class="card full">
      <h2>3）Knowledge Sync</h2>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="knowledge-sync" />
        <label>
          Codex 源
          <div class="row">
            <div>
              <input type="checkbox" name="include_codex" {"checked" if includes else ""} />
            </div>
            <span class="muted">不勾选 = --no-codex</span>
          </div>
        </label>
        <label>
          codex_home（可选）
          <input name="codex_home" value="{_escape(form.get("codex_home", ""))}" />
        </label>
        <label>
          chatgpt-export（逗号分隔，可选）
          <textarea name="chatgpt_exports" rows="2">{_escape(form.get("chatgpt_exports", ""))}</textarea>
        </label>
        <label>
          buildlog-root（逗号分隔，可选）
          <textarea name="buildlog_roots" rows="2">{_escape(form.get("buildlog_roots", ""))}</textarea>
        </label>
        <button type="submit">Run knowledge-sync</button>
      </form>
    </section>

    <section class="card full">
      <h2>4）Knowledge Search</h2>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="knowledge-search" />
        <label>
          Query
          <input name="query" value="{query}" />
        </label>
        <label>
          Source kind（可选）
          <select name="source_kind">
            <option value="" {"selected" if source_kind == "" else ""}></option>
            <option value="codex_session" {"selected" if source_kind == "codex_session" else ""}>codex_session</option>
            <option value="buildlog_run" {"selected" if source_kind == "buildlog_run" else ""}>buildlog_run</option>
            <option value="chatgpt_conversation" {"selected" if source_kind == "chatgpt_conversation" else ""}>chatgpt_conversation</option>
          </select>
        </label>
        <button type="submit">Run knowledge-search</button>
      </form>
    </section>

    <section class="card full">
      <h2>5）Evidence Agent（JD 或面试准备问题）</h2>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="evidence-agent" />
        <label>
          Question
          <textarea name="question" rows="3">{question}</textarea>
        </label>
        <label>
          Model
          <input name="model" value="{model}" />
        </label>
        <label>
          Ollama URL
          <input name="ollama_url" value="{ollama_url}" />
        </label>
        <label>
          Source kind（可选）
          <select name="agent_source_kind">
            <option value="" {"selected" if agent_source_kind == "" else ""}></option>
            <option value="codex_session" {"selected" if agent_source_kind == "codex_session" else ""}>codex_session</option>
            <option value="buildlog_run" {"selected" if agent_source_kind == "buildlog_run" else ""}>buildlog_run</option>
            <option value="chatgpt_conversation" {"selected" if agent_source_kind == "chatgpt_conversation" else ""}>chatgpt_conversation</option>
          </select>
        </label>
        <button type="submit">Run evidence-agent</button>
      </form>
    </section>

    <section class="card full">
      <h2>6）JD 简历草稿（结构化，可追溯）</h2>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="jd-resume-draft" />
        <label>
          Job Description
          <textarea name="job_description" rows="8">{resume_job_description}</textarea>
        </label>
        <label>
          Model
          <input name="resume_model" value="{resume_model}" />
        </label>
        <label>
          Ollama URL
          <input name="resume_ollama_url" value="{resume_ollama_url}" />
        </label>
        <label>
          每次检索轮次（1-3）
          <input name="resume_max_rounds" value="{resume_max_rounds}" />
        </label>
        <label>
          Source kind（可选）
          <select name="resume_source_kind">
            <option value="" {"selected" if resume_source_kind == "" else ""}></option>
            <option value="codex_session" {"selected" if resume_source_kind == "codex_session" else ""}>codex_session</option>
            <option value="buildlog_run" {"selected" if resume_source_kind == "buildlog_run" else ""}>buildlog_run</option>
            <option value="chatgpt_conversation" {"selected" if resume_source_kind == "chatgpt_conversation" else ""}>chatgpt_conversation</option>
          </select>
        </label>
        <button type="submit">生成 JD 简历草稿</button>
      </form>
    </section>

    <section class="card full">
      <h2>最近一次执行结果</h2>
      {_result_card(action_result)}
    </section>
  </div>
</body>
</html>"""


def _serve_control_tower(handler: BaseHTTPRequestHandler, data_root: Path) -> None:
    exists, document = _read_control_tower(data_root)
    if not exists:
        handler.send_error(404, "Control Tower not generated")
        return
    body = document.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class SoloScaleLocalUIHandler(BaseHTTPRequestHandler):
    ui_data_root: Path = Path(".soloscale")
    repo_root: Path = _repo_root()
    latest_form: dict[str, str] = {}

    def _send_page(self, result: UIActionResult | None) -> None:
        data_root = self.ui_data_root.resolve()
        page = _page(result, data_root, self.latest_form)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", ""}:
            self._send_page(None)
            return
        if self.path == "/control-tower":
            _serve_control_tower(self, self.ui_data_root.resolve())
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length)
        self.latest_form = _parse_form(body)
        data_root = Path(self.latest_form.get("data_root", str(self.ui_data_root))).expanduser().resolve()
        result = _run_action(self.latest_form, data_root, self.repo_root)
        self._send_page(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="SoloScale minimal local UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-root",
        default=".soloscale",
        help="SoloScale private data root (default: .soloscale)",
    )
    args = parser.parse_args()

    handler = SoloScaleLocalUIHandler
    handler.ui_data_root = Path(args.data_root).resolve()
    handler.repo_root = _repo_root()

    server = HTTPServer((args.host, args.port), handler)
    print(f"SoloScale local UI: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
