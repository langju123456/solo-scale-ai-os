"""Minimal handoff from a reviewed SoloScale content artifact to BuildLog."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from soloscale.content_workspace import content_run_directory
from soloscale.resume_workspace import ResumeWorkspaceStorageError, _atomic_private_write

Channel = Literal["linkedin", "x"]
_SOURCE_NAMES: dict[Channel, str] = {"linkedin": "02_linkedin.md", "x": "03_x_thread.md"}


class BuildLogHandoffError(ValueError):
    """Raised when BuildLog cannot accept or report a SoloScale content handoff."""


def stage_for_buildlog(*, data_root: Path, run_id: str, channel: Channel) -> dict[str, str]:
    """Stage an exact Content Studio artifact without publishing it."""

    run_dir = content_run_directory(data_root, run_id)
    record_path = run_dir / f"12_buildlog_{channel}.json"
    if record_path.exists() or record_path.is_symlink():
        return _load_record(record_path)
    source = run_dir / _SOURCE_NAMES[channel]
    if source.is_symlink() or not source.is_file():
        raise BuildLogHandoffError("Content artifact is unavailable")
    payload = _run_buildlog(
        "external",
        "stage",
        "--source",
        str(source),
        "--source-run-id",
        run_id,
        "--channel",
        channel,
    )
    buildlog_run_id = payload.get("buildlog_run_id")
    if not isinstance(buildlog_run_id, str) or not buildlog_run_id:
        raise BuildLogHandoffError("BuildLog did not return a staging receipt")
    record = {
        "status": "STAGED_FOR_BUILDLOG_APPROVAL",
        "channel": channel,
        "source_run_id": run_id,
        "source_artifact": _SOURCE_NAMES[channel],
        "buildlog_run_id": buildlog_run_id,
    }
    try:
        _atomic_private_write(record_path, json.dumps(record, indent=2) + "\n")
    except (OSError, ResumeWorkspaceStorageError) as exc:
        raise BuildLogHandoffError("BuildLog handoff was staged but could not be recorded") from exc
    return record


def sync_buildlog_receipt(
    *, data_root: Path, run_id: str, channel: Channel
) -> dict[str, str] | None:
    """Persist BuildLog's returned platform post ID only after it reports success."""

    run_dir = content_run_directory(data_root, run_id)
    handoff = _load_record(run_dir / f"12_buildlog_{channel}.json")
    receipt_path = run_dir / f"13_buildlog_{channel}_receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        return _load_record(receipt_path)
    payload = _run_buildlog("external", "receipt", handoff["buildlog_run_id"])
    if payload.get("receipt") is None and "receipt_id" not in payload:
        return None
    required = ("receipt_id", "platform", "external_post_id", "published_at", "buildlog_run_id")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        raise BuildLogHandoffError("BuildLog publication receipt is incomplete")
    receipt = {key: payload[key] for key in required}
    try:
        _atomic_private_write(receipt_path, json.dumps(receipt, indent=2) + "\n")
    except (OSError, ResumeWorkspaceStorageError) as exc:
        raise BuildLogHandoffError("BuildLog receipt could not be recorded") from exc
    return receipt


def buildlog_handoff_status(
    data_root: Path, run_id: str, channel: Channel
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    run_dir = content_run_directory(data_root, run_id)
    handoff_path = run_dir / f"12_buildlog_{channel}.json"
    receipt_path = run_dir / f"13_buildlog_{channel}_receipt.json"
    handoff = (
        _load_record(handoff_path)
        if handoff_path.is_file() and not handoff_path.is_symlink()
        else None
    )
    receipt = (
        _load_record(receipt_path)
        if receipt_path.is_file() and not receipt_path.is_symlink()
        else None
    )
    return handoff, receipt


def _run_buildlog(*arguments: str) -> dict[str, object]:
    root_value = os.environ.get("SOLOSCALE_BUILDLOG_ROOT", "").strip()
    if not root_value:
        raise BuildLogHandoffError("Set SOLOSCALE_BUILDLOG_ROOT before using BuildLog publishing")
    root = Path(root_value).expanduser()
    if not (root / "src" / "buildlog" / "main.py").is_file():
        raise BuildLogHandoffError("SOLOSCALE_BUILDLOG_ROOT does not contain BuildLog")
    python = root / ".venv" / "bin" / "python"
    executable = str(python) if python.is_file() else sys.executable
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [executable, "-m", "buildlog.main", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise BuildLogHandoffError("BuildLog could not stage this artifact")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BuildLogHandoffError("BuildLog returned an invalid handoff response") from exc
    if not isinstance(parsed, dict):
        raise BuildLogHandoffError("BuildLog returned an invalid handoff response")
    return parsed


def _load_record(path: Path) -> dict[str, str]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildLogHandoffError("BuildLog handoff record is invalid") from exc
    if not isinstance(loaded, dict) or not all(isinstance(value, str) for value in loaded.values()):
        raise BuildLogHandoffError("BuildLog handoff record is invalid")
    return loaded
