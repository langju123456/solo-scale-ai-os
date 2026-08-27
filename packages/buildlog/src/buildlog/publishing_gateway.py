"""Public in-process boundary for reviewed external publishing artifacts."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from buildlog.config import Settings, load_settings
from buildlog.external_artifact import latest_publication_receipt, stage_soloscale_artifact
from buildlog.linkedin_config import load_linkedin_settings
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_identity import LinkedInIdentityService
from buildlog.linkedin_publisher import LinkedInTextPublisher
from buildlog.linkedin_token_store import FileTokenStore
from buildlog.publication_content import FinalArtifactResolver
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationPreview,
    PublishReceipt,
)
from buildlog.publishing_service import PublishingService
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.x_config import load_x_settings
from buildlog.x_http import XHttpClient
from buildlog.x_identity import XIdentityService
from buildlog.x_publisher import XTextPublisher, validate_x_content
from buildlog.x_token_store import FileXTokenStore

PublishingChannel = Literal["linkedin", "x"]


class PublishingGateway:
    """Keep config, tokens, adapters, duplicate checks, and receipts inside BuildLog."""

    def __init__(self, *, data_root: Path, config_root: Path, channel: PublishingChannel) -> None:
        self.data_root = data_root.absolute()
        self.config_root = config_root.absolute()
        self.channel = channel
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.data_root.chmod(0o700)
        configured = load_settings(self.config_root)
        database = self.data_root / "buildlog.sqlite3"
        self.settings: Settings = replace(
            configured,
            runs_dir=self.data_root / "runs",
            database_url=f"sqlite:///{database}",
        )
        self.repository = SQLAlchemyRunRepository(self.settings.database_url)
        self.repository.initialize()

    def stage(self, *, source_path: Path, source_run_id: str) -> str:
        return stage_soloscale_artifact(
            settings=self.settings,
            repository=self.repository,
            source_path=source_path,
            source_run_id=source_run_id,
            channel=self.channel,
        )

    def preview(self, run_id: str) -> PublicationPreview:
        service, http = self._service()
        try:
            return service.preview(run_id)
        finally:
            http.close()

    def publish(
        self,
        run_id: str,
        *,
        confirmation: str,
        approved_content_hash: str,
        approved_account_reference: str,
    ) -> PublishReceipt:
        service, http = self._service()
        try:
            current = service.preview(run_id)
            if confirmation != "PUBLISH":
                return service.publish(run_id, approved=False)
            if (
                current.content_hash != approved_content_hash
                or current.account_reference != approved_account_reference
            ):
                return service.publish(
                    run_id,
                    approved=True,
                    approved_content_hash=approved_content_hash,
                    approved_account_reference=approved_account_reference,
                )
            return service.publish(
                run_id,
                approved=True,
                approved_content_hash=current.content_hash,
                approved_account_reference=current.account_reference,
            )
        finally:
            http.close()

    def latest_receipt(self, run_id: str) -> dict[str, str] | None:
        return latest_publication_receipt(settings=self.settings, run_id=run_id)

    def _service(self) -> tuple[PublishingService, LinkedInHttpClient | XHttpClient]:
        resolver = FinalArtifactResolver(self.repository, allowed_root=self.settings.runs_dir)
        if self.channel == "linkedin":
            linkedin_settings = load_linkedin_settings(self.config_root)
            linkedin_store = FileTokenStore()
            http = LinkedInHttpClient(timeout_seconds=linkedin_settings.request_timeout_seconds)
            service_settings: Any = linkedin_settings
            identity: Any = LinkedInIdentityService(linkedin_settings, http, linkedin_store)
            return (
                PublishingService(
                    service_settings,
                    resolver,
                    identity,
                    LinkedInTextPublisher(linkedin_settings, http, linkedin_store),
                    self.repository,
                ),
                http,
            )
        x_settings = load_x_settings(self.config_root)
        x_store = FileXTokenStore()
        x_http = XHttpClient(timeout_seconds=x_settings.request_timeout_seconds)
        service_settings = x_settings
        identity = XIdentityService(x_settings, x_http, x_store)
        return (
            PublishingService(
                service_settings,
                resolver,
                identity,
                XTextPublisher(x_settings, x_http, x_store),
                self.repository,
                platform=PublicationPlatform.X,
                platform_name="X",
                content_validator=validate_x_content,
            ),
            x_http,
        )
