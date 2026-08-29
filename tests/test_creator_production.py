from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from soloscale.content_models import (
    ClaimStatus,
    ContentBrief,
    ContentClaim,
    ContentReviewDecision,
)
from soloscale.content_workspace import (
    run_content_workspace,
    save_content_review,
)
from soloscale.creator_production import (
    CreatorProductionError,
    CreatorProductionJobManager,
    CreatorProductionRequest,
    assign_artifact_to_account,
    create_run_artifacts,
    load_creator_production_job,
    load_creator_production_jobs,
    load_publication_artifacts,
    load_publish_queue,
    wait_for_creator_job,
)
from soloscale.platform_accounts import (
    ConnectedIdentity,
    save_connected_identity,
)


def _brief() -> ContentBrief:
    return ContentBrief(
        topic="A verified engineering story",
        audience="AI engineers",
        language="English",
        call_to_action="Share your experience.",
        source_label="git:abc123",
        claims=[
            ContentClaim(
                id="CLAIM-01",
                text="A focused test passed.",
                status=ClaimStatus.VERIFIED,
                receipt="git:abc123",
            )
        ],
    )


def _linkedin_identity(*, publish: bool) -> ConnectedIdentity:
    scopes = ("w_member_social",) if publish else ("r_liteprofile",)
    return ConnectedIdentity(
        platform="linkedin",
        external_account_id="linkedin:member:123",
        display_name="pinball",
        handle="pinball",
        avatar_url=None,
        scopes=scopes,
        token_reference="",
        connected_at=datetime.now(UTC).isoformat(),
    )


def _save_linkedin_identity(data_root: Path, *, publish: bool) -> None:
    save_connected_identity(
        data_root,
        _linkedin_identity(publish=publish),
        token_payload={
            "access_token": "test-token",
            "refresh_token": "test-refresh",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )


def test_run_artifacts_seal_draft_and_approved_article_outputs(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())

    draft_artifacts = create_run_artifacts(
        data_root=data_root,
        content_project_id="project-test",
        run_id=run.run_id,
        outputs=["ARTICLE"],
    )
    assert len(draft_artifacts) == 2
    by_platform = {item.platform: item for item in draft_artifacts}
    assert set(by_platform) == {"linkedin", "x"}
    assert by_platform["linkedin"].artifact_type == "ARTICLE"
    assert by_platform["x"].artifact_type == "THREAD"
    assert all(item.review_status == "DRAFT" for item in draft_artifacts)
    assert all(item.status == "READY" for item in draft_artifacts)
    assert all(item.truth_status == "VALIDATED" for item in draft_artifacts)
    source_path = by_platform["linkedin"].source_path
    run_dir = data_root / "content-runs" / run.run_id
    assert (run_dir / source_path).is_file()
    assert by_platform["linkedin"].source_sha256

    save_content_review(
        data_root=data_root,
        run_id=run.run_id,
        decision=ContentReviewDecision.APPROVED,
    )
    approved_artifacts = create_run_artifacts(
        data_root=data_root,
        content_project_id="project-test",
        run_id=run.run_id,
        outputs=["ARTICLE"],
    )
    assert all(item.review_status == "APPROVED" for item in approved_artifacts)
    assert {item.artifact_id for item in load_publication_artifacts(
        data_root, run_id=run.run_id
    )} == {item.artifact_id for item in approved_artifacts}


def test_run_artifacts_seal_video_outputs_from_rendered_media(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    run_dir = data_root / "content-runs" / run.run_id
    for filename in ("21_creator_video_youtube.mp4", "10_creator_video.mp4"):
        (run_dir / filename).write_bytes(b"rendered-video")

    artifacts = create_run_artifacts(
        data_root=data_root,
        content_project_id="project-test",
        run_id=run.run_id,
        outputs=["VIDEO"],
    )

    assert {item.platform for item in artifacts} == {"youtube", "douyin"}
    assert all(item.artifact_type == "VIDEO" for item in artifacts)


def test_assign_artifact_to_account_requires_approval_and_exact_capability(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    artifacts = create_run_artifacts(
        data_root=data_root,
        content_project_id="project-test",
        run_id=run.run_id,
        outputs=["ARTICLE"],
    )
    linkedin_artifact = next(item for item in artifacts if item.platform == "linkedin")
    _save_linkedin_identity(data_root, publish=True)

    with pytest.raises(CreatorProductionError, match="Approve"):
        assign_artifact_to_account(
            data_root=data_root,
            artifact_id=linkedin_artifact.artifact_id,
            channel_account_id="linkedin:member:123",
        )

    save_content_review(
        data_root=data_root,
        run_id=run.run_id,
        decision=ContentReviewDecision.APPROVED,
    )
    approved = next(
        item
        for item in create_run_artifacts(
            data_root=data_root,
            content_project_id="project-test",
            run_id=run.run_id,
            outputs=["ARTICLE"],
        )
        if item.platform == "linkedin"
    )
    with pytest.raises(CreatorProductionError, match="not publish-capable"):
        assign_artifact_to_account(
            data_root=data_root,
            artifact_id=approved.artifact_id,
            channel_account_id="linkedin:member:999",
        )
    queued = assign_artifact_to_account(
        data_root=data_root,
        artifact_id=approved.artifact_id,
        channel_account_id="linkedin:member:123",
    )
    assert queued.status == "READY"
    assert queued.channel_account_name == "pinball"
    again = assign_artifact_to_account(
        data_root=data_root,
        artifact_id=approved.artifact_id,
        channel_account_id="linkedin:member:123",
    )
    assert again.queue_item_id == queued.queue_item_id
    assert [item.queue_item_id for item in load_publish_queue(data_root)] == [
        queued.queue_item_id
    ]


def test_creator_production_job_lifecycle(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    manager = CreatorProductionJobManager()
    try:
        job = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["ARTICLE"],
                language="English",
                ai_editorial=False,
            ),
            runner=lambda: run.run_id,
        )
        finished = wait_for_creator_job(manager, data_root, job.job_id)
        assert finished.phase == "READY"
        assert len(finished.artifact_ids) == 2
        assert finished.model_calls == 0

        not_executed = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["ARTICLE"],
                language="English",
                ai_editorial=True,
            ),
            runner=lambda: run.run_id,
        )
        failed_state = wait_for_creator_job(
            manager, data_root, not_executed.job_id
        )
        assert failed_state.phase == "AI_NOT_EXECUTED"
        assert failed_state.error_code == "AI_NOT_EXECUTED"

        failed = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["ARTICLE"],
                language="English",
                ai_editorial=False,
            ),
            runner=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        failed_final = wait_for_creator_job(manager, data_root, failed.job_id)
        assert failed_final.phase == "FAILED"
        assert failed_final.error_code == "RUNTIMEERROR"
    finally:
        manager.shutdown()


def test_creator_production_jobs_are_persisted_and_listed(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    manager = CreatorProductionJobManager()
    try:
        first = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["ARTICLE"],
                language="English",
                ai_editorial=False,
            ),
            runner=lambda: run.run_id,
        )
        second = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["VIDEO"],
                language="English",
                ai_editorial=False,
            ),
            runner=lambda: run.run_id,
            renderer=lambda _run_id: None,
        )
        jobs = load_creator_production_jobs(data_root)
        assert [job.job_id for job in jobs][:2] == [second.job_id, first.job_id]
        assert load_creator_production_job(data_root, first.job_id).job_id == first.job_id
        assert load_creator_production_job(data_root, "missing") is None
    finally:
        manager.shutdown()


def test_generate_and_queue_auto_assigns_the_single_eligible_account(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    run = run_content_workspace(data_root=data_root, brief=_brief())
    save_content_review(
        data_root=data_root,
        run_id=run.run_id,
        decision=ContentReviewDecision.APPROVED,
    )
    _save_linkedin_identity(data_root, publish=True)
    manager = CreatorProductionJobManager()
    try:
        job = manager.submit(
            data_root=data_root,
            request=CreatorProductionRequest(
                source_kind="CREATE",
                outputs=["ARTICLE"],
                language="English",
                ai_editorial=False,
                add_to_queue=True,
            ),
            runner=lambda: run.run_id,
        )
        finished = wait_for_creator_job(manager, data_root, job.job_id)
        assert finished.phase == "READY"
        assert len(finished.queue_item_ids) == 1
        queue = load_publish_queue(data_root)
        assert len(queue) == 1
        assert queue[0].channel_account_id == "linkedin:member:123"
        assert queue[0].platform == "linkedin"
    finally:
        manager.shutdown()
