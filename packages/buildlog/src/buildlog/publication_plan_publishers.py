"""Pure payload builders for image-bearing publication-plan adapters."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    IndeterminatePublicationError,
    LinkedInNetworkError,
    LinkedInRequestTimeoutError,
    PublicationApprovalRequiredError,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_identity import require_scopes, require_valid_token
from buildlog.linkedin_publisher import _raise_for_status, escape_little_text_plaintext
from buildlog.linkedin_token_store import TokenStore
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationStatus,
    PublishRequest,
    PublishResult,
)
from buildlog.terminal_safety import is_unsafe_terminal_character
from buildlog.x_config import XSettings
from buildlog.x_errors import XAPIError, XNetworkError, XRequestTimeoutError
from buildlog.x_http import XHttpClient
from buildlog.x_identity import require_valid_x_token, require_x_scopes
from buildlog.x_publisher import _post_id, validate_x_content
from buildlog.x_token_store import FileXTokenStore

_LINKEDIN_POST_ID = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]+$")


def build_linkedin_image_post_payload(
    *,
    author_urn: str,
    content: str,
    media_id: str,
    alt_text: str,
) -> dict[str, Any]:
    """Build the official LinkedIn Posts image payload without making a request."""
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
        "content": {"media": {"id": media_id, "altText": alt_text}},
    }


def build_x_image_thread_payload(
    *,
    content: str,
    media_id: str | None,
    reply_to_post_id: str | None,
) -> dict[str, object]:
    """Attach media only to the root and reply only to the immediate predecessor."""
    validate_x_content(content)
    payload: dict[str, object] = {"text": content}
    if media_id is not None:
        payload["media"] = {"media_ids": [media_id]}
    if reply_to_post_id is not None:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_post_id}
    return payload


def build_x_upload_payload(image_bytes: bytes) -> dict[str, str]:
    """Return the X v2 JSON upload shape; encoding occurs in the adapter."""
    import base64

    return {
        "media": base64.b64encode(image_bytes).decode("ascii"),
        "media_category": "tweet_image",
        "media_type": "image/png",
    }


def build_x_metadata_payload(media_id: str, alt_text: str) -> dict[str, object]:
    """Return X's documented alt-text metadata request shape."""
    if not media_id.isdigit() or len(media_id) > 19:
        raise ValueError("X media ID must be one to nineteen digits")
    if not alt_text or len(alt_text) > 1000:
        raise ValueError("X image alt text must contain at most 1000 characters")
    return {"id": media_id, "metadata": {"alt_text": {"text": alt_text}}}


class LinkedInImagePublisher:
    """Publish one already-uploaded image and its exact approved commentary."""

    def __init__(
        self,
        settings: LinkedInSettings,
        http: LinkedInHttpClient,
        token_store: TokenStore,
        *,
        media_id: str,
        alt_text: str,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store
        self.media_id = media_id
        self.alt_text = alt_text

    def publish(self, request: PublishRequest) -> PublishResult:
        if not request.approved:
            raise PublicationApprovalRequiredError(
                "Publication requires explicit human approval."
            )
        if request.platform is not PublicationPlatform.LINKEDIN:
            raise ValueError("LinkedIn image publisher received a non-LinkedIn request")
        token = require_valid_token(self.token_store)
        require_scopes(token, {"w_member_social"})
        try:
            response = self.http.create_post(
                self.settings.posts_url,
                access_token=token.access_token.get_secret_value(),
                api_version=self.settings.api_version,
                payload=build_linkedin_image_post_payload(
                    author_urn=request.author_urn,
                    content=request.content,
                    media_id=self.media_id,
                    alt_text=self.alt_text,
                ),
            )
        except (LinkedInRequestTimeoutError, LinkedInNetworkError) as exc:
            raise IndeterminatePublicationError(
                "LinkedIn image-post outcome is unknown; do not retry automatically."
            ) from exc
        if response.status_code == 201:
            external_id = response.header("x-restli-id")
            if (
                not external_id
                or _LINKEDIN_POST_ID.fullmatch(external_id) is None
                or any(is_unsafe_terminal_character(char) for char in external_id)
            ):
                raise IndeterminatePublicationError(
                    "LinkedIn returned no valid image-post ID; do not retry automatically.",
                    status_code=201,
                )
            return PublishResult(
                platform=PublicationPlatform.LINKEDIN,
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


class XImageThreadPublisher:
    """Publish one X root or reply with no retry and explicit parent binding."""

    def __init__(
        self,
        settings: XSettings,
        http: XHttpClient,
        token_store: FileXTokenStore,
        *,
        media_id: str | None,
        reply_to_post_id: str | None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.token_store = token_store
        self.media_id = media_id
        self.reply_to_post_id = reply_to_post_id

    def publish(self, request: PublishRequest) -> PublishResult:
        if not request.approved:
            raise PublicationApprovalRequiredError(
                "Publication requires explicit human approval."
            )
        if request.platform is not PublicationPlatform.X:
            raise ValueError("X thread publisher received a non-X request")
        validate_x_content(request.content)
        if self.media_id is not None and not self.media_id.isdigit():
            raise ValueError("X media ID must be numeric")
        if self.reply_to_post_id is not None and not self.reply_to_post_id.isdigit():
            raise ValueError("X reply parent must be a numeric post ID")
        token = require_valid_x_token(self.token_store, self.settings)
        require_x_scopes(token, {"tweet.write"})
        try:
            response = self.http.create_post(
                self.settings.posts_url,
                access_token=token.access_token.get_secret_value(),
                payload=build_x_image_thread_payload(
                    content=request.content,
                    media_id=self.media_id,
                    reply_to_post_id=self.reply_to_post_id,
                ),
            )
        except (XRequestTimeoutError, XNetworkError) as exc:
            raise IndeterminatePublicationError(
                "X thread-post outcome is unknown; do not retry automatically."
            ) from exc
        if response.status_code == 201:
            external_id = _post_id(response.json_body)
            if external_id is None:
                raise IndeterminatePublicationError(
                    "X returned no valid thread-post ID; do not retry automatically.",
                    status_code=201,
                )
            return PublishResult(
                platform=PublicationPlatform.X,
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
        if response.status_code >= 500 or response.status_code == 408:
            raise IndeterminatePublicationError(
                "X thread-post outcome is unknown; do not retry automatically.",
                status_code=response.status_code,
            )
        raise XAPIError(
            f"X rejected the thread post (HTTP {response.status_code}).",
            status_code=response.status_code,
        )
