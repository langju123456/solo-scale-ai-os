"""Reusable append-only writer for validated BuildLog run events."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from buildlog.observability_models import ObservationEvent


class AppendOnlyRunEventWriter:
    """Append validated events while continuing an existing run sequence."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        *,
        now: Callable[[], datetime] | None = None,
        durable: bool = False,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self._now = now or (lambda: datetime.now(UTC))
        self._durable = durable
        if path.is_symlink():
            raise OSError("run event path must not be a symbolic link")
        self._sequence = _last_valid_sequence(path, run_id)

    def append(
        self,
        event_type: str,
        *,
        step_name: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> ObservationEvent:
        """Append one event immediately and return its validated model."""
        if self.path.is_symlink():
            raise OSError("run event path must not be a symbolic link")
        self._sequence += 1
        event = ObservationEvent(
            event_id=f"{self.run_id}:event:{self._sequence}",
            sequence=self._sequence,
            run_id=self.run_id,
            event_type=event_type,
            occurred_at=occurred_at or self._now(),
            step_name=step_name,
            payload=payload or {},
        )
        needs_separator = _needs_line_separator(self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            if needs_separator:
                handle.write("\n")
            handle.write(event.model_dump_json())
            handle.write("\n")
            handle.flush()
            if self._durable:
                os.fsync(handle.fileno())
        return event


def _last_valid_sequence(path: Path, run_id: str) -> int:
    if not path.exists():
        return 0
    latest = 0
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return 0
    for raw_line in lines:
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            event = ObservationEvent.model_validate_json(line)
        except ValidationError:
            continue
        if event.run_id == run_id:
            latest = max(latest, event.sequence)
    return latest


def _needs_line_separator(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"
