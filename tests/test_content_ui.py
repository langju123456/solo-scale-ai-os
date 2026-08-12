from pathlib import Path

from soloscale.content_ui import content_page, run_content_form
from soloscale.content_workspace import load_content_run


def _form() -> dict[str, str]:
    return {
        "topic": "Evidence-first product integration",
        "audience": "AI engineers and solo builders",
        "language": "English",
        "source_label": "https://github.com/example/solo-scale/pull/8",
        "verified_claims": (
            "Python 3.11 and 3.12 CI checks passed. | "
            "https://github.com/example/solo-scale/actions/runs/8 | "
            "This does not prove production readiness."
        ),
        "observed_claims": (
            "The local UI exposes three product routes. | git:c39fb61"
        ),
        "hypotheses": "Evidence-first drafts may reduce human edit distance.",
        "planned": "Measure edits across the first three published assets.",
        "call_to_action": "Follow the next measured iteration.",
    }


def test_content_page_is_an_end_user_multichannel_workflow(tmp_path: Path) -> None:
    page = content_page(data_root=tmp_path / ".soloscale")
    assert 'action="/content/generate"' in page
    assert 'name="verified_claims"' in page
    assert "LinkedIn、X Thread 和短视频脚本" in page
    assert 'href="/"' in page
    assert 'href="/learning"' in page
    assert 'href="/advanced"' in page
    assert "自动发布" in page
    assert "DRAFT_REQUIRES_HUMAN_APPROVAL" not in page


def test_content_form_generates_preview_copy_and_downloads(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    result = run_content_form(_form(), data_root)
    assert result.error is None
    assert result.run_id is not None

    run = load_content_run(data_root, result.run_id)
    page = content_page(data_root=data_root, run_id=result.run_id)
    assert "一份素材，三个渠道" in page
    assert run.drafts.linkedin.strip() in page
    assert "复制全部" in page
    assert f"/content/downloads/{result.run_id}/linkedin.md" in page
    assert f"/content/downloads/{result.run_id}/video-script.md" in page
    assert "已私有保存" in page
    assert "Editorial provenance" in page
    assert "deterministic-content-template-v1" in page
    assert "Writer → Fresh Reviewer → Reviser" in page
    assert "没有连接或操作你的社交账号" in page
    assert result.run_id in page
    assert _form()["topic"] in page
    assert _form()["verified_claims"] in page
    assert f'action="/content/render/{result.run_id}"' in page
    assert "生成 MP4 视频" in page


def test_content_form_keeps_errors_user_facing_and_writes_nothing(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    invalid = _form()
    invalid["verified_claims"] = "A claim without a receipt"
    result = run_content_form(invalid, data_root)
    assert result.run_id is None
    assert result.error is not None
    assert "需要填写" in result.error
    assert not data_root.exists()

    page = content_page(data_root=data_root, form=invalid, error=result.error)
    assert invalid["topic"] in page
    assert 'role="alert"' in page
