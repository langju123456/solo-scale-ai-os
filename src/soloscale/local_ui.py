from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from soloscale.knowledge_models import RetrievalHit
from soloscale.knowledge_store import KnowledgeStore, KnowledgeStoreError
from soloscale.resume_docx import (
    ResumeTemplateError,
    extract_candidate_profile,
    tailor_resume_docx,
)
from soloscale.resume_models import CandidateProfile, ResumeMode
from soloscale.resume_workspace import run_resume_workspace as execute_resume_workspace

COMMAND_TIMEOUT_SECONDS = 120
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
_PRIVATE_FILE_MODE = 0o600
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_RUN_ID_RE = re.compile(r"resume-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{10}")


@dataclass
class UIActionResult:
    name: str
    command: str
    return_code: int
    stdout: str
    stderr: str
    elapsed_ms: int


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class FormSubmission:
    fields: dict[str, str]
    files: dict[str, UploadedFile]


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


def _load_json_file(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _build_resume_sections(payload: dict[str, object], *, job_title_hint: str | None) -> str:
    claims = payload.get("claims", [])
    refs = payload.get("refs", [])
    unsupported = payload.get("unsupported", [])
    open_questions = payload.get("open_questions", [])
    if not isinstance(claims, list):
        claims = []
    if not isinstance(refs, list):
        refs = []
    if not isinstance(unsupported, list):
        unsupported = []
    if not isinstance(open_questions, list):
        open_questions = []

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
                for evidence_id in (
                    item.get("evidence_chunk_ids", []) if isinstance(item, dict) else []
                )
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
                    excerpt = _normalize_for_resume(str(ref.get("excerpt", "") or ""), limit=220)
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
    ollama_url = (
        form.get("resume_ollama_url", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
    )
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


def _run_jd_resume_draft(
    form: dict[str, str], data_root: Path, repo_root: Path
) -> UIActionResult | None:
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


def _candidate_profile_from_form(form: dict[str, str]) -> CandidateProfile:
    """Keep supplied resume text as operator claims; never infer new personal facts."""
    base_lines = [
        line.strip(" -•\t") for line in form.get("candidate_base_resume", "").splitlines()
    ]
    return CandidateProfile(
        full_name=form.get("candidate_name", "").strip() or None,
        headline=form.get("candidate_headline", "").strip() or None,
        summary=form.get("candidate_summary", "").strip() or None,
        skills=_split_path_list(form.get("candidate_skills", "")),
        experience_bullets=[line for line in base_lines if line],
    )


def _run_resume_workspace(form: dict[str, str], data_root: Path, repo_root: Path) -> UIActionResult:
    del repo_root
    job_description = form.get("job_description", "").strip()
    if not job_description:
        return UIActionResult("resume-workspace", "resume-workspace", 2, "", "JD 不能为空。", 0)
    mode = ResumeMode(form.get("resume_mode", ResumeMode.LOCAL_ONLY.value))
    if mode is ResumeMode.HYBRID:
        return UIActionResult(
            "resume-workspace",
            "resume-workspace",
            2,
            "",
            "Hybrid research provider 尚未配置；v0.1 不会执行网络调用。请选择 Local-only。",
            0,
        )
    try:
        library_value = form.get("resume_library_root", "").strip()
        library_root = (
            Path(library_value or Path.home() / "Documents" / "Resume Applications")
            .expanduser()
            .resolve()
        )
        store = KnowledgeStore(data_root)
        requirements = form.get("job_description", "").splitlines()
        hits = []
        for requirement in requirements[:24]:
            if requirement.strip():
                hits.extend(store.search(requirement.strip(), limit=3))
        unique_hits = {hit.chunk_id: hit for hit in hits}
        run = execute_resume_workspace(
            data_root=data_root,
            job_description=job_description,
            candidate_profile=_candidate_profile_from_form(form),
            evidence_hits=list(unique_hits.values()),
            company_name=form.get("company_name", "").strip() or None,
            company_url=form.get("company_url", "").strip() or None,
            job_title=form.get("job_title", "").strip() or None,
            job_id=form.get("job_id", "").strip() or None,
            application_library_root=library_root,
            mode=mode,
        )
    except (KnowledgeStoreError, OSError, ValueError) as exc:
        return UIActionResult("resume-workspace", "local KnowledgeStore search", 1, "", str(exc), 0)
    run_path = data_root / "resume-runs" / run.run_id
    return UIActionResult(
        "resume-workspace", "local KnowledgeStore search", 0, f"Resume workspace: {run_path}", "", 0
    )


def _write_private_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, _PRIVATE_FILE_MODE)


def _write_private_bytes(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, _PRIVATE_FILE_MODE)


def _safe_filename_component(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", (value or "").strip(), flags=re.UNICODE)
    return cleaned.strip("-._")[:80] or fallback


def _user_resume_filename(
    profile: CandidateProfile,
    company_name: str | None,
    job_title: str | None,
    job_description: str,
) -> str:
    resolved_title = job_title or next(
        (line.strip() for line in job_description.splitlines() if line.strip()), "Role"
    )
    return "Resume_{}_{}_{}.docx".format(
        _safe_filename_component(profile.full_name, "Candidate"),
        _safe_filename_component(company_name, "Company"),
        _safe_filename_component(resolved_title, "Role"),
    )


def _search_job_evidence(job_description: str, data_root: Path) -> list[RetrievalHit]:
    store = KnowledgeStore(data_root)
    hits = []
    for requirement in job_description.splitlines()[:24]:
        if requirement.strip():
            hits.extend(store.search(requirement.strip(), limit=3))
    return list({hit.chunk_id: hit for hit in hits}.values())


def _run_user_resume(
    form: dict[str, str],
    files: dict[str, UploadedFile],
    data_root: Path,
    repo_root: Path,
) -> UIActionResult:
    """Run the local upload → evidence → workspace → DOCX flow without a subprocess."""
    del repo_root
    started = time.perf_counter()
    job_description = form.get("job_description", "").strip()
    if not job_description:
        return UIActionResult(
            "tailored-resume", "local resume generation", 2, "", "请粘贴完整的 Job Description。", 0
        )
    upload = files.get("resume_template")
    if upload is None or not upload.content:
        return UIActionResult(
            "tailored-resume", "local resume generation", 2, "", "请上传 DOCX 简历模板。", 0
        )
    upload_name = upload.filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if not upload_name.casefold().endswith(".docx"):
        return UIActionResult(
            "tailored-resume",
            "local resume generation",
            2,
            "",
            "当前版本仅支持 .docx 模板。",
            0,
        )

    try:
        profile = extract_candidate_profile(upload.content)
        tailored = tailor_resume_docx(upload.content, job_description)
        library_root = Path(
            form.get("resume_library_root", "").strip()
            or Path.home() / "Documents" / "Resume Applications"
        ).expanduser().resolve()
        evidence_hits = _search_job_evidence(job_description, data_root)
        run = execute_resume_workspace(
            data_root=data_root,
            job_description=job_description,
            candidate_profile=profile,
            evidence_hits=evidence_hits,
            company_name=form.get("company_name", "").strip() or None,
            company_url=form.get("company_url", "").strip() or None,
            job_title=form.get("job_title", "").strip() or None,
            job_id=form.get("job_id", "").strip() or None,
            application_library_root=library_root,
            mode=ResumeMode.LOCAL_ONLY,
        )
        run_dir = data_root / "resume-runs" / run.run_id
        application_value = run.route.get("application_library_path")
        if not isinstance(application_value, str) or not application_value:
            raise OSError("Resume application directory was not created")
        application_dir = Path(application_value)
        output_name = _user_resume_filename(
            profile,
            form.get("company_name", "").strip() or None,
            form.get("job_title", "").strip() or None,
            job_description,
        )
        internal_docx = run_dir / "08_resume.docx"
        external_docx = application_dir / output_name
        _write_private_bytes(internal_docx, tailored.content)
        _write_private_bytes(external_docx, tailored.content)
        user_metadata = {
            "schema_version": "1.0",
            "template_filename": upload_name,
            "template_content_type": upload.content_type,
            "template_sha256": tailored.template_sha256,
            "output_filename": output_name,
            "output_sha256": tailored.output_sha256,
            "claims_preserved": tailored.claims_preserved,
            "source_paragraph_count": tailored.source_paragraph_count,
            "project_blocks_reordered": tailored.project_blocks_reordered,
            "skill_bullets_reordered": tailored.skill_bullets_reordered,
            "internal_docx": str(internal_docx),
            "external_docx": str(external_docx),
            "download_url": f"/downloads/{run.run_id}/resume.docx",
            "network_used": False,
        }
        _write_private_json(run_dir / "09_user_ui.json", user_metadata)

        application_metadata_path = application_dir / "application.json"
        application_metadata = _load_json_file(application_metadata_path) or {}
        application_metadata["resume_docx_filename"] = output_name
        application_metadata["resume_docx_sha256"] = tailored.output_sha256
        application_metadata["claims_preserved"] = tailored.claims_preserved
        _write_private_json(application_metadata_path, application_metadata)

        run_path = run_dir / "run.json"
        run_payload = _load_json_file(run_path) or {}
        route = run_payload.get("route")
        if not isinstance(route, dict):
            route = {}
            run_payload["route"] = route
        route["user_ui"] = True
        route["docx_saved"] = True
        route["docx_sha256"] = tailored.output_sha256
        artifact_paths = run_payload.get("artifact_paths")
        if not isinstance(artifact_paths, list):
            artifact_paths = []
            run_payload["artifact_paths"] = artifact_paths
        for name in ("08_resume.docx", "09_user_ui.json"):
            if name not in artifact_paths:
                artifact_paths.append(name)
        _write_private_json(run_path, run_payload)
    except (KnowledgeStoreError, OSError, ResumeTemplateError, ValueError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return UIActionResult(
            "tailored-resume", "local resume generation", 1, "", str(exc), elapsed_ms
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return UIActionResult(
        "tailored-resume",
        "local resume generation",
        0,
        f"Resume workspace: {run_dir}",
        "",
        elapsed_ms,
    )


def _escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def _parse_form(raw: bytes) -> dict[str, str]:
    return {
        key: values[0] if values else ""
        for key, values in urllib.parse.parse_qs(raw.decode("utf-8")).items()
    }


def _parse_submission(raw: bytes, content_type: str) -> FormSubmission:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("上传内容超过 12 MB 限制。")
    if content_type.startswith("application/x-www-form-urlencoded"):
        return FormSubmission(fields=_parse_form(raw), files={})
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("表单必须使用 multipart/form-data。")
    if "\r" in content_type or "\n" in content_type:
        raise ValueError("Invalid multipart content type")
    message = BytesParser(policy=policy.default).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("ascii")
        + raw
    )
    if not message.is_multipart():
        raise ValueError("无法解析上传表单。")
    fields: dict[str, str] = {}
    files: dict[str, UploadedFile] = {}
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not isinstance(field_name, str) or not field_name:
            continue
        payload = part.get_payload(decode=True)
        content = payload if isinstance(payload, bytes) else b""
        filename = part.get_filename()
        if filename is not None:
            files.setdefault(
                field_name,
                UploadedFile(
                    filename=filename,
                    content_type=part.get_content_type(),
                    content=content,
                ),
            )
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            value = content.decode(charset)
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError("表单文字不是有效的 UTF-8。") from exc
        fields.setdefault(field_name, value)
    return FormSubmission(fields=fields, files=files)


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
        ollama_url = (
            form.get("ollama_url", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
        )
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
    if action == "resume-workspace":
        return _run_resume_workspace(form, data_root, repo_root)

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
    workspace = (
        _resume_workspace_result(result.stdout)
        if result.name == "resume-workspace" and result.return_code == 0
        else ""
    )
    return f"""
<section class="card">
  <h2>{_escape(result.name)} · {status}</h2>
  <p>执行耗时：{result.elapsed_ms}ms</p>
  <p>命令：<code>{_escape(result.command)}</code></p>
  <pre class="{banner}">{_escape(body)}</pre>
  {workspace}
</section>
"""


def _resume_workspace_result(raw_stdout: str) -> str:
    prefix = "Resume workspace: "
    path_text = next(
        (line[len(prefix) :] for line in raw_stdout.splitlines() if line.startswith(prefix)), ""
    )
    run_dir = Path(path_text) if path_text else None
    if run_dir is None:
        return ""
    resume = ""
    try:
        resume = (run_dir / "04_resume.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    verification = _load_json_file(run_dir / "07_verification.json") or {}
    gaps = _load_json_file(run_dir / "05_gaps.json") or {}
    run = _load_json_file(run_dir / "run.json") or {}
    route = run.get("route", {})
    if not isinstance(route, dict):
        route = {}
    application_path = route.get("application_library_path", "")
    coverage = verification.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    gap_items = gaps.get("gaps", [])
    if not isinstance(gap_items, list):
        gap_items = []
    gap_text = (
        "\n".join(f"- {item.get('skill', '')}" for item in gap_items if isinstance(item, dict))
        or "- None"
    )
    summary = (
        f"Requirements: {coverage.get('total', 0)} · strong: {coverage.get('strong', 0)} · "
        f"partial: {coverage.get('partial', 0)} · unsupported: {coverage.get('unsupported', 0)} · "
        "critical covered: "
        f"{coverage.get('critical_covered', 0)}/{coverage.get('critical_total', 0)}"
    )
    return f"""<section class=\"graph-card\"><h3>One-page resume preview</h3>
<pre class=\"success\">{_escape(resume)}</pre><h3>Coverage</h3><p>{_escape(summary)}</p>
<h3>Gaps</h3><pre>{_escape(gap_text)}</pre>
<p class=\"muted\">Private artifacts: <code>{_escape(str(run_dir))}</code></p>
<p class=\"muted\">Application library: <code>{_escape(str(application_path))}</code></p>
{_resume_graph(raw_stdout)}</section>"""


def _resume_graph(raw_stdout: str) -> str:
    """Small native renderer for the frozen graph contract; no frontend dependency."""
    prefix = "Resume workspace: "
    path_text = next(
        (line[len(prefix) :] for line in raw_stdout.splitlines() if line.startswith(prefix)), ""
    )
    graph = _load_json_file(Path(path_text) / "06_graph.json") if path_text else None
    if graph is None:
        return ""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return ""
    # JSON is embedded as a JavaScript expression. Escaping a closing script tag avoids
    # breaking the page while preserving JSON.parse-compatible node details.
    node_json = json.dumps(nodes, ensure_ascii=True).replace("</", "<\\/")
    edge_json = json.dumps(edges, ensure_ascii=True).replace("</", "<\\/")
    return f"""<section class="graph-card"><h3>Skill–Evidence Graph</h3>
<p class="muted">点击节点查看详情；双击展开/收起相连节点。</p>
<div style="overflow:auto"><svg id="resume-graph" role="img"></svg></div>
<pre id="graph-detail" class="success">请选择节点。</pre>
<script>
(function(){{
  const nodes={node_json};
  const edges={edge_json};
  const svg=document.getElementById('resume-graph');
  const detail=document.getElementById('graph-detail');
  const hidden=new Set();
  const columns=6;
  const height=Math.max(360,110*Math.ceil(nodes.length/columns)+50);
  svg.setAttribute('viewBox',`0 0 900 ${{height}}`);
  svg.setAttribute('height',height);
  const byId=Object.fromEntries(nodes.map((n,i)=>[
    n.id,
    {{...n,x:80+(i%columns)*155,y:55+Math.floor(i/columns)*105}},
  ]));
  function draw(){{
    svg.innerHTML='';
    edges.forEach(e=>{{
      const a=byId[e.source];
      const b=byId[e.target];
      if(!a||!b||hidden.has(a.id)||hidden.has(b.id))return;
      const line=document.createElementNS('http://www.w3.org/2000/svg','line');
      line.setAttribute('x1',a.x);
      line.setAttribute('y1',a.y);
      line.setAttribute('x2',b.x);
      line.setAttribute('y2',b.y);
      line.setAttribute('stroke','#64748b');
      svg.append(line);
    }});
    Object.values(byId).forEach(n=>{{
      if(hidden.has(n.id))return;
      const group=document.createElementNS('http://www.w3.org/2000/svg','g');
      const circle=document.createElementNS('http://www.w3.org/2000/svg','circle');
      const label=document.createElementNS('http://www.w3.org/2000/svg','text');
      circle.setAttribute('cx',n.x);
      circle.setAttribute('cy',n.y);
      circle.setAttribute('r','31');
      circle.setAttribute('fill','#1d4ed8');
      label.setAttribute('x',n.x);
      label.setAttribute('y',n.y+5);
      label.setAttribute('text-anchor','middle');
      label.setAttribute('fill','white');
      label.setAttribute('font-size','10');
      label.textContent=n.kind;
      group.append(circle,label);
      group.style.cursor='pointer';
      group.onclick=()=>detail.textContent=JSON.stringify(n,null,2);
      group.ondblclick=()=>{{
        edges.filter(e=>e.source===n.id).forEach(e=>{{
          hidden.has(e.target)?hidden.delete(e.target):hidden.add(e.target);
        }});
        draw();
      }};
      svg.append(group);
    }});
  }}
  draw();
}})();
</script></section>"""


def _workspace_path(raw_stdout: str) -> Path | None:
    prefix = "Resume workspace: "
    path_text = next(
        (line[len(prefix) :] for line in raw_stdout.splitlines() if line.startswith(prefix)), ""
    )
    return Path(path_text) if path_text else None


def _user_result_card(result: UIActionResult | None) -> str:
    if result is None:
        return """<section class="result-card empty-state">
  <span class="result-kicker">输出</span>
  <h2>你的针对性简历会出现在这里</h2>
  <p>上传模板并粘贴 JD 后，SoloScale 会在本地提取要求、核对证据、生成预览和 DOCX。</p>
</section>"""
    if result.return_code != 0:
        message = result.stderr or "生成失败，请检查输入后重试。"
        return f"""<section class="result-card error-state" role="alert">
  <span class="result-kicker">需要处理</span>
  <h2>这次没有生成简历</h2>
  <p>{_escape(message)}</p>
</section>"""

    run_dir = _workspace_path(result.stdout)
    if run_dir is None:
        return """<section class="result-card error-state" role="alert">
  <h2>找不到本次生成结果</h2>
</section>"""
    resume = ""
    try:
        resume = (run_dir / "04_resume.md").read_text(encoding="utf-8")
    except OSError:
        pass
    verification = _load_json_file(run_dir / "07_verification.json") or {}
    gaps_payload = _load_json_file(run_dir / "05_gaps.json") or {}
    user_metadata = _load_json_file(run_dir / "09_user_ui.json") or {}
    coverage = verification.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    gap_items = gaps_payload.get("gaps")
    if not isinstance(gap_items, list):
        gap_items = []
    gap_lines = [
        str(item.get("skill", "")).strip()
        for item in gap_items
        if isinstance(item, dict) and str(item.get("skill", "")).strip()
    ]
    gap_html = "".join(f"<li>{_escape(item)}</li>" for item in gap_lines[:8])
    if not gap_html:
        gap_html = "<li>没有发现明确的未覆盖项。</li>"
    download_url = str(user_metadata.get("download_url", ""))
    output_name = str(user_metadata.get("output_filename", "Tailored Resume.docx"))
    internal_path = str(user_metadata.get("internal_docx", run_dir / "08_resume.docx"))
    external_path = str(user_metadata.get("external_docx", ""))
    project_count = user_metadata.get("project_blocks_reordered", 0)
    skill_count = user_metadata.get("skill_bullets_reordered", 0)
    tailored_count = (project_count if isinstance(project_count, int) else 0) + (
        skill_count if isinstance(skill_count, int) else 0
    )
    download = (
        f'<a class="primary-button download" href="{_escape(download_url)}" download '
        f'title="{_escape(output_name)}">下载 DOCX 简历</a>'
        if download_url
        else ""
    )
    return f"""<section class="result-card success-state" aria-live="polite">
  <div class="result-header">
    <div>
      <span class="result-kicker">已完成 · {result.elapsed_ms} ms</span>
      <h2>针对性简历已生成</h2>
      <p>所有候选人陈述均逐字来自上传模板；本次仅按 JD 相关性调整了 {tailored_count} 个位置。</p>
    </div>
    {download}
  </div>
  <div class="metrics" aria-label="覆盖情况">
    <div><strong>{_escape(str(coverage.get("total", 0)))}</strong><span>岗位要求</span></div>
    <div><strong>{_escape(str(coverage.get("strong", 0)))}</strong><span>强证据</span></div>
    <div><strong>{_escape(str(coverage.get("partial", 0)))}</strong><span>部分证据</span></div>
    <div><strong>{_escape(str(coverage.get("unsupported", 0)))}</strong><span>待补证</span></div>
  </div>
  <div class="result-grid">
    <div>
      <h3>简历内容预览</h3>
      <pre class="resume-preview">{_escape(resume or "DOCX 已生成；Markdown 预览不可用。")}</pre>
    </div>
    <aside>
      <h3>建议人工复核</h3>
      <ul class="gap-list">{gap_html}</ul>
      <p class="privacy-note">生成过程仅使用本地模板、JD 和本地 Evidence Store，没有网络调用。</p>
    </aside>
  </div>
  <details>
    <summary>查看自动保存位置</summary>
    <p><strong>SoloScale 私有运行：</strong><code>{_escape(internal_path)}</code></p>
    <p><strong>Resume Applications：</strong><code>{_escape(external_path)}</code></p>
  </details>
</section>"""


def _user_page(
    action_result: UIActionResult | None, data_root: Path, form: dict[str, str]
) -> str:
    job_description = _escape(form.get("job_description", ""))
    company_name = _escape(form.get("company_name", ""))
    company_url = _escape(form.get("company_url", ""))
    job_title = _escape(form.get("job_title", ""))
    job_id = _escape(form.get("job_id", ""))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>SoloScale · 针对性简历</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      color-scheme: light; --ink:#172033; --muted:#657087; --line:#dfe4ec;
      --panel:#ffffff; --accent:#3157d5; --accent-dark:#2444b5; --soft:#eef2ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--ink); background:
      radial-gradient(circle at 8% 4%, #e7ecff 0, transparent 28%),
      linear-gradient(180deg,#f8f9fc 0,#f2f4f8 100%);
      font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    }}
    a {{ color:var(--accent); }}
    .shell {{ max-width:1180px; margin:0 auto; padding:28px 24px 64px; }}
    nav {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:56px; }}
    .brand {{ display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:-.02em; }}
    .brand-mark {{
      width:34px; height:34px; border-radius:11px; display:grid; place-items:center;
      color:white; background:linear-gradient(135deg,#3157d5,#6c4ad8);
      box-shadow:0 8px 22px #3157d533;
    }}
    .advanced-link {{ color:var(--muted); text-decoration:none; font-size:14px; }}
    .hero {{ max-width:780px; margin:0 auto 34px; text-align:center; }}
    .eyebrow,.result-kicker {{
      color:var(--accent); font-size:12px; font-weight:800; letter-spacing:.14em;
      text-transform:uppercase;
    }}
    h1 {{
      margin:12px 0 14px; font-size:clamp(38px,6vw,64px); line-height:1.02;
      letter-spacing:-.055em;
    }}
    .hero p {{
      margin:0 auto; color:var(--muted); font-size:18px; line-height:1.65;
      max-width:680px;
    }}
    .workspace {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:22px; }}
    .input-card,.result-card {{
      background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid #fff;
      border-radius:24px; padding:28px; box-shadow:0 18px 55px #25304b14,0 1px 2px #25304b14;
    }}
    .input-card h2,.result-card h2 {{ margin:7px 0 8px; font-size:25px; letter-spacing:-.025em; }}
    .input-card>p,.result-card p {{ color:var(--muted); line-height:1.55; }}
    form {{ display:grid; gap:18px; margin-top:24px; }}
    label {{ display:grid; gap:8px; font-weight:700; font-size:14px; }}
    .hint {{ color:var(--muted); font-size:12px; font-weight:400; }}
    input,textarea {{
      width:100%; border:1px solid var(--line); border-radius:13px; padding:13px 14px;
      color:var(--ink); background:#fff; font:inherit; font-weight:400; outline:none;
    }}
    input:focus,textarea:focus {{ border-color:var(--accent); box-shadow:0 0 0 4px #3157d51a; }}
    input[type=file] {{ padding:18px; border-style:dashed; background:#fafbff; }}
    textarea {{ min-height:260px; resize:vertical; line-height:1.5; }}
    .metadata {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .primary-button {{
      border:0; border-radius:13px; padding:14px 18px; color:white; background:var(--accent);
      font-weight:800; text-decoration:none; text-align:center; cursor:pointer;
      transition:.18s ease;
    }}
    .primary-button:hover {{ background:var(--accent-dark); transform:translateY(-1px); }}
    .save-note,.privacy-note {{
      padding:12px 14px; border-radius:12px; background:#f6f7fa; color:var(--muted); font-size:13px;
    }}
    #progress {{
      display:none; padding:14px; border-radius:13px; background:var(--soft); color:#263f9d;
    }}
    #progress.visible {{ display:block; }}
    .empty-state {{
      min-height:420px; display:flex; flex-direction:column; justify-content:center;
      text-align:center;
    }}
    .empty-state p {{ max-width:430px; margin:0 auto; }}
    .error-state {{ border:1px solid #ffd2d2; background:#fffafa; }}
    .result-header {{
      display:flex; justify-content:space-between; gap:18px; align-items:flex-start;
    }}
    .download {{ flex:0 0 auto; margin-top:4px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:22px 0; }}
    .metrics div {{
      padding:12px; border:1px solid var(--line); border-radius:13px; text-align:center;
    }}
    .metrics strong {{ display:block; font-size:24px; }}
    .metrics span {{ display:block; color:var(--muted); font-size:11px; margin-top:3px; }}
    .result-grid {{
      display:grid; grid-template-columns:minmax(0,1.45fr) minmax(180px,.55fr); gap:18px;
    }}
    h3 {{ margin:0 0 10px; font-size:15px; }}
    .resume-preview {{
      margin:0; max-height:450px; overflow:auto; white-space:pre-wrap; padding:18px;
      border:1px solid var(--line); border-radius:14px; background:#fbfbfc;
      color:#2d3444; font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
    }}
    .gap-list {{
      margin:0 0 16px; padding-left:20px; color:#4e596f; line-height:1.55; font-size:13px;
    }}
    details {{
      margin-top:18px; border-top:1px solid var(--line); padding-top:15px; font-size:13px;
    }}
    summary {{ cursor:pointer; font-weight:700; }}
    code {{ word-break:break-all; }}
    @media(max-width:900px) {{
      .workspace {{ grid-template-columns:1fr; }} .result-grid {{ grid-template-columns:1fr; }}
      nav {{ margin-bottom:36px; }}
    }}
    @media(max-width:560px) {{
      .shell {{ padding:20px 14px 40px; }}
      .input-card,.result-card {{ padding:21px; border-radius:19px; }}
      .metadata,.metrics {{ grid-template-columns:1fr 1fr; }} .result-header {{ display:block; }}
      .download {{ display:block; margin-top:16px; }} h1 {{ font-size:40px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <nav>
      <div class="brand"><span class="brand-mark">S</span><span>SoloScale</span></div>
      <a class="advanced-link" href="/advanced">开发者工具 →</a>
    </nav>
    <header class="hero">
      <span class="eyebrow">Resume Workspace</span>
      <h1>把真实经历，变成针对这份工作的简历。</h1>
      <p>
        上传你现有的 Word 简历模板，粘贴 Job Description。
        SoloScale 会在本地完成要求提取、证据核对、针对性排序和双重保存。
      </p>
    </header>
    <div class="workspace">
      <section class="input-card">
        <span class="result-kicker">输入</span>
        <h2>简历模板 + Job Description</h2>
        <p>不会改写或虚构你的经历；生成后请做最后一次人工检查。</p>
        <form id="resume-form" method="post" action="/generate" enctype="multipart/form-data">
          <label>现有简历模板（DOCX）
            <span class="hint">保留原来的字体、版式、章节和联系方式，最大 10 MB。</span>
            <input
              type="file" name="resume_template" accept=".docx,{_DOCX_CONTENT_TYPE}" required
            />
          </label>
          <label>Job Description
            <span class="hint">建议粘贴完整 JD，包括 Required、Preferred 和岗位职责。</span>
            <textarea
              name="job_description" required
              placeholder="Paste the full job description here…"
            >{job_description}</textarea>
          </label>
          <div class="metadata">
            <label>公司（可选）<input name="company_name" value="{company_name}" /></label>
            <label>岗位（可选）<input name="job_title" value="{job_title}" /></label>
            <label>Job URL（可选）
              <input type="url" name="company_url" value="{company_url}" />
            </label>
            <label>Job ID（可选）<input name="job_id" value="{job_id}" /></label>
          </div>
          <input
            type="hidden" name="resume_library_root"
            value="{_escape(str(Path.home() / 'Documents' / 'Resume Applications'))}"
          />
          <div class="save-note">
            自动保存两份：SoloScale 私有运行目录 + Documents/Resume Applications。
          </div>
          <div id="progress" role="status" aria-live="polite">正在读取模板并核对 JD…</div>
          <button id="generate-button" class="primary-button" type="submit">生成针对性简历</button>
        </form>
      </section>
      {_user_result_card(action_result)}
    </div>
  </main>
  <script>
    document.getElementById('resume-form').addEventListener('submit',()=>{{
      const progress=document.getElementById('progress');
      const button=document.getElementById('generate-button');
      progress.classList.add('visible');
      button.disabled=true;
      button.textContent='正在生成…';
      window.setTimeout(()=>{{progress.textContent='正在检索本地证据并生成 DOCX…';}},450);
    }});
  </script>
</body>
</html>"""


def _control_tower_section(data_root: Path) -> str:
    exists, _ = _read_control_tower(data_root)
    if not exists:
        return (
            '<section class="card"><h2>Control Tower</h2>'
            "<p>还未生成。请先执行下方 <strong>Build Control Tower</strong>。</p></section>"
        )
    return (
        '<section class="card"><h2>Control Tower</h2>'
        '<p><a href="/control-tower" target="_blank" rel="noopener">'
        "打开 Control Tower</a></p></section>"
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
    candidate_name = _escape(form.get("candidate_name", ""))
    candidate_headline = _escape(form.get("candidate_headline", ""))
    candidate_summary = _escape(form.get("candidate_summary", ""))
    candidate_skills = _escape(form.get("candidate_skills", ""))
    candidate_base_resume = _escape(form.get("candidate_base_resume", ""))
    company_name = _escape(form.get("company_name", ""))
    company_url = _escape(form.get("company_url", ""))
    job_title = _escape(form.get("job_title", ""))
    job_id = _escape(form.get("job_id", ""))
    resume_library_root = _escape(
        form.get(
            "resume_library_root",
            str(Path.home() / "Documents" / "Resume Applications"),
        )
    )
    resume_mode = form.get("resume_mode", ResumeMode.LOCAL_ONLY.value)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>SoloScale Local UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial;
      margin: 0; background: #0f172a; color: #e2e8f0; padding: 20px;
    }}
    .container {{
      max-width: 1120px; margin: 0 auto; display: grid; gap: 16px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 16px; }}
    .full {{ grid-column: 1 / -1; }}
    h1, h2 {{ margin: 0 0 10px; }}
    form {{ display: grid; gap: 8px; margin-top: 8px; }}
    input, textarea, select {{
      width: 100%; box-sizing: border-box; background: #0f172a; color: #e2e8f0;
      border: 1px solid #475569; border-radius: 8px; padding: 8px;
    }}
    button {{
      cursor: pointer; border-radius: 8px; border: 1px solid #475569;
      padding: 10px 12px; background: #1d4ed8; color: #fff; font-weight: 600;
    }}
    pre {{
      background: #0b1120; border: 1px solid #334155; padding: 12px;
      border-radius: 8px; white-space: pre-wrap; max-height: 280px; overflow: auto;
    }}
    .success {{ border-color: #10b981; color: #a7f3d0; }}
    .error {{ border-color: #ef4444; color: #fecaca; }}
    .small {{ color: #94a3b8; font-size: 0.9rem; }}
    label {{ display: grid; gap: 4px; }}
    .row {{ display: grid; gap: 4px; grid-template-columns: 1fr auto; align-items: end; }}
    .muted {{ color: #94a3b8; font-size: 0.9rem; }}
    .home-link {{ display: inline-block; color: #93c5fd; margin-bottom: 14px; }}
  </style>
</head>
<body>
  <a class="home-link" href="/">← 返回简历生成器</a>
  <h1>SoloScale 本地端（简化版）</h1>
  <p class="small">
    这是个人使用最小界面：用于触发本地流程并读取结果。
    Resume Workspace 会保存候选简历，但不会自动申请、更新 Casebook 或发布内容。
  </p>
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
          <textarea
            name="chatgpt_exports"
            rows="2">{_escape(form.get("chatgpt_exports", ""))}</textarea>
        </label>
        <label>
          buildlog-root（逗号分隔，可选）
          <textarea
            name="buildlog_roots"
            rows="2">{_escape(form.get("buildlog_roots", ""))}</textarea>
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
            <option value="codex_session"
              {"selected" if source_kind == "codex_session" else ""}>codex_session</option>
            <option value="buildlog_run"
              {"selected" if source_kind == "buildlog_run" else ""}>buildlog_run</option>
            <option value="chatgpt_conversation"
              {"selected" if source_kind == "chatgpt_conversation" else ""}
            >chatgpt_conversation</option>
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
            <option value="codex_session"
              {"selected" if agent_source_kind == "codex_session" else ""}
            >codex_session</option>
            <option value="buildlog_run"
              {"selected" if agent_source_kind == "buildlog_run" else ""}
            >buildlog_run</option>
            <option value="chatgpt_conversation"
              {"selected" if agent_source_kind == "chatgpt_conversation" else ""}
            >chatgpt_conversation</option>
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
            <option value="codex_session"
              {"selected" if resume_source_kind == "codex_session" else ""}
            >codex_session</option>
            <option value="buildlog_run"
              {"selected" if resume_source_kind == "buildlog_run" else ""}
            >buildlog_run</option>
            <option value="chatgpt_conversation"
              {"selected" if resume_source_kind == "chatgpt_conversation" else ""}
            >chatgpt_conversation</option>
          </select>
        </label>
        <button type="submit">生成 JD 简历草稿</button>
      </form>
    </section>

    <section class="card full">
      <h2>7）Resume Intelligence Workspace v0.1</h2>
      <p class="muted">
        个人 bullet 只来自下方 Candidate Profile；本地检索命中仅作为图谱证据候选。
        Hybrid 在 v0.1 仅保留接口。
      </p>
      <form method="post" action="/run">
        <input type="hidden" name="action" value="resume-workspace" />
        <label>Job Description
          <textarea name="job_description" rows="7">{resume_job_description}</textarea>
        </label>
        <label>公司名称（可选）<input name="company_name" value="{company_name}" /></label>
        <label>岗位名称（推荐）<input name="job_title" value="{job_title}" /></label>
        <label>Job ID（推荐）<input name="job_id" value="{job_id}" /></label>
        <label>Job URL（可选）<input name="company_url" value="{company_url}" /></label>
        <label>Resume Library Root
          <input name="resume_library_root" value="{resume_library_root}" />
        </label>
        <label>Candidate Name（可选）
          <input name="candidate_name" value="{candidate_name}" />
        </label>
        <label>Headline / Summary（可选）
          <input name="candidate_headline" value="{candidate_headline}" />
        </label>
        <label>Professional Summary（可选）
          <textarea name="candidate_summary" rows="2">{candidate_summary}</textarea>
        </label>
        <label>Skills（逗号分隔）
          <input name="candidate_skills" value="{candidate_skills}" />
        </label>
        <label>Base resume / candidate bullets（每行一条，作为 operator-supplied claim）
          <textarea
            name="candidate_base_resume"
            rows="5">{candidate_base_resume}</textarea>
        </label>
        <label>Mode
          <select name="resume_mode">
            <option value="local-only"
              {"selected" if resume_mode == "local-only" else ""}>Local-only</option>
            <option value="hybrid"
              {"selected" if resume_mode == "hybrid" else ""}
            >Hybrid research（provider required）</option>
          </select>
        </label>
        <button type="submit">Generate Resume Workspace</button>
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


def _serve_resume_download(
    handler: BaseHTTPRequestHandler, data_root: Path, run_id: str
) -> None:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        handler.send_error(404, "Resume not found")
        return
    runs_root = (data_root / "resume-runs").resolve()
    run_dir = (runs_root / run_id).resolve()
    target = run_dir / "08_resume.docx"
    if run_dir.parent != runs_root or target.is_symlink() or not target.is_file():
        handler.send_error(404, "Resume not found")
        return
    metadata = _load_json_file(run_dir / "09_user_ui.json") or {}
    filename = str(metadata.get("output_filename", "Tailored_Resume.docx"))
    safe_ascii = _safe_filename_component(Path(filename).stem, "Tailored_Resume") + ".docx"
    content = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", _DOCX_CONTENT_TYPE)
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header(
        "Content-Disposition",
        f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{urllib.parse.quote(filename)}",
    )
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(content)


class SoloScaleLocalUIHandler(BaseHTTPRequestHandler):
    ui_data_root: Path = Path(".soloscale")
    repo_root: Path = _repo_root()
    latest_form: dict[str, str] = {}
    latest_user_form: dict[str, str] = {}

    def _send_advanced_page(self, result: UIActionResult | None) -> None:
        data_root = self.ui_data_root.resolve()
        page = _page(result, data_root, self.latest_form)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_user_page(self, result: UIActionResult | None) -> None:
        data_root = self.ui_data_root.resolve()
        page = _user_page(result, data_root, self.latest_user_form)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path in {"/", ""}:
            self._send_user_page(None)
            return
        if path == "/advanced":
            self._send_advanced_page(None)
            return
        if path == "/control-tower":
            _serve_control_tower(self, self.ui_data_root.resolve())
            return
        download_match = re.fullmatch(r"/downloads/([^/]+)/resume\.docx", path)
        if download_match is not None:
            _serve_resume_download(self, self.ui_data_root.resolve(), download_match.group(1))
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path not in {"/run", "/generate"}:
            self.send_error(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length < 0 or length > MAX_UPLOAD_BYTES:
            self.send_error(413, "Upload is too large")
            return
        body = self.rfile.read(length)
        if path == "/generate":
            try:
                submission = _parse_submission(body, self.headers.get("Content-Type", ""))
            except (UnicodeError, ValueError) as exc:
                result = UIActionResult(
                    "tailored-resume", "local resume generation", 2, "", str(exc), 0
                )
                self._send_user_page(result)
                return
            self.latest_user_form = submission.fields
            data_root = self.ui_data_root.resolve()
            result = _run_user_resume(
                submission.fields,
                submission.files,
                data_root,
                self.repo_root,
            )
            self._send_user_page(result)
            return
        self.latest_form = _parse_form(body)
        data_root = (
            Path(self.latest_form.get("data_root", str(self.ui_data_root))).expanduser().resolve()
        )
        advanced_result = _run_action(self.latest_form, data_root, self.repo_root)
        self._send_advanced_page(advanced_result)


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
