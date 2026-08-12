"""Mocked tests for OAuth state, callback, exchange, and token storage."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from buildlog.linkedin_callback import (
    OAuthCallback,
    parse_callback_url,
    wait_for_local_callback,
)
from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    CredentialStoreError,
    LinkedInRequestTimeoutError,
    MalformedTokenResponseError,
    OAuthAuthorizationDeniedError,
    OAuthCallbackTimeoutError,
    OAuthStateMismatchError,
    TokenExchangeError,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_oauth import LinkedInOAuthService, parse_token_response
from buildlog.linkedin_token_store import (
    FileOAuthStateStore,
    FileTokenStore,
    LinkedInToken,
)


def test_authorization_url_uses_secure_state_and_exact_scopes(tmp_path: Path) -> None:
    service, state_path, http = _oauth_service(
        tmp_path,
        lambda _request: httpx.Response(500),
    )
    try:
        first = service.start_authorization(now=_now())
        second = service.start_authorization(now=_now())
    finally:
        http.close()

    first_query = parse_qs(urlparse(first.authorization_url).query)
    second_query = parse_qs(urlparse(second.authorization_url).query)
    assert first_query["scope"] == ["openid profile w_member_social"]
    assert first_query["redirect_uri"] == [
        "http://localhost:8765/auth/linkedin/callback"
    ]
    assert first_query["state"][0] != second_query["state"][0]
    assert len(first_query["state"][0]) >= 32
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert second_query["state"][0] not in stored["state_hash"]
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700


def test_oauth_success_exchanges_and_stores_token(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/v2/accessToken"
        assert b"client_secret=client-secret" in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "access-secret",
                "expires_in": 5184000,
                "scope": "openid profile w_member_social",
                "id_token": "id-secret",
                "refresh_token": "refresh-secret",
                "refresh_token_expires_in": 31_536_000,
            },
        )

    service, _state_path, http = _oauth_service(tmp_path, handler)
    try:
        start = service.start_authorization(now=_now())
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
        token = service.complete_authorization(
            OAuthCallback(state=state, code="authorization-secret"),
            now=_now(),
        )
    finally:
        http.close()

    assert token.access_token.get_secret_value() == "access-secret"
    assert token.refresh_token is not None
    assert token.refresh_token.get_secret_value() == "refresh-secret"
    assert token.scopes == {"openid", "profile", "w_member_social"}
    stored = service.token_store.load()
    assert stored is not None
    assert stored.access_token.get_secret_value() == "access-secret"
    raw_store = service.token_store.path.read_text(encoding="utf-8")
    assert "id-secret" not in raw_store
    assert "refresh-secret" not in raw_store
    assert "id_token" not in raw_store
    assert "refresh_token" not in raw_store


def test_oauth_denial_consumes_state(tmp_path: Path) -> None:
    service, _state_path, http = _oauth_service(
        tmp_path,
        lambda _request: httpx.Response(500),
    )
    try:
        start = service.start_authorization(now=_now())
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
        with pytest.raises(OAuthAuthorizationDeniedError):
            service.complete_authorization(
                OAuthCallback(
                    state=state,
                    error="access_denied",
                    error_description="Member denied access",
                ),
                now=_now(),
            )
    finally:
        http.close()


def test_oauth_state_mismatch_is_rejected(tmp_path: Path) -> None:
    service, _state_path, http = _oauth_service(
        tmp_path,
        lambda _request: httpx.Response(500),
    )
    try:
        service.start_authorization(now=_now())
        with pytest.raises(OAuthStateMismatchError):
            service.complete_authorization(
                OAuthCallback(state="wrong-state", code="code"),
                now=_now(),
            )
    finally:
        http.close()


def test_oauth_state_is_one_time_and_expires(tmp_path: Path) -> None:
    service, state_path, http = _oauth_service(
        tmp_path,
        lambda _request: httpx.Response(500),
    )
    try:
        start = service.start_authorization(now=_now())
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
        with pytest.raises(OAuthStateMismatchError, match="expired"):
            service.complete_authorization(
                OAuthCallback(state=state, code="code"),
                now=_now() + timedelta(seconds=601),
            )
        assert not state_path.exists()

        with pytest.raises(OAuthStateMismatchError, match="pending"):
            service.complete_authorization(
                OAuthCallback(state=state, code="code"),
                now=_now() + timedelta(seconds=602),
            )
    finally:
        http.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_oauth_state_rejects_unsafe_permissions(tmp_path: Path) -> None:
    service, state_path, http = _oauth_service(
        tmp_path,
        lambda _request: httpx.Response(500),
    )
    try:
        start = service.start_authorization(now=_now())
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
        state_path.chmod(0o644)

        with pytest.raises(CredentialStoreError, match="state file permissions"):
            service.complete_authorization(
                OAuthCallback(state=state, code="code"),
                now=_now(),
            )
    finally:
        state_path.chmod(0o600)
        state_path.unlink(missing_ok=True)
        http.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_oauth_state_rejects_unsafe_directory_permissions(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "credentials" / "state.json"
    store = FileOAuthStateStore(state_path)
    store.save("pending-state", created_at=_now())
    state_path.parent.chmod(0o755)

    with pytest.raises(CredentialStoreError, match="chmod 700"):
        store.consume("pending-state", now=_now())
    with pytest.raises(CredentialStoreError, match="chmod 700"):
        store.delete()

    assert state_path.exists()


def test_oauth_state_rejects_non_object_payload(tmp_path: Path) -> None:
    state_path = tmp_path / "credentials" / "state.json"
    state_path.parent.mkdir()
    state_path.parent.chmod(0o700)
    state_path.write_text("[]", encoding="utf-8")
    state_path.chmod(0o600)
    store = FileOAuthStateStore(state_path)

    with pytest.raises(OAuthStateMismatchError, match="pending"):
        store.consume("returned-state", now=_now())

    assert not state_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_oauth_state_store_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target-state.json"
    target.write_text(
        json.dumps(
            {
                "state_hash": "a" * 64,
                "created_at": _now().isoformat(),
            }
        ),
        encoding="utf-8",
    )
    target.chmod(0o600)
    link = tmp_path / "state.json"
    link.symlink_to(target)
    store = FileOAuthStateStore(link)

    with pytest.raises(CredentialStoreError, match="symbolic link"):
        store.consume("pending-state", now=_now())
    with pytest.raises(CredentialStoreError, match="stored securely"):
        store.save("replacement-state", created_at=_now())

    store.delete()
    assert not link.is_symlink()
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["state_hash"] == "a" * 64


@pytest.mark.parametrize(
    ("response", "error_type", "expected_message"),
    [
        (
            httpx.Response(
                400,
                json={
                    "error": "invalid_request",
                    "error_description": (
                        "authorization_code=authorization-secret "
                        "client_secret=client-secret"
                    ),
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "ignored": "must-not-be-printed",
                },
            ),
            TokenExchangeError,
            "HTTP 400",
        ),
        (
            httpx.Response(200, content=b"not-json"),
            MalformedTokenResponseError,
            "token response was not a JSON object",
        ),
    ],
)
def test_token_exchange_failures(
    tmp_path: Path,
    response: httpx.Response,
    error_type: type[Exception],
    expected_message: str,
) -> None:
    service, _state_path, http = _oauth_service(
        tmp_path,
        lambda _request: response,
    )
    try:
        with pytest.raises(error_type) as caught:
            service.exchange_code("authorization-secret", now=_now())
    finally:
        http.close()
    message = str(caught.value)
    assert expected_message in message
    if error_type is TokenExchangeError:
        assert "error=invalid_request" in message
        assert "error_description=" in message
        assert "must-not-be-printed" not in message
        assert "authorization-secret" not in message
        assert "client-secret" not in message
        assert "access-secret" not in message
        assert "refresh-secret" not in message


def test_token_exchange_timeout(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    service, _state_path, http = _oauth_service(tmp_path, handler)
    try:
        with pytest.raises(LinkedInRequestTimeoutError):
            service.exchange_code("code", now=_now())
    finally:
        http.close()


def test_token_response_expiration() -> None:
    token = parse_token_response(
        {"access_token": "token", "expires_in": 60},
        now=_now(),
    )

    assert not token.is_expired(now=_now(), skew_seconds=0)
    assert token.is_expired(now=_now() + timedelta(seconds=60), skew_seconds=0)


def test_token_response_accepts_linkedin_comma_delimited_scopes() -> None:
    token = parse_token_response(
        {
            "access_token": "token",
            "expires_in": 60,
            "scope": "openid,profile,w_member_social",
        },
        now=_now(),
    )

    assert token.scopes == {"openid", "profile", "w_member_social"}


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "token", "expires_in": True},
        {"access_token": "token", "expires_in": 60, "token_type": "MAC"},
        {"access_token": "token", "expires_in": 60, "scope": ["openid"]},
        {
            "access_token": "token",
            "expires_in": 60,
            "refresh_token_expires_in": True,
        },
        {
            "access_token": "token\nheader",
            "expires_in": 60,
        },
        {
            "access_token": "tökén",
            "expires_in": 60,
        },
        {
            "access_token": "token\u202evalue",
            "expires_in": 60,
        },
        {
            "access_token": "token",
            "expires_in": 60,
            "scope": "openid bad\x1b[2J",
        },
        {
            "access_token": "token",
            "expires_in": 60,
            "scope": "openid+profile",
        },
        {
            "access_token": "token",
            "expires_in": 60,
            "scope": "openid%0Aprofile",
        },
        {
            "access_token": "token",
            "expires_in": 60,
            "id_token": "id\ud800",
        },
        {
            "access_token": "token",
            "expires_in": 60,
            "refresh_token": "refresh\u202evalue",
        },
    ],
)
def test_token_response_rejects_invalid_provider_types(
    payload: dict[str, object],
) -> None:
    with pytest.raises(MalformedTokenResponseError):
        parse_token_response(payload, now=_now())


def test_token_validation_error_reports_only_invalid_field_name() -> None:
    secret_value = "access-secret\nmust-not-be-printed"

    with pytest.raises(MalformedTokenResponseError) as caught:
        parse_token_response(
            {"access_token": secret_value, "expires_in": 60},
            now=_now(),
        )

    message = str(caught.value)
    assert "fields: access_token" in message
    assert secret_value not in message
    assert "must-not-be-printed" not in message


def test_file_token_store_is_private_atomic_and_deletable(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    store = FileTokenStore(path)
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )

    store.save(token)

    assert "runs" not in path.parts
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert list(path.parent.glob("*.tmp")) == []
    assert store.load() is not None
    assert store.delete()
    assert store.load() is None


def test_file_token_store_cleans_temp_file_when_write_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    store = FileTokenStore(path)
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )

    def interrupt_dump(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("buildlog.linkedin_token_store.json.dump", interrupt_dump)

    with pytest.raises(KeyboardInterrupt):
        store.save(token)

    assert not path.exists()
    assert list(path.parent.glob("*.tmp")) == []


def test_file_token_store_rejects_malformed_content(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    path.parent.mkdir()
    path.parent.chmod(0o700)
    path.write_text('{"access_token":', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(MalformedTokenResponseError, match="local"):
        FileTokenStore(path).load()


def test_file_token_store_rejects_naive_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    path.parent.mkdir()
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(
            {
                "access_token": "token-secret",
                "token_type": "Bearer",
                "expires_at": "2026-07-29T12:00:00",
                "scopes": ["openid"],
                "scope_source": "response",
                "obtained_at": "2026-07-29T11:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(MalformedTokenResponseError, match="local"):
        FileTokenStore(path).load()


def test_file_token_store_rejects_unsafe_scope(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    store = FileTokenStore(path)
    payload = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    ).storage_payload()
    payload["scopes"] = ["openid", "bad\x1b[2J"]
    path.parent.mkdir()
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(MalformedTokenResponseError, match="local"):
        store.load()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_file_token_store_rejects_unsafe_permissions(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    path.parent.mkdir()
    path.parent.chmod(0o700)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(CredentialStoreError, match="permissions"):
        FileTokenStore(path).load()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_file_token_store_rejects_unsafe_directory_permissions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials" / "linkedin.json"
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    path.parent.chmod(0o755)
    store = FileTokenStore(path)

    with pytest.raises(CredentialStoreError, match="directory is unsafe"):
        store.load()
    with pytest.raises(CredentialStoreError, match="directory is unsafe"):
        store.delete()

    assert path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_file_token_store_rejects_writable_parent_directory(
    tmp_path: Path,
) -> None:
    container = tmp_path / "unsafe-parent"
    container.mkdir()
    container.chmod(0o777)
    credentials = container / "credentials"
    credentials.mkdir(mode=0o700)
    path = credentials / "linkedin.json"
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )
    path.write_text(json.dumps(token.storage_payload()), encoding="utf-8")
    path.chmod(0o600)
    store = FileTokenStore(path)

    with pytest.raises(CredentialStoreError, match="parent is unsafe"):
        store.load()
    with pytest.raises(CredentialStoreError, match="stored securely"):
        store.save(token)
    with pytest.raises(CredentialStoreError, match="parent is unsafe"):
        store.delete()

    assert path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_file_token_store_rechecks_new_parent_after_directory_creation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "new-container" / "credentials" / "linkedin.json"
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )
    previous_umask = os.umask(0)
    try:
        with pytest.raises(CredentialStoreError, match="stored securely"):
            FileTokenStore(path).save(token)
    finally:
        os.umask(previous_umask)

    assert not path.exists()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o777


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_file_token_store_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "linkedin.json"
    link.symlink_to(target)
    store = FileTokenStore(link)
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )

    with pytest.raises(CredentialStoreError, match="symbolic link"):
        store.load()
    with pytest.raises(CredentialStoreError, match="stored securely"):
        store.save(token)

    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_file_token_store_rejects_dangling_symbolic_link(tmp_path: Path) -> None:
    link = tmp_path / "linkedin.json"
    link.symlink_to(tmp_path / "missing-target.json")
    store = FileTokenStore(link)

    with pytest.raises(CredentialStoreError, match="symbolic link"):
        store.load()

    assert store.delete()
    assert not link.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_file_token_store_rejects_symbolic_link_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    credentials = tmp_path / "credentials"
    credentials.symlink_to(outside, target_is_directory=True)
    store = FileTokenStore(credentials / "linkedin.json")
    token = LinkedInToken(
        access_token=SecretStr("token-secret"),
        expires_at=_now() + timedelta(hours=1),
        scopes={"openid"},
        scope_source="response",
        obtained_at=_now(),
    )

    with pytest.raises(CredentialStoreError, match="securely"):
        store.save(token)
    with pytest.raises(CredentialStoreError, match="directory is unsafe"):
        store.load()

    assert not (outside / "linkedin.json").exists()

    outside_file = outside / "linkedin.json"
    outside_file.write_text(
        json.dumps(token.storage_payload()),
        encoding="utf-8",
    )
    outside_file.chmod(0o600)
    with pytest.raises(CredentialStoreError, match="directory is unsafe"):
        store.load()
    with pytest.raises(CredentialStoreError, match="directory is unsafe"):
        store.delete()
    assert outside_file.exists()


def test_callback_parser_handles_success_and_denial() -> None:
    success = parse_callback_url(
        "/auth/linkedin/callback?code=abc&state=xyz",
        expected_path="/auth/linkedin/callback",
    )
    denied = parse_callback_url(
        "/auth/linkedin/callback?error=access_denied&state=xyz",
        expected_path="/auth/linkedin/callback",
    )

    assert success.code == "abc"
    assert denied.error == "access_denied"
    assert "abc" not in repr(success)
    assert "xyz" not in repr(success)


@pytest.mark.parametrize(
    "url",
    [
        "/wrong?code=abc&state=xyz",
        "/auth/linkedin/callback?code=abc&state=one&state=two",
        "/auth/linkedin/callback?code=abc&error=denied&state=xyz",
        "/auth/linkedin/callback?state=xyz",
    ],
)
def test_callback_parser_rejects_ambiguous_input(url: str) -> None:
    with pytest.raises(ValueError):
        parse_callback_url(
            url,
            expected_path="/auth/linkedin/callback",
        )


def test_local_callback_timeout_closes_server(monkeypatch) -> None:
    class FakeServer:
        timeout = None
        closed = False

        def handle_request(self):
            pytest.fail("zero timeout must not handle a request")

        def server_close(self):
            self.closed = True

    server = FakeServer()
    monkeypatch.setattr(
        "buildlog.linkedin_callback.HTTPServer",
        lambda *_args, **_kwargs: server,
    )

    with pytest.raises(OAuthCallbackTimeoutError):
        wait_for_local_callback(
            "http://127.0.0.1:8765/auth/linkedin/callback",
            timeout_seconds=0,
        )

    assert server.closed
    assert server.timeout == 0


def test_local_callback_fails_immediately_after_invalid_callback(
    monkeypatch,
) -> None:
    class FakeServer:
        timeout = None
        closed = False
        requests_handled = 0

        def __init__(self, _address, handler_class):
            self.handler_class = handler_class

        def handle_request(self):
            self.requests_handled += 1
            handler = self.handler_class.__new__(self.handler_class)
            handler.path = "/auth/linkedin/callback?state=state"
            handler.wfile = type(
                "Writer",
                (),
                {"write": lambda _self, _body: None},
            )()
            handler.send_response = lambda _status: None
            handler.send_header = lambda _name, _value: None
            handler.end_headers = lambda: None
            handler.do_GET()

        def server_close(self):
            self.closed = True

    server_holder: list[FakeServer] = []

    def make_server(*args):
        server = FakeServer(*args)
        server_holder.append(server)
        return server

    monkeypatch.setattr("buildlog.linkedin_callback.HTTPServer", make_server)

    with pytest.raises(
        OAuthAuthorizationDeniedError,
        match="invalid OAuth callback",
    ):
        wait_for_local_callback(
            "http://127.0.0.1:8765/auth/linkedin/callback",
            timeout_seconds=180,
        )

    assert server_holder[0].requests_handled == 1
    assert server_holder[0].closed


def test_local_callback_invokes_hook_only_after_listener_is_bound(
    monkeypatch,
) -> None:
    order: list[str] = []

    class FakeServer:
        timeout = None

        def __init__(self, _address, handler_class):
            order.append("bound")
            self.handler_class = handler_class

        def handle_request(self):
            handler = self.handler_class.__new__(self.handler_class)
            handler.path = (
                "/auth/linkedin/callback?code=authorization-code&state=state"
            )
            handler.wfile = type(
                "Writer",
                (),
                {"write": lambda _self, _body: None},
            )()
            handler.send_response = lambda _status: None
            handler.send_header = lambda _name, _value: None
            handler.end_headers = lambda: None
            handler.do_GET()

        def server_close(self):
            order.append("closed")

    monkeypatch.setattr("buildlog.linkedin_callback.HTTPServer", FakeServer)

    callback = wait_for_local_callback(
        "http://127.0.0.1:8765/auth/linkedin/callback",
        timeout_seconds=1,
        on_listening=lambda: order.append("opened"),
    )

    assert callback.code == "authorization-code"
    assert order == ["bound", "opened", "closed"]


def test_local_callback_closes_listener_when_ready_hook_fails(
    monkeypatch,
) -> None:
    class FakeServer:
        closed = False

        def __init__(self, _address, _handler_class):
            return

        def server_close(self):
            self.closed = True

    server = FakeServer(None, None)
    monkeypatch.setattr(
        "buildlog.linkedin_callback.HTTPServer",
        lambda *_args, **_kwargs: server,
    )

    def fail_ready_hook() -> None:
        raise RuntimeError("browser unavailable")

    with pytest.raises(RuntimeError, match="browser unavailable"):
        wait_for_local_callback(
            "http://127.0.0.1:8765/auth/linkedin/callback",
            timeout_seconds=1,
            on_listening=fail_ready_hook,
        )

    assert server.closed


def test_local_callback_survives_browser_disconnect(monkeypatch) -> None:
    response_headers: dict[str, str] = {}
    response_bodies: list[bytes] = []

    class BrokenWriter:
        def write(self, body):
            response_bodies.append(body)
            raise BrokenPipeError("browser closed")

    class FakeServer:
        timeout = None
        closed = False

        def __init__(self, _address, handler_class):
            self.handler_class = handler_class

        def handle_request(self):
            handler = self.handler_class.__new__(self.handler_class)
            handler.path = (
                "/auth/linkedin/callback?code=authorization-code&state=state"
            )
            handler.wfile = BrokenWriter()
            handler.send_response = lambda _status: None
            handler.send_header = response_headers.__setitem__
            handler.end_headers = lambda: None
            handler.do_GET()

        def server_close(self):
            self.closed = True

    server_holder: list[FakeServer] = []

    def make_server(*args):
        server = FakeServer(*args)
        server_holder.append(server)
        return server

    monkeypatch.setattr("buildlog.linkedin_callback.HTTPServer", make_server)

    callback = wait_for_local_callback(
        "http://127.0.0.1:8765/auth/linkedin/callback",
        timeout_seconds=1,
    )

    assert callback.code == "authorization-code"
    assert response_headers["Cache-Control"] == "no-store"
    assert response_headers["Pragma"] == "no-cache"
    assert response_headers["Content-Security-Policy"] == (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    assert response_headers["Referrer-Policy"] == "no-referrer"
    assert response_headers["X-Content-Type-Options"] == "nosniff"
    assert b"authorization response received" in response_bodies[0]
    assert b"confirm completion" in response_bodies[0]
    assert b"authorization completed" not in response_bodies[0]
    assert server_holder[0].closed


def _oauth_service(
    tmp_path: Path,
    handler,
) -> tuple[LinkedInOAuthService, Path, LinkedInHttpClient]:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = LinkedInHttpClient(timeout_seconds=1, client=client)
    token_store = FileTokenStore(tmp_path / "credentials" / "linkedin.json")
    state_path = tmp_path / "credentials" / "state.json"
    service = LinkedInOAuthService(
        settings,
        http,
        token_store,
        FileOAuthStateStore(state_path),
    )
    return service, state_path, http


def _now() -> datetime:
    return datetime(2026, 7, 29, tzinfo=UTC)
