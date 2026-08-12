"""Durable single-consumer worker for API-submitted generation jobs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path

from buildlog.artifact_store import ArtifactStore, create_artifact_store
from buildlog.config import Settings
from buildlog.pipeline import PipelineResult, run_pipeline
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.web_repository import SQLAlchemyWebRepository

LOGGER = logging.getLogger(__name__)
PipelineRunner = Callable[
    [Path, Settings, SQLAlchemyRunRepository],
    PipelineResult,
]


class WorkflowWorker:
    """Poll durable jobs and execute the existing bounded LLM pipeline."""

    def __init__(
        self,
        settings: Settings,
        run_repository: SQLAlchemyRunRepository,
        web_repository: SQLAlchemyWebRepository,
        *,
        pipeline_runner: PipelineRunner = run_pipeline,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._settings = settings
        self._run_repository = run_repository
        self._web_repository = web_repository
        self._pipeline_runner = pipeline_runner
        self._artifact_store = artifact_store or create_artifact_store(settings)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """Recover interrupted work and start one asynchronous polling loop."""
        recovered = await asyncio.to_thread(
            self._web_repository.recover_stale_jobs,
            self._settings.web_job_stale_seconds,
            self._settings.web_job_max_attempts,
        )
        if recovered:
            LOGGER.warning("recovered %s stale workflow job(s)", recovered)
        self._task = asyncio.create_task(self._run_forever(), name="buildlog-worker")

    async def stop(self) -> None:
        """Stop polling and wait for the current claim loop to finish."""
        self._stop.set()
        if self._task is not None:
            await self._task

    async def run_once(self) -> bool:
        """Claim and execute at most one job; return whether work was found."""
        claimed = await asyncio.to_thread(self._web_repository.claim_next_job)
        if claimed is None:
            return False
        job, payload = claimed
        await asyncio.to_thread(self._execute, job.id, payload)
        return True

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                found = await self.run_once()
            except Exception:
                LOGGER.exception("workflow worker loop failed")
                found = False
            if not found:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._settings.web_worker_poll_seconds,
                    )
                except TimeoutError:
                    continue

    def _execute(self, job_id: str, payload: dict[str, object]) -> None:
        jobs_dir = self._settings.web_jobs_dir or (
            self._settings.runs_dir.parent / ".buildlog" / "jobs"
        )
        jobs_dir.mkdir(parents=True, exist_ok=True)
        input_path = jobs_dir / f"{job_id}.json"
        temporary_path = input_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(input_path)
        try:
            result = self._pipeline_runner(
                input_path,
                self._settings,
                self._run_repository,
            )
        except Exception as exc:
            category = type(exc).__name__[:100]
            status = self._web_repository.fail_job(
                job_id,
                error_category=category,
                safe_error_message="generation failed; inspect sanitized run telemetry",
                max_attempts=self._settings.web_job_max_attempts,
            )
            LOGGER.warning("workflow job %s transitioned to %s", job_id, status)
            return
        try:
            uploaded = self._artifact_store.mirror_run(result.run_dir)
        except Exception:
            self._web_repository.fail_job(
                job_id,
                error_category="artifact_mirror_failed",
                safe_error_message=(
                    "generation completed but artifact mirroring failed; "
                    "reconcile the recorded run before retrying"
                ),
                max_attempts=self._settings.web_job_max_attempts,
                run_id=result.run_dir.name,
                retryable=False,
            )
            LOGGER.exception(
                "workflow job %s generated run %s but artifact mirroring failed",
                job_id,
                result.run_dir.name,
            )
            return
        self._web_repository.complete_job(job_id, result.run_dir.name)
        LOGGER.info("workflow job %s completed as run %s", job_id, result.run_dir.name)
        if uploaded:
            LOGGER.info("mirrored %s artifact(s) for run %s", uploaded, result.run_dir.name)
