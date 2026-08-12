"""Centralized synchronous HTTP behavior for LinkedIn integration calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from buildlog.linkedin_errors import (
    LinkedInNetworkError,
    LinkedInRequestTimeoutError,
)

USER_AGENT = "BuildLog/0.2"
RESTLI_PROTOCOL_VERSION = "2.0.0"


@dataclass(frozen=True)
class LinkedInHttpResult:
    """HTTP result isolated from the rest of the BuildLog application."""

    status_code: int
    headers: Mapping[str, str] = field(repr=False)
    json_body: Any | None = field(repr=False)
    text: str = field(repr=False)

    def header(self, name: str) -> str | None:
        """Return a response header case-insensitively."""
        normalized_name = name.casefold()
        return next(
            (
                value
                for key, value in self.headers.items()
                if key.casefold() == normalized_name
            ),
            None,
        )


class LinkedInHttpClient:
    """Execute LinkedIn HTTP calls with centralized timeouts and headers."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._timeout = timeout_seconds

    def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def post_form(self, url: str, data: Mapping[str, str]) -> LinkedInHttpResult:
        """Submit an OAuth form request."""
        return self._request(
            "POST",
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )

    def get_userinfo(
        self,
        url: str,
        *,
        access_token: str,
    ) -> LinkedInHttpResult:
        """Retrieve OIDC userinfo with a bearer token."""
        return self._request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    def create_post(
        self,
        url: str,
        *,
        access_token: str,
        api_version: str,
        payload: Mapping[str, Any],
    ) -> LinkedInHttpResult:
        """Create a versioned LinkedIn REST post."""
        return self._request(
            "POST",
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Linkedin-Version": api_version,
                "X-Restli-Protocol-Version": RESTLI_PROTOCOL_VERSION,
                "User-Agent": USER_AGENT,
            },
        )

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> LinkedInHttpResult:
        try:
            response = self._client.request(
                method,
                url,
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise LinkedInRequestTimeoutError(
                "LinkedIn request timed out."
            ) from exc
        except httpx.RequestError as exc:
            raise LinkedInNetworkError(
                "LinkedIn could not be reached. Check the network and try again."
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        return LinkedInHttpResult(
            status_code=response.status_code,
            headers={key.lower(): value for key, value in response.headers.items()},
            json_body=body,
            text=response.text,
        )
