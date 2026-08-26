from __future__ import annotations

import io

import pytest

from soloscale.desktop_credentials import (
    DesktopCredentialError,
    _clear_for_tests,
    _frame_for_tests,
    configure_openai_credential_from_stdin,
    openai_api_key,
    openai_api_key_is_configured,
    read_openai_credential_frame,
)


def test_desktop_credential_frame_supports_empty_and_process_memory_values() -> None:
    _clear_for_tests()
    assert read_openai_credential_frame(_frame_for_tests(b"")) is None

    configure_openai_credential_from_stdin(_frame_for_tests(b"synthetic-test-key"))
    assert openai_api_key_is_configured() is True
    assert openai_api_key() == "synthetic-test-key"
    _clear_for_tests()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00\x00\x00",
        b"\x00\x00\x00\x04abc",
        (513).to_bytes(4, byteorder="big"),
        b"\x00\x00\x00\x02\xff\xfe",
        b"\x00\x00\x00\x05 key ",
    ],
)
def test_desktop_credential_frame_fails_closed_without_secret_echo(raw: bytes) -> None:
    sentinel = "synthetic-test-key"
    with pytest.raises(DesktopCredentialError) as failure:
        read_openai_credential_frame(io.BytesIO(raw + sentinel.encode()))
    assert sentinel not in str(failure.value)
