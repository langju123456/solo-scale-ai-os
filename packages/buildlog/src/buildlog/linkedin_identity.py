"""Authenticated LinkedIn member identity resolution through OIDC userinfo."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    ExpiredTokenError,
    IdentityResolutionError,
    LinkedInForbiddenError,
    LinkedInUnauthorizedError,
    MissingPermissionError,
    MissingTokenError,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_security import redacted_identifier
from buildlog.linkedin_token_store import LinkedInToken, TokenStore
from buildlog.terminal_safety import is_unsafe_terminal_character


class LinkedInIdentity(BaseModel):
    """Safe authenticated-member identity used by publishing."""

    subject: str = Field(repr=False)
    display_name: str = Field(repr=False)
    person_urn: str = Field(repr=False)
    account_reference: str
    author_mapping_source: str = "oidc_userinfo_sub_inferred"

    @property
    def author_reference(self) -> str:
        """Return the platform author identifier consumed by publishing."""
        return self.person_urn

    @property
    def mapping_source(self) -> str:
        """Return how the platform author identifier was derived."""
        return self.author_mapping_source

    @property
    def redacted_subject(self) -> str:
        """Return a terminal-safe identifier."""
        return redacted_identifier(self.subject)


class LinkedInIdentityService:
    """Resolve the authenticated member without trusting an unverified JWT."""

    def __init__(
        self,
        settings: LinkedInSettings,
        http: LinkedInHttpClient,
        token_store: TokenStore,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store

    def resolve(self, *, now: datetime | None = None) -> LinkedInIdentity:
        """Load a valid token and resolve OIDC userinfo."""
        token = require_valid_token(self.token_store, now=now)
        require_scopes(token, {"openid", "profile"})
        response = self.http.get_userinfo(
            self.settings.userinfo_url,
            access_token=token.access_token.get_secret_value(),
        )
        if response.status_code == 401:
            raise LinkedInUnauthorizedError(
                "LinkedIn rejected the token. Run the login command again.",
                status_code=401,
            )
        if response.status_code == 403:
            raise LinkedInForbiddenError(
                "LinkedIn denied profile access. Confirm the OIDC product and "
                "openid/profile scopes are enabled.",
                status_code=403,
            )
        if response.status_code != 200:
            raise IdentityResolutionError(
                "LinkedIn userinfo could not be resolved. Review the OAuth "
                f"configuration and try again (HTTP {response.status_code})."
            )
        payload = response.json_body
        if not isinstance(payload, dict):
            raise IdentityResolutionError(
                "LinkedIn userinfo returned malformed JSON."
            )
        subject = payload.get("sub")
        display_name = payload.get("name")
        if not isinstance(subject, str) or not subject.strip():
            raise IdentityResolutionError(
                "LinkedIn userinfo did not include a non-empty subject identifier."
            )
        if not isinstance(display_name, str) or not display_name.strip():
            given = payload.get("given_name")
            family = payload.get("family_name")
            parts = [
                part.strip()
                for part in (given, family)
                if isinstance(part, str) and part.strip()
            ]
            display_name = " ".join(parts)
        if not display_name:
            raise IdentityResolutionError(
                "LinkedIn userinfo did not include a display name."
            )
        if any(
            is_unsafe_terminal_character(character)
            and character not in {" ", "\t", "\r", "\n"}
            for character in display_name
        ):
            raise IdentityResolutionError(
                "LinkedIn userinfo returned an unsafe display name."
            )
        normalized_subject = subject.strip()
        if (
            ":" in normalized_subject
            or any(character.isspace() for character in normalized_subject)
            or any(
                is_unsafe_terminal_character(character)
                for character in normalized_subject
            )
        ):
            raise IdentityResolutionError(
                "LinkedIn userinfo returned an invalid subject identifier."
            )
        normalized_display_name = " ".join(display_name.split())
        if not normalized_display_name:
            raise IdentityResolutionError(
                "LinkedIn userinfo did not include a display name."
            )
        return LinkedInIdentity(
            subject=normalized_subject,
            display_name=normalized_display_name,
            person_urn=f"urn:li:person:{normalized_subject}",
            account_reference=_account_reference(normalized_subject),
        )


def require_valid_token(
    token_store: TokenStore,
    *,
    now: datetime | None = None,
) -> LinkedInToken:
    """Return a non-expired local token or raise an actionable error."""
    token = token_store.load()
    if token is None:
        raise MissingTokenError(
            "No LinkedIn token exists. Run the login command first."
        )
    if token.is_expired(now=now or datetime.now(UTC)):
        raise ExpiredTokenError(
            "The LinkedIn token has expired. Run the login command to reauthorize."
        )
    return token


def require_scopes(token: LinkedInToken, required: set[str]) -> None:
    """Reject known-incomplete scope grants while preserving unknown status."""
    if token.scope_source != "response":
        return
    missing = required - token.scopes
    if missing:
        formatted = ", ".join(sorted(missing))
        raise MissingPermissionError(
            f"LinkedIn token is missing required scope(s): {formatted}. "
            "Enable the required product and run login again."
        )


def _account_reference(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:20]
