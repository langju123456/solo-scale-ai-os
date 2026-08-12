import json
from pathlib import Path

import pytest

from soloscale.buildlog_handoff import (
    buildlog_handoff_status,
    stage_for_buildlog,
    sync_buildlog_receipt,
)
from soloscale.content_models import ClaimStatus, ContentBrief, ContentClaim
from soloscale.content_workspace import (
    ContentWorkspaceError,
    content_download,
    load_content_run,
    parse_claim_ledger,
    run_content_workspace,
)
from soloscale.video_factory import creator_video_ready, render_creator_video


def _brief() -> ContentBrief:
    return ContentBrief(
        topic="Evidence-first product integration",
        audience="AI engineers and solo builders",
        language="English",
        call_to_action="Follow the next measured iteration.",
        source_label="https://github.com/example/solo-scale/pull/8",
        claims=[
            ContentClaim(
                id="CLAIM-01",
                text="Python 3.11 and 3.12 CI checks passed.",
                status=ClaimStatus.VERIFIED,
                receipt="https://github.com/example/solo-scale/actions/runs/8",
                limits="This does not prove production readiness.",
            ),
            ContentClaim(
                id="CLAIM-02",
                text="A unified local UI now exposes Resume, Learning, and Content routes.",
                status=ClaimStatus.OBSERVED,
                receipt="git:c39fb61",
            ),
            ContentClaim(
                id="CLAIM-03",
                text="The next experiment will measure human edit distance.",
                status=ClaimStatus.PLANNED,
            ),
        ],
    )


def test_parse_claim_ledger_requires_receipts_for_grounded_statuses() -> None:
    claims = parse_claim_ledger(
        "VERIFIED | 285 tests passed. | https://example.test/ci | Local run only.\n"
        "HYPOTHESIS | Evidence-first drafts may reduce editing time."
    )
    assert [claim.id for claim in claims] == ["CLAIM-01", "CLAIM-02"]
    assert claims[0].status is ClaimStatus.VERIFIED
    assert claims[1].receipt is None

    with pytest.raises(ContentWorkspaceError, match="does not satisfy"):
        parse_claim_ledger("OBSERVED | Users preferred the new flow.")


def test_content_workspace_writes_private_reviewable_multichannel_pack(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    run_dir = data_root / "content-runs" / run.run_id

    assert run.status == "DRAFT_REQUIRES_HUMAN_APPROVAL"
    assert run.network_used is False
    assert run.model_used is False
    assert run.publication_performed is False
    assert set(run.artifact_paths) == {path.name for path in run_dir.iterdir()}
    assert run_dir.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in run_dir.iterdir())

    linkedin = (run_dir / "02_linkedin.md").read_text(encoding="utf-8")
    x_thread = (run_dir / "03_x_thread.md").read_text(encoding="utf-8")
    video = (run_dir / "04_video_script.md").read_text(encoding="utf-8")
    verification = json.loads((run_dir / "08_verification.json").read_text())
    assert "CLAIM-01" in linkedin
    assert "This does not prove production readiness" in linkedin
    assert "1/5" in x_thread
    assert "Claim anchors: CLAIM-01" in video
    assert verification == {
        "claim_count": 3,
        "credential_shape_scan_passed": True,
        "every_claim_has_anchor": True,
        "model_used": False,
        "network_used": False,
        "private_path_scan_passed": True,
        "publication_performed": False,
        "status": "PASS",
        "verified_and_observed_have_receipts": True,
    }

    loaded = load_content_run(data_root, run.run_id)
    assert loaded == run
    artifact_name, content = content_download(data_root, run.run_id, "linkedin.md")
    assert artifact_name == "02_linkedin.md"
    assert content.decode("utf-8") == linkedin


def test_content_workspace_repeat_runs_never_overwrite(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    first = run_content_workspace(data_root=data_root, brief=_brief())
    second = run_content_workspace(data_root=data_root, brief=_brief())
    assert first.run_id != second.run_id
    assert len(list((data_root / "content-runs").iterdir())) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_label", "/Users/private/secret.json"),
        ("source_label", "file:///tmp/private.json"),
        ("call_to_action", "Use sk-secretcredential12345"),
    ],
)
def test_content_workspace_rejects_private_paths_and_credential_shapes(
    tmp_path: Path, field: str, value: str
) -> None:
    brief = _brief().model_copy(update={field: value})
    with pytest.raises(ContentWorkspaceError, match="private|credential"):
        run_content_workspace(data_root=tmp_path / ".soloscale", brief=brief)
    assert not (tmp_path / ".soloscale").exists()


def test_content_workspace_rejects_symlinked_data_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_root = tmp_path / ".soloscale"
    data_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContentWorkspaceError, match="symlink"):
        run_content_workspace(data_root=data_root, brief=_brief())
    assert list(outside.iterdir()) == []


def test_content_download_rejects_unknown_names_and_symlinks(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())

    with pytest.raises(ContentWorkspaceError, match="not downloadable"):
        content_download(data_root, run.run_id, "run.json")

    run_dir = data_root / "content-runs" / run.run_id
    original = run_dir / "02_linkedin.md"
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    original.unlink()
    original.symlink_to(outside)
    with pytest.raises(ContentWorkspaceError, match="unsafe"):
        content_download(data_root, run.run_id, "linkedin.md")


def test_creator_video_render_uses_only_saved_storyboard_and_is_non_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    repository_root = tmp_path / "repo"
    renderer = repository_root / "video_factory" / "render.mjs"
    renderer.parent.mkdir(parents=True)
    renderer.write_text("// test renderer", encoding="utf-8")

    def fake_run(command: list[str], **_: object) -> object:
        output = Path(command[-1])
        output.write_bytes(b"mp4")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("soloscale.video_factory.subprocess.run", fake_run)
    output = render_creator_video(
        data_root=data_root, run_id=run.run_id, repository_root=repository_root
    )

    assert output.name == "10_creator_video.mp4"
    assert creator_video_ready(data_root, run.run_id) is True
    input_payload = json.loads((output.parent / "09_creator_video_input.json").read_text())
    assert input_payload["scenes"][0]["claim_ids"] == ["CLAIM-01"]
    assert (output.parent / "11_creator_video_render.json").is_file()
    with pytest.raises(ValueError, match="already has"):
        render_creator_video(
            data_root=data_root,
            run_id=run.run_id,
            repository_root=repository_root,
        )
    artifact_name, artifact = content_download(data_root, run.run_id, "creator-video.mp4")
    assert artifact_name == output.name
    assert artifact == b"mp4"


def test_buildlog_handoff_stages_exact_artifact_and_persists_returned_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    responses = [
        {"buildlog_run_id": "soloscale-linkedin-123"},
        {
            "receipt_id": "receipt-123",
            "platform": "linkedin",
            "external_post_id": "urn:li:share:123",
            "published_at": "2026-08-12T00:00:00+00:00",
            "buildlog_run_id": "soloscale-linkedin-123",
        },
    ]
    monkeypatch.setattr(
        "soloscale.buildlog_handoff._run_buildlog",
        lambda *args: responses.pop(0),
    )

    handoff = stage_for_buildlog(data_root=data_root, run_id=run.run_id, channel="linkedin")
    receipt = sync_buildlog_receipt(data_root=data_root, run_id=run.run_id, channel="linkedin")

    assert handoff["buildlog_run_id"] == "soloscale-linkedin-123"
    assert receipt is not None
    assert receipt["external_post_id"] == "urn:li:share:123"
    assert buildlog_handoff_status(data_root, run.run_id, "linkedin") == (handoff, receipt)
