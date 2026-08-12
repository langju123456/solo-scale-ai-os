from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from soloscale.content_workspace import (
    ContentWorkspaceError,
    content_run_directory,
    load_content_run,
)
from soloscale.resume_workspace import ResumeWorkspaceStorageError, _atomic_private_write

_INPUT_NAME = "09_creator_video_input.json"
_VIDEO_NAME = "10_creator_video.mp4"
_RECEIPT_NAME = "11_creator_video_render.json"
_MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class CreatorVideoError(ValueError):
    """Raised when a local Creator Video render cannot be completed safely."""


def creator_video_ready(data_root: Path, run_id: str) -> bool:
    try:
        path = content_run_directory(data_root, run_id) / _VIDEO_NAME
        metadata = path.lstat()
    except (ContentWorkspaceError, FileNotFoundError):
        return False
    return stat.S_ISREG(metadata.st_mode) and not path.is_symlink() and metadata.st_size > 0


def render_creator_video(*, data_root: Path, run_id: str, repository_root: Path) -> Path:
    """Render a saved storyboard to a non-overwriting, local MP4 artifact."""

    run = load_content_run(data_root, run_id)
    run_dir = content_run_directory(data_root, run_id)
    input_path = run_dir / _INPUT_NAME
    output_path = run_dir / _VIDEO_NAME
    receipt_path = run_dir / _RECEIPT_NAME
    if any(path.exists() or path.is_symlink() for path in (input_path, output_path, receipt_path)):
        raise CreatorVideoError("This run already has a Creator Video render")
    factory_root = repository_root / "video_factory"
    renderer = factory_root / "render.mjs"
    if not renderer.is_file():
        raise CreatorVideoError("Creator Video Factory is unavailable")
    input_payload = {
        "topic": run.brief.topic,
        "sourceLabel": run.brief.source_label,
        "scenes": [
            {
                "id": scene.id,
                "start_second": scene.start_second,
                "end_second": scene.end_second,
                "purpose": scene.purpose,
                "voiceover": scene.voiceover,
                "on_screen_text": scene.on_screen_text,
                "claim_ids": scene.claim_ids,
            }
            for scene in run.drafts.storyboard
        ],
    }
    try:
        _atomic_private_write(input_path, json.dumps(input_payload, ensure_ascii=False) + "\n")
    except (OSError, ResumeWorkspaceStorageError) as exc:
        raise CreatorVideoError("Could not save Creator Video input") from exc
    environment = os.environ.copy()
    if _MACOS_CHROME.is_file():
        environment.setdefault("REMOTION_BROWSER_EXECUTABLE", str(_MACOS_CHROME))
    completed = subprocess.run(
        ["node", str(renderer), "--input", str(input_path), "--output", str(output_path)],
        cwd=factory_root,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
        env=environment,
    )
    if completed.returncode != 0 or not creator_video_ready(data_root, run_id):
        output_path.unlink(missing_ok=True)
        raise CreatorVideoError("Creator Video render failed; review the local renderer setup")
    os.chmod(output_path, 0o600)
    receipt = {
        "status": "RENDERED_LOCAL_MP4",
        "video": _VIDEO_NAME,
        "input": _INPUT_NAME,
        "run_id": run_id,
        "network_used": False,
        "publication_performed": False,
        "renderer": "Remotion 4.0.421",
    }
    try:
        _atomic_private_write(
            receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
    except (OSError, ResumeWorkspaceStorageError) as exc:
        raise CreatorVideoError(
            "Creator Video was rendered but its receipt could not be saved"
        ) from exc
    return output_path
