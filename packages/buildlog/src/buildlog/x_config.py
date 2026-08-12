"""Environment-backed configuration for the X publishing baseline."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from buildlog.x_errors import XConfigurationError

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8766/auth/x/callback"
DEFAULT_AUTHORIZATION_URL = "https://x.com/i/oauth2/authorize"
DEFAULT_TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_API_BASE_URL = "https://api.x.com"
DEFAULT_API_VERSION = "2"
DEFAULT_SCOPES = ("tweet.read", "tweet.write", "users.read")


@dataclass(frozen=True)
class XSettings:
    """Validated OAuth and API settings for one local X user."""

    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    authorization_url: str = DEFAULT_AUTHORIZATION_URL
    token_url: str = DEFAULT_TOKEN_URL
    api_base_url: str = DEFAULT_API_BASE_URL
    api_version: str = DEFAULT_API_VERSION
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    request_timeout_seconds: float = 20.0
    callback_timeout_seconds: float = 180.0

    @property
    def posts_url(self) -> str:
        """Return the official X create-post endpoint."""
        return f"{self.api_base_url.rstrip('/')}/2/tweets"

    @property
    def me_url(self) -> str:
        """Return the official authenticated-user endpoint."""
        return f"{self.api_base_url.rstrip('/')}/2/users/me"


def load_x_settings(
    project_root: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> XSettings:
    """Load the minimal X OAuth public-client configuration."""
    root = project_root or Path.cwd()
    if environ is None:
        env_path = root / ".env"
        _validate_private_env_file(env_path)
        load_dotenv(env_path)
        source = os.environ
    else:
        source = environ

    client_id = source.get("X_CLIENT_ID", "").strip()
    if not client_id:
        raise XConfigurationError(
            "X_CLIENT_ID is missing. Add the OAuth 2.0 Client ID to .env."
        )
    _validate_visible_ascii("X_CLIENT_ID", client_id)
    redirect_uri = source.get("X_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
    _validate_redirect_uri(redirect_uri)

    configured_endpoints = (
        (
            "X_AUTHORIZATION_URL",
            source.get("X_AUTHORIZATION_URL", DEFAULT_AUTHORIZATION_URL).strip(),
            DEFAULT_AUTHORIZATION_URL,
        ),
        (
            "X_TOKEN_URL",
            source.get("X_TOKEN_URL", DEFAULT_TOKEN_URL).strip(),
            DEFAULT_TOKEN_URL,
        ),
        (
            "X_API_BASE_URL",
            source.get("X_API_BASE_URL", DEFAULT_API_BASE_URL).strip(),
            DEFAULT_API_BASE_URL,
        ),
    )
    for name, value, expected in configured_endpoints:
        if value != expected:
            raise XConfigurationError(
                f"{name} must use BuildLog's official X endpoint: {expected}"
            )

    return XSettings(
        client_id=client_id,
        redirect_uri=redirect_uri,
        authorization_url=configured_endpoints[0][1],
        token_url=configured_endpoints[1][1],
        api_base_url=configured_endpoints[2][1],
    )


def x_configuration_status(
    project_root: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return non-secret configuration diagnostics."""
    root = project_root or Path.cwd()
    if environ is None:
        env_path = root / ".env"
        try:
            _validate_private_env_file(env_path)
        except XConfigurationError as exc:
            issue: str | None = str(exc)
        else:
            issue = None
            load_dotenv(env_path)
        source = os.environ
    else:
        issue = None
        source = environ
    try:
        if issue is not None:
            raise XConfigurationError(issue)
        load_x_settings(root, environ=dict(source))
    except XConfigurationError as exc:
        ready = False
        issue = str(exc)
    else:
        ready = True
    return {
        "configuration_ready": ready,
        "configuration_issue": issue,
        "client_id_configured": bool(source.get("X_CLIENT_ID", "").strip()),
        "redirect_uri": source.get("X_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
        "scopes": list(DEFAULT_SCOPES),
    }


def _validate_redirect_uri(value: str) -> None:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise XConfigurationError("X_REDIRECT_URI contains an invalid port.") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        raise XConfigurationError(
            "X_REDIRECT_URI must be an exact loopback HTTP callback URL with "
            "an explicit port and no query or fragment."
        )


def _validate_visible_ascii(name: str, value: str) -> None:
    if any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise XConfigurationError(
            f"{name} must contain only visible ASCII credential characters."
        )


def _validate_private_env_file(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise XConfigurationError("The local .env path must be a regular file.")
    if os.name == "posix" and path.exists():
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise XConfigurationError(
                "The local .env permissions are unsafe. Run chmod 600 .env."
            )
