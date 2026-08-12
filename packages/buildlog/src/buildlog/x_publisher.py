"""Text-only X adapter for `POST /2/tweets`."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from buildlog.linkedin_errors import (
    IndeterminatePublicationError,
    PublicationApprovalRequiredError,
)
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationStatus,
    PublishRequest,
    PublishResult,
)
from buildlog.x_config import XSettings
from buildlog.x_errors import (
    XAPIError,
    XNetworkError,
    XPublicationValidationError,
    XRequestTimeoutError,
)
from buildlog.x_http import XHttpClient
from buildlog.x_identity import require_valid_x_token, require_x_scopes
from buildlog.x_token_store import FileXTokenStore

MAX_X_WEIGHTED_LENGTH = 280
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class XTextPublisher:
    """Submit one approved text post with no retry behavior."""

    def __init__(
        self,
        settings: XSettings,
        http: XHttpClient,
        token_store: FileXTokenStore,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store

    def publish(self, request: PublishRequest) -> PublishResult:
        """Make one X create-post request and map its outcome."""
        if not request.approved:
            raise PublicationApprovalRequiredError(
                "Publication requires explicit human approval."
            )
        if request.platform is not PublicationPlatform.X:
            raise XPublicationValidationError(
                "The X adapter received a non-X publication request."
            )
        validate_x_content(request.content)
        token = require_valid_x_token(self.token_store)
        require_x_scopes(token, {"tweet.write"})
        try:
            response = self.http.create_post(
                self.settings.posts_url,
                access_token=token.access_token.get_secret_value(),
                payload=build_x_post_payload(request.content),
            )
        except (XRequestTimeoutError, XNetworkError) as exc:
            raise IndeterminatePublicationError(
                "The X submission ended without a response. The post may exist; "
                "do not retry automatically."
            ) from exc

        if response.status_code == 201:
            post_id = _post_id(response.json_body)
            if post_id is None:
                raise IndeterminatePublicationError(
                    "X returned HTTP 201 without a valid post ID. Inspect X "
                    "before retrying.",
                    status_code=201,
                )
            return PublishResult(
                platform=PublicationPlatform.X,
                account_reference=request.account_reference,
                run_id=request.run_id,
                status=PublicationStatus.SUCCEEDED,
                content_hash=request.content_hash,
                external_post_id=post_id,
                occurred_at=datetime.now(UTC),
                http_status=201,
                api_endpoint=self.settings.posts_url,
                api_version=self.settings.api_version,
            )
        if response.status_code >= 500 or response.status_code == 408:
            raise IndeterminatePublicationError(
                f"X returned HTTP {response.status_code}; the publication "
                "outcome is ambiguous and must not be retried automatically.",
                status_code=response.status_code,
            )
        raise XAPIError(
            f"X rejected the create-post request (HTTP {response.status_code}).",
            status_code=response.status_code,
        )


def build_x_post_payload(content: str) -> dict[str, Any]:
    """Build the smallest supported X create-post payload."""
    validate_x_content(content)
    return {"text": content}


def validate_x_content(content: str) -> None:
    """Reject blank or conservatively over-limit X text before submission."""
    if not content.strip():
        raise XPublicationValidationError("X post text must not be blank.")
    weighted = x_weighted_length(content)
    if weighted > MAX_X_WEIGHTED_LENGTH:
        raise XPublicationValidationError(
            f"X post text has weighted length {weighted}; the baseline limit "
            f"is {MAX_X_WEIGHTED_LENGTH}."
        )


def x_weighted_length(content: str) -> int:
    """Return a conservative X length: URLs 23, ASCII 1, other code points 2."""
    normalized = unicodedata.normalize("NFC", content)
    total = 0
    cursor = 0
    for match in _URL_PATTERN.finditer(normalized):
        total += _weighted_plain_text(normalized[cursor : match.start()])
        matched_url = match.group(0)
        trailing = matched_url[len(matched_url.rstrip(".,!?;:)]}")) :]
        total += 23
        total += _weighted_plain_text(trailing)
        cursor = match.end()
    return total + _weighted_plain_text(normalized[cursor:])


def _weighted_plain_text(value: str) -> int:
    return sum(1 if ord(character) < 128 else 2 for character in value)


def _post_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get("id")
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        return None
    return value
