"""Tests for LinkedIn configuration and secret-safety boundaries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from buildlog.linkedin_config import (
    DEFAULT_API_VERSION,
    linkedin_configuration_status,
    load_linkedin_settings,
)
from buildlog.linkedin_errors import LinkedInConfigurationError
from buildlog.linkedin_security import redact_linkedin_secrets


def test_linkedin_configuration_loads_safe_defaults() -> None:
    settings = load_linkedin_settings(
        environ={
            "LINKEDIN_CLIENT_ID": "client-id",
            "LINKEDIN_CLIENT_SECRET": "super-secret",
        }
    )

    assert settings.redirect_uri.endswith("/auth/linkedin/callback")
    assert settings.api_version == DEFAULT_API_VERSION
    assert settings.posts_url == "https://api.linkedin.com/rest/posts"
    assert settings.scopes == ("openid", "profile", "w_member_social")
    assert "super-secret" not in repr(settings)
    assert settings.safe_summary()["client_secret_configured"] is True


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LINKEDIN_CLIENT_SECRET": "secret"}, "LINKEDIN_CLIENT_ID"),
        ({"LINKEDIN_CLIENT_ID": "id"}, "LINKEDIN_CLIENT_SECRET"),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_REDIRECT_URI": "https://example.com/callback",
            },
            "LINKEDIN_REDIRECT_URI",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "client\nid",
                "LINKEDIN_CLIENT_SECRET": "secret",
            },
            "LINKEDIN_CLIENT_ID",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "sec\tret",
            },
            "LINKEDIN_CLIENT_SECRET",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "cliënt-id",
                "LINKEDIN_CLIENT_SECRET": "secret",
            },
            "LINKEDIN_CLIENT_ID",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret\u202evalue",
            },
            "LINKEDIN_CLIENT_SECRET",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_API_VERSION": "latest",
            },
            "LINKEDIN_API_VERSION",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_API_VERSION": "202613",
            },
            "LINKEDIN_API_VERSION",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_TOKEN_URL": "https://example.com/token",
            },
            "LINKEDIN_TOKEN_URL",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_USERINFO_URL": "https://example.com/userinfo",
            },
            "LINKEDIN_USERINFO_URL",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_REDIRECT_URI": (
                    "http://user@localhost:8765/auth/linkedin/callback"
                ),
            },
            "LINKEDIN_REDIRECT_URI",
        ),
        (
            {
                "LINKEDIN_CLIENT_ID": "id",
                "LINKEDIN_CLIENT_SECRET": "secret",
                "LINKEDIN_REDIRECT_URI": (
                    "http://localhost:99999/auth/linkedin/callback"
                ),
            },
            "LINKEDIN_REDIRECT_URI",
        ),
    ],
)
def test_linkedin_configuration_validation(
    environ: dict[str, str],
    expected: str,
) -> None:
    with pytest.raises(LinkedInConfigurationError, match=expected):
        load_linkedin_settings(environ=environ)


def test_linkedin_configuration_status_reports_readiness_without_secrets() -> None:
    status = linkedin_configuration_status(
        environ={
            "LINKEDIN_CLIENT_ID": "client-id",
            "LINKEDIN_CLIENT_SECRET": "super-secret",
            "LINKEDIN_REDIRECT_URI": "https://example.com/callback",
        }
    )

    assert status["configuration_ready"] is False
    assert "LINKEDIN_REDIRECT_URI" in str(status["configuration_issue"])
    assert "super-secret" not in repr(status)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_linkedin_configuration_rejects_unsafe_env_permissions(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LINKEDIN_CLIENT_ID=client-id\n"
        "LINKEDIN_CLIENT_SECRET=super-secret\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    with pytest.raises(LinkedInConfigurationError, match="chmod 600"):
        load_linkedin_settings(tmp_path)

    status = linkedin_configuration_status(tmp_path)
    assert status["configuration_ready"] is False
    assert "chmod 600" in str(status["configuration_issue"])
    assert "super-secret" not in repr(status)


def test_secret_redaction_covers_credentials_and_headers() -> None:
    message = (
        "client_secret=secret-value "
        "access_token=access-value "
        "Authorization: Bearer bearer-value "
        "id_token=id-value "
        "detail=\x1b[2J "
        "direction=\u202ehidden "
        "surrogate=\ud800"
    )

    redacted = redact_linkedin_secrets(
        message,
        known_secrets=["secret-value", "access-value", "bearer-value", "id-value"],
    )

    assert "secret-value" not in redacted
    assert "access-value" not in redacted
    assert "bearer-value" not in redacted
    assert "id-value" not in redacted
    assert "\x1b" not in redacted
    assert "\\u001B" in redacted
    assert "\u202e" not in redacted
    assert "\\u202E" in redacted
    assert "\ud800" not in redacted
    assert "\\uD800" in redacted
    assert redacted.count("<redacted>") >= 4


def test_secret_redaction_handles_common_key_styles() -> None:
    redacted = redact_linkedin_secrets(
        "clientSecret=one access-token=two authorizationCode=three"
    )

    assert "one" not in redacted
    assert "two" not in redacted
    assert "three" not in redacted


def test_secret_redaction_handles_overlapping_known_values() -> None:
    redacted = redact_linkedin_secrets(
        "Rejected urn and urn:li:person:member-123",
        known_secrets=["urn", "urn:li:person:member-123"],
    )

    assert "member-123" not in redacted
    assert "urn" not in redacted
    assert redacted.count("<redacted>") == 2


def test_secret_redaction_handles_isolated_bearer_credential() -> None:
    redacted = redact_linkedin_secrets(
        "provider rejected header value Bearer abc.def_123~+/=="
    )

    assert "abc.def_123" not in redacted
    assert "Bearer" not in redacted
    assert "<redacted>" in redacted


@pytest.mark.parametrize(
    "message",
    [
        "{'Authorization': 'Bearer dict-secret'}",
        '{"Authorization": "Bearer json-secret"}',
        "authorization='Bearer assignment-secret'",
    ],
)
def test_secret_redaction_handles_quoted_authorization_headers(
    message: str,
) -> None:
    redacted = redact_linkedin_secrets(message)

    assert "secret" not in redacted
    assert "Bearer" not in redacted
    assert "<redacted>" in redacted
