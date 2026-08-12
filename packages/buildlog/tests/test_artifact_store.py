"""Artifact storage boundary tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from buildlog.artifact_store import (
    AzureBlobArtifactStore,
    NullArtifactStore,
    create_artifact_store,
)
from buildlog.config import Settings


def _settings(tmp_path: Path, backend: str) -> Settings:
    return Settings(
        model="test",
        model_digest=None,
        api_base=None,
        temperature=0,
        max_tokens=10,
        threshold_accuracy=8,
        threshold_specificity=7,
        threshold_readability=7,
        threshold_value=7,
        threshold_evidence=7,
        prompt_version="v1",
        prompts_dir=tmp_path,
        runs_dir=tmp_path,
        database_url="sqlite:///:memory:",
        object_store_backend=backend,
    )


def test_null_artifact_store_preserves_local_default(tmp_path: Path) -> None:
    store = create_artifact_store(_settings(tmp_path, "none"))
    assert isinstance(store, NullArtifactStore)
    assert store.mirror_run(tmp_path) == 0


def test_unknown_artifact_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported object store"):
        create_artifact_store(_settings(tmp_path, "unknown"))


def test_azure_store_mirrors_files_with_content_hash_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    (run_dir / "nested").mkdir(parents=True)
    (run_dir / "06_final.md").write_text("final", encoding="utf-8")
    (run_dir / "nested" / "trace.json").write_text("{}", encoding="utf-8")

    class FakeContainer:
        def __init__(self):
            self.uploads = []

        def create_container(self):
            return None

        def upload_blob(self, name, payload, *, overwrite, metadata):
            self.uploads.append((name, payload, overwrite, metadata))

    container = FakeContainer()
    store = AzureBlobArtifactStore.__new__(AzureBlobArtifactStore)
    store._container = container

    assert store.mirror_run(run_dir) == 2
    assert [upload[0] for upload in container.uploads] == [
        "runs/run-001/06_final.md",
        "runs/run-001/nested/trace.json",
    ]
    assert all(upload[2] is True for upload in container.uploads)
    assert all(len(upload[3]["sha256"]) == 64 for upload in container.uploads)
