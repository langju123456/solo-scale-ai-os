"""Minimal localhost OAuth callback receiver for interactive login."""

from __future__ import annotations

import html
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from typing import Callable
from urllib.parse import parse_qs, urlparse

from buildlog.linkedin_errors import (
    LinkedInConfigurationError,
    OAuthAuthorizationDeniedError,
    OAuthCallbackTimeoutError,
)


@dataclass(frozen=True)
class OAuthCallback:
    """Parsed OAuth callback values."""

    state: str = field(repr=False)
    code: str | None = field(default=None, repr=False)
    error: str | None = None
    error_description: str | None = field(default=None, repr=False)


def parse_callback_url(url: str, *, expected_path: str) -> OAuthCallback:
    """Parse and validate one OAuth callback URL."""
    parsed = urlparse(url)
    if parsed.path != expected_path:
        raise ValueError("callback path does not match the configured redirect URI")
    query = parse_qs(parsed.query)
    state = _single_query_value(query, "state")
    code = _optional_query_value(query, "code")
    error = _optional_query_value(query, "error")
    description = _optional_query_value(query, "error_description")
    if error is None and code is None:
        raise ValueError("callback contained neither authorization code nor error")
    if error is not None and code is not None:
        raise ValueError("callback contained both authorization code and error")
    return OAuthCallback(
        state=state,
        code=code,
        error=error,
        error_description=description,
    )


def wait_for_local_callback(
    redirect_uri: str,
    *,
    timeout_seconds: float,
    on_listening: Callable[[], None] | None = None,
) -> OAuthCallback:
    """Wait for one matching localhost OAuth callback and shut down cleanly."""
    redirect = urlparse(redirect_uri)
    host = redirect.hostname or "localhost"
    port = redirect.port
    if port is None:
        raise ValueError("redirect URI must include an explicit port")
    completed = Event()
    result: list[OAuthCallback] = []
    callback_errors: list[ValueError] = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            """Handle one matching OAuth redirect request."""
            parsed = urlparse(self.path)
            if parsed.path != redirect.path:
                self.send_error(404)
                return
            try:
                callback = parse_callback_url(
                    self.path,
                    expected_path=redirect.path,
                )
            except ValueError as exc:
                callback_errors.append(exc)
                completed.set()
                self._respond(
                    400,
                    "LinkedIn authorization could not be completed. "
                    "Return to the terminal for details.",
                )
                return
            result.append(callback)
            completed.set()
            self._respond(
                200,
                (
                    "LinkedIn authorization returned without approval. "
                    "You may return to the terminal."
                    if callback.error
                    else "LinkedIn authorization response received. "
                    "Return to the terminal to confirm completion."
                ),
            )

        def log_message(self, _format: str, *args: object) -> None:
            """Suppress callback query logging because it contains OAuth data."""
            return

        def _respond(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>BuildLog LinkedIn authorization</title></head>"
                f"<body><p>{html.escape(message)}</p></body></html>"
            ).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                return

    try:
        server = HTTPServer((host, port), CallbackHandler)
    except OSError as exc:
        raise LinkedInConfigurationError(
            f"The local OAuth callback could not listen on {host}:{port}. "
            "Close the process using that port or configure another exact "
            "localhost redirect URI."
        ) from exc
    try:
        if on_listening is not None:
            on_listening()
        server.timeout = min(1.0, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not completed.is_set():
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        if callback_errors:
            raise OAuthAuthorizationDeniedError(
                "LinkedIn returned an invalid OAuth callback. Run login again."
            ) from callback_errors[0]
        raise OAuthCallbackTimeoutError(
            "LinkedIn authorization did not return before the local callback "
            "timed out. Run login again."
        )
    return result[0]


def _single_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"callback field {name!r} must appear exactly once")
    return values[0]


def _optional_query_value(
    query: dict[str, list[str]],
    name: str,
) -> str | None:
    values = query.get(name, [])
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"callback field {name!r} must not be repeated")
    return values[0]
