"""Small authentication boundary for the private hosted application."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


class APIKeyAuthorizer:
    """Validate a configured bearer or private header value in constant time."""

    def __init__(
        self,
        expected_key: str | None,
        *,
        trust_azure_auth: bool = False,
    ) -> None:
        self._expected_key = expected_key
        self._trust_azure_auth = trust_azure_auth

    async def __call__(self, request: Request) -> None:
        if self._trust_azure_auth and request.headers.get(
            "x-ms-client-principal-id", ""
        ).strip():
            return
        if self._expected_key is None:
            return
        authorization = request.headers.get("authorization", "")
        candidate = ""
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
        if not candidate:
            candidate = request.headers.get("x-buildlog-key", "").strip()
        if not candidate or not hmac.compare_digest(candidate, self._expected_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="valid BuildLog API credentials are required",
                headers={"WWW-Authenticate": "Bearer"},
            )
