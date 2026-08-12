"""Focused tests for the X text-publishing vertical slice."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.integrations.httpx_client import OAuth2Client
from pydantic import SecretStr

from buildlog.linkedin_callback import OAuthCallback
from buildlog.linkedin_errors import IndeterminatePublicationError
from buildlog.main import main
from buildlog.publication_content import (
    ResolvedPublicationArtifact,
    publication_content_hash,
)
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationStatus,
    PublishRequest,
    PublishResult,
)
from buildlog.publishing_service import PublishingService
from buildlog.x_config import XSettings, load_x_settings
from buildlog.x_errors import XPublicationValidationError
from buildlog.x_http import XHttpClient
from buildlog.x_identity import XIdentity, XIdentityService
from buildlog.x_oauth import XOAuthService, parse_x_token
from buildlog.x_publisher import (
    XTextPublisher,
    build_x_post_payload,
    validate_x_content,
    x_weighted_length,
)
from buildlog.x_token_store import (
    FileXAuthorizationStore,
    FileXTokenStore,
    XToken,
)


class MemoryTokenStore:
    """Small token store for adapter tests."""

    def __init__(self, token: XToken | None) -> None:
        self.token = token

    def load(self) -> XToken | None:
        return self.token

    def save(self, token: XToken) -> None:
        self.token = token


class MemoryPublishingRepository:
    """Capture receipts and expose no prior duplicates."""

    def __init__(self) -> None:
        self.receipts = []

    def save_publish_receipt(self, receipt) -> None:
        self.receipts.append(receipt)

    def get_publish_receipt(self, receipt_id: str):
        return next(
            (
                receipt
                for receipt in self.receipts
                if receipt.receipt_id == receipt_id
            ),
            None,
        )

    def find_successful_publication(self, **_kwargs):
        return None

    def find_indeterminate_publication(self, **_kwargs):
        return None


class FixedResolver:
    """Return one exact reviewed artifact."""

    def __init__(self, artifact: ResolvedPublicationArtifact) -> None:
        self.artifact = artifact

    def resolve(self, run_id: str) -> ResolvedPublicationArtifact:
        assert run_id == self.artifact.run_id
        return self.artifact


class FixedIdentityService:
    """Return one verified X identity."""

    def resolve(self) -> XIdentity:
        return XIdentity(
            user_id="123456",
            username="langju",
            display_name="Lang Ju",
            account_reference="account-ref",
        )


class FixedPublisher:
    """Return one successful X result and count calls."""

    def __init__(self, settings: XSettings) -> None:
        self.settings = settings
        self.calls = 0

    def publish(self, request: PublishRequest) -> PublishResult:
        self.calls += 1
        return PublishResult(
            platform=PublicationPlatform.X,
            account_reference=request.account_reference,
            run_id=request.run_id,
            status=PublicationStatus.SUCCEEDED,
            content_hash=request.content_hash,
            external_post_id="987654321",
            occurred_at=datetime.now(UTC),
            http_status=201,
            api_endpoint=self.settings.posts_url,
            api_version="2",
        )


def test_x_settings_use_only_official_endpoints() -> None:
    settings = load_x_settings(environ={"X_CLIENT_ID": "client-id"})

    assert settings.authorization_url == "https://x.com/i/oauth2/authorize"
    assert settings.token_url == "https://api.x.com/2/oauth2/token"
    assert settings.posts_url == "https://api.x.com/2/tweets"
    assert settings.redirect_uri == "http://127.0.0.1:8766/auth/x/callback"
    assert settings.scopes == ("tweet.read", "tweet.write", "users.read")


def test_x_oauth_start_uses_pkce_and_required_parameters(tmp_path: Path) -> None:
    settings = XSettings(client_id="client-id")
    service = XOAuthService(
        settings,
        FileXTokenStore(tmp_path / "token.json"),
        FileXAuthorizationStore(tmp_path / "state.json"),
    )

    start = service.start_authorization(now=datetime(2026, 7, 29, tzinfo=UTC))
    parsed = urlparse(start.authorization_url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://x.com/i/oauth2/authorize"
    )
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [settings.redirect_uri]
    assert query["scope"] == ["tweet.read tweet.write users.read"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["state"][0]
    assert "code_verifier" not in query


def test_x_token_parsing_and_private_round_trip(tmp_path: Path) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    token = parse_x_token(
        {
            "access_token": "access-value",
            "token_type": "bearer",
            "expires_in": 7200,
            "scope": "tweet.read tweet.write users.read",
        },
        now=now,
    )
    store = FileXTokenStore(tmp_path / "x.json")

    store.save(token)
    restored = store.load()

    assert restored is not None
    assert restored.access_token.get_secret_value() == "access-value"
    assert restored.expires_at == now + timedelta(seconds=7200)
    assert restored.scopes == {"tweet.read", "tweet.write", "users.read"}
    assert (store.path.stat().st_mode & 0o077) == 0
    assert "access-value" not in repr(restored)


def test_x_oauth_exchange_uses_public_client_and_saved_verifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = XSettings(client_id="client-id")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        form = parse_qs(request.content.decode("utf-8"))
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["fresh-code"]
        assert form["client_id"] == ["client-id"]
        assert form["redirect_uri"] == [settings.redirect_uri]
        assert len(form["code_verifier"][0]) >= 43
        return httpx.Response(
            200,
            json={
                "access_token": "access-value",
                "token_type": "bearer",
                "expires_in": 7200,
                "scope": "tweet.read tweet.write users.read",
            },
        )

    def client_factory() -> OAuth2Client:
        return OAuth2Client(
            client_id=settings.client_id,
            token_endpoint_auth_method="none",
            scope=" ".join(settings.scopes),
            redirect_uri=settings.redirect_uri,
            code_challenge_method="S256",
            transport=httpx.MockTransport(handler),
        )

    token_store = FileXTokenStore(tmp_path / "token.json")
    service = XOAuthService(
        settings,
        token_store,
        FileXAuthorizationStore(tmp_path / "state.json"),
    )
    monkeypatch.setattr(service, "_client", client_factory)
    start = service.start_authorization(now=datetime(2026, 7, 29, tzinfo=UTC))
    state = parse_qs(urlparse(start.authorization_url).query)["state"][0]

    token = service.complete_authorization(
        OAuthCallback(state=state, code="fresh-code"),
        now=datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
    )

    assert len(requests) == 1
    assert token.access_token.get_secret_value() == "access-value"
    assert token_store.load() is not None


def test_x_identity_uses_verified_users_me_response() -> None:
    token = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/2/users/me"
        assert request.headers["authorization"] == "Bearer access-value"
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "123456",
                    "name": "Lang Ju",
                    "username": "langju",
                }
            },
        )

    http = _http(handler)
    try:
        identity = XIdentityService(
            XSettings(client_id="client-id"),
            http,
            MemoryTokenStore(token),
        ).resolve(now=datetime.now(UTC))
    finally:
        http.close()

    assert identity.username == "langju"
    assert identity.author_reference == "123456"
    assert identity.mapping_source == "x_users_me_verified"
    assert "123456" not in repr(identity)


def test_x_weighted_length_is_conservative_for_cjk_and_urls() -> None:
    assert x_weighted_length("abc") == 3
    assert x_weighted_length("工程") == 4
    assert x_weighted_length("see https://example.com/long/path") == 27
    assert x_weighted_length("see https://example.com/path,") == 28


def test_x_content_rejects_over_limit_before_publish() -> None:
    with pytest.raises(XPublicationValidationError, match="weighted length"):
        validate_x_content("x" * 281)


def test_x_publisher_makes_exactly_one_post_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == "/2/tweets"
        assert json.loads(request.content) == {"text": "Evidence before claims."}
        return httpx.Response(
            201,
            json={"data": {"id": "987654321", "text": "Evidence before claims."}},
        )

    http = _http(handler)
    try:
        result = XTextPublisher(
            XSettings(client_id="client-id"),
            http,
            MemoryTokenStore(_token()),
        ).publish(_request("Evidence before claims."))
    finally:
        http.close()

    assert calls == 1
    assert result.status is PublicationStatus.SUCCEEDED
    assert result.external_post_id == "987654321"


def test_x_publisher_does_not_retry_timeout() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    http = _http(handler)
    try:
        with pytest.raises(IndeterminatePublicationError):
            XTextPublisher(
                XSettings(client_id="client-id"),
                http,
                MemoryTokenStore(_token()),
            ).publish(_request("One attempt only."))
    finally:
        http.close()

    assert calls == 1


def test_x_payload_contains_only_text() -> None:
    assert build_x_post_payload("One exact post.") == {"text": "One exact post."}


def test_shared_publishing_service_applies_x_approval_and_receipt(
    tmp_path: Path,
) -> None:
    content = "Reviewed engineering evidence."
    artifact_path = tmp_path / "06_final.md"
    artifact_path.write_text(content, encoding="utf-8")
    artifact = ResolvedPublicationArtifact(
        run_id="run-x",
        artifact_id="run-x:final",
        artifact_path=artifact_path,
        content=content,
        content_hash=publication_content_hash(content),
    )
    settings = XSettings(client_id="client-id")
    publisher = FixedPublisher(settings)
    repository = MemoryPublishingRepository()
    service = PublishingService(
        settings,
        FixedResolver(artifact),
        FixedIdentityService(),
        publisher,
        repository,
        platform=PublicationPlatform.X,
        platform_name="X",
        content_validator=validate_x_content,
    )

    preview = service.preview("run-x")
    receipt = service.publish(
        "run-x",
        approved=True,
        approved_content_hash=preview.content_hash,
        approved_account_reference=preview.account_reference,
    )

    assert preview.platform is PublicationPlatform.X
    assert preview.network_request_will_occur is False
    assert publisher.calls == 1
    assert receipt.platform is PublicationPlatform.X
    assert receipt.external_post_id == "987654321"
    assert repository.receipts == [receipt]


def test_main_dispatches_x_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "buildlog.main.x_main",
        lambda argv: 7 if argv == ["status"] else 8,
    )

    assert main(["x", "status"]) == 7


def _token() -> XToken:
    now = datetime.now(UTC)
    return XToken(
        access_token=SecretStr("access-value"),
        expires_at=now + timedelta(hours=1),
        obtained_at=now,
        scopes={"tweet.read", "tweet.write", "users.read"},
    )


def _http(handler) -> XHttpClient:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.x.com",
    )
    return XHttpClient(timeout_seconds=1, client=client)


def _request(content: str) -> PublishRequest:
    return PublishRequest(
        attempt_id="x-attempt",
        run_id="run-x",
        artifact_id="run-x:final",
        platform=PublicationPlatform.X,
        account_reference="account-ref",
        author_urn="123456",
        content=content,
        content_hash=publication_content_hash(content),
        approved=True,
        api_version="2",
    )
