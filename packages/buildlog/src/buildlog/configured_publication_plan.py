"""Configured BuildLog adapter for human-approved image posts and X threads."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from buildlog.config import Settings, load_settings
from buildlog.external_artifact import stage_soloscale_artifact
from buildlog.linkedin_config import LinkedInSettings, load_linkedin_settings
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_identity import (
    LinkedInIdentity,
    LinkedInIdentityService,
    require_scopes,
    require_valid_token,
)
from buildlog.linkedin_publisher import LinkedInTextPublisher
from buildlog.linkedin_token_store import FileTokenStore
from buildlog.publication_content import FinalArtifactResolver
from buildlog.publication_plan_gateway import (
    PublicationPlan,
    PublicationPlanChannel,
    PublicationPlanError,
    PublicationPlanPreview,
    PublicationPlanResult,
)
from buildlog.publication_plan_publishers import (
    LinkedInImagePublisher,
    XImageThreadPublisher,
    build_x_metadata_payload,
    build_x_upload_payload,
)
from buildlog.publishing_models import PublicationPlatform, PublicationPreview
from buildlog.publishing_service import PublishingService
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.x_config import XSettings, load_x_settings
from buildlog.x_http import XHttpClient
from buildlog.x_identity import (
    XIdentityService,
    require_valid_x_token,
    require_x_scopes,
)
from buildlog.x_publisher import XTextPublisher, validate_x_content
from buildlog.x_token_store import FileXTokenStore

_LINKEDIN_IMAGE_ID = re.compile(r"^urn:li:image:[A-Za-z0-9_-]+$")


class ConfiguredPublicationPlanAdapter:
    """Keep credentials, platform calls, and receipts inside BuildLog."""

    def __init__(
        self,
        *,
        data_root: Path,
        config_root: Path,
        platform: PublicationPlanChannel,
    ) -> None:
        self.data_root = data_root.absolute()
        self.config_root = config_root.absolute()
        self.platform = platform
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.data_root.chmod(0o700)
        configured = load_settings(self.config_root)
        self.settings: Settings = replace(
            configured,
            runs_dir=self.data_root / "runs",
            database_url=f"sqlite:///{self.data_root / 'buildlog.sqlite3'}",
        )
        self.repository = SQLAlchemyRunRepository(self.settings.database_url)
        self.repository.initialize()

    def preview(self, plan: PublicationPlan) -> PublicationPlanPreview:
        """Preflight every exact part without upload or post creation."""
        run_ids = self._stage_parts(plan)
        if plan.platform == "linkedin":
            previews = self._preview_linkedin(run_ids)
        else:
            previews = self._preview_x(run_ids)
        first = previews[0]
        if any(item.account_reference != first.account_reference for item in previews):
            raise PublicationPlanError("authenticated account changed during plan preview")
        return PublicationPlanPreview(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            platform=plan.platform,
            account_reference=first.account_reference,
            account_display_name=first.account_display_name,
            parts=plan.text_parts,
            image=plan.image,
            source_package_id=plan.source_package_id,
            source_receipt_hash=plan.source_receipt_hash,
            duplicate_found=any(item.duplicate_found for item in previews),
            indeterminate_found=any(item.indeterminate_found for item in previews),
        )

    def publish(
        self,
        plan: PublicationPlan,
        *,
        approved_account_reference: str,
    ) -> PublicationPlanResult:
        """Upload once, then persist every post receipt before the next request."""
        preflight = self.preview(plan)
        if preflight.account_reference != approved_account_reference:
            raise PublicationPlanError("authenticated account changed after plan preview")
        if preflight.duplicate_found:
            raise PublicationPlanError("a publication-plan part was already published")
        if preflight.indeterminate_found:
            raise PublicationPlanError("a publication-plan part has an unresolved attempt")
        run_ids = self._stage_parts(plan)
        if plan.platform == "linkedin":
            return self._publish_linkedin(
                plan,
                run_ids,
                approved_account_reference=approved_account_reference,
            )
        return self._publish_x(
            plan,
            run_ids,
            approved_account_reference=approved_account_reference,
        )

    def _preview_linkedin(self, run_ids: list[str]) -> list[PublicationPreview]:
        settings = load_linkedin_settings(self.config_root)
        store = FileTokenStore()
        http = LinkedInHttpClient(timeout_seconds=settings.request_timeout_seconds)
        try:
            identity = LinkedInIdentityService(settings, http, store)
            service_settings: Any = settings
            service_identity: Any = identity
            resolver = FinalArtifactResolver(
                self.repository,
                allowed_root=self.settings.runs_dir,
            )
            service = PublishingService(
                service_settings,
                resolver,
                service_identity,
                LinkedInTextPublisher(settings, http, store),
                self.repository,
            )
            return [service.preview(run_id) for run_id in run_ids]
        finally:
            http.close()

    def _preview_x(self, run_ids: list[str]) -> list[PublicationPreview]:
        settings = load_x_settings(self.config_root)
        store = FileXTokenStore()
        http = XHttpClient(timeout_seconds=settings.request_timeout_seconds)
        try:
            identity = XIdentityService(settings, http, store)
            service_settings: Any = settings
            service_identity: Any = identity
            resolver = FinalArtifactResolver(
                self.repository,
                allowed_root=self.settings.runs_dir,
            )
            service = PublishingService(
                service_settings,
                resolver,
                service_identity,
                XTextPublisher(settings, http, store),
                self.repository,
                platform=PublicationPlatform.X,
                platform_name="X",
                content_validator=validate_x_content,
            )
            return [service.preview(run_id) for run_id in run_ids]
        finally:
            http.close()

    def _publish_linkedin(
        self,
        plan: PublicationPlan,
        run_ids: list[str],
        *,
        approved_account_reference: str,
    ) -> PublicationPlanResult:
        settings = load_linkedin_settings(self.config_root)
        store = FileTokenStore()
        http = LinkedInHttpClient(timeout_seconds=settings.request_timeout_seconds)
        try:
            identity_service = LinkedInIdentityService(settings, http, store)
            identity = identity_service.resolve()
            service_settings: Any = settings
            service_identity: Any = identity_service
            self._require_account(identity.account_reference, approved_account_reference)
            media_id = self._upload_linkedin(plan, settings, store, http, identity)
            self._save_upload_receipt(
                plan,
                approved_account_reference,
                media_id=media_id,
                endpoint=f"{settings.api_base_url.rstrip('/')}/rest/images",
                status="succeeded",
            )
            resolver = FinalArtifactResolver(
                self.repository,
                allowed_root=self.settings.runs_dir,
            )
            service = PublishingService(
                service_settings,
                resolver,
                service_identity,
                LinkedInImagePublisher(
                    settings,
                    http,
                    store,
                    media_id=media_id,
                    alt_text=plan.image.alt_text,
                ),
                self.repository,
            )
            preview = service.preview(run_ids[0])
            receipt = service.publish(
                run_ids[0],
                approved=True,
                approved_content_hash=preview.content_hash,
                approved_account_reference=approved_account_reference,
            )
            assert receipt.external_post_id is not None
            self._save_progress(
                plan,
                approved_account_reference,
                [receipt.receipt_id],
                [receipt.external_post_id],
            )
            return PublicationPlanResult(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                platform=plan.platform,
                account_reference=approved_account_reference,
                post_receipt_ids=[receipt.receipt_id],
                external_post_ids=[receipt.external_post_id],
                status="succeeded",
            )
        finally:
            http.close()

    def _publish_x(
        self,
        plan: PublicationPlan,
        run_ids: list[str],
        *,
        approved_account_reference: str,
    ) -> PublicationPlanResult:
        settings = load_x_settings(self.config_root)
        store = FileXTokenStore()
        http = XHttpClient(timeout_seconds=settings.request_timeout_seconds)
        try:
            identity_service = XIdentityService(settings, http, store)
            identity = identity_service.resolve()
            service_settings: Any = settings
            service_identity: Any = identity_service
            self._require_account(identity.account_reference, approved_account_reference)
            media_id = self._upload_x(
                plan,
                settings,
                store,
                http,
                account_reference=approved_account_reference,
            )
            self._save_upload_receipt(
                plan,
                approved_account_reference,
                media_id=media_id,
                endpoint=f"{settings.api_base_url.rstrip('/')}/2/media/upload",
                status="succeeded",
            )
            resolver = FinalArtifactResolver(
                self.repository,
                allowed_root=self.settings.runs_dir,
            )
            receipt_ids: list[str] = []
            post_ids: list[str] = []
            for index, run_id in enumerate(run_ids):
                publisher = XImageThreadPublisher(
                    settings,
                    http,
                    store,
                    media_id=media_id if index == 0 else None,
                    reply_to_post_id=post_ids[-1] if post_ids else None,
                )
                service = PublishingService(
                    service_settings,
                    resolver,
                    service_identity,
                    publisher,
                    self.repository,
                    platform=PublicationPlatform.X,
                    platform_name="X",
                    content_validator=validate_x_content,
                )
                preview = service.preview(run_id)
                receipt = service.publish(
                    run_id,
                    approved=True,
                    approved_content_hash=preview.content_hash,
                    approved_account_reference=approved_account_reference,
                )
                assert receipt.external_post_id is not None
                receipt_ids.append(receipt.receipt_id)
                post_ids.append(receipt.external_post_id)
                self._save_progress(
                    plan,
                    approved_account_reference,
                    receipt_ids,
                    post_ids,
                )
            return PublicationPlanResult(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                platform=plan.platform,
                account_reference=approved_account_reference,
                post_receipt_ids=receipt_ids,
                external_post_ids=post_ids,
                status="succeeded",
            )
        finally:
            http.close()

    def _upload_linkedin(
        self,
        plan: PublicationPlan,
        settings: LinkedInSettings,
        store: FileTokenStore,
        http: LinkedInHttpClient,
        identity: LinkedInIdentity,
    ) -> str:
        token = require_valid_token(store)
        require_scopes(token, {"w_member_social"})
        endpoint = f"{settings.api_base_url.rstrip('/')}/rest/images?action=initializeUpload"
        try:
            initialized = http.initialize_image(
                endpoint,
                access_token=token.access_token.get_secret_value(),
                api_version=settings.api_version,
                owner_urn=identity.author_reference,
            )
            if initialized.status_code != 200:
                raise PublicationPlanError(
                    f"LinkedIn image initialization failed (HTTP {initialized.status_code})"
                )
            value = (
                initialized.json_body.get("value")
                if isinstance(initialized.json_body, dict)
                else None
            )
            upload_url = value.get("uploadUrl") if isinstance(value, dict) else None
            media_id = value.get("image") if isinstance(value, dict) else None
            if not isinstance(upload_url, str) or not isinstance(media_id, str):
                raise PublicationPlanError("LinkedIn returned malformed image-upload data")
            self._validate_linkedin_upload_url(upload_url)
            if _LINKEDIN_IMAGE_ID.fullmatch(media_id) is None:
                raise PublicationPlanError("LinkedIn returned an invalid image ID")
            uploaded = http.upload_image(
                upload_url,
                access_token=token.access_token.get_secret_value(),
                content=self._image_bytes(plan),
            )
            if not 200 <= uploaded.status_code < 300:
                raise PublicationPlanError(
                    f"LinkedIn image upload failed (HTTP {uploaded.status_code})"
                )
            return media_id
        except Exception as exc:
            self._save_upload_receipt(
                plan,
                identity.account_reference,
                media_id=None,
                endpoint=f"{settings.api_base_url.rstrip('/')}/rest/images",
                status="indeterminate",
                error_category=type(exc).__name__,
            )
            raise

    def _upload_x(
        self,
        plan: PublicationPlan,
        settings: XSettings,
        store: FileXTokenStore,
        http: XHttpClient,
        *,
        account_reference: str,
    ) -> str:
        token = require_valid_x_token(store, settings)
        require_x_scopes(token, {"tweet.write", "media.write"})
        upload_endpoint = f"{settings.api_base_url.rstrip('/')}/2/media/upload"
        metadata_endpoint = f"{settings.api_base_url.rstrip('/')}/2/media/metadata"
        try:
            uploaded = http.upload_media(
                upload_endpoint,
                access_token=token.access_token.get_secret_value(),
                payload=build_x_upload_payload(self._image_bytes(plan)),
            )
            data = uploaded.json_body.get("data") if isinstance(uploaded.json_body, dict) else None
            media_id = data.get("id") if isinstance(data, dict) else None
            if (
                uploaded.status_code != 200
                or not isinstance(media_id, str)
                or not media_id.isdigit()
            ):
                raise PublicationPlanError(
                    f"X image upload failed (HTTP {uploaded.status_code})"
                )
            assert isinstance(data, dict)
            processing = data.get("processing_info")
            if isinstance(processing, dict) and processing.get("state") not in {None, "succeeded"}:
                raise PublicationPlanError("X image upload did not finish synchronously")
            metadata = http.set_media_metadata(
                metadata_endpoint,
                access_token=token.access_token.get_secret_value(),
                payload=build_x_metadata_payload(media_id, plan.image.alt_text),
            )
            if metadata.status_code != 200:
                raise PublicationPlanError(
                    f"X image metadata failed (HTTP {metadata.status_code})"
                )
            return media_id
        except Exception as exc:
            self._save_upload_receipt(
                plan,
                account_reference,
                media_id=None,
                endpoint=upload_endpoint,
                status="indeterminate",
                error_category=type(exc).__name__,
            )
            raise

    def _stage_parts(self, plan: PublicationPlan) -> list[str]:
        return [
            stage_soloscale_artifact(
                settings=self.settings,
                repository=self.repository,
                source_path=self._plan_dir(plan) / f"part-{index:02d}.md",
                source_run_id=f"{plan.source_package_id}/{plan.plan_id}/part-{index:02d}",
                channel=plan.platform,
            )
            for index in range(1, len(plan.text_parts) + 1)
        ]

    def _image_bytes(self, plan: PublicationPlan) -> bytes:
        path = self._plan_dir(plan) / plan.image.filename
        raw = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(raw).hexdigest() != plan.image.sha256:
            raise PublicationPlanError("publication image changed after approval")
        return raw

    def _save_upload_receipt(
        self,
        plan: PublicationPlan,
        account_reference: str,
        *,
        media_id: str | None,
        endpoint: str,
        status: str,
        error_category: str | None = None,
    ) -> None:
        self._atomic_json(
            self._plan_dir(plan) / "upload-receipt.json",
            {
                "upload_attempt_id": f"media-{uuid4()}",
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "platform": plan.platform,
                "account_reference": account_reference,
                "image_sha256": plan.image.sha256,
                "status": status,
                "external_media_id": media_id,
                "api_endpoint": endpoint,
                "error_category": error_category,
                "created_at": datetime.now(UTC).isoformat(),
                "contains_credentials": False,
                "contains_media_bytes": False,
                "contains_post_text": False,
            },
        )

    def _save_progress(
        self,
        plan: PublicationPlan,
        account_reference: str,
        receipt_ids: list[str],
        post_ids: list[str],
    ) -> None:
        self._atomic_json(
            self._plan_dir(plan) / "publication-progress.json",
            {
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "platform": plan.platform,
                "account_reference": account_reference,
                "post_receipt_ids": list(receipt_ids),
                "external_post_ids": list(post_ids),
                "next_eligible_position": len(post_ids) + 1,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def _atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                path.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _plan_dir(self, plan: PublicationPlan) -> Path:
        return self.data_root / "publication-plans" / plan.plan_id

    @staticmethod
    def _require_account(current: str, approved: str) -> None:
        if current != approved:
            raise PublicationPlanError("authenticated account changed after preview")

    @staticmethod
    def _validate_linkedin_upload_url(value: str) -> None:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.linkedin.com"
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/dms-uploads/")
            or parsed.fragment
        ):
            raise PublicationPlanError("LinkedIn returned an unsafe image-upload URL")
