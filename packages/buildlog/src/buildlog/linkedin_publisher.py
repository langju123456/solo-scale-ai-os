"""Text-only personal-member adapter for the LinkedIn Posts API."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    IndeterminatePublicationError,
    LinkedInAPIError,
    LinkedInBadRequestError,
    LinkedInForbiddenError,
    LinkedInNetworkError,
    LinkedInRateLimitedError,
    LinkedInRequestTimeoutError,
    LinkedInServerError,
    LinkedInUnauthorizedError,
    PublicationApprovalRequiredError,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_identity import require_scopes, require_valid_token
from buildlog.linkedin_token_store import TokenStore
from buildlog.publishing_models import (
    PublishRequest,
    PublishResult,
    PublicationStatus,
)
from buildlog.terminal_safety import is_unsafe_terminal_character

_LITTLE_TEXT_RESERVED_CHARACTERS = frozenset(r"|{}@[]()<>#\*_~")
_POST_ID_PATTERN = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]+$")


class LinkedInTextPublisher:
    """Publish one approved public text post through `/rest/posts`."""

    def __init__(
        self,
        settings: LinkedInSettings,
        http: LinkedInHttpClient,
        token_store: TokenStore,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store

    def publish(self, request: PublishRequest) -> PublishResult:
        """Submit one approved request and return a domain result."""
        if not request.approved:
            raise PublicationApprovalRequiredError(
                "Publication requires explicit human approval."
            )
        token = require_valid_token(self.token_store)
        require_scopes(token, {"w_member_social"})
        payload = build_text_post_payload(
            author_urn=request.author_urn,
            content=request.content,
        )
        try:
            response = self.http.create_post(
                self.settings.posts_url,
                access_token=token.access_token.get_secret_value(),
                api_version=self.settings.api_version,
                payload=payload,
            )
        except (LinkedInRequestTimeoutError, LinkedInNetworkError) as exc:
            raise IndeterminatePublicationError(
                "The LinkedIn submission ended without a response. The post may "
                "exist; do not retry automatically. Check LinkedIn and the local "
                "receipt before another attempt."
            ) from exc

        if response.status_code == 201:
            external_id = response.header("x-restli-id")
            if (
                not external_id
                or _POST_ID_PATTERN.fullmatch(external_id) is None
                or any(
                    is_unsafe_terminal_character(character)
                    for character in external_id
                )
            ):
                raise IndeterminatePublicationError(
                    "LinkedIn returned HTTP 201 without a valid x-restli-id. The "
                    "post may exist; inspect LinkedIn before retrying.",
                    status_code=201,
                )
            return PublishResult(
                platform=request.platform,
                account_reference=request.account_reference,
                run_id=request.run_id,
                status=PublicationStatus.SUCCEEDED,
                content_hash=request.content_hash,
                external_post_id=external_id,
                occurred_at=datetime.now(UTC),
                http_status=201,
                api_endpoint=self.settings.posts_url,
                api_version=self.settings.api_version,
            )
        _raise_for_status(response.status_code)
        raise AssertionError("unreachable LinkedIn response mapping")


def build_text_post_payload(
    *,
    author_urn: str,
    content: str,
) -> dict[str, Any]:
    """Build the current Posts API payload for a public text-only post."""
    return {
        "author": author_urn,
        "commentary": escape_little_text_plaintext(content),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def escape_little_text_plaintext(value: str) -> str:
    """Escape LinkedIn little-text control characters as visible plaintext."""
    return "".join(
        f"\\{character}"
        if character in _LITTLE_TEXT_RESERVED_CHARACTERS
        else character
        for character in value
    )


def _raise_for_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        raise IndeterminatePublicationError(
            f"LinkedIn returned unexpected HTTP {status_code} after submission. "
            "The post may exist; inspect LinkedIn before retrying.",
            status_code=status_code,
        )
    if status_code == 408:
        raise IndeterminatePublicationError(
            "LinkedIn returned HTTP 408 after submission. The post may exist; "
            "inspect LinkedIn before retrying.",
            status_code=status_code,
        )
    if status_code == 400 or status_code == 422:
        raise LinkedInBadRequestError(
            "LinkedIn rejected the post payload. Review the preview and configured "
            "API version.",
            status_code=status_code,
        )
    if status_code == 401:
        raise LinkedInUnauthorizedError(
            "LinkedIn rejected the token. Run the login command again.",
            status_code=status_code,
        )
    if status_code == 403:
        raise LinkedInForbiddenError(
            "LinkedIn denied publication. Confirm Share on LinkedIn is enabled, "
            "w_member_social was granted, and the author identity is correct.",
            status_code=status_code,
        )
    if status_code == 429:
        raise LinkedInRateLimitedError(
            "LinkedIn rate limited the request. Wait and review Developer Portal "
            "Analytics before another manually approved attempt.",
            status_code=status_code,
        )
    if status_code >= 500:
        raise LinkedInServerError(
            "LinkedIn returned a server error. Review the receipt before deciding "
            "whether to try again.",
            status_code=status_code,
        )
    raise LinkedInAPIError(
        f"LinkedIn returned unexpected HTTP {status_code}.",
        status_code=status_code,
    )
