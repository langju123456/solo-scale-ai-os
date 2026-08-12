"""Authenticated X user resolution through `GET /2/users/me`."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from buildlog.terminal_safety import is_unsafe_terminal_character
from buildlog.x_config import XSettings
from buildlog.x_errors import (
    XExpiredTokenError,
    XIdentityError,
    XMissingPermissionError,
    XMissingTokenError,
)
from buildlog.x_http import XHttpClient
from buildlog.x_token_store import FileXTokenStore, XToken


class XIdentity(BaseModel):
    """Verified authenticated X identity used by publishing."""

    user_id: str = Field(pattern=r"^[0-9]+$", repr=False)
    username: str = Field(pattern=r"^[A-Za-z0-9_]{1,50}$")
    display_name: str = Field(min_length=1, repr=False)
    account_reference: str
    mapping_source: str = "x_users_me_verified"

    @property
    def author_reference(self) -> str:
        """Return X's verified numeric user ID."""
        return self.user_id

    @property
    def redacted_user_id(self) -> str:
        """Return a stable non-reversible diagnostic identifier."""
        return hashlib.sha256(self.user_id.encode("utf-8")).hexdigest()[:12]


class XIdentityService:
    """Resolve one authenticated X user from a valid local token."""

    def __init__(
        self,
        settings: XSettings,
        http: XHttpClient,
        token_store: FileXTokenStore,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store

    def resolve(self, *, now: datetime | None = None) -> XIdentity:
        """Return the verified identity from X's authenticated-user endpoint."""
        token = require_valid_x_token(self.token_store, now=now)
        require_x_scopes(token, {"users.read"})
        response = self.http.get_me(
            self.settings.me_url,
            access_token=token.access_token.get_secret_value(),
        )
        if response.status_code != 200:
            raise XIdentityError(
                "X could not resolve the authenticated user "
                f"(HTTP {response.status_code})."
            )
        payload = response.json_body
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise XIdentityError("X returned malformed authenticated-user data.")
        user_id = data.get("id")
        username = data.get("username")
        display_name = data.get("name")
        if (
            not isinstance(user_id, str)
            or re.fullmatch(r"[0-9]+", user_id) is None
            or not isinstance(username, str)
            or re.fullmatch(r"[A-Za-z0-9_]{1,50}", username) is None
            or not isinstance(display_name, str)
            or not display_name.strip()
        ):
            raise XIdentityError("X returned an incomplete authenticated identity.")
        normalized_name = " ".join(display_name.split())
        if any(
            is_unsafe_terminal_character(character)
            for character in normalized_name
        ):
            raise XIdentityError("X returned an unsafe display name.")
        return XIdentity(
            user_id=user_id,
            username=username,
            display_name=normalized_name,
            account_reference=hashlib.sha256(
                user_id.encode("utf-8")
            ).hexdigest()[:20],
        )


def require_valid_x_token(
    token_store: FileXTokenStore,
    *,
    now: datetime | None = None,
) -> XToken:
    """Return a present, non-expired X token."""
    token = token_store.load()
    if token is None:
        raise XMissingTokenError("No X token exists. Run `buildlog x login`.")
    if token.is_expired(now=now or datetime.now(UTC)):
        raise XExpiredTokenError(
            "The X token has expired. Run `buildlog x login` again."
        )
    return token


def require_x_scopes(token: XToken, required: set[str]) -> None:
    """Require the exact user-context scopes needed by an operation."""
    missing = required - token.scopes
    if missing:
        raise XMissingPermissionError(
            "X token is missing required scope(s): "
            + ", ".join(sorted(missing))
        )
