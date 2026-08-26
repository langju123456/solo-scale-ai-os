# ruff: noqa: E501
"""User-facing Work Context over existing private SoloScale stores."""

from __future__ import annotations

import html
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from soloscale.conversation_intake import (
    ConversationIntakeError,
    discover_codex_sources,
    parse_chatgpt_export,
    parse_codex_session,
)
from soloscale.evidence_hub import EvidenceHub, EvidenceHubError
from soloscale.knowledge_models import ParsedSource, SyncReport
from soloscale.knowledge_store import KnowledgeStore, KnowledgeStoreError
from soloscale.ui_shell import (
    DEFAULT_UI_LOCALE,
    UILocale,
    render_app_shell,
    ui_text,
    ui_url,
)


class WorkContextError(ValueError):
    """Raised when an explicitly approved work source cannot be imported safely."""


@dataclass(frozen=True)
class WorkContextSnapshot:
    """Safe counts for product UI; private bodies and locators are deliberately absent."""

    resume_runs: int = 0
    codex_sessions: int = 0
    chatgpt_exports: int = 0
    buildlog_runs: int = 0
    knowledge_documents: int = 0
    reusable_items: int = 0
    project_connected: bool = False
    codex_folder_available: bool = False
    last_synced: bool = False

    @property
    def ai_conversations(self) -> int:
        return self.codex_sessions + self.chatgpt_exports

    @property
    def has_work(self) -> bool:
        return bool(
            self.resume_runs
            or self.knowledge_documents
            or self.project_connected
            or self.reusable_items
        )


@dataclass(frozen=True)
class WorkImportResult:
    """Sanitized import totals suitable for a user-facing success decision."""

    discovered: int
    imported: int
    updated: int
    skipped: int
    failed: int


def _count_private_runs(root: Path, directory_name: str, prefix: str) -> int:
    directory = root / directory_name
    if not directory.is_dir() or directory.is_symlink():
        return 0
    try:
        return sum(
            1
            for child in directory.iterdir()
            if child.name.startswith(prefix) and child.is_dir() and not child.is_symlink()
        )
    except OSError:
        return 0


def load_work_context(
    data_root: Path,
    *,
    workspace_root: Path | None = None,
    home: Path | None = None,
) -> WorkContextSnapshot:
    """Load safe product counts without creating a catalog or reading source bodies."""

    root = Path(data_root)
    knowledge_documents = 0
    codex_sessions = 0
    chatgpt_exports = 0
    buildlog_runs = 0
    last_synced = False
    knowledge_database = root / "knowledge" / "index.sqlite3"
    if knowledge_database.is_file() and not knowledge_database.is_symlink():
        try:
            knowledge_status = KnowledgeStore(root).status()
        except (KnowledgeStoreError, OSError, ValueError):
            knowledge_status = None
        if knowledge_status is not None:
            knowledge_documents = knowledge_status.documents
            codex_sessions = knowledge_status.source_counts.get("codex_session", 0)
            chatgpt_exports = knowledge_status.source_counts.get("chatgpt_export", 0)
            buildlog_runs = knowledge_status.source_counts.get("buildlog_run", 0)
            last_synced = knowledge_status.last_synced_at is not None

    reusable_items = 0
    if EvidenceHub.catalog_exists(root):
        try:
            reusable_items = EvidenceHub(root).status().evidence_count
        except (EvidenceHubError, OSError, ValueError):
            reusable_items = 0

    selected_home = home or Path.home()
    codex_home = selected_home / ".codex"
    codex_folder_available = any(
        (codex_home / name).is_dir() and not (codex_home / name).is_symlink()
        for name in ("sessions", "archived_sessions")
    )
    project_connected = bool(
        workspace_root is not None
        and workspace_root.is_dir()
        and not workspace_root.is_symlink()
        and (workspace_root / ".git").exists()
        and not (workspace_root / ".git").is_symlink()
    )
    return WorkContextSnapshot(
        resume_runs=_count_private_runs(root, "resume-runs", "resume-"),
        codex_sessions=codex_sessions,
        chatgpt_exports=chatgpt_exports,
        buildlog_runs=buildlog_runs,
        knowledge_documents=knowledge_documents,
        reusable_items=reusable_items,
        project_connected=project_connected,
        codex_folder_available=codex_folder_available,
        last_synced=last_synced,
    )


def _import_result(report: SyncReport, *, discovered: int, parse_failures: int = 0) -> WorkImportResult:
    return WorkImportResult(
        discovered=discovered,
        imported=report.imported,
        updated=report.updated,
        skipped=report.skipped,
        failed=report.failed + parse_failures,
    )


def import_codex_history(
    data_root: Path,
    *,
    codex_home: Path | None = None,
) -> WorkImportResult:
    """Import only the standard Codex source selected by an explicit user action."""

    selected_home = Path(codex_home) if codex_home is not None else Path.home() / ".codex"
    try:
        paths = discover_codex_sources(selected_home)
    except (ConversationIntakeError, OSError, ValueError) as exc:
        raise WorkContextError("Codex history could not be inspected safely.") from exc
    if not paths:
        raise WorkContextError("No supported Codex history was found.")

    parsed: list[ParsedSource] = []
    parse_failures = 0
    for path in paths:
        try:
            parsed.append(parse_codex_session(path))
        except (ConversationIntakeError, OSError, ValueError):
            parse_failures += 1
    if not parsed:
        raise WorkContextError("No supported Codex history could be imported.")
    try:
        report = KnowledgeStore(Path(data_root)).sync(parsed)
    except (KnowledgeStoreError, OSError, ValueError) as exc:
        raise WorkContextError("Codex history could not be saved safely.") from exc
    return _import_result(report, discovered=len(paths), parse_failures=parse_failures)


def import_chatgpt_export(data_root: Path, export_path: Path) -> WorkImportResult:
    """Import one user-selected ChatGPT JSON/ZIP without modifying the source file."""

    selected = Path(export_path)
    if (
        not selected.is_file()
        or selected.is_symlink()
        or selected.suffix.casefold() not in {".json", ".zip"}
    ):
        raise WorkContextError("Choose one regular ChatGPT JSON or ZIP export.")
    try:
        parsed = parse_chatgpt_export(selected)
    except (ConversationIntakeError, OSError, ValueError) as exc:
        raise WorkContextError("The selected ChatGPT export could not be imported safely.") from exc
    if not parsed:
        raise WorkContextError("The selected ChatGPT export contains no supported conversations.")
    try:
        report = KnowledgeStore(Path(data_root)).sync(parsed)
    except (KnowledgeStoreError, OSError, ValueError) as exc:
        raise WorkContextError("The selected ChatGPT export could not be saved safely.") from exc
    return _import_result(report, discovered=len(parsed))


def import_chatgpt_export_bytes(
    data_root: Path,
    *,
    filename: str,
    content: bytes,
) -> WorkImportResult:
    """Import a browser-selected export through a private, short-lived local file."""

    suffix = Path(filename).suffix.casefold()
    if suffix not in {".json", ".zip"} or not content:
        raise WorkContextError("Choose one non-empty ChatGPT JSON or ZIP export.")
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix="soloscale-chatgpt-", suffix=suffix)
        temporary_path = Path(raw_path)
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
        return import_chatgpt_export(data_root, temporary_path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def render_work_context_strip(
    snapshot: WorkContextSnapshot,
    locale: UILocale = DEFAULT_UI_LOCALE,
) -> str:
    """Render the compact shared foundation above the three outcome cards."""

    resume = ui_text(locale, "简历 ✓", "Resume ✓") if snapshot.resume_runs else ui_text(locale, "简历待添加", "Add resume")
    projects = ui_text(locale, "1 个项目", "1 project") if snapshot.project_connected else ui_text(locale, "项目待添加", "Add project")
    conversations = ui_text(locale, f"{snapshot.ai_conversations} 个 AI 对话", f"{snapshot.ai_conversations} AI conversations") if snapshot.ai_conversations else ui_text(locale, "AI 对话待添加", "Add AI conversations")
    return f"""<section class="work-context-strip" aria-label="{html.escape(ui_text(locale, '我的工作资料', 'Your work'))}">
  <div><span class="kicker">{html.escape(ui_text(locale, '我的工作资料', 'Your work'))}</span>
  <strong>{html.escape(resume)} · {html.escape(projects)} · {html.escape(conversations)}</strong></div>
  <p>{html.escape(ui_text(locale, '添加一次，在简历、面试和内容中反复使用。', 'Bring it once, then reuse it across applications, interviews, and content.'))}</p>
  <a href="{ui_url('/work', locale)}">{html.escape(ui_text(locale, '添加或管理资料', 'Add or manage work'))}<span aria-hidden="true">→</span></a>
</section>"""


def render_use_my_work(
    snapshot: WorkContextSnapshot,
    locale: UILocale = DEFAULT_UI_LOCALE,
    *,
    boundary: str,
) -> str:
    """Render a truthful, non-configurable summary for one outcome page."""

    if snapshot.has_work:
        summary = ui_text(
            locale,
            f"已添加 {snapshot.knowledge_documents} 份资料、{snapshot.resume_runs} 份简历记录和 {1 if snapshot.project_connected else 0} 个本地项目。",
            f"Available: {snapshot.knowledge_documents} records, {snapshot.resume_runs} resume runs, and {1 if snapshot.project_connected else 0} local projects.",
        )
    else:
        summary = ui_text(
            locale,
            "还没有添加工作资料；你仍然可以先完成当前任务。",
            "No work has been added yet. You can still complete this task first.",
        )
    return f"""<aside class="use-my-work">
  <div><span class="kicker">{html.escape(ui_text(locale, '使用我的工作资料', 'Use my work'))}</span><strong>{html.escape(summary)}</strong></div>
  <p>{html.escape(boundary)}</p>
  <a href="{ui_url('/work', locale)}">{html.escape(ui_text(locale, '管理资料', 'Manage work'))} →</a>
</aside>"""


def work_page(
    *,
    data_root: Path,
    workspace_root: Path | None,
    locale: UILocale = DEFAULT_UI_LOCALE,
    desktop_mode: bool,
    chatgpt_export_selected: bool = False,
    notice: str | None = None,
    error: str | None = None,
) -> str:
    """Render a user-level work source page without internal Evidence terminology."""

    snapshot = load_work_context(data_root, workspace_root=workspace_root)
    resume_status = ui_text(locale, f"{snapshot.resume_runs} 份申请记录", f"{snapshot.resume_runs} application records") if snapshot.resume_runs else ui_text(locale, "还未添加", "Not added yet")
    project_status = ui_text(locale, "已选择 1 个本地 Git 项目", "1 local Git project selected") if snapshot.project_connected else ui_text(locale, "还未选择项目", "No project selected")
    codex_status = ui_text(locale, f"已导入 {snapshot.codex_sessions} 个会话", f"{snapshot.codex_sessions} sessions imported") if snapshot.codex_sessions else ui_text(locale, "这台 Mac 上有可添加的 Codex 资料", "Codex data is available to add on this Mac") if snapshot.codex_folder_available else ui_text(locale, "没有发现标准 Codex 资料目录", "No standard Codex data folder found")
    chatgpt_status = ui_text(locale, f"已导入 {snapshot.chatgpt_exports} 个对话", f"{snapshot.chatgpt_exports} conversations imported") if snapshot.chatgpt_exports else ui_text(locale, "选择官方导出的 JSON 或 ZIP", "Choose an official JSON or ZIP export")
    notice_html = f'<div class="notice" role="status">{html.escape(notice)}</div>' if notice else ""
    error_html = f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ""
    if desktop_mode:
        project_action = (
            f'<a class="source-action" href="soloscale://choose-work-repository">{html.escape(ui_text(locale, "更换本地项目", "Change local project"))}</a>'
            if snapshot.project_connected
            else f'<a class="source-action" href="soloscale://choose-work-repository">{html.escape(ui_text(locale, "选择本地 Git 项目", "Choose a local Git project"))}</a>'
        )
    elif snapshot.project_connected:
        project_action = f'<span class="source-help">{html.escape(ui_text(locale, "本地项目已选择；可在 macOS App 中更换。", "A local project is selected; change it from the macOS app."))}</span>'
    else:
        project_action = f'<span class="source-help">{html.escape(ui_text(locale, "请在 macOS App 中选择目录。", "Choose a folder from the macOS app."))}</span>'
    project_refresh = (
        f'''<div class="project-next"><strong>{html.escape(ui_text(locale, "下一步", "Next step"))}</strong>
        <p>{html.escape(ui_text(locale, "创建一个只包含分支、提交和变更数量的本地项目快照。SoloScale 不会上传项目代码。", "Create a local project snapshot containing only branch, commit, and change counts. SoloScale does not upload your project code."))}</p>
        <form method="post" action="/work/refresh" data-progress-label="{html.escape(ui_text(locale, '正在准备项目快照', 'Preparing project snapshot'))}"><input type="hidden" name="ui_locale" value="{locale}" />
        <button type="submit" data-loading-label="{html.escape(ui_text(locale, '正在准备…', 'Preparing…'))}">{html.escape(ui_text(locale, "准备这个项目", "Prepare this project"))}</button></form></div>'''
        if snapshot.project_connected
        else ""
    )
    if desktop_mode:
        chatgpt_action = (
            f'''<form method="post" action="/work/import-chatgpt"><input type="hidden" name="ui_locale" value="{locale}" />
            <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准读取刚刚选择的导出文件。", "I approve reading the export I just selected."))}</label>
            <button type="submit">{html.escape(ui_text(locale, "导入已选择的文件", "Import selected export"))}</button></form>'''
            if chatgpt_export_selected
            else f'<a class="source-action" href="soloscale://choose-chatgpt-export">{html.escape(ui_text(locale, "选择 ChatGPT 导出文件", "Choose ChatGPT export"))}</a>'
        )
    else:
        chatgpt_action = f'''<form method="post" action="/work/import-chatgpt" enctype="multipart/form-data"><input type="hidden" name="ui_locale" value="{locale}" />
        <input type="file" name="chatgpt_export" accept=".json,.zip,application/json,application/zip" required />
        <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准读取这个导出文件。", "I approve reading this export."))}</label>
        <button type="submit">{html.escape(ui_text(locale, "导入 ChatGPT 记录", "Import ChatGPT history"))}</button></form>'''
    codex_action = (
        f'''<form method="post" action="/work/import-codex"><input type="hidden" name="ui_locale" value="{locale}" />
        <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准这次读取标准 Codex 历史目录。", "I approve reading the standard Codex history folder for this import."))}</label>
        <button type="submit">{html.escape(ui_text(locale, "添加 Codex 历史", "Add Codex history"))}</button></form>'''
        if snapshot.codex_folder_available
        else ""
    )
    body = f"""{notice_html}{error_html}
<section class="privacy-promise">
  <strong>{html.escape(ui_text(locale, '先看见，再授权。', 'See it first. Approve it second.'))}</strong>
  <p>{html.escape(ui_text(locale, 'SoloScale 只检查标准位置是否存在，以及你主动选择了什么。点击导入前不会读取对话正文，也不会扫描整台 Mac；资料不会上传。', 'SoloScale only checks whether standard locations exist and what you explicitly selected. It does not read conversation bodies before import, scan your whole Mac, or upload your work.'))}</p>
</section>
<div class="source-grid">
  <article class="source-card"><span class="source-icon">R</span><div><span class="kicker">{html.escape(ui_text(locale, '简历与文档', 'Resume & documents'))}</span><h2>{html.escape(resume_status)}</h2><p>{html.escape(ui_text(locale, '只使用你确认过的经历生成申请材料。', 'Application drafts use only experience you confirm.'))}</p></div><a class="source-action" href="{ui_url('/resume', locale)}">{html.escape(ui_text(locale, '上传或更新简历', 'Upload or update resume'))}</a></article>
  <article class="source-card"><span class="source-icon project">P</span><div><span class="kicker">{html.escape(ui_text(locale, '本地项目', 'Local projects'))}</span><h2>{html.escape(project_status)}</h2><p>{html.escape(ui_text(locale, '只处理你通过系统选择器明确授权的 Git 项目。', 'Only Git projects you explicitly choose through the system picker are used.'))}</p></div>{project_action}{project_refresh}</article>
  <article class="source-card"><span class="source-icon codex">C</span><div><span class="kicker">Codex</span><h2>{html.escape(codex_status)}</h2><p>{html.escape(ui_text(locale, '导入前只确认标准目录存在；不会读取对话正文。', 'Before approval, SoloScale checks only that the standard folder exists—not conversation bodies.'))}</p></div>{codex_action}</article>
  <article class="source-card"><span class="source-icon chat">AI</span><div><span class="kicker">ChatGPT</span><h2>{html.escape(chatgpt_status)}</h2><p>{html.escape(ui_text(locale, '只读取你主动选择的官方导出文件。', 'Only the official export file you explicitly choose is read.'))}</p></div>{chatgpt_action}</article>
  <article class="source-card muted-card"><span class="source-icon github">GH</span><div><span class="kicker">GitHub</span><h2>{html.escape(ui_text(locale, '稍后连接', 'Connect later'))}</h2><p>{html.escape(ui_text(locale, '本轮不新增 OAuth 或云端连接器；本地 Git 项目现在即可使用。', 'No new OAuth or cloud connector is added in this slice; local Git projects work now.'))}</p></div></article>
  <article class="source-card muted-card"><span class="source-icon file">+</span><div><span class="kicker">{html.escape(ui_text(locale, '其他文件', 'Additional files'))}</span><h2>{html.escape(ui_text(locale, '从简历或导出文件开始', 'Start with a resume or export'))}</h2><p>{html.escape(ui_text(locale, '暂不扫描任意文件夹，也不提供尚未支持的文件连接器。', 'SoloScale does not scan arbitrary folders or advertise unsupported file connectors.'))}</p></div></article>
</div>
<aside class="work-reuse"><strong>{html.escape(ui_text(locale, '添加一次，到处复用。', 'Bring your work once. Reuse it everywhere.'))}</strong><span>{html.escape(ui_text(locale, '同一份真实工作可以帮助你准备申请、面试和公开内容；每个结果仍保留自己的事实与人工确认边界。', 'The same real work can support applications, interview preparation, and public content while each outcome keeps its own truth and approval boundary.'))}</span><a href="{ui_url('/', locale)}">{html.escape(ui_text(locale, '稍后再添加', "I'll add more later"))}</a></aside>"""
    return render_app_shell(
        active="work",
        locale=locale,
        current_url="/work",
        title=f"SoloScale · {ui_text(locale, '我的工作资料', 'Your work')}",
        eyebrow=ui_text(locale, "我的工作资料", "Your work"),
        heading=ui_text(locale, "从你已经完成的工作开始。", "Start with work you have already done."),
        description=ui_text(
            locale,
            "资料添加一次，SoloScale 就能在申请、面试准备和专业内容中反复使用，减少你重复解释。",
            "Bring your resume, projects, and AI-assisted work once so SoloScale can reuse them across applications, interviews, and professional content.",
        ),
        body=body,
        extra_css="""
#main-content{display:grid;gap:18px}.privacy-promise{display:flex;align-items:flex-start;gap:16px;padding:18px 20px;border:1px solid #cfe3da;border-radius:16px;background:linear-gradient(135deg,#f2faf6,#fff)}.privacy-promise strong{min-width:max-content;color:var(--success)}.privacy-promise p{margin:0;color:var(--text-muted)}.source-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.source-card{display:grid;grid-template-columns:auto 1fr;gap:14px;padding:22px;border:1px solid white;border-radius:var(--radius-xl);background:rgba(255,255,255,.9);box-shadow:var(--shadow-card)}.source-card>div{min-width:0}.source-card h2{margin:6px 0;font-size:20px}.source-card p{margin:0;color:var(--text-muted)}.source-icon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:var(--brand-soft);color:var(--brand);font-weight:900}.source-icon.project{background:var(--success-soft);color:var(--success)}.source-icon.chat{background:#f3edfb;color:var(--brand-secondary)}.source-action,.source-card form,.project-next{grid-column:2}.source-action{justify-self:start;font-weight:800;text-decoration:none}.source-card form{display:grid;gap:10px}.source-card form button{justify-self:start}.project-next{display:grid;gap:8px;padding:13px 14px;border:1px solid #cfe3da;border-radius:14px;background:var(--success-soft)}.project-next p{margin:0!important;font-size:12px}.project-next form{grid-column:auto}.source-help,.consent{font-size:12px;color:var(--text-muted)}.consent{display:flex!important;flex-direction:row!important;gap:8px!important;align-items:flex-start!important}.consent input{margin-top:3px}.muted-card{background:rgba(248,249,252,.85);box-shadow:none}.work-reuse{display:flex;align-items:center;gap:14px;padding:18px 20px;border:1px solid var(--border);border-radius:16px;background:rgba(255,255,255,.75)}.work-reuse span{flex:1;color:var(--text-muted)}.work-reuse a{font-weight:800;text-decoration:none;white-space:nowrap}
@media(max-width:820px){.source-grid{grid-template-columns:1fr}.privacy-promise,.work-reuse{flex-direction:column}.source-card{grid-template-columns:auto 1fr}}
""",
    )
