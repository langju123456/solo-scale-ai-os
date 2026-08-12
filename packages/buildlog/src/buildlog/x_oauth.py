"""OAuth 2.0 Authorization Code with PKCE for X."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import OAuth2Client
from pydantic import SecretStr, ValidationError

from buildlog.linkedin_callback import OAuthCallback
from buildlog.x_config import XSettings
from buildlog.x_errors import XOAuthError
from buildlog.x_token_store import (
    FileXAuthorizationStore,
    FileXTokenStore,
    PendingXAuthorization,
    XToken,
)


@dataclass(frozen=True)
class XAuthorizationStart:
    """One safe authorization URL for an interactive X login."""

    authorization_url: str = field(repr=False)


class XOAuthService:
    """Create a PKCE authorization and exchange its one-time code."""

    def __init__(
        self,
        settings: XSettings,
        token_store: FileXTokenStore,
        authorization_store: FileXAuthorizationStore,
    ) -> None:
        self.settings = settings
        self.token_store = token_store
        self.authorization_store = authorization_store

    def start_authorization(
        self,
        *,
        now: datetime | None = None,
    ) -> XAuthorizationStart:
        """Create an Authlib PKCE URL and save its verifier privately."""
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        client = self._client()
        try:
            authorization_url, returned_state = client.create_authorization_url(
                self.settings.authorization_url,
                state=state,
                code_verifier=code_verifier,
            )
        finally:
            client.close()
        self.authorization_store.save(
            PendingXAuthorization(
                state=SecretStr(returned_state),
                code_verifier=SecretStr(code_verifier),
                created_at=now or datetime.now(UTC),
            )
        )
        return XAuthorizationStart(authorization_url=authorization_url)

    def complete_authorization(
        self,
        callback: OAuthCallback,
        *,
        now: datetime | None = None,
    ) -> XToken:
        """Validate callback state, exchange the code, and save the token."""
        completed_at = now or datetime.now(UTC)
        pending = self.authorization_store.consume(
            callback.state,
            now=completed_at,
        )
        if callback.error is not None:
            detail = callback.error_description or callback.error
            safe_detail = " ".join(detail.split())[:200]
            raise XOAuthError(f"X authorization was denied: {safe_detail}")
        if not callback.code:
            raise XOAuthError("X callback did not contain an authorization code.")

        client = self._client()
        try:
            payload = client.fetch_token(
                self.settings.token_url,
                grant_type="authorization_code",
                code=callback.code,
                redirect_uri=self.settings.redirect_uri,
                code_verifier=pending.code_verifier.get_secret_value(),
            )
        except (OAuthError, httpx.HTTPError, ValueError) as exc:
            raise XOAuthError(
                "X token exchange failed. Verify the Client ID, exact callback "
                "URL, app type, and that the authorization code is fresh."
            ) from exc
        finally:
            client.close()
        token = parse_x_token(payload, now=completed_at)
        self.token_store.save(token)
        return token

    def _client(self) -> OAuth2Client:
        return OAuth2Client(
            client_id=self.settings.client_id,
            token_endpoint_auth_method="none",
            scope=" ".join(self.settings.scopes),
            redirect_uri=self.settings.redirect_uri,
            code_challenge_method="S256",
            timeout=self.settings.request_timeout_seconds,
        )


def parse_x_token(payload: Any, *, now: datetime) -> XToken:
    """Validate Authlib's X token mapping without exposing credential values."""
    if not isinstance(payload, dict):
        raise XOAuthError("X token response was not a JSON object.")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or expires_in <= 0
    ):
        raise XOAuthError(
            "X token response omitted access_token or a valid expires_in."
        )
    token_type = payload.get("token_type", "Bearer")
    refresh_token = payload.get("refresh_token")
    if not isinstance(token_type, str) or (
        refresh_token is not None and not isinstance(refresh_token, str)
    ):
        raise XOAuthError("X token response contained invalid credential metadata.")
    raw_scope = payload.get("scope", "")
    if isinstance(raw_scope, str):
        scopes = {value for value in raw_scope.replace(",", " ").split() if value}
    elif isinstance(raw_scope, list) and all(
        isinstance(value, str) for value in raw_scope
    ):
        scopes = set(raw_scope)
    else:
        raise XOAuthError("X token response contained an invalid scope value.")
    try:
        return XToken(
            access_token=SecretStr(access_token),
            refresh_token=(
                SecretStr(refresh_token)
                if isinstance(refresh_token, str) and refresh_token
                else None
            ),
            token_type=token_type,
            expires_at=now + timedelta(seconds=expires_in),
            obtained_at=now,
            scopes=scopes,
        )
    except (OverflowError, ValidationError) as exc:
        raise XOAuthError("X token response contained invalid values.") from exc
