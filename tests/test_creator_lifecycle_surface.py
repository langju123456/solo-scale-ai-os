from __future__ import annotations

from pathlib import Path

from soloscale.creator_production import (
    CreatorProductionError,
    CreatorProductionJobManager,
    CreatorProductionRequest,
    latest_job_for_story,
    list_creator_jobs,
    wait_for_creator_job,
)


def _story_request(story_id: str) -> CreatorProductionRequest:
    return CreatorProductionRequest(
        source_kind="STORY",
        source_story_id=story_id,
        outputs=["ARTICLE"],
        language="English",
        ai_editorial=False,
        add_to_queue=False,
    )


def test_latest_job_for_story_returns_most_recent_persisted_job(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".soloscale"
    manager = CreatorProductionJobManager()

    def failing_runner() -> str:
        raise CreatorProductionError("bounded failure")

    first = manager.submit(
        data_root=data_root,
        request=_story_request("M1-13"),
        runner=failing_runner,
    )
    second = manager.submit(
        data_root=data_root,
        request=_story_request("M1-13"),
        runner=failing_runner,
    )
    wait_for_creator_job(manager, data_root, first.job_id)
    wait_for_creator_job(manager, data_root, second.job_id)

    latest = latest_job_for_story(data_root, "M1-13")
    assert latest is not None
    assert latest.job_id == second.job_id
    assert latest_job_for_story(data_root, "M1-14") is None


def test_failed_job_is_persisted_and_listed(tmp_path: Path) -> None:
    data_root = tmp_path / ".soloscale"
    manager = CreatorProductionJobManager()

    def failing_runner() -> str:
        raise CreatorProductionError("bounded failure")

    job = manager.submit(
        data_root=data_root,
        request=_story_request("M1-13"),
        runner=failing_runner,
    )
    wait_for_creator_job(manager, data_root, job.job_id)

    listed = list_creator_jobs(data_root)
    persisted = next(item for item in listed if item.job_id == job.job_id)
    assert persisted.phase == "FAILED"
    assert persisted.error_code is not None
    assert persisted.request.source_story_id == "M1-13"
