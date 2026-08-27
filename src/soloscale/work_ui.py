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
    SourceState,
    UILocale,
    render_app_shell,
    render_source_state,
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
    project_name: str | None = None
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
    project_name = workspace_root.name if project_connected and workspace_root else None
    return WorkContextSnapshot(
        resume_runs=_count_private_runs(root, "resume-runs", "resume-"),
        codex_sessions=codex_sessions,
        chatgpt_exports=chatgpt_exports,
        buildlog_runs=buildlog_runs,
        knowledge_documents=knowledge_documents,
        reusable_items=reusable_items,
        project_connected=project_connected,
        project_name=project_name,
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


def _source_row(
    *,
    key: str,
    state: SourceState,
    locale: UILocale,
    name: str,
    summary: str,
    detail: str = "",
    actions: str = "",
) -> str:
    """Render one compact source row with a shared semantic state."""

    detail_html = f"<p>{html.escape(detail)}</p>" if detail else ""
    actions_html = f'<div class="source-actions">{actions}</div>' if actions else ""
    return f'''<article class="source-row" data-source="{html.escape(key)}">
  {render_source_state(state, locale)}
  <div class="source-copy"><h3>{html.escape(name)}</h3><strong>{html.escape(summary)}</strong>{detail_html}</div>
  {actions_html}
</article>'''


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
    """Render the truthful Work Context onboarding and source setup page."""

    snapshot = load_work_context(data_root, workspace_root=workspace_root)
    notice_html = f'<div class="notice" role="status">{html.escape(notice)}</div>' if notice else ""
    error_html = f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ""

    def processing_form_attributes(source: str, copy: str) -> str:
        return (
            f'data-processing-source="{html.escape(source)}" '
            f'data-processing-copy="{html.escape(copy)}" '
            f'data-progress-label="{html.escape(copy)}"'
        )

    codex_name = "Codex"
    codex_processing = ui_text(
        locale,
        "正在建立本地索引 · 完成后本页会自动更新",
        "Building the local index · this page will update when complete",
    )
    codex_action = ""
    if snapshot.codex_folder_available:
        codex_action = f'''<form method="post" action="/work/import-codex" {processing_form_attributes(codex_name, codex_processing)}>
  <input type="hidden" name="ui_locale" value="{locale}" />
  <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准这次读取标准 Codex 历史目录。", "I approve reading the standard Codex history folder for this import."))}</label>
  <button class="source-control" type="submit" data-loading-label="{html.escape(ui_text(locale, '开始处理…', 'Starting…'))}">{html.escape(ui_text(locale, "更新 Codex 历史" if snapshot.codex_sessions else "添加 Codex 历史", "Update Codex history" if snapshot.codex_sessions else "Add Codex history"))}</button>
</form>'''

    chatgpt_name = "ChatGPT Export"
    chatgpt_processing = ui_text(
        locale,
        "正在导入你选择的文件 · 完成后本页会自动更新",
        "Importing the file you selected · this page will update when complete",
    )
    if desktop_mode and chatgpt_export_selected:
        chatgpt_action = f'''<form method="post" action="/work/import-chatgpt" {processing_form_attributes(chatgpt_name, chatgpt_processing)}>
  <input type="hidden" name="ui_locale" value="{locale}" />
  <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准读取刚刚选择的导出文件。", "I approve reading the export I just selected."))}</label>
  <button class="source-control" type="submit" data-loading-label="{html.escape(ui_text(locale, '开始导入…', 'Starting import…'))}">{html.escape(ui_text(locale, "导入已选择的文件", "Import selected export"))}</button>
</form>'''
    elif desktop_mode:
        chatgpt_action = f'<a class="source-control" href="soloscale://choose-chatgpt-export">{html.escape(ui_text(locale, "选择导出文件", "Choose export file"))}</a>'
    else:
        chatgpt_action = f'''<form method="post" action="/work/import-chatgpt" enctype="multipart/form-data" {processing_form_attributes(chatgpt_name, chatgpt_processing)}>
  <input type="hidden" name="ui_locale" value="{locale}" />
  <input type="file" name="chatgpt_export" accept=".json,.zip,application/json,application/zip" required />
  <label class="consent"><input type="checkbox" name="approve" value="yes" required />{html.escape(ui_text(locale, "我批准读取这个导出文件。", "I approve reading this export."))}</label>
  <button class="source-control" type="submit" data-loading-label="{html.escape(ui_text(locale, '开始导入…', 'Starting import…'))}">{html.escape(ui_text(locale, "导入 ChatGPT 记录", "Import ChatGPT history"))}</button>
</form>'''

    connected_rows: list[str] = []
    add_more_rows: list[str] = []

    resume_actions = (
        f'<a class="source-control" href="{ui_url("/resume", locale)}">{html.escape(ui_text(locale, "查看", "View"))}</a>'
        f'<a class="source-control" href="{ui_url("/resume#resume-form", locale)}">{html.escape(ui_text(locale, "上传或更新简历", "Upload or update resume"))}</a>'
    )
    if snapshot.resume_runs:
        connected_rows.append(
            _source_row(
                key="resume-library",
                state="READY",
                locale=locale,
                name="Resume Library",
                summary=ui_text(locale, f"{snapshot.resume_runs} 份申请记录", f"{snapshot.resume_runs} application records"),
                detail=ui_text(locale, "只使用你确认过的经历。", "Uses only experience you have confirmed."),
                actions=resume_actions,
            )
        )
    else:
        add_more_rows.append(
            _source_row(
                key="resume-library",
                state="AVAILABLE",
                locale=locale,
                name="Resume Library",
                summary=ui_text(locale, "还没有简历申请记录", "No resume application records yet"),
                detail=ui_text(locale, "上传现有简历后即可开始。", "Upload your current resume to get started."),
                actions=f'<a class="source-control" href="{ui_url("/resume#resume-form", locale)}">{html.escape(ui_text(locale, "上传简历", "Upload resume"))}</a>',
            )
        )

    if snapshot.project_connected:
        project_processing = ui_text(
            locale,
            "正在准备本地项目快照 · 完成后本页会自动更新",
            "Preparing the local project snapshot · this page will update when complete",
        )
        change_project = (
            f'<a class="source-control" href="soloscale://choose-work-repository">{html.escape(ui_text(locale, "更换项目", "Change project"))}</a>'
            if desktop_mode
            else f'<span class="source-help">{html.escape(ui_text(locale, "可在 macOS App 中更换项目。", "Change the project from the macOS app."))}</span>'
        )
        prepare_project = f'''<form method="post" action="/work/refresh" {processing_form_attributes("Local Git", project_processing)}>
  <input type="hidden" name="ui_locale" value="{locale}" />
  <button class="source-control" type="submit" data-loading-label="{html.escape(ui_text(locale, '开始准备…', 'Starting…'))}">{html.escape(ui_text(locale, "准备这个项目", "Prepare this project"))}</button>
</form>'''
        connected_rows.append(
            _source_row(
                key="local-git",
                state="READY",
                locale=locale,
                name="Local Git",
                summary=ui_text(locale, f"{snapshot.project_name or 'Git'} · 已选择", f"{snapshot.project_name or 'Git'} · selected"),
                detail=ui_text(locale, "只准备分支、提交和变更摘要；不会上传项目代码。", "Prepares branch, commit, and change summaries only; project code is not uploaded."),
                actions=change_project + prepare_project,
            )
        )
    else:
        project_action = (
            f'<a class="source-control" href="soloscale://choose-work-repository">{html.escape(ui_text(locale, "选择本地 Git 项目", "Choose a local Git project"))}</a>'
            if desktop_mode
            else f'<span class="source-help">{html.escape(ui_text(locale, "请在 macOS App 中选择目录。", "Choose a folder from the macOS app."))}</span>'
        )
        add_more_rows.append(
            _source_row(
                key="local-git",
                state="AVAILABLE",
                locale=locale,
                name="Local Git",
                summary=ui_text(locale, "还没有选择项目", "No project selected"),
                detail=ui_text(locale, "只使用你通过系统选择器明确授权的 Git 项目。", "Only a Git project you explicitly choose through the system picker is used."),
                actions=project_action,
            )
        )

    if snapshot.codex_sessions:
        connected_rows.append(
            _source_row(
                key="codex",
                state="READY",
                locale=locale,
                name=codex_name,
                summary=ui_text(locale, f"已导入 {snapshot.codex_sessions} 个会话", f"{snapshot.codex_sessions} sessions imported"),
                detail=ui_text(locale, "只有你明确批准导入时才会读取标准历史目录。", "The standard history folder is read only after your explicit import approval."),
                actions=codex_action,
            )
        )
    elif snapshot.codex_folder_available:
        add_more_rows.append(
            _source_row(
                key="codex",
                state="AVAILABLE",
                locale=locale,
                name=codex_name,
                summary=ui_text(locale, "这台 Mac 上有可添加的 Codex 资料", "Codex data is available to add on this Mac"),
                detail=ui_text(locale, "导入前只确认标准目录存在，不读取正文。", "Before approval, SoloScale checks only that the standard folder exists, not its contents."),
                actions=codex_action,
            )
        )
    else:
        add_more_rows.append(
            _source_row(
                key="codex",
                state="NEEDS_ATTENTION",
                locale=locale,
                name=codex_name,
                summary=ui_text(locale, "未找到标准 Codex 历史目录", "No standard Codex history folder found"),
                detail=ui_text(locale, "现有资料不受影响；此页不会扫描其他文件夹。", "Existing work is unaffected, and this page does not scan other folders."),
            )
        )

    if snapshot.chatgpt_exports:
        connected_rows.append(
            _source_row(
                key="chatgpt-export",
                state="READY",
                locale=locale,
                name=chatgpt_name,
                summary=ui_text(locale, f"已导入 {snapshot.chatgpt_exports} 个对话", f"{snapshot.chatgpt_exports} conversations imported"),
                detail=ui_text(locale, "只读取你主动选择并批准导入的官方导出文件。", "Only an official export you explicitly choose and approve is read."),
                actions=chatgpt_action,
            )
        )
    else:
        add_more_rows.append(
            _source_row(
                key="chatgpt-export",
                state="NEEDS_ATTENTION" if chatgpt_export_selected else "AVAILABLE",
                locale=locale,
                name=chatgpt_name,
                summary=ui_text(locale, "已选择文件，等待你批准导入", "File selected; waiting for your import approval") if chatgpt_export_selected else ui_text(locale, "导入 ChatGPT 官方导出的 JSON 或 ZIP", "Import an official ChatGPT JSON or ZIP export"),
                detail=ui_text(locale, "SoloScale 只读取你主动选择的文件。", "SoloScale reads only the file you explicitly choose."),
                actions=chatgpt_action,
            )
        )

    add_more_rows.append(
        _source_row(
            key="github",
            state="NOT_CONNECTED",
            locale=locale,
            name="GitHub",
            summary=ui_text(locale, "尚未连接 GitHub", "GitHub is not connected"),
            detail=ui_text(locale, "当前版本尚未提供 GitHub OAuth；请先使用本地 Git 项目。", "GitHub OAuth is not available in this version; use a local Git project for now."),
        )
    )

    connected_html = "".join(connected_rows) or f'<p class="section-empty">{html.escape(ui_text(locale, "还没有已连接的工作资料。你可以从下方添加。", "No work sources are connected yet. Add one below when you are ready."))}</p>'
    add_more_html = "".join(add_more_rows)
    unavailable_html = _source_row(
        key="additional-files",
        state="UNAVAILABLE",
        locale=locale,
        name=ui_text(locale, "其他文件", "Additional files"),
        summary=ui_text(locale, "更多文件来源即将支持。", "More file sources are coming later."),
        detail=ui_text(locale, "当前不扫描任意文件夹，也不显示无法执行的操作。", "SoloScale does not scan arbitrary folders or show actions that cannot run."),
    )

    body = f"""{notice_html}{error_html}
<section class="privacy-note" aria-labelledby="privacy-heading">
  <div><strong id="privacy-heading">{html.escape(ui_text(locale, '只读取你明确选择的资料。本地优先，不扫描整台 Mac。', 'Only work you explicitly choose is read. Local first; no whole-Mac scanning.'))}</strong>
  <details><summary>{html.escape(ui_text(locale, '了解数据边界', 'Understand the data boundary'))}</summary>
    <p>{html.escape(ui_text(locale, 'SoloScale 只检查标准位置是否存在，以及你主动选择了什么。点击导入前不会读取对话正文；不会自动扫描其他项目或文件夹，也不会因为打开此页而上传资料或调用模型。', 'SoloScale checks only whether standard locations exist and what you explicitly selected. It does not read conversation bodies before import, scan other projects or folders automatically, upload your work, or call a model merely because this page opens.'))}</p>
  </details></div>
</section>
<section id="work-processing" class="source-section processing-section" aria-labelledby="processing-heading" hidden>
  <div class="section-heading"><h2 id="processing-heading">{html.escape(ui_text(locale, '处理中', 'Processing'))}</h2></div>
  <article class="source-row processing-row" aria-live="polite">
    {render_source_state("PROCESSING", locale)}
    <div class="source-copy"><h3 id="processing-source">{html.escape(ui_text(locale, '工作资料', 'Work source'))}</h3><strong id="processing-copy"></strong></div>
  </article>
</section>
<div class="source-sections">
  <section class="source-section" aria-labelledby="connected-heading">
    <div class="section-heading"><h2 id="connected-heading">{html.escape(ui_text(locale, '已连接', 'Connected'))}</h2><span>{len(connected_rows)}</span></div>
    <div class="source-list">{connected_html}</div>
  </section>
  <section class="source-section" aria-labelledby="add-more-heading">
    <div class="section-heading"><h2 id="add-more-heading">{html.escape(ui_text(locale, '添加更多', 'Add More'))}</h2></div>
    <div class="source-list">{add_more_html}</div>
  </section>
  <section class="source-section unavailable-section" aria-labelledby="unavailable-heading">
    <div class="section-heading"><h2 id="unavailable-heading">{html.escape(ui_text(locale, '暂不可用', 'Not Available Yet'))}</h2></div>
    <div class="source-list">{unavailable_html}</div>
  </section>
</div>
<footer class="work-actions">
  <a class="quiet-action" href="{ui_url('/', locale)}">{html.escape(ui_text(locale, '稍后再添加', 'Add more later'))}</a>
  <a class="primary" href="{ui_url('/', locale)}">{html.escape(ui_text(locale, '完成并继续', 'Continue'))}</a>
</footer>"""
    return render_app_shell(
        active="work",
        locale=locale,
        current_url="/work",
        title=f"SoloScale · {ui_text(locale, '你的工作资料', 'Your Work Context')}",
        eyebrow=ui_text(locale, "工作资料", "Work Context"),
        heading=ui_text(locale, "你的工作资料", "Your Work Context"),
        description=ui_text(
            locale,
            "选择 SoloScale 可以使用的资料。以后随时可以修改。",
            "Choose the work sources SoloScale can use. You can change them anytime.",
        ),
        body=body,
        compact_hero=True,
        extra_css="""
#main-content{display:grid;gap:22px}.privacy-note{padding:14px 16px;border:1px solid #cfe3da;border-radius:14px;background:linear-gradient(135deg,#f2faf6,#fff)}.privacy-note>div{display:grid;gap:5px}.privacy-note strong{color:var(--success)}.privacy-note details{color:var(--text-muted);font-size:13px}.privacy-note summary{width:max-content;color:var(--brand);font-weight:800;cursor:pointer}.privacy-note p{max-width:880px;margin:8px 0 0}.source-sections{display:grid;gap:26px}.source-section{display:grid;gap:10px}.source-section[hidden]{display:none!important}.section-heading{display:flex;align-items:center;gap:10px;padding-bottom:8px;border-bottom:1px solid var(--border)}.section-heading h2{margin:0;font-size:18px;letter-spacing:-.015em}.section-heading span{min-width:25px;height:25px;display:grid;place-items:center;border-radius:999px;background:var(--surface-subtle);color:var(--text-muted);font-size:12px;font-weight:800}.source-list{display:grid;gap:9px}.source-row{display:grid;grid-template-columns:116px minmax(210px,1fr) minmax(220px,auto);align-items:center;gap:18px;padding:16px 18px;border:1px solid var(--border);border-radius:16px;background:rgba(255,255,255,.84)}.source-copy{min-width:0}.source-copy h3{margin:0 0 2px;font-size:16px}.source-copy strong{display:block;font-size:14px}.source-copy p{margin:3px 0 0;color:var(--text-muted);font-size:12px}.source-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;flex-wrap:wrap}.source-actions form{display:flex;align-items:center;justify-content:flex-end;gap:9px;flex-wrap:wrap}.source-control{display:inline-flex;align-items:center;justify-content:center;min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--brand);font:inherit;font-size:12px;font-weight:800;text-decoration:none;white-space:nowrap}.source-control:hover{border-color:var(--brand);background:var(--brand-soft)}button.source-control{color:var(--brand)}.consent{display:flex;max-width:255px;flex-direction:row;align-items:flex-start;gap:7px;color:var(--text-muted);font-size:11px;font-weight:500}.consent input{margin-top:1px}.source-help{max-width:250px;color:var(--text-muted);font-size:12px}.processing-section{padding:14px;border:1px solid #cfd6f6;border-radius:16px;background:var(--brand-soft)}.processing-section .section-heading{border-color:#cfd6f6}.processing-row{border:0;background:rgba(255,255,255,.7)}.source-row.is-processing .source-actions{opacity:.42;pointer-events:none}.section-empty{margin:0;padding:16px 18px;border:1px dashed var(--border);border-radius:14px;color:var(--text-muted)}.unavailable-section .source-row{background:var(--surface-subtle);box-shadow:none}.work-actions{display:flex;justify-content:flex-end;align-items:center;gap:12px;padding-top:3px}.work-actions .primary{min-width:150px}.quiet-action{padding:10px 12px;color:var(--text-muted);font-weight:750;text-decoration:none}
@media(max-width:940px){.source-row{grid-template-columns:105px 1fr}.source-actions{grid-column:2;justify-content:flex-start}.source-actions form{justify-content:flex-start}}
@media(max-width:620px){.source-row{grid-template-columns:1fr;gap:9px}.source-actions{grid-column:1;justify-content:flex-start}.source-actions form{align-items:flex-start;justify-content:flex-start;flex-direction:column}.consent{max-width:none}.work-actions{align-items:stretch;flex-direction:column-reverse}.work-actions a{width:100%;text-align:center}}
""",
        script="""
(() => {
  const section = document.getElementById("work-processing");
  const source = document.getElementById("processing-source");
  const copy = document.getElementById("processing-copy");
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.processingSource) return;
    source.textContent = form.dataset.processingSource;
    copy.textContent = form.dataset.processingCopy || "";
    section.hidden = false;
    form.closest(".source-row")?.classList.add("is-processing");
  }, true);
})();
""",
    )
