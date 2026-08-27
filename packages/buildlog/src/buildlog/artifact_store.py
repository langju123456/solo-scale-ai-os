"""Object-storage boundary for durable copies of completed run artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from buildlog.config import Settings


class ArtifactStore(Protocol):
    """Mirror one completed run without changing local pipeline semantics."""

    def mirror_run(self, run_dir: Path) -> int:
        """Upload run files and return the number of durable objects written."""


class NullArtifactStore:
    """Local default that leaves existing filesystem persistence unchanged."""

    def mirror_run(self, run_dir: Path) -> int:
        return 0


class AzureBlobArtifactStore:
    """Mirror run artifacts to one private Azure Blob container."""

    def __init__(
        self,
        *,
        container_name: str,
        account_url: str | None = None,
        connection_string: str | None = None,
    ) -> None:
        from azure.storage.blob import ContainerClient

        if connection_string:
            self._container = ContainerClient.from_connection_string(
                connection_string,
                container_name,
            )
        elif account_url:
            from azure.identity import DefaultAzureCredential

            self._container = ContainerClient(
                account_url=account_url,
                container_name=container_name,
                credential=DefaultAzureCredential(),
            )
        else:
            raise ValueError(
                "Azure object storage requires an account URL or connection string"
            )

    def mirror_run(self, run_dir: Path) -> int:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._container.create_container()
        except ResourceExistsError:
            pass
        uploaded = 0
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(run_dir).as_posix()
            blob_name = f"runs/{run_dir.name}/{relative}"
            payload = path.read_bytes()
            self._container.upload_blob(
                blob_name,
                payload,
                overwrite=True,
                metadata={"sha256": hashlib.sha256(payload).hexdigest()},
            )
            uploaded += 1
        return uploaded


def create_artifact_store(settings: Settings) -> ArtifactStore:
    """Resolve a configured durable artifact implementation."""
    if settings.object_store_backend == "none":
        return NullArtifactStore()
    if settings.object_store_backend == "azure_blob":
        return AzureBlobArtifactStore(
            container_name=settings.azure_storage_container,
            account_url=settings.azure_storage_account_url,
            connection_string=settings.azure_storage_connection_string,
        )
    raise ValueError(f"unsupported object store backend: {settings.object_store_backend}")
