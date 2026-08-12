"""Validated domain models for downstream publication attempts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from buildlog.terminal_safety import is_unsafe_terminal_character

_LINKEDIN_POST_ID_PATTERN = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]+$")
_LINKEDIN_AUTHOR_PATTERN = re.compile(r"^urn:li:person:[^:\s]+$")
_X_POST_ID_PATTERN = re.compile(r"^[0-9]+$")


class PublicationPlatform(StrEnum):
    """Supported publication destination."""

    LINKEDIN = "linkedin"
    X = "x"


class PublicationStatus(StrEnum):
    """Terminal result of one network publication attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class PublishRequest(BaseModel):
    """Human-approved content passed through the publisher boundary."""

    attempt_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    platform: PublicationPlatform
    account_reference: str = Field(min_length=1)
    author_urn: str = Field(min_length=1, repr=False)
    content: str = Field(min_length=1, repr=False)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    api_version: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("author_urn")
    @classmethod
    def validate_author_urn(cls, value: str) -> str:
        """Reject control characters at the platform-neutral boundary."""
        return _reject_control_characters(value, label="author URN")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """Reject unsafe publication text before it reaches an adapter."""
        return _reject_unsafe_content(value)

    @model_validator(mode="after")
    def validate_platform_fields(self) -> PublishRequest:
        """Require the author and API version shape for the selected platform."""
        _validate_author_reference(self.platform, self.author_urn)
        _validate_api_version(self.platform, self.api_version)
        return self


class PublishResult(BaseModel):
    """Domain result returned by a platform adapter."""

    platform: PublicationPlatform
    account_reference: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: PublicationStatus
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_post_id: str | None = Field(
        default=None,
    )
    occurred_at: datetime
    http_status: int | None = None
    api_endpoint: str = Field(min_length=1)
    api_version: str = Field(min_length=1)
    error_category: str | None = None
    safe_error_message: str | None = None

    @field_validator("external_post_id")
    @classmethod
    def validate_external_post_id(cls, value: str | None) -> str | None:
        """Reject unsafe or malformed identifiers returned by an adapter."""
        validated = _reject_control_characters(value, label="external post ID")
        return validated

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        """Require an unambiguous publication result timestamp."""
        validated = _require_timezone(
            value,
            label="publication result timestamp",
        )
        assert validated is not None
        return validated

    @model_validator(mode="after")
    def validate_success(self) -> PublishResult:
        """Require the fields that make an adapter success unambiguous."""
        _validate_external_post_id(self.platform, self.external_post_id)
        _validate_api_version(self.platform, self.api_version)
        if self.status is PublicationStatus.SUCCEEDED:
            if self.external_post_id is None or self.http_status != 201:
                raise ValueError(
                    "successful publication result requires a post ID and HTTP 201"
                )
            if self.error_category is not None or self.safe_error_message is not None:
                raise ValueError(
                    "successful publication result must not contain error details"
                )
        return self


class PublishReceipt(BaseModel):
    """Persisted operational record for one publication attempt."""

    receipt_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    platform: PublicationPlatform
    account_reference: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PublicationStatus
    external_post_id: str | None = Field(
        default=None,
    )
    created_at: datetime
    published_at: datetime | None = None
    api_endpoint: str = Field(min_length=1)
    api_version: str = Field(min_length=1)
    http_status: int | None = None
    error_category: str | None = None
    safe_error_message: str | None = None
    duplicate_of_receipt_id: str | None = None

    @field_validator("external_post_id")
    @classmethod
    def validate_external_post_id(cls, value: str | None) -> str | None:
        """Reject unsafe or malformed identifiers before persistence."""
        validated = _reject_control_characters(value, label="external post ID")
        return validated

    @field_validator("created_at", "published_at")
    @classmethod
    def validate_receipt_timestamps(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """Require unambiguous operational receipt timestamps."""
        return _require_timezone(value, label="publication receipt timestamp")

    @model_validator(mode="after")
    def validate_status_fields(self) -> PublishReceipt:
        """Keep confirmed and unconfirmed publication fields consistent."""
        _validate_external_post_id(self.platform, self.external_post_id)
        _validate_api_version(self.platform, self.api_version)
        if self.status is PublicationStatus.SUCCEEDED:
            if (
                self.external_post_id is None
                or self.published_at is None
                or self.http_status != 201
            ):
                raise ValueError(
                    "successful receipt requires a post ID, publication time, "
                    "and HTTP 201"
                )
            if self.error_category is not None or self.safe_error_message is not None:
                raise ValueError(
                    "successful receipt must not contain error details"
                )
        elif self.published_at is not None:
            raise ValueError(
                "non-successful receipt must not contain a publication time"
            )
        if (
            self.status is PublicationStatus.FAILED
            and self.external_post_id is not None
        ):
            raise ValueError(
                "failed receipt must not contain an external post ID"
            )
        return self


class PublicationPreview(BaseModel):
    """Complete pre-publication view presented to the human."""

    platform: PublicationPlatform
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    account_reference: str = Field(min_length=1)
    account_display_name: str = Field(min_length=1, repr=False)
    content_length: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(min_length=1, repr=False)
    duplicate_found: bool
    duplicate_external_post_id: str | None = None
    duplicate_published_at: datetime | None = None
    indeterminate_found: bool = False
    indeterminate_receipt_id: str | None = None
    indeterminate_created_at: datetime | None = None
    network_request_will_occur: Literal[False] = False

    @field_validator("duplicate_published_at", "indeterminate_created_at")
    @classmethod
    def validate_prior_timestamps(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """Require unambiguous timestamps for prior publication attempts."""
        return _require_timezone(value, label="prior publication timestamp")

    @field_validator("account_display_name")
    @classmethod
    def validate_account_display_name(cls, value: str) -> str:
        """Reject terminal controls in the human-visible account name."""
        validated = _reject_control_characters(value, label="account display name")
        assert validated is not None
        return validated

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """Reject unsafe text before a preview is rendered."""
        return _reject_unsafe_content(value)

    @model_validator(mode="after")
    def validate_prior_results(self) -> PublicationPreview:
        """Keep duplicate and unresolved summary fields internally consistent."""
        if self.content_length != len(self.content):
            raise ValueError(
                "preview content length must match the displayed content"
            )
        if self.duplicate_found and (
            self.duplicate_external_post_id is None
            or self.duplicate_published_at is None
        ):
            raise ValueError(
                "duplicate preview requires a prior post ID and publication time"
            )
        if not self.duplicate_found and (
            self.duplicate_external_post_id is not None
            or self.duplicate_published_at is not None
        ):
            raise ValueError(
                "non-duplicate preview must not include prior publication details"
            )
        if self.indeterminate_found and (
            self.indeterminate_receipt_id is None
            or self.indeterminate_created_at is None
        ):
            raise ValueError(
                "indeterminate preview requires a receipt ID and attempt time"
            )
        if not self.indeterminate_found and (
            self.indeterminate_receipt_id is not None
            or self.indeterminate_created_at is not None
        ):
            raise ValueError(
                "resolved preview must not include indeterminate attempt details"
            )
        return self


class Publisher(Protocol):
    """Minimal replaceable platform adapter boundary."""

    def publish(self, request: PublishRequest) -> PublishResult:
        """Submit one explicitly approved publication."""


def _reject_control_characters(
    value: str | None,
    *,
    label: str,
) -> str | None:
    if value is not None and any(
        is_unsafe_terminal_character(character)
        for character in value
    ):
        raise ValueError(f"{label} contains invalid control characters")
    return value


def _require_timezone(
    value: datetime | None,
    *,
    label: str,
) -> datetime | None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC) if value is not None else None


def _reject_unsafe_content(value: str) -> str:
    if any(
        is_unsafe_terminal_character(character)
        and character not in {"\n", "\t"}
        for character in value
    ):
        raise ValueError("publication content contains unsafe control characters")
    return value


def _validate_author_reference(
    platform: PublicationPlatform,
    value: str,
) -> None:
    pattern = (
        _LINKEDIN_AUTHOR_PATTERN
        if platform is PublicationPlatform.LINKEDIN
        else _X_POST_ID_PATTERN
    )
    if pattern.fullmatch(value) is None:
        raise ValueError("author reference has invalid format for platform")


def _validate_external_post_id(
    platform: PublicationPlatform,
    value: str | None,
) -> None:
    if value is None:
        return
    pattern = (
        _LINKEDIN_POST_ID_PATTERN
        if platform is PublicationPlatform.LINKEDIN
        else _X_POST_ID_PATTERN
    )
    if pattern.fullmatch(value) is None:
        raise ValueError("external post ID has invalid format for platform")


def _validate_api_version(
    platform: PublicationPlatform,
    value: str,
) -> None:
    valid = (
        re.fullmatch(r"[0-9]{6}", value) is not None
        if platform is PublicationPlatform.LINKEDIN
        else value == "2"
    )
    if not valid:
        raise ValueError("API version has invalid format for platform")
