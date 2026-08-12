"""Human-controlled downstream publication orchestration."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from time import monotonic
from typing import Callable, Protocol
from uuid import uuid4

from pydantic import ValidationError

from buildlog.exceptions import BuildLogError
from buildlog.linkedin_errors import (
    DuplicatePublicationBlockedError,
    IndeterminatePublicationError,
    IndeterminatePublicationBlockedError,
    LinkedInAPIError,
    LinkedInNetworkError,
    LinkedInRequestTimeoutError,
    LinkedInServerError,
    PublicationApprovalRequiredError,
    PublicationReceiptPersistenceError,
)
from buildlog.linkedin_security import redact_linkedin_secrets
from buildlog.publication_content import FinalArtifactResolver
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationPreview,
    PublicationStatus,
    PublishReceipt,
    PublishRequest,
    PublishResult,
    Publisher,
)
from buildlog.publishing_observability import PublishingEventRecorder
from buildlog.publishing_repository import PublishingRepository


class PublicationSettings(Protocol):
    """Platform settings required by publication orchestration."""

    api_version: str

    @property
    def posts_url(self) -> str:
        """Return the create-post endpoint."""


class PublicationIdentity(Protocol):
    """Authenticated identity consumed by publication orchestration."""

    account_reference: str
    display_name: str
    author_reference: str
    mapping_source: str


class PublicationIdentityService(Protocol):
    """Resolve one authenticated platform identity."""

    def resolve(self) -> PublicationIdentity:
        """Return the current authenticated identity."""


class PublishingService:
    """Preview, approve, deduplicate, publish, observe, and persist receipts."""

    def __init__(
        self,
        settings: PublicationSettings,
        resolver: FinalArtifactResolver,
        identity_service: PublicationIdentityService,
        publisher: Publisher,
        repository: PublishingRepository,
        *,
        platform: PublicationPlatform = PublicationPlatform.LINKEDIN,
        platform_name: str = "LinkedIn",
        content_validator: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.identity_service = identity_service
        self.publisher = publisher
        self.repository = repository
        self.platform = platform
        self.platform_name = platform_name
        self.content_validator = content_validator

    def preview(self, run_id: str) -> PublicationPreview:
        """Return the exact content and duplicate state without publishing."""
        artifact = self.resolver.resolve(run_id)
        if self.content_validator is not None:
            self.content_validator(artifact.content)
        recorder = PublishingEventRecorder(run_id, artifact.artifact_path.parent)
        identity = self._resolve_identity(recorder)
        duplicate = self.repository.find_successful_publication(
            platform=self.platform,
            account_reference=identity.account_reference,
            content_hash=artifact.content_hash,
        )
        indeterminate = self.repository.find_indeterminate_publication(
            platform=self.platform,
            account_reference=identity.account_reference,
            content_hash=artifact.content_hash,
        )
        recorder.emit(
            "publish_previewed",
            {
                "platform": self.platform.value,
                "artifact_id": artifact.artifact_id,
                "account_reference": identity.account_reference,
                "content_hash": artifact.content_hash,
                "content_length": len(artifact.content),
                "duplicate_found": duplicate is not None,
                "indeterminate_found": indeterminate is not None,
                "network_publish_occurred": False,
            },
        )
        return PublicationPreview(
            platform=self.platform,
            run_id=run_id,
            artifact_id=artifact.artifact_id,
            artifact_path=str(artifact.artifact_path),
            account_reference=identity.account_reference,
            account_display_name=identity.display_name,
            content_length=len(artifact.content),
            content_hash=artifact.content_hash,
            content=artifact.content,
            duplicate_found=duplicate is not None,
            duplicate_external_post_id=(
                duplicate.external_post_id if duplicate is not None else None
            ),
            duplicate_published_at=(
                duplicate.published_at if duplicate is not None else None
            ),
            indeterminate_found=indeterminate is not None,
            indeterminate_receipt_id=(
                indeterminate.receipt_id if indeterminate is not None else None
            ),
            indeterminate_created_at=(
                indeterminate.created_at if indeterminate is not None else None
            ),
        )

    def publish(
        self,
        run_id: str,
        *,
        approved: bool,
        approved_content_hash: str | None = None,
        approved_account_reference: str | None = None,
        allow_duplicate: bool = False,
    ) -> PublishReceipt:
        """Publish one run after approval, persisting every network outcome."""
        artifact = self.resolver.resolve(run_id)
        if self.content_validator is not None:
            self.content_validator(artifact.content)
        recorder = PublishingEventRecorder(run_id, artifact.artifact_path.parent)
        if not approved:
            recorder.emit(
                "publish_approval_required",
                {
                    "platform": self.platform.value,
                    "artifact_id": artifact.artifact_id,
                    "content_hash": artifact.content_hash,
                },
            )
            raise PublicationApprovalRequiredError(
                "Publication requires --confirm and the exact interactive "
                "confirmation PUBLISH."
            )
        if not approved_content_hash or not approved_account_reference:
            recorder.emit(
                "publish_approval_required",
                {
                    "platform": self.platform.value,
                    "artifact_id": artifact.artifact_id,
                    "content_hash": artifact.content_hash,
                    "reason": "approval_not_bound_to_preview",
                },
            )
            raise PublicationApprovalRequiredError(
                "Publication approval must be bound to a fresh preview."
            )
        if artifact.content_hash != approved_content_hash:
            recorder.emit(
                "publish_approval_stale",
                {
                    "platform": self.platform.value,
                    "artifact_id": artifact.artifact_id,
                    "approved_content_hash": approved_content_hash,
                    "current_content_hash": artifact.content_hash,
                    "reason": "content_changed_after_preview",
                },
            )
            raise PublicationApprovalRequiredError(
                "The final artifact changed after preview. Preview the run again "
                "before publishing."
            )
        identity = self._resolve_identity(recorder)
        if identity.account_reference != approved_account_reference:
            recorder.emit(
                "publish_approval_stale",
                {
                    "platform": self.platform.value,
                    "artifact_id": artifact.artifact_id,
                    "content_hash": artifact.content_hash,
                    "approved_account_reference": approved_account_reference,
                    "current_account_reference": identity.account_reference,
                    "reason": "account_changed_after_preview",
                },
            )
            raise PublicationApprovalRequiredError(
                f"The authenticated {self.platform_name} account changed after preview. "
                "Preview the run again before publishing."
            )
        duplicate = self.repository.find_successful_publication(
            platform=self.platform,
            account_reference=identity.account_reference,
            content_hash=artifact.content_hash,
        )
        indeterminate = self.repository.find_indeterminate_publication(
            platform=self.platform,
            account_reference=identity.account_reference,
            content_hash=artifact.content_hash,
        )
        if duplicate is not None and not allow_duplicate:
            recorder.emit(
                "publish_duplicate_blocked",
                {
                    "platform": self.platform.value,
                    "account_reference": identity.account_reference,
                    "content_hash": artifact.content_hash,
                    "prior_receipt_id": duplicate.receipt_id,
                    "prior_external_post_id": duplicate.external_post_id,
                },
            )
            raise DuplicatePublicationBlockedError(
                duplicate.external_post_id,
                duplicate.published_at,
            )
        if indeterminate is not None and not allow_duplicate:
            recorder.emit(
                "publish_indeterminate_blocked",
                {
                    "platform": self.platform.value,
                    "account_reference": identity.account_reference,
                    "content_hash": artifact.content_hash,
                    "prior_receipt_id": indeterminate.receipt_id,
                    "prior_created_at": indeterminate.created_at.isoformat(),
                },
            )
            raise IndeterminatePublicationBlockedError(
                indeterminate.receipt_id,
                indeterminate.created_at,
                self.platform_name,
            )
        prior_receipt = _latest_prior_receipt(duplicate, indeterminate)

        attempt_id = f"{self.platform.value}-{uuid4()}"
        created_at = datetime.now(UTC)
        recorder.emit(
            "publish_approved",
            {
                "attempt_id": attempt_id,
                "platform": self.platform.value,
                "account_reference": identity.account_reference,
                "content_hash": artifact.content_hash,
                "allow_duplicate": allow_duplicate,
            },
        )
        request = PublishRequest(
            attempt_id=attempt_id,
            run_id=run_id,
            artifact_id=artifact.artifact_id,
            platform=self.platform,
            account_reference=identity.account_reference,
            author_urn=identity.author_reference,
            content=artifact.content,
            content_hash=artifact.content_hash,
            approved=True,
            api_version=self.settings.api_version,
        )
        started = monotonic()
        recorder.emit(
            "publish_started",
            {
                "attempt_id": attempt_id,
                "platform": self.platform.value,
                "account_reference": identity.account_reference,
                "content_hash": artifact.content_hash,
                "content_length": len(artifact.content),
                "api_version": self.settings.api_version,
            },
        )
        result: PublishResult | None = None
        try:
            raw_result = self.publisher.publish(request)
            result = _revalidate_publish_result(
                raw_result,
                platform_name=self.platform_name,
            )
            _validate_publish_result(
                request,
                result,
                expected_api_endpoint=self.settings.posts_url,
                platform_name=self.platform_name,
            )
        except KeyboardInterrupt as interruption:
            error = IndeterminatePublicationError(
                f"{self.platform_name} submission was interrupted before its "
                "outcome was confirmed. The post may exist; inspect "
                f"{self.platform_name} and the local "
                "receipt before another attempt."
            )
            receipt = self._failure_receipt(
                request,
                created_at=created_at,
                status=PublicationStatus.INDETERMINATE,
                error=error,
                prior_receipt=prior_receipt,
            )
            self._persist_receipt(
                receipt,
                recorder=recorder,
                publication_error=error,
            )
            recorder.emit(
                "publish_result_indeterminate",
                _receipt_event_payload(receipt, started),
                preserve_outcome=True,
            )
            raise error from interruption
        except (
            IndeterminatePublicationError,
            LinkedInNetworkError,
            LinkedInRequestTimeoutError,
            LinkedInServerError,
        ) as exc:
            receipt = self._failure_receipt(
                request,
                created_at=created_at,
                status=PublicationStatus.INDETERMINATE,
                error=exc,
                prior_receipt=prior_receipt,
                external_post_id=(
                    result.external_post_id
                    if isinstance(result, PublishResult)
                    else None
                ),
            )
            self._persist_receipt(
                receipt,
                recorder=recorder,
                publication_error=exc,
            )
            recorder.emit(
                "publish_result_indeterminate",
                _receipt_event_payload(receipt, started),
                preserve_outcome=True,
            )
            raise
        except BuildLogError as exc:
            receipt = self._failure_receipt(
                request,
                created_at=created_at,
                status=PublicationStatus.FAILED,
                error=exc,
                prior_receipt=prior_receipt,
            )
            self._persist_receipt(
                receipt,
                recorder=recorder,
                publication_error=exc,
            )
            recorder.emit(
                "publish_failed",
                _receipt_event_payload(receipt, started),
                preserve_outcome=True,
            )
            raise
        except Exception as exc:
            error = IndeterminatePublicationError(
                "The publisher failed unexpectedly after submission began. The "
                f"post may exist; inspect {self.platform_name} and the local receipt before "
                "another attempt."
            )
            receipt = self._failure_receipt(
                request,
                created_at=created_at,
                status=PublicationStatus.INDETERMINATE,
                error=error,
                prior_receipt=prior_receipt,
            )
            self._persist_receipt(
                receipt,
                recorder=recorder,
                publication_error=error,
            )
            recorder.emit(
                "publish_result_indeterminate",
                _receipt_event_payload(receipt, started),
                preserve_outcome=True,
            )
            raise error from exc

        receipt = PublishReceipt(
            receipt_id=f"receipt-{uuid4()}",
            attempt_id=attempt_id,
            run_id=run_id,
            artifact_id=artifact.artifact_id,
            platform=result.platform,
            account_reference=result.account_reference,
            content_hash=result.content_hash,
            status=result.status,
            external_post_id=result.external_post_id,
            created_at=created_at,
            published_at=result.occurred_at,
            api_endpoint=result.api_endpoint,
            api_version=result.api_version,
            http_status=result.http_status,
            duplicate_of_receipt_id=(
                prior_receipt.receipt_id if prior_receipt is not None else None
            ),
        )
        self._persist_receipt(receipt, recorder=recorder)
        recorder.emit(
            "publish_succeeded",
            _receipt_event_payload(receipt, started),
            preserve_outcome=True,
        )
        return receipt

    def _resolve_identity(
        self,
        recorder: PublishingEventRecorder,
    ) -> PublicationIdentity:
        try:
            identity = self.identity_service.resolve()
        except BuildLogError as exc:
            recorder.emit(
                f"{self.platform.value}_identity_failed",
                {
                    "platform": self.platform.value,
                    "error_category": _error_category(exc),
                },
            )
            raise
        recorder.emit(
            f"{self.platform.value}_identity_resolved",
            {
                "platform": self.platform.value,
                "account_reference": identity.account_reference,
                "author_mapping_source": identity.mapping_source,
            },
        )
        return identity

    def _persist_receipt(
        self,
        receipt: PublishReceipt,
        *,
        recorder: PublishingEventRecorder,
        publication_error: BuildLogError | None = None,
    ) -> None:
        try:
            self.repository.save_publish_receipt(receipt)
        except (Exception, KeyboardInterrupt) as exc:
            recorder.emit(
                "publish_receipt_failed",
                {
                    "attempt_id": receipt.attempt_id,
                    "receipt_id": receipt.receipt_id,
                    "platform": receipt.platform.value,
                    "account_reference": receipt.account_reference,
                    "content_hash": receipt.content_hash,
                    "publication_status": receipt.status.value,
                    "external_post_id": receipt.external_post_id,
                    "error_category": "receipt_persistence",
                },
                preserve_outcome=True,
            )
            if receipt.status is PublicationStatus.SUCCEEDED:
                raise PublicationReceiptPersistenceError(
                    f"{self.platform_name} created the post "
                    f"({receipt.external_post_id}), but the local receipt could "
                    "not be saved. Do not publish again; repair the local database "
                    "and record the external post before another attempt."
                ) from exc
            if publication_error is not None:
                raise PublicationReceiptPersistenceError(
                    f"{self.platform_name} publication ended as {receipt.status.value}, and "
                    "the local receipt could not be saved. Do not retry until "
                    f"{self.platform_name} and the local database have been inspected."
                ) from exc
            raise PublicationReceiptPersistenceError(
                "The local publication receipt could not be saved."
            ) from exc

    def _failure_receipt(
        self,
        request: PublishRequest,
        *,
        created_at: datetime,
        status: PublicationStatus,
        error: BuildLogError,
        prior_receipt: PublishReceipt | None,
        external_post_id: str | None = None,
    ) -> PublishReceipt:
        return PublishReceipt(
            receipt_id=f"receipt-{uuid4()}",
            attempt_id=request.attempt_id,
            run_id=request.run_id,
            artifact_id=request.artifact_id,
            platform=request.platform,
            account_reference=request.account_reference,
            content_hash=request.content_hash,
            status=status,
            external_post_id=external_post_id,
            created_at=created_at,
            api_endpoint=self.settings.posts_url,
            api_version=self.settings.api_version,
            http_status=(
                getattr(error, "status_code", None)
            ),
            error_category=_error_category(error),
            safe_error_message=redact_linkedin_secrets(
                error,
                known_secrets=[request.content, request.author_urn],
            ),
            duplicate_of_receipt_id=(
                prior_receipt.receipt_id if prior_receipt is not None else None
            ),
        )


def _error_category(error: BuildLogError) -> str:
    name = type(error).__name__
    if isinstance(error, IndeterminatePublicationError):
        cause = error.__cause__
        if isinstance(
            cause,
            (LinkedInNetworkError, LinkedInRequestTimeoutError),
        ):
            return _error_category(cause)
        return "indeterminate"
    if isinstance(error, LinkedInAPIError):
        return f"linkedin_http_{error.status_code}"
    if isinstance(error, LinkedInNetworkError):
        return "linkedin_network"
    if isinstance(error, LinkedInRequestTimeoutError):
        return "linkedin_request_timeout"
    stem = name.removesuffix("Error")
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stem)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", words).lower()


def _validate_publish_result(
    request: PublishRequest,
    result: PublishResult,
    *,
    expected_api_endpoint: str,
    platform_name: str = "LinkedIn",
) -> None:
    mismatches = [
        name
        for name, expected, actual in (
            ("platform", request.platform, result.platform),
            ("account_reference", request.account_reference, result.account_reference),
            ("run_id", request.run_id, result.run_id),
            ("content_hash", request.content_hash, result.content_hash),
            ("api_version", request.api_version, result.api_version),
            ("api_endpoint", expected_api_endpoint, result.api_endpoint),
        )
        if expected != actual
    ]
    if result.status is not PublicationStatus.SUCCEEDED:
        mismatches.append("status")
    if not result.external_post_id:
        mismatches.append("external_post_id")
    if result.http_status != 201:
        mismatches.append("http_status")
    if mismatches:
        fields = ", ".join(mismatches)
        raise IndeterminatePublicationError(
            "The publisher returned an inconsistent success result "
            f"({fields}). Do not retry until {platform_name} has been inspected."
        )


def _revalidate_publish_result(
    value: object,
    *,
    platform_name: str = "LinkedIn",
) -> PublishResult:
    if not isinstance(value, PublishResult):
        raise IndeterminatePublicationError(
            "The publisher returned an invalid result type. Do not retry until "
            f"the adapter and {platform_name} have been inspected."
        )
    try:
        return PublishResult.model_validate(value.model_dump(mode="python"))
    except ValidationError as exc:
        raise IndeterminatePublicationError(
            "The publisher returned an invalid result. Do not retry until the "
            f"adapter and {platform_name} have been inspected."
        ) from exc


def _receipt_event_payload(
    receipt: PublishReceipt,
    started: float,
) -> dict[str, object]:
    return {
        "attempt_id": receipt.attempt_id,
        "receipt_id": receipt.receipt_id,
        "platform": receipt.platform.value,
        "account_reference": receipt.account_reference,
        "content_hash": receipt.content_hash,
        "status": receipt.status.value,
        "external_post_id": receipt.external_post_id,
        "http_status": receipt.http_status,
        "api_version": receipt.api_version,
        "error_category": receipt.error_category,
        "duration_ms": max(0, round((monotonic() - started) * 1000)),
    }


def _latest_prior_receipt(
    successful: PublishReceipt | None,
    indeterminate: PublishReceipt | None,
) -> PublishReceipt | None:
    candidates = [
        receipt
        for receipt in (successful, indeterminate)
        if receipt is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda receipt: receipt.created_at)
