"""Mocked identity and LinkedIn text-publisher integration tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from pydantic import SecretStr

from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    ExpiredTokenError,
    IdentityResolutionError,
    IndeterminatePublicationError,
    LinkedInBadRequestError,
    LinkedInForbiddenError,
    LinkedInRateLimitedError,
    LinkedInServerError,
    LinkedInUnauthorizedError,
    MissingPermissionError,
)
from buildlog.linkedin_http import LinkedInHttpClient, LinkedInHttpResult
from buildlog.linkedin_identity import LinkedInIdentityService
from buildlog.linkedin_publisher import (
    LinkedInTextPublisher,
    build_text_post_payload,
    escape_little_text_plaintext,
)
from buildlog.linkedin_token_store import LinkedInToken
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationPreview,
    PublicationStatus,
    PublishReceipt,
    PublishRequest,
    PublishResult,
)


class MemoryTokenStore:
    """In-memory token store for deterministic integration tests."""

    def __init__(self, token: LinkedInToken | None) -> None:
        self.token = token

    @property
    def path(self):
        raise AssertionError("path is not needed in this test")

    def load(self) -> LinkedInToken | None:
        return self.token

    def save(self, token: LinkedInToken) -> None:
        self.token = token

    def delete(self) -> bool:
        existed = self.token is not None
        self.token = None
        return existed


def test_identity_success_uses_userinfo_without_decoding_id_token() -> None:
    settings = _settings()
    token = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/userinfo"
        assert request.headers["authorization"] == "Bearer access-secret"
        return httpx.Response(200, json={"sub": "member-123", "name": "Ju L"})

    http = _http(handler)
    try:
        identity = LinkedInIdentityService(
            settings,
            http,
            MemoryTokenStore(token),
        ).resolve(now=_now())
    finally:
        http.close()

    assert identity.display_name == "Ju L"
    assert identity.person_urn == "urn:li:person:member-123"
    assert identity.account_reference != "member-123"
    assert identity.author_mapping_source == "oidc_userinfo_sub_inferred"
    assert "member-123" not in identity.redacted_subject
    assert "member-123" not in repr(identity)
    assert "Ju L" not in repr(identity)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, LinkedInUnauthorizedError),
        (403, LinkedInForbiddenError),
        (500, IdentityResolutionError),
    ],
)
def test_identity_http_failures(status: int, error_type: type[Exception]) -> None:
    http = _http(lambda _request: httpx.Response(status, json={"message": "error"}))
    try:
        with pytest.raises(error_type):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).resolve(now=_now())
    finally:
        http.close()


def test_identity_missing_claims_is_rejected() -> None:
    http = _http(lambda _request: httpx.Response(200, json={"name": "Ju L"}))
    try:
        with pytest.raises(IdentityResolutionError):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).resolve(now=_now())
    finally:
        http.close()


@pytest.mark.parametrize(
    "subject",
    ["member\n123", "member\x1b[2J", "member\u202e123", "member\ud800"],
)
def test_identity_rejects_unsafe_subject(subject: str) -> None:
    http = _http(
        lambda _request: _escaped_json_response(
            {"sub": subject, "name": "Ju L"}
        )
    )
    try:
        with pytest.raises(IdentityResolutionError, match="subject"):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).resolve(now=_now())
    finally:
        http.close()


def test_identity_normalizes_display_name_for_terminal_output() -> None:
    http = _http(
        lambda _request: httpx.Response(
            200,
            json={"sub": "member-123", "name": "Ju\n  L"},
        )
    )
    try:
        identity = LinkedInIdentityService(
            _settings(),
            http,
            MemoryTokenStore(_token()),
        ).resolve(now=_now())
    finally:
        http.close()

    assert identity.display_name == "Ju L"


@pytest.mark.parametrize(
    "display_name",
    [
        "Ju\x0c L",
        "Ju\x85 L",
        "Ju\x1b[2J L",
        "Ju\u202e L",
        "Ju\ud800 L",
    ],
)
def test_identity_rejects_unsafe_display_name(display_name: str) -> None:
    http = _http(
        lambda _request: _escaped_json_response(
            {"sub": "member-123", "name": display_name}
        )
    )
    try:
        with pytest.raises(IdentityResolutionError, match="display name"):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).resolve(now=_now())
    finally:
        http.close()


def test_identity_rejects_expired_token_without_http() -> None:
    token = _token(expires_at=_now() - timedelta(seconds=1))
    http = _http(
        lambda _request: pytest.fail("expired token must not reach userinfo")
    )
    try:
        with pytest.raises(ExpiredTokenError):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(token),
            ).resolve(now=_now())
    finally:
        http.close()


def test_known_missing_identity_scope_is_rejected() -> None:
    token = _token(scopes={"w_member_social"})
    http = _http(
        lambda _request: pytest.fail("missing scope must not reach userinfo")
    )
    try:
        with pytest.raises(MissingPermissionError):
            LinkedInIdentityService(
                _settings(),
                http,
                MemoryTokenStore(token),
            ).resolve(now=_now())
    finally:
        http.close()


def test_text_post_payload_and_required_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            201,
            headers={"x-restli-id": "urn:li:share:123"},
        )

    settings = _settings()
    http = _http(handler)
    try:
        result = LinkedInTextPublisher(
            settings,
            http,
            MemoryTokenStore(_token()),
        ).publish(_request())
    finally:
        http.close()

    headers = captured["headers"]
    payload = captured["payload"]
    assert headers["linkedin-version"] == "202607"
    assert headers["x-restli-protocol-version"] == "2.0.0"
    assert headers["authorization"] == "Bearer access-secret"
    assert payload == build_text_post_payload(
        author_urn="urn:li:person:member-123",
        content="A grounded engineering post.",
    )
    assert result.external_post_id == "urn:li:share:123"
    assert result.http_status == 201


@pytest.mark.parametrize(
    "external_post_id",
    [
        "urn:li:share:not-numeric",
        "urn:li:share:123:456",
        "urn:li:ugcPost:abc",
    ],
)
def test_publisher_rejects_malformed_success_post_id(
    external_post_id: str,
) -> None:
    http = _http(
        lambda _request: httpx.Response(
            201,
            headers={"x-restli-id": external_post_id},
        )
    )
    try:
        with pytest.raises(
            IndeterminatePublicationError,
            match="valid x-restli-id",
        ):
            LinkedInTextPublisher(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request())
    finally:
        http.close()


def test_text_post_payload_escapes_little_text_as_plaintext() -> None:
    content = r"Build *this* #carefully with [evidence](url) \ traces."

    payload = build_text_post_payload(
        author_urn="urn:li:person:member-123",
        content=content,
    )

    assert payload["commentary"] == (
        r"Build \*this\* \#carefully with \[evidence\]\(url\) \\ traces."
    )
    assert escape_little_text_plaintext(payload["commentary"]) != content


def test_little_text_plaintext_escapes_every_reserved_character() -> None:
    reserved = r"|{}@[]()<>#\*_~"

    escaped = escape_little_text_plaintext(reserved)

    assert escaped == "".join(f"\\{character}" for character in reserved)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (200, IndeterminatePublicationError),
        (202, IndeterminatePublicationError),
        (400, LinkedInBadRequestError),
        (401, LinkedInUnauthorizedError),
        (403, LinkedInForbiddenError),
        (408, IndeterminatePublicationError),
        (429, LinkedInRateLimitedError),
        (500, LinkedInServerError),
    ],
)
def test_publisher_maps_linkedin_errors(
    status: int,
    error_type: type[Exception],
) -> None:
    http = _http(lambda _request: httpx.Response(status, json={"message": "safe"}))
    try:
        with pytest.raises(error_type):
            LinkedInTextPublisher(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request())
    finally:
        http.close()


def test_publisher_timeout_is_indeterminate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    http = _http(handler)
    try:
        with pytest.raises(IndeterminatePublicationError):
            LinkedInTextPublisher(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request())
    finally:
        http.close()


def test_publisher_connection_error_is_indeterminate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    http = _http(handler)
    try:
        with pytest.raises(IndeterminatePublicationError):
            LinkedInTextPublisher(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request())
    finally:
        http.close()


@pytest.mark.parametrize(
    "external_id",
    [
        None,
        "not-a-post-urn",
        "urn:li:share:123\x1b[2J",
    ],
)
def test_success_without_valid_post_identifier_is_indeterminate(
    external_id: str | None,
) -> None:
    headers = {"x-restli-id": external_id} if external_id is not None else {}
    http = _http(
        lambda _request: httpx.Response(201, headers=headers, content=b"")
    )
    try:
        with pytest.raises(IndeterminatePublicationError):
            LinkedInTextPublisher(
                _settings(),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request())
    finally:
        http.close()


def test_success_with_directional_post_identifier_is_indeterminate() -> None:
    class UnsafeHeaderHttp:
        def create_post(self, *_args, **_kwargs):
            return LinkedInHttpResult(
                status_code=201,
                headers={"x-restli-id": "urn:li:share:123\u202e456"},
                json_body=None,
                text="",
            )

    with pytest.raises(IndeterminatePublicationError):
        LinkedInTextPublisher(
            _settings(),
            UnsafeHeaderHttp(),
            MemoryTokenStore(_token()),
        ).publish(_request())


def test_publishing_models_reject_control_characters_in_urns() -> None:
    request_payload = _request().model_dump()
    request_payload["author_urn"] = "urn:li:person:member\x1b[2J"
    with pytest.raises(ValueError, match="author URN"):
        PublishRequest.model_validate(request_payload)

    with pytest.raises(ValueError, match="external post ID"):
        PublishResult(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            run_id="run-001",
            status=PublicationStatus.SUCCEEDED,
            content_hash="a" * 64,
            external_post_id="urn:li:share:123\x1b[2J",
            occurred_at=_now(),
            http_status=201,
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
        )

    with pytest.raises(ValueError, match="external post ID"):
        PublishResult(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            run_id="run-001",
            status=PublicationStatus.SUCCEEDED,
            content_hash="a" * 64,
            external_post_id="urn:li:share:123\u202e456",
            occurred_at=_now(),
            http_status=201,
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
        )


def test_publishing_models_reject_unsafe_human_visible_text() -> None:
    request_payload = _request().model_dump()
    request_payload["content"] = "Visible\x0chidden"
    with pytest.raises(ValueError, match="publication content"):
        PublishRequest.model_validate(request_payload)

    with pytest.raises(ValueError, match="account display name"):
        PublicationPreview(
            platform=PublicationPlatform.LINKEDIN,
            run_id="run-001",
            artifact_id="run-001:final",
            artifact_path="/tmp/run-001/06_final.md",
            account_reference="account-ref",
            account_display_name="Ju\u202e L",
            content_length=13,
            content_hash="a" * 64,
            content="Grounded post",
            duplicate_found=False,
        )

    with pytest.raises(ValueError, match="publication content"):
        PublicationPreview(
            platform=PublicationPlatform.LINKEDIN,
            run_id="run-001",
            artifact_id="run-001:final",
            artifact_path="/tmp/run-001/06_final.md",
            account_reference="account-ref",
            account_display_name="Ju L",
            content_length=13,
            content_hash="a" * 64,
            content="Grounded\u202epost",
            duplicate_found=False,
        )


def test_publish_request_repr_hides_content_author_and_metadata() -> None:
    request = _request().model_copy(
        update={"metadata": {"private_note": "do-not-log"}},
    )

    rendered = repr(request)
    assert "A grounded engineering post." not in rendered
    assert "urn:li:person:member-123" not in rendered
    assert "do-not-log" not in rendered


@pytest.mark.parametrize(
    "extra",
    [
        {
            "duplicate_external_post_id": "urn:li:share:123",
            "duplicate_published_at": datetime(2026, 7, 29, tzinfo=UTC),
        },
        {
            "indeterminate_receipt_id": "receipt-001",
            "indeterminate_created_at": datetime(2026, 7, 29, tzinfo=UTC),
        },
    ],
)
def test_publication_preview_rejects_hidden_prior_details(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="must not include"):
        PublicationPreview(
            platform=PublicationPlatform.LINKEDIN,
            run_id="run-001",
            artifact_id="run-001:final",
            artifact_path="/tmp/run-001/06_final.md",
            account_reference="account-ref",
            account_display_name="Ju L",
            content_length=13,
            content_hash="a" * 64,
            content="Grounded post",
            duplicate_found=False,
            indeterminate_found=False,
            **extra,
        )


def test_publication_preview_rejects_incorrect_content_length() -> None:
    with pytest.raises(ValueError, match="content length"):
        PublicationPreview(
            platform=PublicationPlatform.LINKEDIN,
            run_id="run-001",
            artifact_id="run-001:final",
            artifact_path="/tmp/run-001/06_final.md",
            account_reference="account-ref",
            account_display_name="Ju L",
            content_length=999,
            content_hash="a" * 64,
            content="Grounded post",
            duplicate_found=False,
        )


def test_publication_result_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        PublishResult(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            run_id="run-001",
            status=PublicationStatus.SUCCEEDED,
            content_hash="a" * 64,
            external_post_id="urn:li:share:123",
            occurred_at=datetime(2026, 7, 29),
            http_status=201,
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
        )


def test_publication_result_normalizes_timestamp_to_utc() -> None:
    result = PublishResult(
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        run_id="run-001",
        status=PublicationStatus.SUCCEEDED,
        content_hash="a" * 64,
        external_post_id="urn:li:share:123",
        occurred_at=datetime(
            2026,
            7,
            29,
            20,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        http_status=201,
        api_endpoint="https://api.linkedin.com/rest/posts",
        api_version="202607",
    )

    assert result.occurred_at == datetime(2026, 7, 29, 12, tzinfo=UTC)
    assert result.occurred_at.tzinfo is UTC


def test_successful_publication_result_rejects_error_details() -> None:
    with pytest.raises(ValueError, match="must not contain error details"):
        PublishResult(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            run_id="run-001",
            status=PublicationStatus.SUCCEEDED,
            content_hash="a" * 64,
            external_post_id="urn:li:share:123",
            occurred_at=_now(),
            http_status=201,
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
            error_category="conflicting_success",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": PublicationStatus.INDETERMINATE,
            "published_at": datetime(2026, 7, 29, tzinfo=UTC),
        },
        {
            "status": PublicationStatus.FAILED,
            "external_post_id": "urn:li:share:123",
        },
    ],
)
def test_publication_receipt_rejects_inconsistent_non_success_fields(
    overrides: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "receipt_id": "receipt-001",
        "attempt_id": "attempt-001",
        "run_id": "run-001",
        "artifact_id": "run-001:final",
        "platform": PublicationPlatform.LINKEDIN,
        "account_reference": "account-ref",
        "content_hash": "a" * 64,
        "status": PublicationStatus.INDETERMINATE,
        "created_at": _now(),
        "api_endpoint": "https://api.linkedin.com/rest/posts",
        "api_version": "202607",
    }
    payload.update(overrides)

    with pytest.raises(ValueError, match="receipt"):
        PublishReceipt.model_validate(payload)


def _settings() -> LinkedInSettings:
    return LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
        api_version="202607",
    )


def _token(
    *,
    expires_at: datetime | None = None,
    scopes: set[str] | None = None,
) -> LinkedInToken:
    return LinkedInToken(
        access_token=SecretStr("access-secret"),
        id_token=SecretStr("untrusted-id-token"),
        expires_at=expires_at or datetime(2099, 1, 1, tzinfo=UTC),
        scopes=scopes or {"openid", "profile", "w_member_social"},
        scope_source="response",
        obtained_at=_now(),
    )


def _request() -> PublishRequest:
    return PublishRequest(
        attempt_id="attempt-001",
        run_id="run-001",
        artifact_id="run-001:final",
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        author_urn="urn:li:person:member-123",
        content="A grounded engineering post.",
        content_hash="a" * 64,
        approved=True,
        api_version="202607",
    )


def _http(handler) -> LinkedInHttpClient:
    return LinkedInHttpClient(
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _escaped_json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )


def _now() -> datetime:
    return datetime(2026, 7, 29, tzinfo=UTC)
