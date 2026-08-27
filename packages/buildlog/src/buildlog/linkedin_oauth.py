"""LinkedIn OAuth Authorization Code flow orchestration."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretStr, ValidationError

from buildlog.linkedin_callback import OAuthCallback
from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    MalformedTokenResponseError,
    OAuthAuthorizationDeniedError,
    TokenExchangeError,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_security import redact_linkedin_secrets
from buildlog.linkedin_token_store import (
    LinkedInToken,
    OAuthStateStore,
    TokenStore,
)


@dataclass(frozen=True)
class AuthorizationStart:
    """Safe result of beginning an OAuth authorization."""

    authorization_url: str = field(repr=False)


class LinkedInOAuthService:
    """Coordinate OAuth state, token exchange, and local token persistence."""

    def __init__(
        self,
        settings: LinkedInSettings,
        http: LinkedInHttpClient,
        token_store: TokenStore,
        state_store: OAuthStateStore,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store
        self.state_store = state_store

    def start_authorization(
        self,
        *,
        now: datetime | None = None,
    ) -> AuthorizationStart:
        """Create an authorization URL and persist one-time CSRF state."""
        created_at = now or datetime.now(UTC)
        state = secrets.token_urlsafe(32)
        self.state_store.save(state, created_at=created_at)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "state": state,
                "scope": " ".join(self.settings.scopes),
            }
        )
        return AuthorizationStart(
            authorization_url=f"{self.settings.authorization_url}?{query}"
        )

    def complete_authorization(
        self,
        callback: OAuthCallback,
        *,
        now: datetime | None = None,
    ) -> LinkedInToken:
        """Validate callback state, exchange its code, and save the token."""
        completed_at = now or datetime.now(UTC)
        self.state_store.consume(callback.state, now=completed_at)
        if callback.error is not None:
            safe_description = redact_linkedin_secrets(
                callback.error_description or callback.error
            )
            raise OAuthAuthorizationDeniedError(
                f"LinkedIn authorization was denied: {safe_description}. "
                "Run login again when ready."
            )
        if not callback.code:
            raise TokenExchangeError(
                "LinkedIn callback did not include an authorization code."
            )
        token = self.exchange_code(callback.code, now=completed_at)
        self.token_store.save(token)
        return token

    def exchange_code(
        self,
        code: str,
        *,
        now: datetime | None = None,
    ) -> LinkedInToken:
        """Exchange one authorization code for a validated token."""
        response = self.http.post_form(
            self.settings.token_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret.get_secret_value(),
                "redirect_uri": self.settings.redirect_uri,
            },
        )
        if response.status_code != 200:
            body = response.json_body if isinstance(response.json_body, dict) else {}
            known_secrets = (
                code,
                self.settings.client_secret.get_secret_value(),
                *(
                    value
                    for key in (
                        "access_token",
                        "refresh_token",
                        "authorization_code",
                        "code",
                        "client_secret",
                    )
                    if isinstance((value := body.get(key)), str)
                ),
            )
            provider_details = [
                f"{name}={redact_linkedin_secrets(body[name], known_secrets=known_secrets)}"
                for name in ("error", "error_description")
                if isinstance(body.get(name), str)
            ]
            details = (
                f" LinkedIn response: {', '.join(provider_details)}."
                if provider_details
                else ""
            )
            raise TokenExchangeError(
                f"LinkedIn token exchange failed with HTTP {response.status_code}."
                f"{details} Verify the redirect URI, "
                "Client ID, Client Secret, and that the authorization code is fresh."
            )
        return parse_token_response(
            response.json_body,
            now=now or datetime.now(UTC),
        )


def parse_token_response(
    payload: Any,
    *,
    now: datetime,
) -> LinkedInToken:
    """Validate LinkedIn's token response without exposing token values."""
    if not isinstance(payload, dict):
        raise MalformedTokenResponseError(
            "LinkedIn token response was not a JSON object."
        )
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or expires_in <= 0
    ):
        raise MalformedTokenResponseError(
            "LinkedIn token response omitted access_token or a valid expires_in."
        )
    raw_scope = payload.get("scope")
    scopes = _parse_scopes(raw_scope)
    refresh_token = payload.get("refresh_token")
    refresh_expires_in = payload.get("refresh_token_expires_in")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an invalid refresh_token."
        )
    if refresh_expires_in is not None and (
        not isinstance(refresh_expires_in, int)
        or isinstance(refresh_expires_in, bool)
        or refresh_expires_in <= 0
    ):
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an invalid refresh_token_expires_in."
        )
    id_token = payload.get("id_token")
    if id_token is not None and not isinstance(id_token, str):
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an invalid id_token."
        )
    token_type = payload.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.casefold() != "bearer":
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an unsupported token_type."
        )
    try:
        expires_at = now + timedelta(seconds=expires_in)
        refresh_expires_at = (
            now + timedelta(seconds=refresh_expires_in)
            if refresh_expires_in is not None
            else None
        )
    except OverflowError as exc:
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an invalid expiration."
        ) from exc
    try:
        return LinkedInToken(
            access_token=SecretStr(access_token),
            token_type="Bearer",
            expires_at=expires_at,
            scopes=scopes,
            scope_source="response" if raw_scope is not None else "unavailable",
            id_token=SecretStr(id_token) if id_token else None,
            refresh_token=SecretStr(refresh_token) if refresh_token else None,
            refresh_expires_at=refresh_expires_at,
            obtained_at=now,
        )
    except ValidationError as exc:
        invalid_fields = sorted(
            {
                ".".join(str(part) for part in error["loc"])
                for error in exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            }
        )
        raise MalformedTokenResponseError(
            "LinkedIn token response contained invalid credential values "
            f"(fields: {', '.join(invalid_fields)})."
        ) from exc


def _parse_scopes(value: object) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, str):
        raise MalformedTokenResponseError(
            "LinkedIn token response contained an invalid scope value."
        )
    return {scope for scope in re.split(r"[\s,]+", value) if scope}
