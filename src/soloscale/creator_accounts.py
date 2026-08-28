# ruff: noqa: E501
"""Local account directory for SoloScale Creator workflows."""

from __future__ import annotations

import html
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from soloscale.ui_shell import UILocale, render_app_shell, ui_text

CreatorPlatform = Literal[
    "douyin", "xiaohongshu", "youtube", "x", "linkedin", "github", "independent_site"
]
CreatorAccountStatus = Literal["NOT_CONFIGURED", "ACTIVE", "NEEDS_ATTENTION"]

PLATFORMS: tuple[CreatorPlatform, ...] = (
    "douyin", "xiaohongshu", "youtube", "x", "linkedin", "github", "independent_site"
)
STATUSES: tuple[CreatorAccountStatus, ...] = (
    "NOT_CONFIGURED", "ACTIVE", "NEEDS_ATTENTION"
)
LABELS: dict[CreatorPlatform, str] = {
    "douyin": "Douyin", "xiaohongshu": "Xiaohongshu", "youtube": "YouTube",
    "x": "X", "linkedin": "LinkedIn", "github": "GitHub",
    "independent_site": "Independent Site",
}


class CreatorAccountError(ValueError):
    """Raised when an account entry is invalid."""


@dataclass(frozen=True)
class CreatorAccount:
    platform: CreatorPlatform
    display_name: str = ""
    handle: str = ""
    profile_url: str = ""
    admin_url: str = ""
    status: CreatorAccountStatus = "NOT_CONFIGURED"


def _settings_path(data_root: Path) -> Path:
    return data_root.expanduser().absolute() / "settings" / "creator-accounts.json"


def _text(value: str, field: str, limit: int = 160) -> str:
    cleaned = value.strip()
    if len(cleaned) > limit or any(ord(character) < 32 for character in cleaned):
        raise CreatorAccountError(f"Invalid {field}")
    return cleaned


def _url(value: str, field: str) -> str:
    cleaned = _text(value, field, 2048)
    if not cleaned:
        return ""
    parsed = urlsplit(cleaned)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise CreatorAccountError(f"Invalid {field}")
    return cleaned


def normalize_account(
    *, platform: str, display_name: str = "", handle: str = "",
    profile_url: str = "", admin_url: str = "", status: str = "NOT_CONFIGURED",
) -> CreatorAccount:
    """Validate exactly the six fields accepted by Account Center v0.1."""
    if platform not in PLATFORMS:
        raise CreatorAccountError("Unsupported platform")
    if status not in STATUSES:
        raise CreatorAccountError("Unsupported status")
    return CreatorAccount(
        platform=platform,
        display_name=_text(display_name, "display_name"),
        handle=_text(handle, "handle"),
        profile_url=_url(profile_url, "profile_url"),
        admin_url=_url(admin_url, "admin_url"),
        status=status,
    )


def load_creator_accounts(data_root: Path) -> tuple[CreatorAccount, ...]:
    """Load the seven stable platform slots, filling any missing entries."""
    stored: dict[CreatorPlatform, CreatorAccount] = {}
    path = _settings_path(data_root)
    if path.is_file() and not path.is_symlink():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries = payload.get("accounts", []) if isinstance(payload, dict) else []
            for raw in entries:
                if isinstance(raw, dict):
                    account = normalize_account(**{key: str(raw.get(key, "")) for key in (
                        "platform", "display_name", "handle", "profile_url", "admin_url", "status"
                    )})
                    stored[account.platform] = account
        except (OSError, json.JSONDecodeError, CreatorAccountError, TypeError):
            stored = {}
    return tuple(stored.get(platform, CreatorAccount(platform)) for platform in PLATFORMS)


def save_creator_account(data_root: Path, account: CreatorAccount) -> Path:
    """Atomically update one local account entry with private permissions."""
    root = data_root.expanduser().absolute()
    settings = root / "settings"
    if root.is_symlink() or settings.is_symlink():
        raise CreatorAccountError("Account settings cannot use symlinked directories")
    settings.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(settings, 0o700)
    accounts = {item.platform: item for item in load_creator_accounts(root)}
    accounts[account.platform] = account
    payload = {"schema_version": "1.0", "accounts": [
        asdict(accounts[platform]) for platform in PLATFORMS
    ]}
    descriptor, temporary_name = tempfile.mkstemp(prefix=".creator-accounts-", dir=settings)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _settings_path(root))
        os.chmod(_settings_path(root), 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return _settings_path(root)


def creator_accounts_page(
    data_root: Path, *, locale: UILocale = "zh-CN", notice: str | None = None
) -> str:
    """Render profile/admin shortcuts and inline editing; no platform integration."""
    status_copy = {
        "NOT_CONFIGURED": ui_text(locale, "未配置", "Not configured"),
        "ACTIVE": ui_text(locale, "可用", "Active"),
        "NEEDS_ATTENTION": ui_text(locale, "需检查", "Needs attention"),
    }
    cards: list[str] = []
    for account in load_creator_accounts(data_root):
        label = LABELS[account.platform]
        title = account.display_name or label
        handle = f"@{account.handle.lstrip('@')}" if account.handle else ""
        profile = (
            f'<a class="secondary-button" href="{html.escape(account.profile_url, quote=True)}" target="_blank" rel="noopener noreferrer">{ui_text(locale, "打开主页", "Open profile")}</a>'
            if account.profile_url else ""
        )
        admin = (
            f'<a class="secondary-button" href="{html.escape(account.admin_url, quote=True)}" target="_blank" rel="noopener noreferrer">{ui_text(locale, "打开后台", "Open admin")}</a>'
            if account.admin_url else ""
        )
        options = "".join(
            f'<option value="{value}"{" selected" if value == account.status else ""}>{status_copy[value]}</option>'
            for value in STATUSES
        )
        cards.append(f'''<article class="account-card" data-platform="{account.platform}">
<div class="account-head"><div><span>{label}</span><h2>{html.escape(title)}</h2><p>{html.escape(handle)}</p></div><strong data-status="{account.status}">{status_copy[account.status]}</strong></div>
<div class="account-actions">{profile}{admin}<details><summary>{ui_text(locale, "编辑", "Edit")}</summary>
<form method="post" action="/creator/accounts/save"><input type="hidden" name="ui_locale" value="{locale}" /><input type="hidden" name="platform" value="{account.platform}" />
<label>{ui_text(locale, "显示名称", "Display name")}<input name="display_name" maxlength="160" value="{html.escape(account.display_name, quote=True)}" /></label>
<label>{ui_text(locale, "账号名", "Handle")}<input name="handle" maxlength="160" value="{html.escape(account.handle, quote=True)}" /></label>
<label>{ui_text(locale, "主页 URL", "Profile URL")}<input name="profile_url" maxlength="2048" value="{html.escape(account.profile_url, quote=True)}" placeholder="https://" /></label>
<label>{ui_text(locale, "管理后台 URL", "Admin URL")}<input name="admin_url" maxlength="2048" value="{html.escape(account.admin_url, quote=True)}" placeholder="https://" /></label>
<label>{ui_text(locale, "状态", "Status")}<select name="status">{options}</select></label><button type="submit">{ui_text(locale, "保存", "Save")}</button></form>
</details></div></article>''')
    notice_html = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
    body = f'''{notice_html}<section class="boundary"><strong>{ui_text(locale, "这里只保存入口信息", "This stores entry links only")}</strong><p>{ui_text(locale, "不连接 OAuth，不自动发布，也不读取 Analytics 或 Inbox。", "No OAuth, automatic publishing, Analytics, or Inbox access.")}</p></section><section class="account-grid">{"".join(cards)}</section>'''
    return render_app_shell(
        active="accounts", locale=locale, current_url="/creator/accounts",
        title=f"SoloScale · {ui_text(locale, '账号中心', 'Account Center')}",
        eyebrow=ui_text(locale, "建立影响力", "Build visibility"),
        heading=ui_text(locale, "所有账号入口，一个地方管理。", "Manage every account entry in one place."),
        description=ui_text(locale, "保存主页与后台地址，需要时一键打开。", "Save profile and admin links, then open them in one click."),
        body=body, compact_hero=True,
        extra_css="""
.boundary{margin-bottom:18px;padding:16px 18px;border:1px solid var(--border);border-radius:16px;background:var(--brand-soft)}.boundary p{margin:5px 0 0;color:var(--text-muted)}.account-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}.account-card{padding:19px;border:1px solid var(--border);border-radius:18px;background:#fff}.account-head{display:flex;justify-content:space-between;gap:16px}.account-head span{font-size:11px;font-weight:850;color:var(--brand)}.account-head h2{margin:5px 0}.account-head p{min-height:20px;margin:0;color:var(--text-muted)}.account-head>strong{align-self:flex-start;padding:6px 9px;border-radius:999px;background:var(--surface-subtle);font-size:11px}.account-head>strong[data-status="ACTIVE"]{background:var(--success-soft);color:var(--success)}.account-head>strong[data-status="NEEDS_ATTENTION"]{background:var(--warning-soft);color:var(--warning)}.account-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:17px}.account-actions details{width:100%;margin-top:4px}.account-actions summary{cursor:pointer;font-weight:800;color:var(--brand)}.account-actions form{display:grid;gap:10px;margin-top:12px}.account-actions label{display:grid;gap:5px}.account-actions button{justify-self:start}.notice{padding:12px 14px;border-radius:12px;background:var(--success-soft);color:var(--success);font-weight:750}
""",
    )
