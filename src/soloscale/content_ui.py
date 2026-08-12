from __future__ import annotations

import html
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from soloscale.buildlog_handoff import buildlog_handoff_status
from soloscale.content_models import ContentBrief, ContentRun
from soloscale.content_workspace import (
    ContentWorkspaceError,
    load_content_run,
    parse_claim_ledger,
    run_content_workspace,
)
from soloscale.video_factory import creator_video_ready


@dataclass(frozen=True)
class ContentFormResult:
    run_id: str | None
    error: str | None
    elapsed_ms: int


def _escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def _grounded_lines(raw: str, status: str) -> list[str]:
    lines: list[str] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", maxsplit=2)]
        if len(parts) < 2 or not parts[1]:
            raise ContentWorkspaceError(
                f"{status} 第 {line_number} 行需要填写：事实 | 证据链接 | 边界（可选）"
            )
        limit = parts[2] if len(parts) == 3 else ""
        lines.append(f"{status} | {parts[0]} | {parts[1]} | {limit}")
    return lines


def _ungrounded_lines(raw: str, status: str) -> list[str]:
    return [f"{status} | {line.strip()}" for line in raw.splitlines() if line.strip()]


def _brief_from_form(form: dict[str, str]) -> ContentBrief:
    ledger_lines = [
        *_grounded_lines(form.get("verified_claims", ""), "VERIFIED"),
        *_grounded_lines(form.get("observed_claims", ""), "OBSERVED"),
        *_ungrounded_lines(form.get("hypotheses", ""), "HYPOTHESIS"),
        *_ungrounded_lines(form.get("planned", ""), "PLANNED"),
    ]
    language: Literal["English", "中文"] = (
        "中文" if form.get("language") == "中文" else "English"
    )
    return ContentBrief(
        topic=form.get("topic", "").strip(),
        audience=form.get("audience", "").strip(),
        language=language,
        call_to_action=form.get("call_to_action", "").strip(),
        source_label=form.get("source_label", "").strip(),
        claims=parse_claim_ledger("\n".join(ledger_lines)),
    )


def run_content_form(form: dict[str, str], data_root: Path) -> ContentFormResult:
    started = time.perf_counter()
    try:
        brief = _brief_from_form(form)
        run = run_content_workspace(data_root=data_root, brief=brief)
    except (ContentWorkspaceError, OSError, ValidationError, ValueError) as exc:
        return ContentFormResult(
            run_id=None,
            error=str(exc) or "输入不完整，请检查后重试。",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
    return ContentFormResult(
        run_id=run.run_id,
        error=None,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _recent_runs(data_root: Path) -> list[str]:
    runs_root = data_root / "content-runs"
    if runs_root.is_symlink() or not runs_root.is_dir():
        return []
    candidates: list[str] = []
    for path in sorted(runs_root.iterdir(), key=lambda item: item.name, reverse=True):
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            load_content_run(data_root, path.name)
        except ContentWorkspaceError:
            continue
        candidates.append(path.name)
        if len(candidates) == 6:
            break
    return candidates


def _form_from_run(run: ContentRun) -> dict[str, str]:
    grouped: dict[str, list[str]] = {
        "VERIFIED": [],
        "OBSERVED": [],
        "HYPOTHESIS": [],
        "PLANNED": [],
    }
    for claim in run.brief.claims:
        if claim.status.value in {"VERIFIED", "OBSERVED"}:
            parts = [claim.text, claim.receipt or ""]
            if claim.limits:
                parts.append(claim.limits)
            grouped[claim.status.value].append(" | ".join(parts))
        else:
            grouped[claim.status.value].append(claim.text)
    return {
        "topic": run.brief.topic,
        "audience": run.brief.audience,
        "language": run.brief.language,
        "call_to_action": run.brief.call_to_action,
        "source_label": run.brief.source_label,
        "verified_claims": "\n".join(grouped["VERIFIED"]),
        "observed_claims": "\n".join(grouped["OBSERVED"]),
        "hypotheses": "\n".join(grouped["HYPOTHESIS"]),
        "planned": "\n".join(grouped["PLANNED"]),
    }


def _result_html(run: ContentRun, *, data_root: Path, video_ready: bool) -> str:
    run_id = _escape(run.run_id)
    linkedin = _escape(run.drafts.linkedin)
    x_posts = "".join(
        f'<article class="x-post"><span>POST {index}</span><p>{_escape(post)}</p></article>'
        for index, post in enumerate(run.drafts.x_thread, start=1)
    )
    scenes = "".join(
        "<article class=\"scene\">"
        f"<span>{scene.start_second:02d}–{scene.end_second:02d}s</span>"
        f"<strong>{_escape(scene.purpose)}</strong>"
        f"<p>{_escape(scene.voiceover)}</p>"
        f"<small>{_escape(scene.visual)}</small>"
        "</article>"
        for scene in run.drafts.storyboard
    )
    downloads = (
        ("LinkedIn", "linkedin.md"),
        ("X Thread", "x-thread.md"),
        ("视频脚本", "video-script.md"),
        ("Storyboard", "storyboard.json"),
        ("Publish Pack", "publish-pack.json"),
    )
    video_download = f"/content/downloads/{run_id}/creator-video.mp4"
    video_action = (
        f'''<video class="creator-video" controls preload="metadata">
          <source src="{video_download}" type="video/mp4" />
        </video>
        <a class="text-link" href="{video_download}" download>下载 MP4</a>'''
        if video_ready
        else f'''<form method="post" action="/content/render/{run_id}" class="render-form">
          <button class="primary" type="submit">生成 MP4 视频</button>
          <small>本机 Remotion 渲染；只使用本次 storyboard，不会发布。</small>
        </form>'''
    )
    buildlog = "".join(
        _buildlog_channel_html(data_root, run.run_id, channel)
        for channel in ("linkedin", "x")
    )
    download_links = "".join(
        f'<a href="/content/downloads/{run_id}/{name}" download>{label}</a>'
        for label, name in downloads
    )
    return f"""<section id="results" class="result-panel">
      <div class="result-head">
        <div><span class="kicker">已生成</span><h2>一份素材，三个渠道</h2>
          <p>内容已私有保存；复制或下载后，人工检查再发布。</p></div>
        <div class="downloads">{download_links}</div>
      </div>
      <div class="tabs" role="tablist">
        <button type="button" class="tab active" data-tab="linkedin">LinkedIn</button>
        <button type="button" class="tab" data-tab="x">X Thread</button>
        <button type="button" class="tab" data-tab="video">短视频脚本</button>
      </div>
      <div class="tab-panel active" data-panel="linkedin">
        <div class="panel-title"><h3>LinkedIn Draft</h3>
          <button type="button" class="copy" data-copy="linkedin-copy">复制</button></div>
        <pre id="linkedin-copy">{linkedin}</pre>
      </div>
      <div class="tab-panel" data-panel="x">
        <div class="panel-title"><h3>X Thread</h3>
          <button type="button" class="copy" data-copy="x-copy">复制全部</button></div>
        <div id="x-copy" class="x-thread">{x_posts}</div>
      </div>
      <div class="tab-panel" data-panel="video">
        <div class="panel-title"><h3>约 {run.drafts.storyboard[-1].end_second} 秒视频</h3>
          <a class="text-link" href="/content/downloads/{run_id}/video-script.md"
            download>下载脚本</a>
        </div>
        <div class="storyboard">{scenes}</div>
        <div class="creator-video-result">{video_action}</div>
      </div>
      <p class="review-note">SoloScale 没有连接或操作你的社交账号，也没有自动发布。</p>
      <section class="buildlog-handoff">
        <span class="kicker">BuildLog publishing</span>
        <h3>发布前，交给已有的 BuildLog 审核与确认流程</h3>
        <p>BuildLog 会显示精确文本、检查重复内容，并在你确认后才调用平台。</p>
        {buildlog}
      </section>
    </section>"""


def _buildlog_channel_html(data_root: Path, run_id: str, channel: str) -> str:
    label = "LinkedIn" if channel == "linkedin" else "X"
    try:
        handoff, receipt = buildlog_handoff_status(data_root, run_id, channel)
    except ValueError:
        handoff, receipt = None, None
    if handoff is None:
        return (
            f'<form method="post" action="/content/buildlog/{run_id}/{channel}">'
            f'<button class="secondary" type="submit">交给 BuildLog 发布 {label}</button></form>'
        )
    run = _escape(handoff["buildlog_run_id"])
    if receipt is not None:
        return (
            f'<p><strong>{label} 已发布</strong> · Post ID: {_escape(receipt["external_post_id"])} '
            f'· BuildLog receipt: {_escape(receipt["receipt_id"])}</p>'
        )
    heading = f"<strong>{label} 已转交 BuildLog</strong> · Run: {run}"
    return f'''<div class="handoff-state"><p>{heading}</p>
      <p>在 BuildLog 中预览并输入 PUBLISH 后，再回这里同步回执。</p>
      <form method="post" action="/content/buildlog/{run_id}/{channel}/receipt">
        <button class="secondary" type="submit">同步 {label} 发布回执</button>
      </form></div>'''


def content_page(
    *,
    data_root: Path,
    form: dict[str, str] | None = None,
    run_id: str | None = None,
    error: str | None = None,
) -> str:
    values = form or {}
    run: ContentRun | None = None
    if run_id:
        try:
            run = load_content_run(data_root, run_id)
        except ContentWorkspaceError:
            error = "这份内容记录不可用，请重新生成。"
    if run is not None and not values:
        values = _form_from_run(run)
    recent = _recent_runs(data_root)
    recent_html = "".join(
        f'<a href="/content?run_id={_escape(item)}">{_escape(item)}</a>' for item in recent
    )
    if not recent_html:
        recent_html = "<span>生成后会显示在这里。</span>"
    error_html = (
        f'<div class="error" role="alert">{_escape(error)}</div>' if error else ""
    )
    result_html = (
        _result_html(
            run,
            data_root=data_root,
            video_ready=creator_video_ready(data_root, run.run_id),
        )
        if run is not None
        else """<section class="empty">
      <span class="kicker">Preview</span><h2>今天要发的内容，会直接在这里预览</h2>
      <p>你只提供可确认的事实和边界；SoloScale 负责排成 LinkedIn、X 和短视频版本。</p>
    </section>"""
    )
    language = values.get("language", "English")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>SoloScale · Content Studio</title>
<style>
:root {{
  --ink:#182033; --muted:#667085; --line:#dfe4ec; --blue:#3157d5; --soft:#eef2ff;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; color:var(--ink); background:#f5f7fb;
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}}
a {{ color:var(--blue); }}
.shell {{ max-width:1220px; margin:auto; padding:26px 24px 70px; }}
nav {{
  display:flex; justify-content:space-between; align-items:center; margin-bottom:46px;
}}
.brand {{ font-weight:850; font-size:18px; }}
.nav-links {{ display:flex; gap:18px; align-items:center; }}
.nav-links a {{ text-decoration:none; color:var(--muted); font-size:14px; }}
.nav-links .active {{ color:var(--blue); font-weight:800; }}
.hero {{ max-width:780px; margin:0 auto 32px; text-align:center; }}
.kicker {{
  color:var(--blue); font-size:12px; font-weight:850; letter-spacing:.13em;
  text-transform:uppercase;
}}
h1 {{
  font-size:clamp(40px,6vw,68px); line-height:1.02; letter-spacing:-.055em;
  margin:10px 0 15px;
}}
.hero p,.empty p,.result-head p {{ color:var(--muted); line-height:1.65; }}
.grid {{
  display:grid; grid-template-columns:minmax(340px,.8fr) minmax(0,1.2fr);
  gap:22px; align-items:start;
}}
.form-card,.empty,.result-panel {{
  background:white; border:1px solid #fff; border-radius:24px; padding:28px;
  box-shadow:0 18px 55px #25304b14;
}}
h2 {{ margin:7px 0 8px; letter-spacing:-.025em; }}
form {{ display:grid; gap:16px; margin-top:22px; }}
label {{ display:grid; gap:7px; font-size:14px; font-weight:750; }}
.hint {{ color:var(--muted); font-size:12px; font-weight:400; line-height:1.45; }}
input,textarea,select {{
  width:100%; border:1px solid var(--line); border-radius:13px; padding:12px 13px;
  background:white; color:var(--ink); font:inherit;
}}
textarea {{ min-height:92px; resize:vertical; line-height:1.5; }}
textarea.large {{ min-height:140px; }}
input:focus,textarea:focus,select:focus {{
  outline:0; border-color:var(--blue); box-shadow:0 0 0 4px #3157d51a;
}}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
.primary {{
  border:0; border-radius:13px; padding:14px 18px; background:var(--blue);
  color:white; font-weight:850; cursor:pointer;
}}
.primary:disabled {{ opacity:.65; }}
.boundary {{
  font-size:12px; color:var(--muted); padding:12px 13px; background:#f7f8fa;
  border-radius:12px;
}}
.error {{ padding:12px 14px; background:#fff1f1; color:#9b1c1c; border-radius:12px; }}
.empty {{
  min-height:460px; display:flex; flex-direction:column; justify-content:center;
  text-align:center;
}}
.result-head {{
  display:flex; justify-content:space-between; gap:18px; align-items:flex-start;
}}
.downloads {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
.downloads a,.text-link {{
  font-size:12px; font-weight:750; text-decoration:none; padding:8px 10px;
  border:1px solid var(--line); border-radius:9px;
}}
.tabs {{
  display:flex; gap:7px; margin:22px 0 14px; padding:5px; background:#f2f4f8;
  border-radius:12px;
}}
.tab {{
  flex:1; border:0; border-radius:9px; padding:10px; background:transparent;
  color:var(--muted); font-weight:750; cursor:pointer;
}}
.tab.active {{ background:white; color:var(--blue); box-shadow:0 1px 4px #17203314; }}
.tab-panel {{ display:none; }}
.tab-panel.active {{ display:block; }}
.panel-title {{
  display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
}}
.panel-title h3 {{ margin:0; }}
.copy {{
  border:0; background:var(--soft); color:var(--blue); border-radius:9px;
  padding:8px 12px; font-weight:750; cursor:pointer;
}}
pre {{
  white-space:pre-wrap; word-break:break-word; background:#fafbfc;
  border:1px solid var(--line); border-radius:14px; padding:18px;
  font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;
  max-height:650px; overflow:auto;
}}
.x-thread,.storyboard {{ display:grid; gap:10px; }}
.x-post,.scene {{ border:1px solid var(--line); border-radius:14px; padding:14px; }}
.x-post span,.scene span {{
  color:var(--blue); font-size:10px; font-weight:850; letter-spacing:.1em;
}}
.x-post p,.scene p {{ white-space:pre-wrap; line-height:1.5; margin:8px 0; }}
.scene {{ display:grid; gap:6px; }}
.scene small {{ color:var(--muted); }}
.review-note {{
  margin:18px 0 0; padding:12px; background:#fff8e8; color:#72510c;
  border-radius:12px; font-size:12px;
}}
.recent {{
  margin-top:18px; display:flex; gap:9px; flex-wrap:wrap; color:var(--muted);
  font-size:12px;
}}
.recent a {{ text-decoration:none; }}
@media(max-width:900px) {{
  .grid {{ grid-template-columns:1fr; }}
  .result-head {{ display:block; }}
  .downloads {{ justify-content:flex-start; margin-top:12px; }}
}}
@media(max-width:580px) {{
  .shell {{ padding:18px 13px 45px; }}
  .form-card,.empty,.result-panel {{ padding:20px; border-radius:18px; }}
  .two {{ grid-template-columns:1fr; }}
  nav {{ align-items:flex-start; }}
  .nav-links {{ gap:10px; flex-wrap:wrap; justify-content:flex-end; }}
}}
</style>
</head>
<body>
<main class="shell">
<nav>
  <div class="brand">SoloScale</div>
  <div class="nav-links">
    <a href="/">Resume</a><a href="/learning">Learning</a>
    <a class="active" href="/content">Content</a><a href="/advanced">Advanced</a>
  </div>
</nav>
<header class="hero">
  <span class="kicker">Content Studio</span>
  <h1>把已验证的工作，变成今天能发的内容。</h1>
  <p>一次输入，同时得到 LinkedIn、X Thread 和短视频脚本。
    所有稿件先本地保存、先预览，再由你决定是否发布。</p>
</header>
<div class="grid">
<section class="form-card">
<span class="kicker">输入</span><h2>证据 + 受众 + CTA</h2>
<p class="hint">第一条已验证事实会成为开头。数字、结果和结论都应附证据。</p>
{error_html}
<form id="content-form" method="post" action="/content/generate">
<label>主题
  <input name="topic" maxlength="180" required
    value="{_escape(values.get('topic', ''))}"
    placeholder="例如：Why green tests were not enough to publish" />
</label>
<div class="two">
  <label>受众
    <input name="audience" maxlength="500" required
      value="{_escape(values.get('audience', 'AI engineers and solo builders'))}" />
  </label>
  <label>输出语言
    <select name="language">
      <option {'selected' if language == 'English' else ''}>English</option>
      <option {'selected' if language == '中文' else ''}>中文</option>
    </select>
  </label>
</div>
<label>来源 / 项目链接
  <input name="source_label" maxlength="500" required
    value="{_escape(values.get('source_label', ''))}"
    placeholder="GitHub PR、公开文档或证据包标识" />
</label>
<label>已验证事实
  <span class="hint">每行：事实 | 证据链接 | 这条证据不能证明什么（可选）</span>
  <textarea class="large" name="verified_claims" required
    placeholder="Python CI checks passed. | https://github.com/... | Local run only."
  >{_escape(values.get('verified_claims', ''))}</textarea>
</label>
<label>个人观察（可选）
  <span class="hint">每行：观察 | 对应记录或链接 | 边界（可选）</span>
  <textarea name="observed_claims"
  >{_escape(values.get('observed_claims', ''))}</textarea>
</label>
<label>待验证假设（可选）
  <span class="hint">每行一条，不会被写成已验证结论。</span>
  <textarea name="hypotheses">{_escape(values.get('hypotheses', ''))}</textarea>
</label>
<label>下一步（可选）
  <span class="hint">每行一条，会使用未来时态和 PLANNED 标签。</span>
  <textarea name="planned">{_escape(values.get('planned', ''))}</textarea>
</label>
<label>CTA
  <input name="call_to_action" maxlength="220" required
    value="{_escape(values.get('call_to_action', 'Follow the next measured iteration.'))}" />
</label>
<div class="boundary">生成器不会调用模型或网络，不会连接社交账号，也不会自动发布。</div>
<button id="generate-content" class="primary" type="submit">生成三渠道内容</button>
</form>
<div class="recent"><strong>最近内容：</strong>{recent_html}</div>
</section>
{result_html}
</div>
</main>
<script>
document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => {{
  document.querySelectorAll('.tab,.tab-panel').forEach(item => {{
    item.classList.remove('active');
  }});
  button.classList.add('active');
  const panel = document.querySelector('[data-panel="' + button.dataset.tab + '"]');
  panel.classList.add('active');
}}));
document.querySelectorAll('.copy').forEach(button => {{
  button.addEventListener('click', async () => {{
    const target = document.getElementById(button.dataset.copy);
    try {{
      await navigator.clipboard.writeText(target.innerText);
      button.textContent = '已复制';
    }} catch {{
      button.textContent = '请手动复制';
    }}
  }});
}});
const form = document.getElementById('content-form');
form.addEventListener('submit', () => {{
  const button = document.getElementById('generate-content');
  button.disabled = true;
  button.textContent = '正在生成…';
}});
</script>
</body>
</html>"""
