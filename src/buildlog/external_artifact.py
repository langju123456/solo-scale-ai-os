"""Stage a reviewed external artifact for BuildLog's existing publish controls."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from buildlog.config import Settings
from buildlog.domain import ArtifactRecord, IterationRecord, ProjectRecord, PromptVersionRecord, RunRecord
from buildlog.hashing import sha256_file
from buildlog.repository import RunRepository
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository

_PROJECT_ID = "soloscale-content"
_PROMPT_ID = "external-artifact-v1"


class ExternalArtifactError(ValueError):
    """Raised when an external artifact cannot safely enter the publish workflow."""


def stage_soloscale_artifact(
    *,
    settings: Settings,
    repository: RunRepository,
    source_path: Path,
    source_run_id: str,
    channel: str,
) -> str:
    """Copy one exact reviewed artifact into a completed BuildLog publishing run."""

    if channel not in {"linkedin", "x"}:
        raise ExternalArtifactError("channel must be linkedin or x")
    if source_path.is_symlink() or not source_path.is_file():
        raise ExternalArtifactError("source artifact must be a regular file")
    try:
        content = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExternalArtifactError("source artifact is unreadable") from exc
    if not content.strip():
        raise ExternalArtifactError("source artifact must not be blank")
    source_hash = sha256_file(source_path)
    digest = hashlib.sha256(f"{source_run_id}:{channel}:{source_hash}".encode()).hexdigest()[:20]
    run_id = f"soloscale-{channel}-{digest}"
    target_dir = settings.runs_dir / run_id
    target = target_dir / "06_final.md"
    existing = repository.get_run(run_id)
    if existing is not None:
        _validate_existing(repository, run_id, target, source_hash)
        return run_id
    if target_dir.exists():
        raise ExternalArtifactError("BuildLog target directory already exists without a run")
    target_dir.mkdir(parents=True, mode=0o700)
    try:
        _write_private(target, content)
        _write_private(
            target_dir / "00_soloscale_source.json",
            json.dumps(
                {
                    "source_system": "SoloScale",
                    "source_run_id": source_run_id,
                    "channel": channel,
                    "source_artifact_sha256": source_hash,
                },
                sort_keys=True,
            )
            + "\n",
        )
        now = datetime.now(UTC)
        repository.save_project(ProjectRecord(id=_PROJECT_ID, name="SoloScale Content"))
        iteration_id = f"{run_id}:iteration"
        repository.save_iteration(
            IterationRecord(
                id=iteration_id,
                project_id=_PROJECT_ID,
                title=f"SoloScale {channel} publication",
                goal="Publish an explicitly reviewed SoloScale content artifact.",
                context="Imported without generation; original content remains unchanged.",
                problem="External artifact needs BuildLog approval and receipt controls.",
                audience="SoloScale operator",
                raw_input={"source_run_id": source_run_id, "source_sha256": source_hash},
                created_at=now,
            )
        )
        repository.save_prompt_version(
            PromptVersionRecord(
                id=_PROMPT_ID,
                prompt_name="external-artifact",
                version="v1",
                file_path="external://soloscale",
                content_hash=hashlib.sha256(b"external-artifact-v1").hexdigest(),
            )
        )
        repository.save_run(
            RunRecord(
                id=run_id,
                iteration_id=iteration_id,
                model="external-artifact",
                planner_prompt_version_id=_PROMPT_ID,
                writer_prompt_version_id=_PROMPT_ID,
                evaluator_prompt_version_id=_PROMPT_ID,
                reviser_prompt_version_id=_PROMPT_ID,
                started_at=now,
            )
        )
        repository.save_artifact(
            ArtifactRecord(
                id=f"{run_id}:final",
                run_id=run_id,
                artifact_type="final",
                file_path=str(target.resolve()),
                content_hash=sha256_file(target),
            )
        )
        repository.complete_run(run_id, False, now)
    except Exception:
        raise
    return run_id


def latest_publication_receipt(*, settings: Settings, run_id: str) -> dict[str, str] | None:
    """Return one safe, completed receipt summary for a staged external run."""

    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()
    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = (
            session.query(PublishReceiptTable)
            .filter(PublishReceiptTable.run_id == run_id)
            .filter(PublishReceiptTable.status == "succeeded")
            .order_by(PublishReceiptTable.published_at.desc())
            .first()
        )
    if row is None:
        return None
    return {
        "receipt_id": row.id,
        "platform": row.platform,
        "external_post_id": row.external_post_id,
        "published_at": row.published_at.isoformat(),
        "buildlog_run_id": run_id,
    }


def _validate_existing(repository: RunRepository, run_id: str, target: Path, source_hash: str) -> None:
    artifacts = [item for item in repository.list_artifacts(run_id) if item.artifact_type == "final"]
    if len(artifacts) != 1 or not target.is_file() or target.is_symlink():
        raise ExternalArtifactError("existing BuildLog import is incomplete")
    source_record = target.parent / "00_soloscale_source.json"
    try:
        recorded = json.loads(source_record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalArtifactError("existing BuildLog import has no valid source record") from exc
    if recorded.get("source_artifact_sha256") != source_hash:
        raise ExternalArtifactError("existing BuildLog import does not match the source artifact")


def _write_private(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ExternalArtifactError("BuildLog artifact permissions are unsafe")
