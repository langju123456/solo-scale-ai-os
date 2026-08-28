import json
import stat
from pathlib import Path

import pytest

from soloscale.creator_accounts import (
    CreatorAccountError,
    creator_accounts_page,
    load_creator_accounts,
    normalize_account,
    save_creator_account,
)


def test_account_center_exposes_all_platforms_and_only_configured_links(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    save_creator_account(
        data_root,
        normalize_account(
            platform="linkedin",
            display_name="Lang Ju",
            handle="lang-ju",
            profile_url="https://www.linkedin.com/in/lang-ju",
            admin_url="https://www.linkedin.com/in/lang-ju/edit/",
            status="ACTIVE",
        ),
    )

    page = creator_accounts_page(data_root)

    for platform in (
        "douyin",
        "xiaohongshu",
        "youtube",
        "x",
        "linkedin",
        "github",
        "independent_site",
    ):
        assert f'data-platform="{platform}"' in page
    assert page.count('action="/creator/accounts/save"') == 7
    assert "打开主页" in page
    assert "打开后台" in page
    assert 'target="_blank" rel="noopener noreferrer"' in page
    assert "YouTube OAuth" in page
    assert "自动发布" in page
    assert 'href="/creator/accounts?lang=zh-CN"' in page


def test_account_center_persists_only_directory_fields_with_private_permissions(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    account = normalize_account(
        platform="youtube",
        display_name="SoloScale",
        handle="@soloscale",
        profile_url="https://youtube.com/@soloscale",
        admin_url="https://studio.youtube.com/channel/example",
        status="ACTIVE",
    )

    path = save_creator_account(data_root, account)
    payload = json.loads(path.read_text(encoding="utf-8"))
    youtube = next(
        item for item in payload["accounts"] if item["platform"] == "youtube"
    )

    assert set(youtube) == {
        "platform",
        "display_name",
        "handle",
        "profile_url",
        "admin_url",
        "status",
    }
    assert len(load_creator_accounts(data_root)) == 7
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("profile_url", "javascript:alert(1)"),
        ("admin_url", "file:///tmp/admin"),
        ("profile_url", "https://user:password@example.com/profile"),
    ),
)
def test_account_center_rejects_unsafe_external_urls(field: str, value: str) -> None:
    values = {"platform": "x", field: value}
    with pytest.raises(CreatorAccountError):
        normalize_account(**values)


def test_account_center_renders_english_copy(tmp_path: Path) -> None:
    page = creator_accounts_page(tmp_path / ".soloscale", locale="en")
    assert "Manage every account entry in one place." in page
    assert "Open profile" not in page
    assert "Open admin" not in page
    assert "Edit" in page
    assert "Account links and controlled connections" in page
    assert "Google OAuth Desktop credential is missing" in page
