"""Environment-backed configuration for the LinkedIn integration."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import SecretStr

from buildlog.linkedin_errors import LinkedInConfigurationError

DEFAULT_REDIRECT_URI = "http://localhost:8765/auth/linkedin/callback"
DEFAULT_AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
DEFAULT_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
DEFAULT_API_BASE_URL = "https://api.linkedin.com"
DEFAULT_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
DEFAULT_API_VERSION = "202607"
DEFAULT_SCOPES = ("openid", "profile", "w_member_social")


@dataclass(frozen=True)
class LinkedInSettings:
    """Validated runtime settings for LinkedIn OAuth and publishing."""

    client_id: str
    client_secret: SecretStr
    redirect_uri: str = DEFAULT_REDIRECT_URI
    api_version: str = DEFAULT_API_VERSION
    authorization_url: str = DEFAULT_AUTHORIZATION_URL
    token_url: str = DEFAULT_TOKEN_URL
    api_base_url: str = DEFAULT_API_BASE_URL
    userinfo_url: str = DEFAULT_USERINFO_URL
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    request_timeout_seconds: float = 20.0
    callback_timeout_seconds: float = 180.0

    @property
    def posts_url(self) -> str:
        """Return the centralized current LinkedIn Posts endpoint."""
        return f"{self.api_base_url.rstrip('/')}/rest/posts"

    def safe_summary(self) -> dict[str, object]:
        """Return configuration diagnostics without credential values."""
        return {
            "client_id_configured": bool(self.client_id),
            "client_secret_configured": bool(self.client_secret.get_secret_value()),
            "redirect_uri": self.redirect_uri,
            "api_version": self.api_version,
            "authorization_url": self.authorization_url,
            "token_url": self.token_url,
            "api_base_url": self.api_base_url,
            "scopes": list(self.scopes),
        }


def load_linkedin_settings(
    project_root: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> LinkedInSettings:
    """Load and validate LinkedIn settings with actionable failures."""
    root = project_root or Path.cwd()
    if environ is None:
        env_path = root / ".env"
        _validate_private_env_file(env_path)
        load_dotenv(env_path)
        source = os.environ
    else:
        source = environ

    client_id = source.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = source.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if not client_id:
        raise LinkedInConfigurationError(
            "LINKEDIN_CLIENT_ID is missing. Add it to your local .env file."
        )
    if not client_secret:
        raise LinkedInConfigurationError(
            "LINKEDIN_CLIENT_SECRET is missing. Add it to your local .env file."
        )
    _validate_credential_value("LINKEDIN_CLIENT_ID", client_id)
    _validate_credential_value("LINKEDIN_CLIENT_SECRET", client_secret)

    redirect_uri = source.get(
        "LINKEDIN_REDIRECT_URI",
        DEFAULT_REDIRECT_URI,
    ).strip()
    _validate_redirect_uri(redirect_uri)
    api_version = source.get("LINKEDIN_API_VERSION", DEFAULT_API_VERSION).strip()
    if (
        len(api_version) != 6
        or not api_version.isdigit()
        or not 1 <= int(api_version[4:]) <= 12
    ):
        raise LinkedInConfigurationError(
            "LINKEDIN_API_VERSION must use a valid LinkedIn YYYYMM month."
        )

    authorization_url = source.get(
        "LINKEDIN_AUTHORIZATION_URL",
        DEFAULT_AUTHORIZATION_URL,
    ).strip()
    token_url = source.get("LINKEDIN_TOKEN_URL", DEFAULT_TOKEN_URL).strip()
    api_base_url = source.get(
        "LINKEDIN_API_BASE_URL",
        DEFAULT_API_BASE_URL,
    ).strip()
    userinfo_url = source.get(
        "LINKEDIN_USERINFO_URL",
        DEFAULT_USERINFO_URL,
    ).strip()
    for name, value, expected in (
        ("LINKEDIN_AUTHORIZATION_URL", authorization_url, DEFAULT_AUTHORIZATION_URL),
        ("LINKEDIN_TOKEN_URL", token_url, DEFAULT_TOKEN_URL),
        ("LINKEDIN_API_BASE_URL", api_base_url, DEFAULT_API_BASE_URL),
        ("LINKEDIN_USERINFO_URL", userinfo_url, DEFAULT_USERINFO_URL),
    ):
        if value != expected:
            raise LinkedInConfigurationError(
                f"{name} must use BuildLog's official LinkedIn endpoint: {expected}"
            )

    return LinkedInSettings(
        client_id=client_id,
        client_secret=SecretStr(client_secret),
        redirect_uri=redirect_uri,
        api_version=api_version,
        authorization_url=authorization_url,
        token_url=token_url,
        api_base_url=api_base_url,
        userinfo_url=userinfo_url,
    )


def linkedin_configuration_status(
    project_root: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return non-secret diagnostics even when credentials are incomplete."""
    root = project_root or Path.cwd()
    file_issue: str | None = None
    if environ is None:
        env_path = root / ".env"
        try:
            _validate_private_env_file(env_path)
        except LinkedInConfigurationError as exc:
            file_issue = str(exc)
        else:
            load_dotenv(env_path)
        source = os.environ
    else:
        source = environ
    try:
        if file_issue is not None:
            raise LinkedInConfigurationError(file_issue)
        load_linkedin_settings(root, environ=dict(source))
    except LinkedInConfigurationError as exc:
        configuration_ready = False
        configuration_issue = str(exc)
    else:
        configuration_ready = True
        configuration_issue = None
    return {
        "configuration_ready": configuration_ready,
        "configuration_issue": configuration_issue,
        "client_id_configured": bool(
            source.get("LINKEDIN_CLIENT_ID", "").strip()
        ),
        "client_secret_configured": bool(
            source.get("LINKEDIN_CLIENT_SECRET", "").strip()
        ),
        "redirect_uri": source.get(
            "LINKEDIN_REDIRECT_URI",
            DEFAULT_REDIRECT_URI,
        ).strip(),
        "api_version": source.get(
            "LINKEDIN_API_VERSION",
            DEFAULT_API_VERSION,
        ).strip(),
    }


def _validate_redirect_uri(value: str) -> None:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise LinkedInConfigurationError(
            "LINKEDIN_REDIRECT_URI contains an invalid port."
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or port is None
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        raise LinkedInConfigurationError(
            "LINKEDIN_REDIRECT_URI must be an exact localhost HTTP callback URL "
            "with an explicit port and no query or fragment."
        )


def _validate_credential_value(name: str, value: str) -> None:
    if any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise LinkedInConfigurationError(
            f"{name} must contain only visible ASCII credential characters."
        )


def _validate_private_env_file(path: Path) -> None:
    if path.is_symlink():
        raise LinkedInConfigurationError(
            "The local .env file must not be a symbolic link."
        )
    if not path.exists():
        return
    if not path.is_file():
        raise LinkedInConfigurationError(
            "The local .env path is not a regular file."
        )
    if os.name == "posix":
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise LinkedInConfigurationError(
                "The local .env file permissions are unsafe. Run chmod 600 .env "
                "before using LinkedIn credentials."
            )
