import json
from pathlib import Path

from soloscale.content_canon import load_month_one_canon
from soloscale.content_models import ContentReviewDecision
from soloscale.content_ui import content_page, editorial_publishing_page, run_content_form
from soloscale.content_workspace import save_content_review
from soloscale.creator_accounts import normalize_account, save_creator_account
from soloscale.creator_production import create_run_artifacts
from soloscale.creator_workspace import creator_history_page, creator_overview_page
from soloscale.platform_accounts import ConnectedIdentity, save_connected_identity


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

    assert "第一个月 · 工程故事库" in story_bank
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
    assert "Artifact + exact account = publication task" in publish
    assert 'aria-current="page">History / Cost</a>' in history
    assert "A verified engineering story" in history
    assert "it adds no Analytics" in history


def test_publish_queue_shows_connected_account_capability_truth(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    result = run_content_form(_content_form(), data_root)
    assert result.run_id is not None
    save_content_review(
        data_root=data_root,
        run_id=result.run_id,
        decision=ContentReviewDecision.APPROVED,
    )
    create_run_artifacts(
        data_root=data_root,
        content_project_id="project-test",
        run_id=result.run_id,
        outputs=["ARTICLE"],
    )
    save_connected_identity(
        data_root,
        ConnectedIdentity(
            platform="linkedin",
            external_account_id="linkedin:member:123",
            display_name="pinball",
            handle="pinball",
            avatar_url=None,
            scopes=("r_liteprofile",),
            token_reference="",
            connected_at="2026-08-28T00:00:00+00:00",
        ),
        token_payload={
            "access_token": "test-token",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )

    publish = editorial_publishing_page(
        data_root=data_root, locale="en", creator_mode=True
    )

    assert "pinball" in publish
    assert "Connected · needs publishing permission" in publish
    assert "Manage accounts and permissions" in publish


def test_publish_queue_targets_one_persisted_approved_artifact(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    selected_run_id = "content-20260828T120000Z-aaaaaaaaaa"
    other_run_id = "content-20260828T110000Z-bbbbbbbbbb"
    for run_id in (selected_run_id, other_run_id):
        run_dir = data_root / "content-runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "26_distribution_package.json").write_text(
            json.dumps({"run_id": run_id}), encoding="utf-8"
        )

    selected = editorial_publishing_page(
        data_root=data_root,
        locale="zh-CN",
        creator_mode=True,
        selected_run_id=selected_run_id,
    )
    assert f'data-approved-artifact="{selected_run_id}"' in selected
    assert selected_run_id in selected
    assert other_run_id not in selected
    assert "将发布已审核版本" in selected
    assert 'name="verified_claims"' not in selected

    invalid_zh = editorial_publishing_page(
        data_root=data_root,
        locale="zh-CN",
        creator_mode=True,
        selected_run_id="content-missing",
    )
    invalid_en = editorial_publishing_page(
        data_root=data_root,
        locale="en",
        creator_mode=True,
        selected_run_id="content-missing",
    )
    assert "当前内容还没有可发布版本" in invalid_zh
    assert "This content has no publishable version yet" in invalid_en
    assert 'role="alert"' in invalid_zh
