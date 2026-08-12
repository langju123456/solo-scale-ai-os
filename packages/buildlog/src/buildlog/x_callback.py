"""Local OAuth callback receiver for X authorization."""

from __future__ import annotations

import html
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from typing import Callable
from urllib.parse import urlparse

from buildlog.linkedin_callback import OAuthCallback, parse_callback_url
from buildlog.x_errors import (
    XCallbackTimeoutError,
    XConfigurationError,
    XOAuthError,
)


def wait_for_x_callback(
    redirect_uri: str,
    *,
    timeout_seconds: float,
    on_listening: Callable[[], None] | None = None,
) -> OAuthCallback:
    """Wait for the configured X callback while ignoring unrelated paths."""
    redirect = urlparse(redirect_uri)
    host = redirect.hostname or "localhost"
    port = redirect.port
    if port is None:
        raise XConfigurationError("X_REDIRECT_URI must include a port.")
    completed = Event()
    result: list[OAuthCallback] = []
    invalid_callback = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != redirect.path:
                self.send_error(404)
                return
            try:
                callback = parse_callback_url(
                    self.path,
                    expected_path=redirect.path,
                )
            except ValueError:
                invalid_callback.set()
                completed.set()
                self._respond(400, "X authorization could not be completed.")
                return
            result.append(callback)
            completed.set()
            self._respond(
                200,
                (
                    "X authorization returned without approval."
                    if callback.error
                    else "X authorization response received. Return to BuildLog."
                ),
            )

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _respond(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>BuildLog X authorization</title></head>"
                f"<body><p>{html.escape(message)}</p></body></html>"
            ).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                return

    try:
        server = HTTPServer((host, port), CallbackHandler)
    except OSError as exc:
        raise XConfigurationError(
            f"The X callback could not listen on {host}:{port}."
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
    if result:
        return result[0]
    if invalid_callback.is_set():
        raise XOAuthError("X returned an invalid OAuth callback. Run login again.")
    raise XCallbackTimeoutError(
        "X authorization did not return before the local callback timed out."
    )
