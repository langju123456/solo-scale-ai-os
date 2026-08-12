"""Typed, actionable failures for LinkedIn authentication and publishing."""

from __future__ import annotations

from datetime import datetime

from buildlog.exceptions import BuildLogError


class LinkedInError(BuildLogError):
    """Base class for LinkedIn integration failures."""


class LinkedInConfigurationError(LinkedInError):
    """Required local LinkedIn configuration is missing or invalid."""


class OAuthStateMismatchError(LinkedInError):
    """The OAuth callback state did not match the pending authorization."""


class OAuthAuthorizationDeniedError(LinkedInError):
    """The member denied or interrupted OAuth authorization."""


class OAuthCallbackTimeoutError(LinkedInError):
    """The local OAuth callback was not received before its deadline."""


class TokenExchangeError(LinkedInError):
    """LinkedIn rejected an authorization-code exchange."""


class MissingTokenError(LinkedInError):
    """No local LinkedIn token is available."""


class ExpiredTokenError(LinkedInError):
    """The local LinkedIn token is expired."""


class MalformedTokenResponseError(LinkedInError):
    """A token response or stored token is incomplete or malformed."""


class CredentialStoreError(LinkedInError):
    """Local LinkedIn credential state could not be read or written safely."""


class IdentityResolutionError(LinkedInError):
    """The authenticated LinkedIn member could not be resolved safely."""


class MissingPermissionError(LinkedInError):
    """The token does not include a required OAuth permission."""


class PublicationApprovalRequiredError(LinkedInError):
    """Publication was attempted without explicit human approval."""


class DuplicatePublicationBlockedError(LinkedInError):
    """An identical successful publication already exists."""

    def __init__(
        self,
        external_post_id: str | None,
        published_at: datetime | None,
    ) -> None:
        self.external_post_id = external_post_id
        self.published_at = published_at
        detail = external_post_id or "unknown external post ID"
        when = published_at.isoformat() if published_at else "unknown time"
        super().__init__(
            "identical content was already published "
            f"as {detail} at {when}; use --allow-duplicate only after review"
        )


class IndeterminatePublicationBlockedError(LinkedInError):
    """A matching unresolved publication may already exist."""

    def __init__(
        self,
        receipt_id: str,
        created_at: datetime,
        platform_name: str = "LinkedIn",
    ) -> None:
        self.receipt_id = receipt_id
        self.created_at = created_at
        super().__init__(
            "an identical publication attempt has an indeterminate outcome "
            f"({receipt_id}, {created_at.isoformat()}); inspect {platform_name} and the "
            "receipt before using --allow-duplicate"
        )


class PublicationValidationError(LinkedInError):
    """The selected run or publish request is not publication-ready."""


class PublicationReceiptPersistenceError(LinkedInError):
    """A publication outcome could not be recorded in the local receipt store."""


class LinkedInRequestTimeoutError(LinkedInError):
    """A LinkedIn HTTP request exceeded its configured timeout."""


class LinkedInNetworkError(LinkedInError):
    """A LinkedIn HTTP request ended before a response was available."""


class LinkedInAPIError(LinkedInError):
    """LinkedIn returned a handled non-success status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


class LinkedInBadRequestError(LinkedInAPIError):
    """LinkedIn rejected request validation."""


class LinkedInUnauthorizedError(LinkedInAPIError):
    """LinkedIn rejected the access token."""


class LinkedInForbiddenError(LinkedInAPIError):
    """LinkedIn rejected the app permission or author."""


class LinkedInRateLimitedError(LinkedInAPIError):
    """LinkedIn rate limits prevented the request."""


class LinkedInServerError(LinkedInAPIError):
    """LinkedIn returned a server-side failure."""


class IndeterminatePublicationError(LinkedInError):
    """Submission may have reached LinkedIn, so an automatic retry is unsafe."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message)
