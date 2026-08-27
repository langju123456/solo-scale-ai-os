"""Typed failures for X authentication and text publishing."""

from __future__ import annotations

from buildlog.exceptions import BuildLogError


class XError(BuildLogError):
    """Base class for the X integration."""


class XConfigurationError(XError):
    """Required X configuration is missing or invalid."""


class XCredentialStoreError(XError):
    """Local X OAuth state could not be read or written."""


class XOAuthError(XError):
    """X OAuth authorization or token exchange failed."""


class XCallbackTimeoutError(XOAuthError):
    """X did not return to the local callback before its deadline."""


class XMissingTokenError(XError):
    """No local X user token exists."""


class XExpiredTokenError(XError):
    """The local X user token is expired."""


class XMissingPermissionError(XError):
    """The X token lacks a required scope."""


class XIdentityError(XError):
    """The authenticated X user could not be resolved."""


class XPublicationValidationError(XError):
    """The selected artifact cannot be published to X."""


class XAPIError(XError):
    """X returned a handled non-success response."""

    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


class XNetworkError(XError):
    """An X HTTP request ended without a response."""


class XRequestTimeoutError(XError):
    """An X HTTP request exceeded its timeout."""
