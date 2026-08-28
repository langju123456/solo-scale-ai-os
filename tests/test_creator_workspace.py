from pathlib import Path

from soloscale.content_canon import load_month_one_canon
from soloscale.content_ui import content_page, editorial_publishing_page, run_content_form
from soloscale.creator_accounts import normalize_account, save_creator_account
from soloscale.creator_workspace import creator_history_page, creator_overview_page


def _content_form() -> dict[str, str]:
    return {
        "topic": "A verified engineering story",
        "audience": "AI engineers",
        "language": "English",
        "source_label": "git:abc123",
        "verified_claims": "A focused test passed. | git:abc123",
        "observed_claims": "",
        "hypotheses": "",
        "planned": "",
        "call_to_action": "Share your experience.",
        "generation_mode": "template",
    }


def test_creator_overview_uses_existing_account_and_content_state(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    save_creator_account(
        data_root,
        normalize_account(platform="youtube", display_name="SoloScale", status="ACTIVE"),
    )
    result = run_content_form(_content_form(), data_root)
    assert result.run_id is not None

    page = creator_overview_page(data_root)

    assert "创作者工作区" in page
    assert "1 / 7" in page
    assert "A verified engineering story" in page
    assert 'href="/creator/accounts?lang=zh-CN"' in page
    assert 'href="/creator/stories?lang=zh-CN"' in page
    assert 'href="/creator/create?lang=zh-CN"' in page
    assert 'href="/creator/publish?lang=zh-CN"' in page
    assert 'href="/creator/history?lang=zh-CN"' in page


def test_story_bank_and_create_are_distinct_views(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    story = load_month_one_canon().stories[0]

    story_bank = content_page(data_root=data_root, workspace_view="stories")
    create = content_page(
        data_root=data_root,
        workspace_view="create",
        canon_story_id=story.story_id,
        canon_format="video",
    )

    assert "Month 1 · Engineering Story Library" in story_bank
    assert ".reference-video-upload,.grid{display:none!important}" in story_bank
    assert "target.searchParams.set('canon_story_id'" in story_bank
    assert 'aria-current="page">故事库</a>' in story_bank
    assert ".month-one-canon{display:none!important}" in create
    assert 'name="creator_view" value="create"' in create
    assert f'month-one-canon:{story.story_id}' in create
    assert 'aria-current="page">创作</a>' in create


def test_publish_and_history_stay_inside_creator_workspace(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    result = run_content_form(_content_form(), data_root)
    assert result.run_id is not None

    publish = editorial_publishing_page(
        data_root=data_root, locale="en", creator_mode=True
    )
    history = creator_history_page(data_root, locale="en")

    assert 'aria-current="page">Publish Queue</a>' in publish
    assert "This page never reads tokens or publishes automatically." in publish
    assert 'aria-current="page">History / Cost</a>' in history
    assert "A verified engineering story" in history
    assert "it adds no Analytics" in history
