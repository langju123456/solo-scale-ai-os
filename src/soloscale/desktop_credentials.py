"""Process-memory credential handoff for the packaged macOS desktop app."""

from __future__ import annotations

import io
import sys
from typing import BinaryIO

_MAX_CREDENTIAL_BYTES = 512
_openai_api_key: str | None = None


class DesktopCredentialError(RuntimeError):
    """A desktop credential frame is missing or malformed."""


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise DesktopCredentialError("desktop credential handoff is incomplete")
        data.extend(chunk)
    return bytes(data)


def read_openai_credential_frame(stream: BinaryIO) -> str | None:
    """Read one bounded big-endian credential frame without retaining raw bytes."""

    header = _read_exact(stream, 4)
    payload_length = int.from_bytes(header, byteorder="big", signed=False)
    if payload_length > _MAX_CREDENTIAL_BYTES:
        raise DesktopCredentialError("desktop credential handoff exceeds its limit")
    if payload_length == 0:
        if stream.read(1):
            raise DesktopCredentialError("desktop credential handoff has trailing data")
        return None
    payload = _read_exact(stream, payload_length)
    if stream.read(1):
        raise DesktopCredentialError("desktop credential handoff has trailing data")
    try:
        credential = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise DesktopCredentialError("desktop credential handoff is invalid") from None
    if not credential or credential != credential.strip():
        raise DesktopCredentialError("desktop credential handoff is invalid")
    return credential


def configure_openai_credential_from_stdin(
    stream: BinaryIO | None = None,
) -> None:
    """Load the one-shot desktop credential into process memory only."""

    global _openai_api_key
    selected_stream = stream or sys.stdin.buffer
    _openai_api_key = read_openai_credential_frame(selected_stream)


def openai_api_key() -> str | None:
    """Return the in-memory credential for immediate gateway construction."""

    return _openai_api_key


def openai_api_key_is_configured() -> bool:
    return _openai_api_key is not None


def _clear_for_tests() -> None:
    global _openai_api_key
    _openai_api_key = None


def _frame_for_tests(value: bytes) -> io.BytesIO:
    return io.BytesIO(len(value).to_bytes(4, byteorder="big") + value)
