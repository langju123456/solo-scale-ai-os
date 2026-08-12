"""Worker boundary tests with the LLM pipeline replaced by a deterministic fake."""

from __future__ import annotations

import asyncio
from pathlib import Path

from buildlog.config import Settings
from buildlog.pipeline import PipelineResult
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.web_repository import SQLAlchemyWebRepository
from buildlog.web_worker import WorkflowWorker


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        model="test-model",
        model_digest=None,
        api_base=None,
        temperature=0,
        max_tokens=100,
        threshold_accuracy=8,
        threshold_specificity=7,
        threshold_readability=7,
        threshold_value=7,
        threshold_evidence=7,
        prompt_version="v1",
        prompts_dir=tmp_path / "prompts",
        runs_dir=tmp_path / "runs",
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        web_worker_enabled=False,
        web_job_max_attempts=2,
        web_jobs_dir=tmp_path / "jobs",
    )


def _repositories(settings: Settings):
    run_repository = SQLAlchemyRunRepository(settings.database_url)
    run_repository.initialize()
    return run_repository, SQLAlchemyWebRepository(run_repository.engine)


def test_worker_completes_a_durable_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_repository, web_repository = _repositories(settings)
    created = web_repository.create_job(
        input_payload={"id": "iteration-4", "title": "Worker test"},
        idempotency_key="worker-success-001",
    )

    def fake_pipeline(input_path, _settings, _repository):
        assert input_path.exists()
        run_dir = tmp_path / "runs" / "run-from-worker"
        run_dir.mkdir(parents=True)
        final_path = run_dir / "06_final.md"
        final_path.write_text("done", encoding="utf-8")
        return PipelineResult(run_dir, final_path, {"accuracy": 9}, False)

    class RecordingStore:
        def __init__(self):
            self.run_dirs = []

        def mirror_run(self, run_dir):
            self.run_dirs.append(run_dir)
            return 1

    store = RecordingStore()
    worker = WorkflowWorker(
        settings,
        run_repository,
        web_repository,
        pipeline_runner=fake_pipeline,
        artifact_store=store,
    )

    assert asyncio.run(worker.run_once()) is True
    completed = web_repository.get_job(created.job.id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.run_id == "run-from-worker"
    assert store.run_dirs == [tmp_path / "runs" / "run-from-worker"]


def test_worker_sanitizes_failure_and_schedules_bounded_retry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_repository, web_repository = _repositories(settings)
    created = web_repository.create_job(
        input_payload={"id": "iteration-5"},
        idempotency_key="worker-failure-001",
    )

    def failing_pipeline(*_args):
        raise RuntimeError("secret provider response")

    worker = WorkflowWorker(
        settings,
        run_repository,
        web_repository,
        pipeline_runner=failing_pipeline,
    )

    assert asyncio.run(worker.run_once()) is True
    queued = web_repository.get_job(created.job.id)
    assert queued is not None
    assert queued.status == "queued"
    assert queued.error_category == "RuntimeError"
    assert "secret provider response" not in queued.safe_error_message


def test_worker_records_run_without_regenerating_after_mirror_failure(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_repository, web_repository = _repositories(settings)
    created = web_repository.create_job(
        input_payload={"id": "iteration-6"},
        idempotency_key="worker-mirror-failure-001",
    )

    def fake_pipeline(input_path, _settings, _repository):
        run_dir = tmp_path / "runs" / "run-needs-reconciliation"
        run_dir.mkdir(parents=True)
        final_path = run_dir / "06_final.md"
        final_path.write_text("done", encoding="utf-8")
        return PipelineResult(run_dir, final_path, {"accuracy": 9}, False)

    class FailingStore:
        def mirror_run(self, _run_dir):
            raise RuntimeError("storage credential must not leak")

    worker = WorkflowWorker(
        settings,
        run_repository,
        web_repository,
        pipeline_runner=fake_pipeline,
        artifact_store=FailingStore(),
    )

    assert asyncio.run(worker.run_once()) is True
    failed = web_repository.get_job(created.job.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.run_id == "run-needs-reconciliation"
    assert failed.error_category == "artifact_mirror_failed"
    assert "credential" not in failed.safe_error_message
    assert web_repository.claim_next_job() is None
