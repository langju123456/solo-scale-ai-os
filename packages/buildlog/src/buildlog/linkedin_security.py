"""Secret redaction helpers for LinkedIn diagnostics and telemetry."""

from __future__ import annotations

import re
from collections.abc import Iterable

from buildlog.terminal_safety import is_unsafe_terminal_character

_AUTHORIZATION_PATTERN = re.compile(
    r"""(?ix)
    (?P<prefix>
        ["']?
        Authorization
        ["']?
        \s*[:=]\s*
        ["']?
    )
    (?:Bearer\s+)?
    (?P<value>[^"'\s,;}\]]+)
    """
)
_BEARER_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"""(?ix)
    (?P<prefix>
        ["']?
        (?:client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|
           authorization[_-]?code|oauth[_-]?state)
        ["']?
        \s*[:=]\s*
        ["']?
    )
    (?P<value>[^"'\s,;}\]]+)
    """
)


def redact_linkedin_secrets(
    value: object,
    *,
    known_secrets: Iterable[str] = (),
) -> str:
    """Return text with LinkedIn credential material replaced."""
    redacted = str(value)
    secrets_by_length = sorted(
        {secret for secret in known_secrets if secret},
        key=len,
        reverse=True,
    )
    for secret in secrets_by_length:
        redacted = redacted.replace(secret, "<redacted>")
    redacted = _AUTHORIZATION_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        redacted,
    )
    redacted = _BEARER_CREDENTIAL_PATTERN.sub("<redacted>", redacted)
    redacted = _SENSITIVE_VALUE_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        redacted,
    )
    return "".join(
        character
        if not is_unsafe_terminal_character(character)
        else f"\\u{ord(character):04X}"
        for character in redacted
    )


def redacted_identifier(value: str, *, visible: int = 4) -> str:
    """Return a stable identifier with only a short suffix visible."""
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * min(8, len(value) - visible)}{value[-visible:]}"
