"""Durable queue and dashboard repository tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from buildlog.persistence_models import WorkflowJobTable
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.web_repository import SQLAlchemyWebRepository


def _repository(tmp_path: Path) -> SQLAlchemyWebRepository:
    run_repository = SQLAlchemyRunRepository(f"sqlite:///{tmp_path / 'web.db'}")
    run_repository.initialize()
    return SQLAlchemyWebRepository(run_repository.engine)


def test_job_lifecycle_retries_once_then_fails(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created = repository.create_job(
        input_payload={"id": "iteration-1"},
        idempotency_key="job-lifecycle-001",
    )

    first_claim = repository.claim_next_job()
    assert first_claim is not None
    assert first_claim[0].id == created.job.id
    assert first_claim[0].attempt_count == 1
    assert repository.fail_job(
        created.job.id,
        error_category="ProviderTimeout",
        safe_error_message="generation failed",
        max_attempts=2,
    ) == "queued"

    second_claim = repository.claim_next_job()
    assert second_claim is not None
    assert second_claim[0].attempt_count == 2
    assert repository.fail_job(
        created.job.id,
        error_category="ProviderTimeout",
        safe_error_message="generation failed",
        max_attempts=2,
    ) == "failed"
    assert repository.get_job(created.job.id).status == "failed"


def test_stale_running_job_is_recovered(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created = repository.create_job(
        input_payload={"id": "iteration-2"},
        idempotency_key="stale-job-001",
    )
    assert repository.claim_next_job() is not None
    with repository.engine.begin() as connection:
        connection.execute(
            update(WorkflowJobTable)
            .where(WorkflowJobTable.id == created.job.id)
            .values(started_at=datetime.now(UTC) - timedelta(hours=1))
        )

    assert repository.recover_stale_jobs(60, 2) == 1
    recovered = repository.get_job(created.job.id)
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.error_category == "stale_worker"


def test_dashboard_tracks_queue_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_job(
        input_payload={"id": "iteration-3"},
        idempotency_key="dashboard-job-001",
    )

    metrics = repository.dashboard()

    assert metrics.total_runs == 0
    assert metrics.queued_jobs == 1
    assert metrics.completion_rate == 0.0
    assert metrics.p95_pipeline_latency_ms is None
