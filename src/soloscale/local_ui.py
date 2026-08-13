from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

from soloscale.buildlog_handoff import (
    BuildLogHandoffError,
    Channel,
    preview_for_buildlog,
    publish_via_buildlog,
)
from soloscale.content_ui import (
    ContentFormResult,
    content_page,
    editorial_publishing_page,
    run_content_form,
)
from soloscale.content_workspace import ContentWorkspaceError, content_download
from soloscale.editorial_publishing_handoff import (
    EditorialChannel,
    EditorialPublishingError,
    editorial_image_preview,
    preview_editorial_day,
    publish_editorial_preview,
)
from soloscale.evidence_hub import EvidenceHubError
from soloscale.evidence_ui import evidence_page, refresh_evidence_catalog
from soloscale.knowledge_models import RetrievalHit
from soloscale.knowledge_store import (
    InvalidKnowledgeQueryError,
    KnowledgeStore,
    KnowledgeStoreError,
)
from soloscale.learning_traceability import (
    DEFAULT_TARGET_REQUIREMENT,
    LearningTraceabilityError,
    load_interview_anchor_pack,
    run_learning_traceability,
    save_learning_response,
)
from soloscale.resume_docx import (
    ResumeTemplateError,
    extract_candidate_profile,
    tailor_resume_docx,
)
from soloscale.resume_models import CandidateProfile, InterviewDefenseRecord, ResumeMode
from soloscale.resume_workspace import (
    ResumeWorkspaceStorageError,
    _atomic_private_write,
    _atomic_private_write_bytes,
    _reject_symlink_ancestry,
    load_interview_defense_records,
    map_interview_defense_bullet,
)
from soloscale.resume_workspace import (
    run_resume_workspace as execute_resume_workspace,
)
from soloscale.video_factory import CreatorVideoError, render_creator_video
from soloscale.video_generation import (
    GoogleVeoClient,
    VideoGenerationError,
    VideoGenerationRequest,
    create_job,
    load_job,
    provider_status,
    save_job,
)

COMMAND_TIMEOUT_SECONDS = 120
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
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


def _video_page(data_root: Path, job_id: str | None = None, error: str | None = None) -> str:
    job = load_job(data_root, job_id) if job_id else None
    configuration = provider_status()
    detail = ""
    if job:
        outgoing = html.escape(json.dumps(job.request.external_payload(), indent=2), quote=True)
        detail = f"""<section>
<h2>External submission preview</h2>
<p>Only this distilled brief and selected excerpts will leave this machine.
Raw chat and Codex histories are excluded.</p>
<pre>{outgoing}</pre>
<p>Status: <strong>{job.status}</strong> · estimated cost:
${job.estimated_cost_usd:.2f}</p>"""
        if job.status == "AWAITING_APPROVAL" and configuration == "READY":
            detail += f"""<form method="post" action="/video/submit/{job.job_id}">
<label>Type PUBLISH to authorize this one Vertex AI generation
<input name="confirmation" required></label>
<button class="primary">Submit to Google Vertex AI</button></form>"""
        elif job.status == "AWAITING_APPROVAL":
            detail += (
                "<p><strong>PROVIDER_NOT_CONFIGURED</strong> · "
                "This private draft is saved and can be submitted later.</p>"
            )
        elif job.status in {"SUBMITTED", "RUNNING"}:
            detail += f"""<form method="post" action="/video/poll/{job.job_id}">
<button class="secondary">Refresh progress</button></form>"""
        elif job.status == "SUCCEEDED" and job.output_path:
            local_video = f"/video/downloads/{job.job_id}/output.mp4"
            detail += f'''<video controls src="{local_video}"></video>
<p><a href="{local_video}" download>Download generated video</a></p>'''
        detail += "</section>"
    message = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Creator Video</title></head><body><main><nav>
<a href="/">Resume</a> · <a href="/learning">Learning</a> ·
<a href="/content">Content Studio</a> · <a href="/evidence">Evidence Center</a> ·
<a href="/video">Creator Video</a> ·
<a href="/publishing">Publishing</a>
</nav><h1>Creator Video</h1>
<p>Google Vertex AI Veo · cloud generation. Provider:
<strong>{configuration}</strong>. Local Remotion remains experimental.</p>
{message}<form method="post" action="/video/prepare">
<label>Topic<input name="topic" required></label>
<label>Script or design document<textarea name="script" required></textarea></label>
<label>Selected evidence IDs (one per line, optional)
<textarea name="evidence_ids"></textarea></label>
<label>Selected evidence excerpts (one per line, optional)
<textarea name="evidence_excerpts"></textarea></label>
<label>SoloScale content run ID (optional)<input name="content_run_id"></label>
<label>Platform<input name="platform" value="Short video"></label>
<label>Language<input name="language" value="English"></label>
<label>Style<input name="style" value="Cinematic product demo"></label>
<button class="primary">Save brief and preview external submission</button>
</form>{detail}</main></body></html>"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _run_jd_resume_draft(
    form: dict[str, str], data_root: Path, repo_root: Path
) -> UIActionResult | None:
    del form, data_root, repo_root
    return UIActionResult(
        name="jd-resume-draft",
        command="jd-resume-draft",
        return_code=2,
        stdout="",
        stderr=(
            "该旧入口已停用：Evidence Agent 结果只能用于证据发现，不能直接生成简历事实。"
            "请使用 Resume Intelligence Workspace，并显式提供 Candidate Profile。"
        ),
        elapsed_ms=0,
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
    job_description = form.get("job_description", "").strip()
    if not job_description:
        return UIActionResult("resume-workspace", "resume-workspace", 2, "", "JD 不能为空。", 0)
    try:
        mode = ResumeMode(form.get("resume_mode", ResumeMode.LOCAL_ONLY.value))
    except ValueError:
        return UIActionResult(
            "resume-workspace",
            "resume-workspace",
            2,
            "",
            "Resume mode 无效。请选择 Local-only 或 Hybrid research。",
            0,
        )
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
        _reject_symlink_ancestry(data_root)
        library_value = form.get("resume_library_root", "").strip()
        library_root = (
            Path(library_value or Path.home() / "Documents" / "Resume Applications")
            .expanduser()
            .absolute()
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
            repository_root=repo_root,
            mode=mode,
        )
    except (KnowledgeStoreError, OSError, ValueError) as exc:
        return UIActionResult("resume-workspace", "local KnowledgeStore search", 1, "", str(exc), 0)
    run_path = data_root / "resume-runs" / run.run_id
    return UIActionResult(
        "resume-workspace", "local KnowledgeStore search", 0, f"Resume workspace: {run_path}", "", 0
    )


def _write_private_json(path: Path, payload: object) -> None:
    _atomic_private_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
    )


def _write_private_bytes(path: Path, content: bytes) -> None:
    _atomic_private_write_bytes(path, content)


def _find_soffice() -> str | None:
    candidates = [
        os.environ.get("SOLOSCALE_SOFFICE"),
        shutil.which("soffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/Applications/LibreOfficeDev.app/Contents/MacOS/soffice",
    ]
    runtime_root = Path.home() / ".cache" / "codex-runtimes"
    try:
        candidates.extend(
            str(path)
            for path in sorted(
                runtime_root.glob("*/dependencies/bin/override/soffice"), reverse=True
            )
        )
    except OSError:
        pass
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _create_resume_pdf_preview(source: Path, target: Path) -> bool:
    """Render a private PDF preview when a local LibreOffice binary is available."""
    soffice = _find_soffice()
    if soffice is None:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="soloscale-resume-preview-") as temp_dir:
            temp_root = Path(temp_dir)
            profile_dir = temp_root / "profile"
            output_dir = temp_root / "output"
            profile_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            completed = subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(source),
                ],
                capture_output=True,
                check=False,
                timeout=30,
            )
            generated = output_dir / f"{source.stem}.pdf"
            if completed.returncode != 0 or not generated.is_file():
                return False
            content = generated.read_bytes()
            if not content.startswith(b"%PDF-"):
                return False
            _write_private_bytes(target, content)
            return True
    except (OSError, subprocess.SubprocessError):
        return False


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


def _bisect_evidence_query(query: str) -> tuple[str, ...]:
    """Split an invalid store query without dropping any JD content."""
    words = query.split()
    if len(words) > 1:
        midpoint = len(words) // 2
        return (" ".join(words[:midpoint]), " ".join(words[midpoint:]))
    if len(query) > 1:
        midpoint = len(query) // 2
        return (query[:midpoint], query[midpoint:])
    return ()


def _search_evidence_query(store: KnowledgeStore, query: str) -> list[RetrievalHit]:
    """Search a complete JD fragment, automatically batching store-safe queries."""
    normalized = " ".join(query.split())
    if not normalized:
        return []
    try:
        return store.search(normalized, limit=3)
    except InvalidKnowledgeQueryError:
        parts = _bisect_evidence_query(normalized)
        if not parts:
            return []
        hits: list[RetrievalHit] = []
        for part in parts:
            hits.extend(_search_evidence_query(store, part))
        return hits


def _search_job_evidence(job_description: str, data_root: Path) -> list[RetrievalHit]:
    store = KnowledgeStore(data_root)
    hits: list[RetrievalHit] = []
    for requirement in job_description.splitlines():
        hits.extend(_search_evidence_query(store, requirement))
    return list({hit.chunk_id: hit for hit in hits}.values())


def _run_user_resume(
    form: dict[str, str],
    files: dict[str, UploadedFile],
    data_root: Path,
    repo_root: Path,
) -> UIActionResult:
    """Run the local upload → evidence → workspace → DOCX flow without a subprocess."""
    started = time.perf_counter()
    job_description = form.get("job_description", "").strip()
    if not job_description:
        return UIActionResult(
            "tailored-resume", "local resume generation", 2, "", "请粘贴完整的 Job Description。", 0
        )
    if form.get("approve_candidate_claims") != "yes":
        return UIActionResult(
            "tailored-resume",
            "local resume generation",
            2,
            "",
            "请确认只使用你上传简历中的真实经历和技能。",
            0,
        )
    tailoring_instructions = form.get("tailoring_instructions", "").strip()
    if len(tailoring_instructions) > 1200:
        return UIActionResult(
            "tailored-resume",
            "local resume generation",
            2,
            "",
            "针对性说明不能超过 1200 个字符。",
            0,
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
        _reject_symlink_ancestry(data_root)
        profile = extract_candidate_profile(upload.content)
        relevance_text = "\n".join(
            part for part in (job_description, tailoring_instructions) if part
        )
        tailored = tailor_resume_docx(upload.content, relevance_text)
        profile_claims = profile.experience_bullets + profile.project_bullets
        approved_claims = [
            {
                "id": f"PROFILE-{index:02d}",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            for index, text in enumerate(profile_claims, start=1)
        ]
        output_name = _user_resume_filename(
            profile,
            form.get("company_name", "").strip() or None,
            form.get("job_title", "").strip() or None,
            job_description,
        )
        library_root = (
            Path(
                form.get("resume_library_root", "").strip()
                or Path.home() / "Documents" / "Resume Applications"
            )
            .expanduser()
            .absolute()
        )
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
            repository_root=repo_root,
            application_resume_bytes=tailored.content,
            application_resume_filename=output_name,
            application_resume_metadata={
                "template_filename": upload_name,
                "template_sha256": tailored.template_sha256,
                "claims_preserved": tailored.claims_preserved,
                "source_paragraph_count": tailored.source_paragraph_count,
                "project_blocks_reordered": tailored.project_blocks_reordered,
                "skill_bullets_reordered": tailored.skill_bullets_reordered,
                "operator_approved_profile_claims_sha256": hashlib.sha256(
                    json.dumps(approved_claims, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "tailoring_instructions_sha256": hashlib.sha256(
                    tailoring_instructions.encode("utf-8")
                ).hexdigest(),
            },
            mode=ResumeMode.LOCAL_ONLY,
        )
        run_dir = data_root / "resume-runs" / run.run_id
        application_value = run.route.get("application_library_path")
        if not isinstance(application_value, str) or not application_value:
            raise OSError("Resume application directory was not created")
        application_dir = Path(application_value)
        internal_docx = run_dir / "08_resume.docx"
        external_docx = application_dir / output_name
        if (
            not internal_docx.is_file()
            or not external_docx.is_file()
            or internal_docx.read_bytes() != tailored.content
            or external_docx.read_bytes() != tailored.content
        ):
            raise OSError("Published resume DOCX failed byte-integrity verification")
        preview_pdf = run_dir / "10_resume_preview.pdf"
        preview_created = _create_resume_pdf_preview(internal_docx, preview_pdf)
        user_metadata: dict[str, object] = {
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
            "preview_url": f"/previews/{run.run_id}/resume.pdf" if preview_created else "",
            "preview_generated": preview_created,
            "network_used": False,
            "tailoring_instructions": tailoring_instructions,
            "operator_approved_profile_claims": approved_claims,
        }
        _write_private_json(run_dir / "09_user_ui.json", user_metadata)
        application_receipt = {
            "schema_version": "1.0",
            "status": "PRIVATE_APPLICATION_DRAFT_SAVED",
            "run_id": run.run_id,
            "operator_approved_profile_claims": approved_claims,
            "tailoring_instructions": tailoring_instructions,
            "tailoring_instructions_sha256": hashlib.sha256(
                tailoring_instructions.encode("utf-8")
            ).hexdigest(),
            "resume_sha256": tailored.output_sha256,
            "application_directory": str(application_dir),
            "final_human_review_required": True,
            "job_application_submitted": False,
        }
        _write_private_json(run_dir / "application_receipt.json", application_receipt)

        run_path = run_dir / "run.json"
        run_payload = _load_json_file(run_path) or {}
        route = run_payload.get("route")
        if not isinstance(route, dict):
            route = {}
            run_payload["route"] = route
        route["user_ui"] = True
        route["docx_saved"] = True
        route["docx_sha256"] = tailored.output_sha256
        route["preview_generated"] = preview_created
        artifact_paths = run_payload.get("artifact_paths")
        if not isinstance(artifact_paths, list):
            artifact_paths = []
            run_payload["artifact_paths"] = artifact_paths
        output_artifacts = ["09_user_ui.json", "application_receipt.json"]
        if preview_created:
            output_artifacts.append("10_resume_preview.pdf")
        for name in output_artifacts:
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


def _run_learning_workspace(
    form: dict[str, str],
    data_root: Path,
    repo_root: Path,
) -> UIActionResult:
    start = time.perf_counter()
    requirement = form.get("target_requirement", "").strip()
    try:
        run = run_learning_traceability(
            data_root=data_root,
            repository_root=repo_root,
            target_requirement=requirement,
        )
    except (LearningTraceabilityError, OSError, ValueError) as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return UIActionResult(
            "learning-traceability",
            "local deterministic traceability build",
            1,
            "",
            str(exc),
            elapsed_ms,
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return UIActionResult(
        "learning-traceability",
        "local deterministic traceability build",
        0,
        f"Learning workspace: {run.private_run_path}",
        "",
        elapsed_ms,
    )


def _save_learning_response(
    form: dict[str, str],
    data_root: Path,
) -> UIActionResult:
    start = time.perf_counter()
    try:
        receipt, receipt_path = save_learning_response(
            data_root=data_root,
            run_id=form.get("run_id", ""),
            stage=form.get("stage", ""),
            response=form.get("response", ""),
        )
    except ValueError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return UIActionResult(
            "learning-response",
            "private local response save",
            2,
            "",
            str(exc),
            elapsed_ms,
        )
    except (LearningTraceabilityError, OSError) as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return UIActionResult(
            "learning-response",
            "private local response save",
            1,
            "",
            str(exc),
            elapsed_ms,
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return UIActionResult(
        "learning-response",
        "private local response save",
        0,
        (
            f"Learning response saved: {receipt_path}\n"
            f"Status: {receipt.status}\n"
            "Mastery advanced: False"
        ),
        "",
        elapsed_ms,
    )


def _learning_response_location(stage: str) -> str:
    stage_slug = {
        "Explain": "explain",
        "Trace": "trace",
    }.get(stage)
    if stage_slug is None:
        raise ValueError("learning response stage is invalid")
    return f"/learning?response_saved={stage_slug}#exercise-{stage_slug}"


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
        (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + raw
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
        f"Requirements: {coverage.get('total', 0)} · strong lexical candidates: "
        f"{coverage.get('lexical_candidate_strong', 0)} · partial lexical candidates: "
        f"{coverage.get('lexical_candidate_partial', 0)} · no lexical candidate: "
        f"{coverage.get('no_lexical_candidate', 0)} · critical with candidate: "
        f"{coverage.get('critical_with_candidate', 0)}/{coverage.get('critical_total', 0)}"
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
    preview_url = str(user_metadata.get("preview_url", ""))
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
    if preview_url:
        preview_action = (
            f'<a class="preview-link" href="{_escape(preview_url)}" target="_blank" '
            'rel="noopener">在新窗口打开</a>'
        )
        preview_content = f"""<div class="resume-pdf-shell">
        <object class="resume-pdf-preview" data="{_escape(preview_url)}" type="application/pdf">
          <p>浏览器无法内嵌 PDF。<a href="{_escape(preview_url)}" target="_blank"
            rel="noopener">打开简历预览</a></p>
        </object>
      </div>"""
        preview_note = "这是最终 DOCX 的本地 PDF 渲染；确认内容和版式后再下载。"
    else:
        preview_action = ""
        fallback_preview = resume or "DOCX 已生成；文字预览不可用。"
        preview_content = f'<pre class="resume-preview">{_escape(fallback_preview)}</pre>'
        preview_note = "本机未找到 DOCX 渲染器，当前显示内容预览；最终版式保留上传模板。"
    defense = _interview_defense_panel(data_root=run_dir.parents[1], run_id=run_dir.name)
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
      <div class="preview-heading"><h3>简历预览</h3>{preview_action}</div>
      {preview_content}
      <p class="preview-note">{_escape(preview_note)}</p>
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
    <p><strong>Application Receipt：</strong>
      <code>{_escape(str(run_dir / "application_receipt.json"))}</code></p>
  </details>
  {defense}
</section>"""


def _available_learning_anchor_pack(
    data_root: Path, repo_root: Path
) -> tuple[str, dict[str, object]] | None:
    root = data_root / "learning-runs"
    if root.is_symlink() or not root.is_dir():
        return None
    for candidate in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            pack = load_interview_anchor_pack(
                data_root=data_root,
                repository_root=repo_root,
                run_id=candidate.name,
            )
        except (LearningTraceabilityError, OSError, ValueError):
            continue
        return candidate.name, pack
    return None


def _validated_record_anchor_pack(
    *,
    record: InterviewDefenseRecord,
    data_root: Path,
    repo_root: Path,
    learning_run_id: str,
) -> dict[str, object]:
    if record.mapping is None or record.mapping.learning_run_id != learning_run_id:
        raise ValueError("mapping mismatch")
    pack = load_interview_anchor_pack(
        data_root=data_root,
        repository_root=repo_root,
        run_id=learning_run_id,
    )
    project = pack.get("project")
    if not isinstance(project, dict) or record.mapping.anchor_pack != pack:
        raise ValueError("mapping anchor pack mismatch")
    if (
        record.mapping.case_id != pack.get("case_id")
        or record.mapping.mapping_basis != "OPERATOR_CONFIRMED"
        or record.mapping.repository != project.get("repository")
        or record.mapping.branch != project.get("branch")
        or record.mapping.commit != project.get("commit")
    ):
        raise ValueError("mapping identity mismatch")
    return pack


def _interview_defense_panel(*, data_root: Path, run_id: str, repo_root: Path | None = None) -> str:
    repo_root = repo_root or _repo_root()
    try:
        records = load_interview_defense_records(data_root=data_root, run_id=run_id)
    except (ResumeWorkspaceStorageError, ValueError):
        return (
            '<section class="interview-defense"><h3>Interview Defense</h3>'
            "<p>Unavailable.</p></section>"
        )
    available = _available_learning_anchor_pack(data_root, repo_root)
    rows: list[str] = []
    for record in records:
        display_status = record.status.value
        if record.mapping is not None and record.status.value == "MAPPED":
            try:
                _validated_record_anchor_pack(
                    record=record,
                    data_root=data_root,
                    repo_root=repo_root,
                    learning_run_id=record.mapping.learning_run_id,
                )
            except (LearningTraceabilityError, OSError, ValueError):
                display_status = "NEEDS_MAPPING"
                action = "映射已失效；请修复原始 Learning run，或重新生成这次简历后再关联。"
            else:
                href = (
                    "/learning?"
                    + urllib.parse.urlencode(
                        {
                            "run_id": record.mapping.learning_run_id,
                            "resume_run_id": run_id,
                            "bullet_id": record.bullet_id,
                        }
                    )
                    + "#interview-defense"
                )
                action = f'<a href="{_escape(href)}">Interview Defense →</a>'
        elif available is not None:
            learning_run, _pack = available
            action = f"""<form method="post" action="/resume/interview-defense/map">
  <input type="hidden" name="resume_run_id" value="{_escape(run_id)}" />
  <input type="hidden" name="bullet_id" value="{_escape(record.bullet_id)}" />
  <input type="hidden" name="learning_run_id" value="{_escape(learning_run)}" />
  <button type="submit">确认关联 Conversation RAG 锚点</button>
</form>"""
        else:
            action = '<a href="/learning">先建立 Learning 黄金案例。</a>'
        rows.append(
            f"<li><code>{_escape(record.bullet_id)}</code> · "
            f"{_escape(display_status)}<br />"
            f"{_escape(record.bullet_text)}<br />{action}</li>"
        )
    if available is None:
        availability = "当前没有可用的 Conversation RAG Learning run。"
    else:
        learning_run, pack = available
        project = pack.get("project")
        if not isinstance(project, dict):
            availability = "当前没有可用的 Conversation RAG Learning run。"
        else:
            availability = (
                "待你确认关联："
                f"{learning_run} · {project.get('repository')} · "
                f"{project.get('branch')} · {project.get('commit')}"
            )
    return (
        '<section class="interview-defense"><h3>Interview Defense</h3>'
        f"<p>{_escape(availability)}</p><ul>{''.join(rows)}</ul></section>"
    )


def _user_page(action_result: UIActionResult | None, data_root: Path, form: dict[str, str]) -> str:
    job_description = _escape(form.get("job_description", ""))
    company_name = _escape(form.get("company_name", ""))
    company_url = _escape(form.get("company_url", ""))
    job_title = _escape(form.get("job_title", ""))
    job_id = _escape(form.get("job_id", ""))
    tailoring_instructions = _escape(form.get("tailoring_instructions", ""))
    workspace_class = (
        "workspace has-result"
        if action_result is not None and action_result.return_code == 0
        else "workspace"
    )
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
    .nav-links {{ display:flex; gap:16px; align-items:center; }}
    .advanced-link {{ color:var(--muted); text-decoration:none; font-size:14px; }}
    .advanced-link.current {{ color:var(--accent); font-weight:800; }}
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
    .workspace.has-result {{ grid-template-columns:minmax(320px,.72fr) minmax(0,1.28fr); }}
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
    .preview-heading {{
      display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:10px;
    }}
    .preview-heading h3 {{ margin:0; }}
    .preview-link {{ font-size:12px; font-weight:700; text-decoration:none; }}
    .resume-pdf-shell {{
      height:680px; overflow:hidden; border:1px solid var(--line); border-radius:14px;
      background:#dfe3ea; box-shadow:inset 0 1px 3px #17203318;
    }}
    .resume-pdf-preview {{ width:100%; height:100%; border:0; background:white; }}
    .preview-note {{ margin:9px 0 0; font-size:12px; }}
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
      .workspace.has-result {{ grid-template-columns:1fr; }}
      nav {{ margin-bottom:36px; }}
    }}
    @media(max-width:560px) {{
      .shell {{ padding:20px 14px 40px; }}
      .input-card,.result-card {{ padding:21px; border-radius:19px; }}
      .metadata,.metrics {{ grid-template-columns:1fr 1fr; }} .result-header {{ display:block; }}
      .download {{ display:block; margin-top:16px; }} h1 {{ font-size:40px; }}
      .resume-pdf-shell {{ height:560px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <nav>
      <div class="brand"><span class="brand-mark">S</span><span>SoloScale</span></div>
      <div class="nav-links">
        <a class="advanced-link current" href="/">Resume</a>
        <a class="advanced-link" href="/learning">Learning</a>
        <a class="advanced-link" href="/content">Content</a>
        <a class="advanced-link" href="/video">Video</a>
        <a class="advanced-link" href="/publishing">Publishing</a>
        <a class="advanced-link" href="/advanced">Advanced</a>
      </div>
    </nav>
    <header class="hero">
      <span class="eyebrow">Resume Workspace</span>
      <h1>把真实经历，变成针对这份工作的简历。</h1>
      <p>
        上传你现有的 Word 简历模板，粘贴 Job Description。
        SoloScale 会在本地完成要求提取、证据核对、针对性排序和双重保存。
      </p>
    </header>
    <div class="{workspace_class}">
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
          <label>针对性说明（可选）
            <span class="hint">例如：突出 RAG、后端工程和产品交付。
              说明只影响已有内容的排序，不会新增经历。</span>
            <textarea name="tailoring_instructions" maxlength="1200"
              placeholder="Prioritize relevant existing projects and skills…"
            >{tailoring_instructions}</textarea>
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
            value="{_escape(str(Path.home() / "Documents" / "Resume Applications"))}"
          />
          <div class="save-note">
            自动保存两份：SoloScale 私有运行目录 + Documents/Resume Applications。
          </div>
          <label><input type="checkbox" name="approve_candidate_claims" value="yes" required />
            我确认上传简历中的经历、项目和技能是真实的，并批准仅使用这些 claims 生成草稿。
          </label>
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


def _latest_learning_run(data_root: Path) -> Path | None:
    runs_root = data_root / "learning-runs"
    if not runs_root.is_dir() or runs_root.is_symlink():
        return None
    candidates = sorted(
        (
            path
            for path in runs_root.iterdir()
            if path.name.startswith("learning-")
            and path.is_dir()
            and not path.is_symlink()
            and (path / "run.json").is_file()
        ),
        key=lambda path: path.name,
    )
    return candidates[-1] if candidates else None


def _learning_graph(run_dir: Path) -> str:
    graph = _load_json_file(run_dir / "04_evidence_graph.json")
    if graph is None:
        return '<p class="error">Graph artifact is unavailable.</p>'
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return '<p class="error">Graph artifact has an invalid shape.</p>'
    node_json = json.dumps(nodes, ensure_ascii=True).replace("</", "<\\/")
    edge_json = json.dumps(edges, ensure_ascii=True).replace("</", "<\\/")
    run_id = json.dumps(run_dir.name)
    return f"""<section class="panel graph-panel">
  <h2>Interactive evidence graph</h2>
  <p class="muted">Click a node for its evidence neighborhood. Double-click to focus it.</p>
  <div class="graph-scroll"><svg id="learning-graph" role="img"></svg></div>
  <div class="detail-grid">
    <pre id="learning-node-detail">Select a Concept, Code, Test, or Decision node.</pre>
    <p><a id="learning-source-link" class="button-link hidden" target="_blank"
      rel="noopener">Open grounded local source</a></p>
  </div>
  <script>
  (function(){{
    const nodes={node_json};
    const edges={edge_json};
    const runId={run_id};
    const svg=document.getElementById('learning-graph');
    const detail=document.getElementById('learning-node-detail');
    const sourceLink=document.getElementById('learning-source-link');
    const columns=5;
    const width=1060;
    const rowHeight=128;
    const height=Math.max(450,rowHeight*Math.ceil(nodes.length/columns)+70);
    svg.setAttribute('viewBox',`0 0 ${{width}} ${{height}}`);
    svg.setAttribute('height',height);
    const byId=Object.fromEntries(nodes.map((node,index)=>[
      node.id,
      {{...node,x:105+(index%columns)*210,y:65+Math.floor(index/columns)*rowHeight}},
    ]));
    let focused=null;
    function related(nodeId){{
      const ids=new Set();
      edges.forEach(edge=>{{
        if(edge.source===nodeId)ids.add(edge.target);
        if(edge.target===nodeId)ids.add(edge.source);
      }});
      return [...ids].map(id=>byId[id]).filter(Boolean).map(node=>({{
        id:node.id,kind:node.kind,label:node.label,
      }}));
    }}
    function traceability(nodeId){{
      const visited=new Set([nodeId]);
      const pending=[nodeId];
      while(pending.length){{
        const current=pending.shift();
        edges.forEach(edge=>{{
          let candidate=null;
          if(edge.source===current)candidate=edge.target;
          if(edge.target===current)candidate=edge.source;
          if(candidate&&!visited.has(candidate)){{visited.add(candidate);pending.push(candidate);}}
        }});
      }}
      visited.delete(nodeId);
      return [...visited].map(id=>byId[id]).filter(Boolean).map(node=>({{
        id:node.id,kind:node.kind,label:node.label,
      }}));
    }}
    function inspect(node){{
      detail.textContent=JSON.stringify({{
        id:node.id,
        kind:node.kind,
        label:node.label,
        truth_stage:node.truth_stage,
        detail:node.detail,
        related:related(node.id),
        traceability:traceability(node.id),
      }},null,2);
      if(node.kind==='CODE'||node.kind==='TEST'){{
        sourceLink.href='/learning/source?run_id='+encodeURIComponent(runId)
          +'&anchor_id='+encodeURIComponent(node.id);
        sourceLink.classList.remove('hidden');
      }}else{{
        sourceLink.classList.add('hidden');
      }}
    }}
    function draw(){{
      svg.innerHTML='';
      edges.forEach(edge=>{{
        const source=byId[edge.source];
        const target=byId[edge.target];
        if(!source||!target)return;
        if(focused&&source.id!==focused&&target.id!==focused)return;
        const line=document.createElementNS('http://www.w3.org/2000/svg','line');
        line.setAttribute('x1',source.x);
        line.setAttribute('y1',source.y);
        line.setAttribute('x2',target.x);
        line.setAttribute('y2',target.y);
        line.setAttribute('stroke','#64748b');
        line.setAttribute('stroke-width','1.5');
        svg.append(line);
      }});
      Object.values(byId).forEach(node=>{{
        if(focused&&node.id!==focused&&!related(focused).some(item=>item.id===node.id))return;
        const group=document.createElementNS('http://www.w3.org/2000/svg','g');
        const box=document.createElementNS('http://www.w3.org/2000/svg','rect');
        const kind=document.createElementNS('http://www.w3.org/2000/svg','text');
        const label=document.createElementNS('http://www.w3.org/2000/svg','text');
        box.setAttribute('x',node.x-84);
        box.setAttribute('y',node.y-34);
        box.setAttribute('width','168');
        box.setAttribute('height','68');
        box.setAttribute('rx','12');
        box.setAttribute('fill',node.kind==='CONCEPT'?'#0f766e':'#1e3a8a');
        box.setAttribute('stroke',node.id===focused?'#fbbf24':'#60a5fa');
        kind.setAttribute('x',node.x);
        kind.setAttribute('y',node.y-8);
        kind.setAttribute('text-anchor','middle');
        kind.setAttribute('fill','#bfdbfe');
        kind.setAttribute('font-size','10');
        kind.textContent=node.kind;
        label.setAttribute('x',node.x);
        label.setAttribute('y',node.y+12);
        label.setAttribute('text-anchor','middle');
        label.setAttribute('fill','white');
        label.setAttribute('font-size','10');
        label.textContent=(node.label||'').slice(0,25);
        group.append(box,kind,label);
        group.style.cursor='pointer';
        group.onclick=()=>inspect(node);
        group.ondblclick=()=>{{focused=focused===node.id?null:node.id;draw();inspect(node);}};
        svg.append(group);
      }});
    }}
    draw();
  }})();
  </script>
</section>"""


def _learning_source_excerpt(
    data_root: Path,
    repo_root: Path,
    run_id: str,
    anchor_id: str,
) -> tuple[str, str]:
    if re.fullmatch(r"learning-[A-Za-z0-9T-]+", run_id) is None:
        raise ValueError("invalid learning run id")
    if re.fullmatch(r"[A-Z0-9-]+", anchor_id) is None:
        raise ValueError("invalid learning anchor id")
    runs_root = data_root / "learning-runs"
    run_dir = runs_root / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("learning run is unavailable")
    anchors_path = run_dir / "03_code_anchors.json"
    if anchors_path.is_symlink():
        raise ValueError("learning anchors are unavailable")
    anchors = _load_json_file(anchors_path)
    if anchors is None:
        raise ValueError("learning anchors are unavailable")
    candidates: list[object] = []
    for key in ("code_anchors", "verification_anchors"):
        value = anchors.get(key, [])
        if isinstance(value, list):
            candidates.extend(value)
    selected = next(
        (item for item in candidates if isinstance(item, dict) and item.get("id") == anchor_id),
        None,
    )
    if not isinstance(selected, dict):
        raise ValueError("learning anchor is unavailable")
    relative_file = selected.get("file")
    line_start = selected.get("line_start")
    line_end = selected.get("line_end")
    if not isinstance(relative_file, str) or not isinstance(line_start, int):
        raise ValueError("learning anchor is invalid")
    if not isinstance(line_end, int) or line_start < 1 or line_end < line_start:
        raise ValueError("learning anchor is invalid")
    if line_end - line_start > 200:
        raise ValueError("learning anchor exceeds the local excerpt limit")
    resolved_root = repo_root.resolve()
    resolved_file = (resolved_root / relative_file).resolve()
    try:
        resolved_file.relative_to(resolved_root)
    except ValueError:
        raise ValueError("learning anchor escapes the repository") from None
    if not resolved_file.is_file() or resolved_file.is_symlink():
        raise ValueError("learning source file is unavailable")
    lines = resolved_file.read_text(encoding="utf-8").splitlines()
    excerpt_start = max(1, line_start - 3)
    excerpt_end = min(len(lines), line_end + 3)
    numbered = "\n".join(
        f"{line_number:>5}  {lines[line_number - 1]}"
        for line_number in range(excerpt_start, excerpt_end + 1)
    )
    title = f"{relative_file}:{line_start}-{line_end}"
    return title, numbered


def _learning_list(value: object) -> str:
    if not isinstance(value, list):
        return "<li>Not available.</li>"
    return "".join(f"<li>{_escape(str(item))}</li>" for item in value)


def _anchor_pack_parts(
    pack: dict[str, object],
) -> tuple[
    dict[str, str],
    list[str],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    project_value = pack.get("project")
    keywords_value = pack.get("keywords")
    reasoning_value = pack.get("reasoning")
    code_value = pack.get("code")
    tests_value = pack.get("tests")
    if not isinstance(project_value, dict):
        raise ValueError("interview anchor project is invalid")
    project = {
        key: value
        for key in ("repository", "branch", "commit")
        if isinstance((value := project_value.get(key)), str) and value
    }
    if set(project) != {"repository", "branch", "commit"}:
        raise ValueError("interview anchor project is invalid")
    if not isinstance(keywords_value, list) or not all(
        isinstance(value, str) for value in keywords_value
    ):
        raise ValueError("interview anchor keywords are invalid")

    def object_list(value: object, label: str) -> list[dict[str, object]]:
        if not isinstance(value, list) or not value:
            raise ValueError(f"interview anchor {label} is invalid")
        result: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"interview anchor {label} is invalid")
            result.append(dict(item))
        return result

    return (
        project,
        list(keywords_value),
        object_list(reasoning_value, "reasoning"),
        object_list(code_value, "code"),
        object_list(tests_value, "tests"),
    )


def _anchor_pack_text(bullet: str, pack: dict[str, object]) -> str:
    project, keywords, reasoning, code, tests = _anchor_pack_parts(pack)
    lines = [
        f"Selected bullet: {bullet}",
        f"Case: {pack['case_id']}",
        f"Keywords: {', '.join(keywords)}",
        (f"Project: {project['repository']} · {project['branch']} · {project['commit']}"),
        "Reasoning:",
    ]
    lines += [f"- {item['path']} · {item['sha256']}" for item in reasoning]
    lines.append("Code:")
    lines += [
        f"- {item['file']}:{item['symbol']}:{item['line_start']}-"
        f"{item['line_end']} · {item['sha256']}"
        for item in code
    ]
    lines.append("Tests:")
    lines += [
        f"- {item['file']}:{item['symbol']}:{item['line_start']}-"
        f"{item['line_end']} · {item['command']} · {item['receipt_state']}"
        for item in tests
    ]
    lines.append(
        "Truth boundary: These anchors locate real implementation and committed test "
        "definitions but do not prove authorship, mastery, semantic claim support, test "
        "execution, or resume eligibility."
    )
    return "\n".join(lines)


def _anchor_pack_html(pack: dict[str, object]) -> str:
    project, keywords, reasoning, code, tests = _anchor_pack_parts(pack)

    def rows(items: list[dict[str, object]], kind: str) -> str:
        rendered: list[str] = []
        for item in items:
            if kind == "reasoning":
                locator = f"{item['path']} · sha256:{item['sha256']}"
            else:
                locator = (
                    f"{item['file']} · {item['symbol']} · "
                    f"L{item['line_start']}-{item['line_end']} · sha256:{item['sha256']}"
                )
                if kind == "tests":
                    locator += f" · {item['command']} · {item['receipt_state']}"
            rendered.append(f"<li><code>{_escape(locator)}</code></li>")
        return "".join(rendered)

    project_text = f"{project['repository']} · {project['branch']} · {project['commit']}"
    return f"""
<p><strong>Project</strong><br /><code>{_escape(project_text)}</code></p>
<p><strong>Keywords</strong><br />{_escape(", ".join(keywords))}</p>
<details open><summary>Reasoning anchors</summary><ul>{rows(reasoning, "reasoning")}</ul></details>
<details><summary>Code anchors</summary><ul>{rows(code, "code")}</ul></details>
<details><summary>Test anchors</summary><ul>{rows(tests, "tests")}</ul></details>
"""


def _learning_page(
    data_root: Path,
    repo_root: Path,
    form: dict[str, str],
    result: UIActionResult | None = None,
) -> str:
    requested_run_id = form.get("run_id", "")
    if requested_run_id:
        run_dir = (
            data_root / "learning-runs" / requested_run_id
            if re.fullmatch(r"learning-\d{8}T\d{6}Z-[0-9a-f]{10}", requested_run_id)
            else None
        )
    else:
        run_dir = _latest_learning_run(data_root)
    if run_dir is not None and (run_dir.is_symlink() or not run_dir.is_dir()):
        run_dir = None
    response_saved_stage = form.get("response_saved_stage", "")
    target_requirement = _escape(form.get("target_requirement", DEFAULT_TARGET_REQUIREMENT))
    result_html = ""
    if result is not None:
        class_name = "success" if result.return_code == 0 else "error"
        body = result.stdout or result.stderr
        result_html = (
            f'<div class="notice {class_name}"><strong>{_escape(body)}</strong>'
            f" · {result.elapsed_ms}ms</div>"
        )
        if result.return_code == 0:
            prefix = "Learning workspace: "
            path_text = next(
                (
                    line[len(prefix) :]
                    for line in result.stdout.splitlines()
                    if line.startswith(prefix)
                ),
                "",
            )
            if path_text:
                run_dir = Path(path_text)
    dashboard = ""
    if run_dir is not None:
        case = _load_json_file(run_dir / "01_case.json") or {}
        mastery = _load_json_file(run_dir / "06_mastery.json") or {}
        learning_plan = _load_json_file(run_dir / "07_learning_plan.json") or {}
        claim = _load_json_file(run_dir / "09_claim_eligibility.json") or {}
        run = _load_json_file(run_dir / "run.json") or {}
        architecture = learning_plan.get("architecture_walkthrough_5_minutes", [])
        deep_dive = learning_plan.get("technical_deep_dive_15_minutes", {})
        decisions = learning_plan.get("decisions_and_trade_offs", [])
        unknowns = learning_plan.get("known_failures_and_unknowns", [])
        exercises = learning_plan.get("exercises", {})
        explain = exercises.get("Explain", {}) if isinstance(exercises, dict) else {}
        trace = exercises.get("Trace", {}) if isinstance(exercises, dict) else {}
        engineering_state = _escape(str(case.get("engineering_state", "UNKNOWN")))
        mastery_level = _escape(str(mastery.get("level", "UNKNOWN")))
        mastery_ready = _escape(str(mastery.get("interview_ready", False)))
        next_action = _escape(str(mastery.get("next_action", "UNKNOWN")))
        engineering_truth = _escape(str(claim.get("engineering_truth_stage", "UNKNOWN")))
        interview_ready = _escape(str(claim.get("interview_ready", False)))
        resume_eligible = _escape(str(claim.get("resume_eligible", False)))
        explain_saved = (
            '<p class="saved-response" role="status">Explain response saved privately. '
            "Review is still required; mastery was not advanced.</p>"
            if response_saved_stage == "explain"
            else ""
        )
        trace_saved = (
            '<p class="saved-response" role="status">Trace response saved privately. '
            "Review is still required; mastery was not advanced.</p>"
            if response_saved_stage == "trace"
            else ""
        )
        backlink = ""
        resume_run_id = form.get("resume_run_id", "")
        bullet_id = form.get("bullet_id", "")
        if resume_run_id or bullet_id:
            try:
                records = load_interview_defense_records(data_root=data_root, run_id=resume_run_id)
                linked = next(item for item in records if item.bullet_id == bullet_id)
                pack = _validated_record_anchor_pack(
                    record=linked,
                    data_root=data_root,
                    repo_root=repo_root,
                    learning_run_id=run_dir.name,
                )
                pack_json = json.dumps(_anchor_pack_text(linked.bullet_text, pack)).replace(
                    "</", "<\\/"
                )
                backlink = f"""<section id="interview-defense" class="panel">
  <h2>Interview Defense anchors</h2>
  <p><strong>{_escape(linked.bullet_id)}</strong> · {_escape(linked.bullet_text)}</p>
  <p>Exact Learning run: <code>{_escape(run_dir.name)}</code></p>
  {_anchor_pack_html(pack)}
  <p class="muted">这些锚点用于定位真实实现，不证明作者身份、掌握程度、语义支持、
  测试执行或简历资格。</p>
  <button id="copy-anchor-pack" type="button">复制锚点包</button>
  <span id="copy-anchor-status" class="muted"></span>
  <script>
    document.getElementById('copy-anchor-pack').addEventListener('click',async()=>{{
      try{{
        await navigator.clipboard.writeText({pack_json});
        document.getElementById('copy-anchor-status').textContent=' 已复制';
      }}catch(_error){{
        document.getElementById('copy-anchor-status').textContent=' 复制不可用';
      }}
    }});
  </script>
</section>"""
            except (
                ResumeWorkspaceStorageError,
                LearningTraceabilityError,
                OSError,
                ValueError,
                StopIteration,
            ):
                backlink = (
                    '<section id="interview-defense" class="panel error">'
                    "<p>Interview Defense mapping is unavailable.</p></section>"
                )
        dashboard = f"""
<section class="status-grid">
  <article class="status-card verified">
    <span>Engineering</span><strong>{engineering_state}</strong>
    <small>Real code + committed test definitions</small>
  </article>
  <article class="status-card warning">
    <span>Human mastery</span><strong>{mastery_level}</strong>
    <small>Interview ready: {mastery_ready}</small>
  </article>
  <article class="status-card action">
    <span>Exact next action</span><strong>{next_action}</strong>
    <small>No automatic promotion</small>
  </article>
</section>
<section class="panel hero-copy">
  <p class="eyebrow">30-second explanation</p>
  <h2>Conversation RAG · Chunking + Retrieval</h2>
  <p>{_escape(str(learning_plan.get("plain_language_30_seconds", "Not available.")))}</p>
  <div class="button-row">
    <a class="button-link" href="#exercise-explain">Start Explain</a>
    <a class="button-link secondary" href="#exercise-trace">Start Trace</a>
  </div>
</section>
{_learning_graph(run_dir)}
{backlink}
<section class="panel">
  <h2>Target JD relevance</h2>
  <p>{_escape(str(claim.get("target_requirement", "Not available.")))}</p>
  <div class="truth-grid">
    <div><span>Engineering truth</span><strong>{engineering_truth}</strong></div>
    <div><span>Interview ready</span><strong>{interview_ready}</strong></div>
    <div><span>Resume eligible</span><strong>{resume_eligible}</strong></div>
  </div>
  <p class="warning-text">{_escape(str(claim.get("rationale", "Not available.")))}</p>
</section>
<details class="panel" open>
  <summary>Architecture · 5 minutes</summary>
  <ol>{_learning_list(architecture)}</ol>
</details>
<details class="panel">
  <summary>Code, evidence, and 15-minute deep dive</summary>
  <pre>{_escape(json.dumps(deep_dive, ensure_ascii=False, indent=2))}</pre>
  <h3>Decisions and trade-offs</h3><ul>{_learning_list(decisions)}</ul>
  <h3>Known failures and unknowns</h3><ul>{_learning_list(unknowns)}</ul>
</details>
<section id="exercise-explain" class="panel exercise">
  <p class="eyebrow">L1 · Explain exercise</p>
  <h2>Start without an answer key</h2>
  <p>{_escape(str(explain.get("prompt", "Not available.")))}</p>
  <p class="muted">Completion requires a user-authored receipt;
    opening this card does not pass L1.</p>
  <form class="response-form" method="post"
    action="/learning/respond#exercise-explain">
    <input type="hidden" name="run_id" value="{_escape(run_dir.name)}" />
    <input type="hidden" name="stage" value="Explain" />
    <input type="hidden" name="target_requirement" value="{target_requirement}" />
    <label for="learning-explain-response">Your Explain response</label>
    <textarea id="learning-explain-response" name="response" rows="8" maxlength="20000"
      required
      placeholder="Explain it. Include one trade-off and one truth boundary."></textarea>
    <button type="submit">Save private Explain response</button>
  </form>
  {explain_saved}
  <p class="muted">Saved locally as pending review. Submission never advances mastery.</p>
</section>
<section id="exercise-trace" class="panel exercise">
  <p class="eyebrow">L2 · Trace exercise</p>
  <h2>Follow the real symbols</h2>
  <p>{_escape(str(trace.get("prompt", "Not available.")))}</p>
  <p class="muted">Use the graph's Code and Test nodes to open bounded local excerpts.</p>
  <form class="response-form" method="post"
    action="/learning/respond#exercise-trace">
    <input type="hidden" name="run_id" value="{_escape(run_dir.name)}" />
    <input type="hidden" name="stage" value="Trace" />
    <input type="hidden" name="target_requirement" value="{target_requirement}" />
    <label for="learning-trace-response">Your Trace response</label>
    <textarea id="learning-trace-response" name="response" rows="8" maxlength="20000"
      required
      placeholder="Trace the recorded symbols and tests. Name any remaining unknowns."></textarea>
    <button type="submit">Save private Trace response</button>
  </form>
  {trace_saved}
  <p class="muted">Saved locally as pending review. Submission never advances mastery.</p>
</section>
<section class="panel footnote">
  <strong>Private run</strong> <code>{_escape(str(run_dir))}</code><br />
  <span>Branch {_escape(str(run.get("branch", "")))} ·
    commit {_escape(str(run.get("commit", "")))}</span>
</section>
"""
    else:
        dashboard = """
<section class="panel empty-state">
  <h2>No Learning Traceability run yet</h2>
  <p>Build the one bounded Conversation RAG golden case below.</p>
</section>
"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SoloScale Learning Control Tower</title>
  <style>
    :root {{ color-scheme: dark; --panel:#111827; --line:#334155; --muted:#94a3b8; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:#07111f; color:#e5edf7; }}
    header {{ padding:28px max(24px,calc((100vw - 1180px)/2)); border-bottom:1px solid var(--line);
      background:linear-gradient(135deg,#0f172a,#112a46); }}
    header a {{ color:#93c5fd; }}
    main {{ max-width:1180px; margin:0 auto; padding:24px; display:grid; gap:18px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:16px;
      padding:20px; }}
    .build-form {{ display:grid; grid-template-columns:1fr auto; gap:12px; align-items:end; }}
    label {{ display:grid; gap:7px; color:var(--muted); }}
    input,textarea {{ width:100%; border:1px solid #475569; border-radius:10px; padding:12px;
      background:#081321; color:white; }}
    textarea {{ resize:vertical; min-height:150px; line-height:1.55; }}
    button,.button-link {{ display:inline-block; border:0; border-radius:10px; padding:11px 15px;
      background:#2563eb; color:white; font-weight:700; text-decoration:none; cursor:pointer; }}
    .button-link.secondary {{ background:#0f766e; }}
    .button-row {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .notice {{ border:1px solid; border-radius:12px; padding:12px; }}
    .success {{ border-color:#10b981; color:#a7f3d0; }}
    .error {{ border-color:#ef4444; color:#fecaca; }}
    .saved-response {{ border-left:3px solid #10b981; padding:9px 12px;
      background:#052e2b; color:#a7f3d0; border-radius:8px; }}
    .status-grid,.truth-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
    .status-card,.truth-grid div {{ border:1px solid var(--line); border-radius:14px; padding:16px;
      background:#0b1525; display:grid; gap:7px; }}
    .status-card span,.truth-grid span,.eyebrow {{ color:var(--muted); text-transform:uppercase;
      letter-spacing:.09em; font-size:.75rem; }}
    .status-card strong {{ font-size:1.15rem; }} .verified strong {{ color:#6ee7b7; }}
    .warning strong,.warning-text {{ color:#fcd34d; }} .action strong {{ color:#93c5fd; }}
    .hero-copy p:not(.eyebrow) {{ max-width:820px; font-size:1.08rem; line-height:1.7; }}
    .graph-scroll {{ overflow:auto; background:#081321; border-radius:12px; }}
    #learning-graph {{ width:1060px; display:block; }}
    pre {{ white-space:pre-wrap; overflow:auto; padding:14px; background:#081321;
      border-radius:10px; color:#c7d2fe; }}
    details summary {{ cursor:pointer; font-size:1.1rem; font-weight:700; }}
    li {{ margin:.55rem 0; line-height:1.55; }} .muted,.footnote span {{ color:var(--muted); }}
    .hidden {{ display:none; }} code {{ color:#bae6fd; word-break:break-all; }}
    @media(max-width:760px) {{
      .status-grid,.truth-grid,.build-form {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <header><a href="/">Resume</a> · <a href="/content">Content Studio</a> ·
    <a href="/video">Creator Video</a> ·
    <a href="/publishing">Publishing</a> ·
    <a href="/advanced">Advanced</a><h1>Learning Control Tower</h1>
    <p>One real capability. Evidence first. Mastery stays human-earned.</p></header>
  <main>
    <form class="panel build-form" method="post" action="/learning/run">
      <label>Current target-JD requirement
        <input name="target_requirement" value="{target_requirement}" required />
      </label>
      <button type="submit">Build / refresh golden case</button>
    </form>
    {result_html}
    {dashboard}
  </main>
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
    <a href="/learning">Open Learning Control Tower</a> ·
    <a href="/content">Open Content Studio</a> · <a href="/evidence">Open Evidence Center</a>.
    <a href="/video">Open Creator Video</a>.
    <a href="/publishing">Open Publishing</a>.
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
            <option value="chatgpt_export"
              {"selected" if source_kind == "chatgpt_export" else ""}
            >chatgpt_export</option>
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
            <option value="chatgpt_export"
              {"selected" if agent_source_kind == "chatgpt_export" else ""}
            >chatgpt_export</option>
          </select>
        </label>
        <button type="submit">Run evidence-agent</button>
      </form>
    </section>

    <section class="card full">
      <h2>6）Evidence discovery（旧 JD 简历入口已停用）</h2>
      <p class="muted">
        Evidence Agent 的 claims 只能作为待人工核验的证据发现结果，不能直接变成简历事实。
        请使用下方 Resume Intelligence Workspace，并显式提供 Candidate Profile。
      </p>
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


def _resume_run_artifact(data_root: Path, run_id: str, filename: str) -> Path | None:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        return None
    runs_root = (data_root / "resume-runs").resolve()
    run_dir = (runs_root / run_id).resolve()
    target = run_dir / filename
    if run_dir.parent != runs_root or target.is_symlink() or not target.is_file():
        return None
    return target


def _serve_resume_download(handler: BaseHTTPRequestHandler, data_root: Path, run_id: str) -> None:
    target = _resume_run_artifact(data_root, run_id, "08_resume.docx")
    if target is None:
        handler.send_error(404, "Resume not found")
        return
    run_dir = target.parent
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


def _serve_resume_preview(handler: BaseHTTPRequestHandler, data_root: Path, run_id: str) -> None:
    target = _resume_run_artifact(data_root, run_id, "10_resume_preview.pdf")
    if target is None:
        handler.send_error(404, "Resume preview not found")
        return
    content = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header("Content-Disposition", 'inline; filename="resume-preview.pdf"')
    handler.send_header("Cache-Control", "private, no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(content)


def _serve_learning_source(
    handler: BaseHTTPRequestHandler,
    data_root: Path,
    repo_root: Path,
) -> None:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    run_id = query.get("run_id", [""])[0]
    anchor_id = query.get("anchor_id", [""])[0]
    try:
        title, excerpt = _learning_source_excerpt(
            data_root,
            repo_root,
            run_id,
            anchor_id,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        handler.send_error(404, str(exc))
        return
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{_escape(title)}</title><style>
body{{margin:0;padding:24px;background:#07111f;color:#e5edf7;font-family:ui-monospace,monospace}}
a{{color:#93c5fd}} pre{{white-space:pre;overflow:auto;background:#111827;border:1px solid #334155;
border-radius:12px;padding:18px;line-height:1.55}}</style></head><body>
<p><a href="/learning">← Learning Control Tower</a></p><h1>{_escape(title)}</h1>
<p>Read-only bounded excerpt from a recorded anchor.</p>
<pre>{_escape(excerpt)}</pre></body></html>"""
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
    latest_user_form: dict[str, str] = {}
    latest_learning_form: dict[str, str] = {}
    latest_content_form: dict[str, str] = {}

    def _video_data_root(self) -> Path:
        return self.ui_data_root.absolute() / "video"

    def _send_advanced_page(self, result: UIActionResult | None) -> None:
        data_root = self.ui_data_root.absolute()
        page = _page(result, data_root, self.latest_form)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_user_page(self, result: UIActionResult | None) -> None:
        data_root = self.ui_data_root.absolute()
        page = _user_page(result, data_root, self.latest_user_form)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_learning_page(
        self, result: UIActionResult | None, response_saved_stage: str | None = None
    ) -> None:
        data_root = self.ui_data_root.absolute()
        display_form = dict(self.latest_learning_form)
        if response_saved_stage is not None:
            display_form["response_saved_stage"] = response_saved_stage
        page = _learning_page(data_root, self.repo_root, display_form, result)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_content_page(
        self,
        *,
        run_id: str | None = None,
        result: ContentFormResult | None = None,
    ) -> None:
        page = content_page(
            data_root=self.ui_data_root.absolute(),
            form=self.latest_content_form,
            run_id=run_id,
            error=result.error if result is not None else None,
        )
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_evidence_page(self) -> None:
        body = evidence_page(self.ui_data_root.absolute()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_video_page(self, job_id: str | None = None, error: str | None = None) -> None:
        try:
            page = _video_page(self._video_data_root(), job_id, error)
        except VideoGenerationError:
            page = _video_page(self._video_data_root(), None, "Video job is unavailable")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        if path == "/learning":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self.latest_learning_form = {key: values[0] for key, values in query.items() if values}
            saved_stage = query.get("response_saved", [""])[0]
            self._send_learning_page(
                None, saved_stage if saved_stage in {"explain", "trace"} else None
            )
            return
        if path == "/content":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            run_id = query.get("run_id", [""])[0]
            self._send_content_page(run_id=run_id or None)
            return
        if path == "/evidence":
            self._send_evidence_page()
            return
        if path == "/video":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._send_video_page(query.get("job_id", [None])[0])
            return
        if path == "/publishing":
            body = editorial_publishing_page(data_root=self.ui_data_root.absolute()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        editorial_image_match = re.fullmatch(
            r"/publishing/editorial/(linkedin|x)/image", path
        )
        if editorial_image_match is not None:
            try:
                content = editorial_image_preview(
                    self.ui_data_root.absolute(),
                    cast(EditorialChannel, editorial_image_match.group(1)),
                )
            except (EditorialPublishingError, OSError):
                self.send_error(404, "Editorial image preview not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        video_download_match = re.fullmatch(
            r"/video/downloads/(video-[a-f0-9]{12})/output\.mp4", path
        )
        if video_download_match is not None:
            try:
                job = load_job(self._video_data_root(), video_download_match.group(1))
                output = Path(job.output_path or "")
                if not output.is_file() or output.is_symlink():
                    raise VideoGenerationError("video output is unavailable")
                content = output.read_bytes()
            except (OSError, VideoGenerationError):
                self.send_error(404, "Video output not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", 'attachment; filename="output.mp4"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        content_download_match = re.fullmatch(r"/content/downloads/([^/]+)/([^/]+)", path)
        if content_download_match is not None:
            try:
                filename, content = content_download(
                    self.ui_data_root.absolute(),
                    content_download_match.group(1),
                    content_download_match.group(2),
                )
            except ContentWorkspaceError:
                self.send_error(404, "Content artifact not found")
                return
            content_type = (
                "application/json"
                if filename.endswith(".json")
                else "video/mp4"
                if filename.endswith(".mp4")
                else "text/markdown; charset=utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{content_download_match.group(2)}"',
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/learning/source":
            _serve_learning_source(self, self.ui_data_root.absolute(), self.repo_root)
            return
        if path == "/control-tower":
            _serve_control_tower(self, self.ui_data_root.absolute())
            return
        download_match = re.fullmatch(r"/downloads/([^/]+)/resume\.docx", path)
        if download_match is not None:
            _serve_resume_download(self, self.ui_data_root.absolute(), download_match.group(1))
            return
        preview_match = re.fullmatch(r"/previews/([^/]+)/resume\.pdf", path)
        if preview_match is not None:
            _serve_resume_preview(self, self.ui_data_root.absolute(), preview_match.group(1))
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/evidence/refresh":
            try:
                receipt = refresh_evidence_catalog(
                    self.ui_data_root.absolute(), repository_root=self.repo_root
                )
            except (EvidenceHubError, OSError, ValueError):
                location = "/evidence?refresh=failed"
            else:
                location = (
                    "/evidence?refresh=complete"
                    if receipt.status.value == "succeeded"
                    else "/evidence?refresh=failed"
                )
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/video/prepare":
            length = int(self.headers.get("Content-Length", "0") or 0)
            form = _parse_form(self.rfile.read(length))
            try:
                request = VideoGenerationRequest(
                    topic=form.get("topic", ""),
                    script=form.get("script", ""),
                    platform=form.get("platform", "Short video"),
                    language=form.get("language", "English"),
                    style=form.get("style", "Cinematic product demo"),
                    content_run_id=form.get("content_run_id", "").strip() or None,
                    evidence_ids=[
                        x.strip() for x in form.get("evidence_ids", "").splitlines() if x.strip()
                    ],
                    evidence_excerpts=[
                        x.strip()
                        for x in form.get("evidence_excerpts", "").splitlines()
                        if x.strip()
                    ],
                )
                job = create_job(self._video_data_root(), request)
            except (VideoGenerationError, ValueError, OSError) as exc:
                self._send_video_page(error=str(exc))
                return
            self.send_response(303)
            self.send_header("Location", f"/video?job_id={job.job_id}")
            self.end_headers()
            return
        submit_match = re.fullmatch(r"/video/submit/(video-[a-f0-9]{12})", path)
        poll_match = re.fullmatch(r"/video/poll/(video-[a-f0-9]{12})", path)
        if submit_match or poll_match:
            matched_video_action = submit_match or poll_match
            assert matched_video_action is not None
            job_id = matched_video_action.group(1)
            form = _parse_form(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)))
            try:
                job = load_job(self._video_data_root(), job_id)
                if submit_match:
                    if form.get("confirmation") != "PUBLISH":
                        raise VideoGenerationError("Type PUBLISH to authorize the external request")
                    if job.estimated_cost_usd > 1.0:
                        raise VideoGenerationError(
                            "Estimated cost exceeds the authorized $1.00 limit"
                        )
                    job = GoogleVeoClient().submit(job)
                else:
                    job = GoogleVeoClient().poll(job, data_root=self._video_data_root())
                save_job(self._video_data_root(), job)
            except (VideoGenerationError, OSError, ValueError) as exc:
                self._send_video_page(job_id, str(exc))
                return
            self.send_response(303)
            self.send_header("Location", f"/video?job_id={job_id}")
            self.end_headers()
            return
        if path == "/content/generate":
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self.send_error(400, "Invalid Content-Length")
                return
            if length < 0 or length > MAX_UPLOAD_BYTES:
                self.send_error(413, "Content brief is too large")
                return
            self.latest_content_form = _parse_form(self.rfile.read(length))
            content_result = run_content_form(
                self.latest_content_form, self.ui_data_root.absolute()
            )
            if content_result.run_id is None:
                self._send_content_page(result=content_result)
                return
            self.send_response(303)
            self.send_header(
                "Location",
                "/content?"
                + urllib.parse.urlencode({"run_id": content_result.run_id})
                + "#results",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content_render_match = re.fullmatch(r"/content/render/(content-[^/]+)", path)
        if content_render_match is not None:
            run_id = content_render_match.group(1)
            try:
                render_creator_video(
                    data_root=self.ui_data_root.absolute(),
                    run_id=run_id,
                    repository_root=self.repo_root,
                )
            except (ContentWorkspaceError, CreatorVideoError, OSError, subprocess.TimeoutExpired):
                self.send_error(422, "Creator Video render failed")
                return
            self.send_response(303)
            location = "/content?" + urllib.parse.urlencode({"run_id": run_id}) + "#results"
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/publishing/editorial/preview":
            length = int(self.headers.get("Content-Length", "0") or 0)
            form = _parse_form(self.rfile.read(length))
            channel = form.get("channel", "")
            try:
                if channel not in {"linkedin", "x"}:
                    raise EditorialPublishingError("select LinkedIn or X")
                preview_editorial_day(
                    data_root=self.ui_data_root.absolute(),
                    day_directory=Path(form.get("day_directory", "")),
                    channel=cast(EditorialChannel, channel),
                )
            except (EditorialPublishingError, OSError):
                self.send_error(422, "Editorial package preview failed")
                return
            self.send_response(303)
            self.send_header("Location", "/publishing")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        editorial_publish_match = re.fullmatch(r"/publishing/editorial/(linkedin|x)/publish", path)
        if editorial_publish_match is not None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            form = _parse_form(self.rfile.read(length))
            try:
                publish_editorial_preview(
                    data_root=self.ui_data_root.absolute(),
                    channel=cast(EditorialChannel, editorial_publish_match.group(1)),
                    confirmation=form.get("confirmation", ""),
                )
            except (EditorialPublishingError, OSError):
                self.send_error(422, "Editorial publication failed")
                return
            self.send_response(303)
            self.send_header("Location", "/publishing")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        buildlog_match = re.fullmatch(
            r"/content/buildlog/(content-[^/]+)/(linkedin|x)(/publish)?", path
        )
        if buildlog_match is not None:
            run_id, channel, publish_path = buildlog_match.groups()
            if channel not in {"linkedin", "x"}:
                self.send_error(404)
                return
            channel = cast(Channel, channel)
            try:
                if publish_path:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                    publish_form = _parse_form(self.rfile.read(length))
                    publish_via_buildlog(
                        data_root=self.ui_data_root.absolute(),
                        run_id=run_id,
                        channel=channel,
                        confirmation=publish_form.get("confirmation", ""),
                    )
                    query = {"run_id": run_id, "buildlog": "published"}
                else:
                    preview_for_buildlog(
                        data_root=self.ui_data_root.absolute(),
                        run_id=run_id,
                        channel=channel,
                    )
                    query = {"run_id": run_id, "buildlog": "previewed"}
            except (
                BuildLogHandoffError,
                ContentWorkspaceError,
                OSError,
                subprocess.TimeoutExpired,
            ):
                self.send_error(422, "BuildLog handoff failed")
                return
            self.send_response(303)
            self.send_header("Location", "/content?" + urllib.parse.urlencode(query) + "#results")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/learning/run":
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length)
            self.latest_learning_form = _parse_form(body)
            self._send_learning_page(
                _run_learning_workspace(
                    self.latest_learning_form, self.ui_data_root.absolute(), self.repo_root
                )
            )
            return
        if path == "/learning/respond":
            length = int(self.headers.get("Content-Length", "0") or 0)
            response_form = _parse_form(self.rfile.read(length))
            self.latest_learning_form = {
                "target_requirement": response_form.get(
                    "target_requirement",
                    self.latest_learning_form.get("target_requirement", DEFAULT_TARGET_REQUIREMENT),
                )
            }
            learning_result = _save_learning_response(response_form, self.ui_data_root.absolute())
            if learning_result.return_code == 0:
                self.send_response(303)
                self.send_header(
                    "Location", _learning_response_location(response_form.get("stage", ""))
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_learning_page(learning_result)
            return
        if path == "/resume/interview-defense/map":
            length = int(self.headers.get("Content-Length", "0") or 0)
            mapping_form = _parse_form(self.rfile.read(length))
            try:
                map_interview_defense_bullet(
                    data_root=self.ui_data_root.absolute(),
                    repository_root=self.repo_root,
                    resume_run_id=mapping_form.get("resume_run_id", ""),
                    bullet_id=mapping_form.get("bullet_id", ""),
                    learning_run_id=mapping_form.get("learning_run_id", ""),
                )
            except (ResumeWorkspaceStorageError, LearningTraceabilityError, OSError, ValueError):
                self.send_error(400, "Interview Defense mapping could not be validated")
                return
            self.send_response(303)
            self.send_header(
                "Location",
                "/learning?"
                + urllib.parse.urlencode(
                    {
                        "run_id": mapping_form.get("learning_run_id", ""),
                        "resume_run_id": mapping_form.get("resume_run_id", ""),
                        "bullet_id": mapping_form.get("bullet_id", ""),
                    }
                )
                + "#interview-defense",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
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
            data_root = self.ui_data_root.absolute()
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
            Path(self.latest_form.get("data_root", str(self.ui_data_root))).expanduser().absolute()
        )
        advanced_result = _run_action(self.latest_form, data_root, self.repo_root)
        self._send_advanced_page(advanced_result)


def main() -> None:
    parser = argparse.ArgumentParser(description="SoloScale minimal local UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-root",
        default=str(Path.home() / "Documents" / "SoloScaleData"),
        help="SoloScale private data root (default: ~/Documents/SoloScaleData)",
    )
    args = parser.parse_args()

    handler = SoloScaleLocalUIHandler
    handler.ui_data_root = Path(args.data_root).expanduser().absolute()
    handler.repo_root = _repo_root()

    server = HTTPServer((args.host, args.port), handler)
    print(f"SoloScale local UI: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
