"""Tests for crash-tolerant append-only run event continuation."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buildlog.event_writer import AppendOnlyRunEventWriter
from buildlog.observability_models import ObservationEvent


def test_event_writer_continues_existing_run_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    existing = ObservationEvent(
        event_id="run-001:event:7",
        sequence=7,
        run_id="run-001",
        event_type="run_completed",
        occurred_at=datetime(2026, 7, 29, tzinfo=UTC),
        payload={},
    )
    path.write_text(existing.model_dump_json() + "\n", encoding="utf-8")
    writer = AppendOnlyRunEventWriter(path, "run-001")

    event = writer.append("publish_previewed", payload={"content_hash": "abc"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["sequence"] == 7
    assert json.loads(lines[1])["sequence"] == 8
    assert event.event_id == "run-001:event:8"


def test_event_writer_continues_after_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"partial":', encoding="utf-8")
    writer = AppendOnlyRunEventWriter(path, "run-001")

    event = writer.append("publish_previewed", payload={"content_hash": "abc"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"partial":'
    assert json.loads(lines[1])["event_type"] == "publish_previewed"
    assert event.sequence == 1


def test_event_writer_continues_after_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\xff")
    writer = AppendOnlyRunEventWriter(path, "run-001")

    event = writer.append("publish_previewed", payload={"content_hash": "abc"})

    lines = path.read_bytes().splitlines()
    assert lines[0] == b"\xff"
    assert json.loads(lines[1])[b"event_type".decode()] == "publish_previewed"
    assert event.sequence == 1


def test_event_writer_preserves_sequence_before_invalid_utf8_tail(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    existing = ObservationEvent(
        event_id="run-001:event:7",
        sequence=7,
        run_id="run-001",
        event_type="run_completed",
        occurred_at=datetime(2026, 7, 29, tzinfo=UTC),
        payload={},
    )
    path.write_bytes(existing.model_dump_json().encode("utf-8") + b"\n\xff")
    writer = AppendOnlyRunEventWriter(path, "run-001")

    event = writer.append("publish_previewed")

    assert event.sequence == 8
    assert json.loads(path.read_bytes().splitlines()[-1])["sequence"] == 8


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_event_writer_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "outside.jsonl"
    target.write_text("unchanged\n", encoding="utf-8")
    path = tmp_path / "events.jsonl"
    path.symlink_to(target)

    with pytest.raises(OSError, match="symbolic link"):
        AppendOnlyRunEventWriter(path, "run-001")

    assert target.read_text(encoding="utf-8") == "unchanged\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_event_writer_rejects_symbolic_link_created_after_initialization(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    writer = AppendOnlyRunEventWriter(path, "run-001")
    target = tmp_path / "outside.jsonl"
    target.write_text("unchanged\n", encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(OSError, match="symbolic link"):
        writer.append("publish_started")

    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_event_writer_syncs_only_when_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[int] = []
    monkeypatch.setattr("buildlog.event_writer.os.fsync", synced.append)

    AppendOnlyRunEventWriter(
        tmp_path / "normal.jsonl",
        "run-001",
    ).append("run_completed")
    AppendOnlyRunEventWriter(
        tmp_path / "durable.jsonl",
        "run-002",
        durable=True,
    ).append("publish_started")

    assert len(synced) == 1
