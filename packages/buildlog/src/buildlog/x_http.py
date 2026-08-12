"""Small no-retry HTTP boundary for X user and post requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from buildlog.x_errors import XNetworkError, XRequestTimeoutError

USER_AGENT = "BuildLog/0.2"


@dataclass(frozen=True)
class XHttpResult:
    """Response data required by X integration adapters."""

    status_code: int
    json_body: Any | None


class XHttpClient:
    """Call X with a fixed timeout and no automatic retries."""

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

    def get_me(self, url: str, *, access_token: str) -> XHttpResult:
        """Resolve the authenticated X user."""
        return self._request(
            "GET",
            url,
            headers=_headers(access_token),
        )

    def create_post(
        self,
        url: str,
        *,
        access_token: str,
        payload: Mapping[str, Any],
    ) -> XHttpResult:
        """Make exactly one create-post request."""
        return self._request(
            "POST",
            url,
            headers=_headers(access_token),
            json=payload,
        )

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> XHttpResult:
        try:
            response = self._client.request(
                method,
                url,
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise XRequestTimeoutError("X request timed out.") from exc
        except httpx.RequestError as exc:
            raise XNetworkError("X could not be reached.") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        return XHttpResult(status_code=response.status_code, json_body=body)


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
