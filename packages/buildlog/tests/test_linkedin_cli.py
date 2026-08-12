"""Tests for compatible CLI dispatch and explicit publication confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from buildlog.linkedin_cli import linkedin_main
from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    OAuthCallbackTimeoutError,
    PublicationApprovalRequiredError,
    PublicationValidationError,
)
from buildlog.linkedin_identity import LinkedInIdentity
from buildlog.linkedin_token_store import LinkedInToken
from buildlog.main import main
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationPreview,
    PublicationStatus,
    PublishReceipt,
)


class FakeHttp:
    def close(self) -> None:
        return


class FakePublishingService:
    def __init__(self) -> None:
        self.publish_calls: list[
            tuple[str, bool, str | None, str | None, bool]
        ] = []

    def preview(self, run_id: str) -> PublicationPreview:
        return PublicationPreview(
            platform=PublicationPlatform.LINKEDIN,
            run_id=run_id,
            artifact_id=f"{run_id}:final",
            artifact_path=f"/tmp/{run_id}/06_final.md",
            account_reference="account-ref",
            account_display_name="Ju L",
            content_length=13,
            content_hash="a" * 64,
            content="Grounded post",
            duplicate_found=False,
        )

    def publish(
        self,
        run_id: str,
        *,
        approved: bool,
        approved_content_hash: str | None = None,
        approved_account_reference: str | None = None,
        allow_duplicate: bool = False,
    ) -> PublishReceipt:
        self.publish_calls.append(
            (
                run_id,
                approved,
                approved_content_hash,
                approved_account_reference,
                allow_duplicate,
            )
        )
        if not approved:
            raise PublicationApprovalRequiredError("approval required")
        return PublishReceipt(
            receipt_id="receipt-001",
            attempt_id="attempt-001",
            run_id=run_id,
            artifact_id=f"{run_id}:final",
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            content_hash="a" * 64,
            status=PublicationStatus.SUCCEEDED,
            external_post_id="urn:li:share:123",
            created_at=_now(),
            published_at=_now(),
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
            http_status=201,
        )


def test_publish_requires_confirm_flag(monkeypatch, tmp_path: Path) -> None:
    service = FakePublishingService()
    _patch_service(monkeypatch, service)

    result = linkedin_main(
        ["publish", "run-001"],
        project_root=tmp_path,
    )

    assert result == 1
    assert service.publish_calls == [
        ("run-001", False, None, None, False)
    ]


def test_publish_requires_exact_interactive_confirmation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = FakePublishingService()
    _patch_service(monkeypatch, service)

    result = linkedin_main(
        ["publish", "run-001", "--confirm"],
        project_root=tmp_path,
        input_func=lambda _prompt: "yes",
    )

    assert result == 2
    assert service.publish_calls == []


def test_publish_eof_cancels_without_submission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = FakePublishingService()
    _patch_service(monkeypatch, service)

    def end_input(_prompt: str) -> str:
        raise EOFError

    result = linkedin_main(
        ["publish", "run-001", "--confirm"],
        project_root=tmp_path,
        input_func=end_input,
    )

    assert result == 2
    assert service.publish_calls == []


def test_publish_submits_after_exact_confirmation(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    service = FakePublishingService()
    _patch_service(monkeypatch, service)

    result = linkedin_main(
        ["publish", "run-001", "--confirm", "--allow-duplicate"],
        project_root=tmp_path,
        input_func=lambda _prompt: "PUBLISH",
    )

    assert result == 0
    assert service.publish_calls == [
        ("run-001", True, "a" * 64, "account-ref", True)
    ]
    captured = capsys.readouterr()
    assert "Platform: linkedin" in captured.out
    assert "LinkedIn publication succeeded." in captured.out
    assert "--allow-duplicate is active" in captured.out


def test_linkedin_error_output_escapes_terminal_controls(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )

    class FailingService:
        def preview(self, _run_id):
            raise PublicationValidationError("bad run\x1b[2J")

    monkeypatch.setattr(
        "buildlog.linkedin_cli.load_linkedin_settings",
        lambda _root: settings,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli._publishing_service",
        lambda _root, _settings: (FailingService(), FakeHttp()),
    )

    result = linkedin_main(
        ["preview", "run-001"],
        project_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "\x1b" not in captured.err
    assert "\\u001B" in captured.err


def test_publish_interrupt_before_confirmation_reports_no_submission(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )

    class InterruptedService:
        def preview(self, _run_id):
            raise KeyboardInterrupt

    monkeypatch.setattr(
        "buildlog.linkedin_cli.load_linkedin_settings",
        lambda _root: settings,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli._publishing_service",
        lambda _root, _settings: (InterruptedService(), FakeHttp()),
    )

    result = linkedin_main(
        ["publish", "run-001", "--confirm"],
        project_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert result == 130
    assert "No post was submitted" in captured.err
    assert "Inspect LinkedIn" not in captured.err


def test_publish_interrupt_after_confirmation_requires_manual_outcome_review(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    class InterruptedAfterConfirmationService(FakePublishingService):
        def publish(
            self,
            run_id: str,
            *,
            approved: bool,
            approved_content_hash: str | None = None,
            approved_account_reference: str | None = None,
            allow_duplicate: bool = False,
        ) -> PublishReceipt:
            raise KeyboardInterrupt

    service = InterruptedAfterConfirmationService()
    _patch_service(monkeypatch, service)

    result = linkedin_main(
        ["publish", "run-001", "--confirm"],
        project_root=tmp_path,
        input_func=lambda _prompt: "PUBLISH",
    )

    captured = capsys.readouterr()
    assert result == 130
    assert "Inspect LinkedIn and local receipts" in captured.err


def test_main_dispatches_linkedin_without_changing_legacy_parser(monkeypatch) -> None:
    monkeypatch.setattr(
        "buildlog.main.linkedin_main",
        lambda argv: 7 if argv == ["status"] else 8,
    )

    assert main(["linkedin", "status"]) == 7


def test_status_escapes_terminal_controls(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(
        "buildlog.linkedin_cli.linkedin_configuration_status",
        lambda _root: {
            "configuration_ready": False,
            "configuration_issue": "invalid value\x1b[2J",
            "client_id_configured": True,
            "client_secret_configured": True,
            "redirect_uri": "http://localhost:8765/callback\x1b[2J",
            "api_version": "202607\x07",
        },
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.FileTokenStore",
        lambda: type(
            "EmptyTokenStore",
            (),
            {"path": Path("/tmp/token.json"), "load": lambda self: None},
        )(),
    )

    result = linkedin_main(["status"], project_root=tmp_path)

    captured = capsys.readouterr()
    assert result == 0
    assert "\x1b" not in captured.out
    assert "\x07" not in captured.out
    assert "\\u001B" in captured.out
    assert "\\u0007" in captured.out


def test_whoami_never_prints_token_or_raw_member_identity(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )
    token = LinkedInToken(
        access_token=SecretStr("access-secret"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes={"openid", "profile", "w_member_social"},
        scope_source="response",
        obtained_at=_now(),
    )

    class TokenStore:
        def load(self):
            return token

    class IdentityService:
        def __init__(self, *_args):
            return

        def resolve(self):
            return LinkedInIdentity(
                subject="raw-member-123",
                display_name="Ju L",
                person_urn="urn:li:person:raw-member-123",
                account_reference="safe-account-reference",
            )

    monkeypatch.setattr(
        "buildlog.linkedin_cli.load_linkedin_settings",
        lambda _root: settings,
    )
    monkeypatch.setattr("buildlog.linkedin_cli.FileTokenStore", TokenStore)
    monkeypatch.setattr(
        "buildlog.linkedin_cli.LinkedInHttpClient",
        lambda **_kwargs: FakeHttp(),
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.LinkedInIdentityService",
        IdentityService,
    )

    result = linkedin_main(["whoami"], project_root=tmp_path)

    captured = capsys.readouterr()
    assert result == 0
    assert "Ju L" in captured.out
    assert "safe-account-reference" in captured.out
    assert "access-secret" not in captured.out
    assert "raw-member-123" not in captured.out
    assert "urn:li:person" not in captured.out


def test_logout_deletes_token_and_pending_state_without_configuration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    deleted = {"token": False, "state": False}

    class TokenStore:
        def delete(self):
            deleted["token"] = True
            return True

    class StateStore:
        def delete(self):
            deleted["state"] = True

    monkeypatch.setattr(
        "buildlog.linkedin_cli.FileTokenStore",
        TokenStore,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.FileOAuthStateStore",
        StateStore,
    )

    result = linkedin_main(["logout"], project_root=tmp_path)

    assert result == 0
    assert deleted == {"token": True, "state": True}


def test_login_cleans_state_when_browser_open_fails(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )

    class StateStore:
        deleted = False

        def save(self, _state, *, created_at):
            return

        def consume(self, _state, *, now, max_age_seconds=600):
            return

        def delete(self):
            self.deleted = True

    state_store = StateStore()
    monkeypatch.setattr(
        "buildlog.linkedin_cli.load_linkedin_settings",
        lambda _root: settings,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.FileTokenStore",
        lambda: object(),
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.FileOAuthStateStore",
        lambda: state_store,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.LinkedInHttpClient",
        lambda **_kwargs: FakeHttp(),
    )

    def fail_callback(*_args, **kwargs):
        kwargs["on_listening"]()
        raise OAuthCallbackTimeoutError("callback timed out")

    monkeypatch.setattr(
        "buildlog.linkedin_cli.wait_for_local_callback",
        fail_callback,
    )

    result = linkedin_main(
        ["login"],
        project_root=tmp_path,
        browser_opener=lambda _url: (_ for _ in ()).throw(
            OSError("browser unavailable")
        ),
    )

    assert result == 1
    assert state_store.deleted
    captured = capsys.readouterr()
    assert "browser could not be opened" in captured.err
    assert "state=" in captured.err


def _patch_service(monkeypatch, service: FakePublishingService) -> None:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli.load_linkedin_settings",
        lambda _root: settings,
    )
    monkeypatch.setattr(
        "buildlog.linkedin_cli._publishing_service",
        lambda _root, _settings: (service, FakeHttp()),
    )


def _now() -> datetime:
    return datetime(2026, 7, 29, tzinfo=UTC)
