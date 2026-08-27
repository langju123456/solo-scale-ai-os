"""Minimal human-controlled CLI for LinkedIn authentication and publishing."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from buildlog.config import load_settings
from buildlog.exceptions import BuildLogError
from buildlog.linkedin_callback import wait_for_local_callback
from buildlog.linkedin_config import (
    LinkedInSettings,
    linkedin_configuration_status,
    load_linkedin_settings,
)
from buildlog.linkedin_http import LinkedInHttpClient
from buildlog.linkedin_identity import (
    LinkedInIdentityService,
    require_valid_token,
)
from buildlog.linkedin_oauth import LinkedInOAuthService
from buildlog.linkedin_publisher import LinkedInTextPublisher
from buildlog.linkedin_security import redact_linkedin_secrets
from buildlog.linkedin_token_store import (
    FileOAuthStateStore,
    FileTokenStore,
)
from buildlog.publication_content import FinalArtifactResolver
from buildlog.publishing_models import PublicationPreview
from buildlog.publishing_service import PublishingService
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


def linkedin_main(
    argv: list[str],
    *,
    project_root: Path | None = None,
    input_func: Callable[[str], str] = input,
    browser_opener: Callable[[str], object] = webbrowser.open,
) -> int:
    """Run one LinkedIn integration command."""
    root = project_root or Path.cwd()
    parser = _parser()
    args = parser.parse_args(argv)
    publish_confirmation_given = False
    try:
        if args.command == "status":
            return _status(root)
        if args.command == "logout":
            return _logout()
        settings = load_linkedin_settings(root)
        if args.command == "login":
            return _login(
                settings,
                no_browser=args.no_browser,
                browser_opener=browser_opener,
            )
        if args.command == "whoami":
            return _whoami(settings)
        if args.command == "preview":
            service, http = _publishing_service(root, settings)
            try:
                preview = service.preview(args.run_id)
            finally:
                http.close()
            _print_preview(preview)
            return 0
        if args.command == "publish":
            service, http = _publishing_service(root, settings)
            try:
                if not args.confirm:
                    service.publish(args.run_id, approved=False)
                preview = service.preview(args.run_id)
                _print_preview(preview)
                if args.allow_duplicate:
                    print(
                        "\nWARNING: --allow-duplicate is active. Confirm that "
                        "LinkedIn and prior receipts were inspected before "
                        "submitting identical or unresolved content."
                    )
                try:
                    confirmation = input_func(
                        "\nType PUBLISH to submit this exact content to LinkedIn: "
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nPublication cancelled. No post was submitted.")
                    return 2
                if confirmation != "PUBLISH":
                    print("Publication cancelled. No post was submitted.")
                    return 2
                publish_confirmation_given = True
                receipt = service.publish(
                    args.run_id,
                    approved=True,
                    approved_content_hash=preview.content_hash,
                    approved_account_reference=preview.account_reference,
                    allow_duplicate=args.allow_duplicate,
                )
            finally:
                http.close()
            published_at = receipt.published_at
            external_post_id = receipt.external_post_id
            assert published_at is not None
            assert external_post_id is not None
            print("\nLinkedIn publication succeeded.")
            print(f"Platform: {_terminal_text(receipt.platform.value)}")
            print(f"Post ID: {_terminal_text(external_post_id)}")
            print(f"Published at: {_terminal_text(published_at.isoformat())}")
            print(f"Receipt ID: {_terminal_text(receipt.receipt_id)}")
            return 0
    except BuildLogError as exc:
        print(f"BuildLog LinkedIn failed: {_terminal_text(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "publish" and publish_confirmation_given:
            print(
                "\nLinkedIn publication was interrupted. Inspect LinkedIn and "
                "local receipts before trying again.",
                file=sys.stderr,
            )
        elif args.command == "publish":
            print(
                "\nPublication cancelled before submission. No post was submitted.",
                file=sys.stderr,
            )
        else:
            print("\nBuildLog LinkedIn operation cancelled.", file=sys.stderr)
        return 130
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildlog linkedin",
        description=(
            "Authenticate, preview, and explicitly publish a BuildLog artifact."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    login = subparsers.add_parser(
        "login",
        help="Authorize LinkedIn through a localhost OAuth callback.",
    )
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL without opening a browser.",
    )
    subparsers.add_parser(
        "status",
        help="Show non-secret configuration and token status.",
    )
    subparsers.add_parser(
        "whoami",
        help="Resolve the authenticated LinkedIn member safely.",
    )
    subparsers.add_parser(
        "logout",
        help="Delete the local LinkedIn token and pending OAuth state.",
    )
    preview = subparsers.add_parser(
        "preview",
        help="Show exact content and duplicate state without publishing.",
    )
    preview.add_argument("run_id")
    publish = subparsers.add_parser(
        "publish",
        help="Publish after preview and exact interactive approval.",
    )
    publish.add_argument("run_id")
    publish.add_argument(
        "--confirm",
        action="store_true",
        help="Enable the interactive PUBLISH approval prompt.",
    )
    publish.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Allow identical content only after explicit review and approval.",
    )
    return parser


def _login(
    settings: LinkedInSettings,
    *,
    no_browser: bool,
    browser_opener: Callable[[str], object],
) -> int:
    token_store = FileTokenStore()
    state_store = FileOAuthStateStore()
    http = LinkedInHttpClient(timeout_seconds=settings.request_timeout_seconds)
    oauth = LinkedInOAuthService(settings, http, token_store, state_store)
    try:
        try:
            start = oauth.start_authorization()
            if not no_browser:
                print("Opening LinkedIn authorization in your browser.")
                on_listening = lambda: _open_authorization_url(
                    start.authorization_url,
                    browser_opener,
                )
            else:
                on_listening = lambda: _show_authorization_url(
                    start.authorization_url
                )
            print(
                f"\nWaiting for callback at {settings.redirect_uri} "
                f"for up to {int(settings.callback_timeout_seconds)} seconds..."
            )
            callback = wait_for_local_callback(
                settings.redirect_uri,
                timeout_seconds=settings.callback_timeout_seconds,
                on_listening=on_listening,
            )
            token = oauth.complete_authorization(callback)
        finally:
            state_store.delete()
    finally:
        http.close()
    print("\nLinkedIn authorization stored locally.")
    print(f"Expires at: {_terminal_text(token.expires_at.isoformat())}")
    print(
        "Scopes: "
        + (
            ", ".join(_terminal_text(scope) for scope in sorted(token.scopes))
            if token.scopes
            else "not returned by provider"
        )
    )
    return 0


def _show_authorization_url(authorization_url: str) -> None:
    print("Open this LinkedIn authorization URL:")
    print(authorization_url)


def _open_authorization_url(
    authorization_url: str,
    browser_opener: Callable[[str], object],
) -> None:
    try:
        opened = browser_opener(authorization_url)
    except Exception:
        opened = False
    if opened is False:
        print(
            "The browser could not be opened automatically. "
            "Open this one-time URL manually:",
            file=sys.stderr,
        )
        print(authorization_url, file=sys.stderr)


def _status(root: Path) -> int:
    config = linkedin_configuration_status(root)
    store = FileTokenStore()
    token = store.load()
    print("LinkedIn configuration:")
    print(f"  Ready for login: {_yes_no(config['configuration_ready'])}")
    if config["configuration_issue"]:
        print(f"  Configuration issue: {_terminal_text(config['configuration_issue'])}")
    print(f"  Client ID configured: {_yes_no(config['client_id_configured'])}")
    print(
        "  Client Secret configured: "
        f"{_yes_no(config['client_secret_configured'])}"
    )
    print(f"  Redirect URI: {_terminal_text(config['redirect_uri'])}")
    print(f"  API version: {_terminal_text(config['api_version'])}")
    print(f"  Token file: {_terminal_text(store.path)}")
    print(f"  Token exists: {_yes_no(token is not None)}")
    if token is not None:
        print(f"  Token expired: {_yes_no(token.is_expired(now=datetime.now(UTC)))}")
        print(f"  Token expires at: {_terminal_text(token.expires_at.isoformat())}")
        print(f"  Scope source: {_terminal_text(token.scope_source)}")
        print(
            "  Scopes: "
            + (
                ", ".join(_terminal_text(scope) for scope in sorted(token.scopes))
                if token.scopes
                else "not returned by provider"
            )
        )
    return 0


def _whoami(settings: LinkedInSettings) -> int:
    store = FileTokenStore()
    http = LinkedInHttpClient(timeout_seconds=settings.request_timeout_seconds)
    try:
        identity = LinkedInIdentityService(settings, http, store).resolve()
        token = require_valid_token(store)
    finally:
        http.close()
    print("Authenticated LinkedIn member:")
    print(f"  Display name: {_terminal_text(identity.display_name)}")
    print(f"  Member identifier: {_terminal_text(identity.redacted_subject)}")
    print(f"  Account reference: {_terminal_text(identity.account_reference)}")
    print(f"  Author mapping: {_terminal_text(identity.author_mapping_source)}")
    print(f"  Token expires at: {_terminal_text(token.expires_at.isoformat())}")
    print(
        "  Scopes: "
        + (
            ", ".join(_terminal_text(scope) for scope in sorted(token.scopes))
            if token.scopes
            else "not returned by provider"
        )
    )
    return 0


def _logout() -> int:
    token_deleted = FileTokenStore().delete()
    FileOAuthStateStore().delete()
    print(
        "LinkedIn local credentials deleted."
        if token_deleted
        else "No local LinkedIn token was present."
    )
    return 0


def _publishing_service(
    root: Path,
    linkedin_settings: LinkedInSettings,
) -> tuple[PublishingService, LinkedInHttpClient]:
    buildlog_settings = load_settings(root)
    repository = SQLAlchemyRunRepository(buildlog_settings.database_url)
    repository.initialize()
    token_store = FileTokenStore()
    http = LinkedInHttpClient(
        timeout_seconds=linkedin_settings.request_timeout_seconds
    )
    identity = LinkedInIdentityService(linkedin_settings, http, token_store)
    publisher = LinkedInTextPublisher(linkedin_settings, http, token_store)
    service = PublishingService(
        linkedin_settings,
        FinalArtifactResolver(
            repository,
            allowed_root=buildlog_settings.runs_dir,
        ),
        identity,
        publisher,
        repository,
    )
    return service, http


def _print_preview(preview: PublicationPreview) -> None:
    print("LinkedIn publication preview:")
    print(f"  Platform: {_terminal_text(preview.platform.value)}")
    print(f"  Run: {_terminal_text(preview.run_id)}")
    print(f"  Artifact: {_terminal_text(preview.artifact_path)}")
    print(f"  Account: {_terminal_text(preview.account_display_name)}")
    print(f"  Account reference: {_terminal_text(preview.account_reference)}")
    print(f"  Content length: {preview.content_length}")
    print(f"  Content SHA-256: {_terminal_text(preview.content_hash)}")
    print(f"  Identical successful publication: {_yes_no(preview.duplicate_found)}")
    if preview.duplicate_found:
        published_at = preview.duplicate_published_at
        assert published_at is not None
        print(f"  Prior post ID: {_terminal_text(preview.duplicate_external_post_id)}")
        print(
            "  Prior published at: "
            f"{_terminal_text(published_at.isoformat())}"
        )
    print(
        "  Matching indeterminate attempt: "
        f"{_yes_no(preview.indeterminate_found)}"
    )
    if preview.indeterminate_found:
        attempted_at = preview.indeterminate_created_at
        assert attempted_at is not None
        print(f"  Prior receipt ID: {_terminal_text(preview.indeterminate_receipt_id)}")
        print(
            "  Prior attempted at: "
            f"{_terminal_text(attempted_at.isoformat())}"
        )
    print("  Network publication from preview: no")
    print("\n--- exact content ---\n")
    print(preview.content)
    print("\n--- end content ---")


def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def _terminal_text(value: object) -> str:
    """Render one diagnostic value without terminal control sequences."""
    return redact_linkedin_secrets(value)
