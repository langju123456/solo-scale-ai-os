"""Minimal CLI for X OAuth, preview, and approved text publishing."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from buildlog.config import load_settings
from buildlog.exceptions import BuildLogError
from buildlog.linkedin_security import redact_linkedin_secrets
from buildlog.publication_content import FinalArtifactResolver
from buildlog.publishing_models import PublicationPlatform, PublicationPreview
from buildlog.publishing_service import PublishingService
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.x_callback import wait_for_x_callback
from buildlog.x_config import XSettings, load_x_settings, x_configuration_status
from buildlog.x_http import XHttpClient
from buildlog.x_identity import XIdentityService, require_valid_x_token
from buildlog.x_oauth import XOAuthService
from buildlog.x_publisher import XTextPublisher, validate_x_content, x_weighted_length
from buildlog.x_token_store import FileXAuthorizationStore, FileXTokenStore


def x_main(
    argv: list[str],
    *,
    project_root: Path | None = None,
    input_func: Callable[[str], str] = input,
    browser_opener: Callable[[str], object] = webbrowser.open,
) -> int:
    """Run one X integration command."""
    root = project_root or Path.cwd()
    args = _parser().parse_args(argv)
    submission_approved = False
    try:
        if args.command == "status":
            return _status(root)
        if args.command == "logout":
            return _logout()
        settings = load_x_settings(root)
        if args.command == "login":
            return _login(
                settings,
                no_browser=args.no_browser,
                browser_opener=browser_opener,
            )
        if args.command == "whoami":
            return _whoami(settings)
        service, http = _publishing_service(root, settings)
        try:
            if args.command == "preview":
                preview = service.preview(args.run_id)
                _print_preview(preview)
                return 0
            if not args.confirm:
                service.publish(args.run_id, approved=False)
            preview = service.preview(args.run_id)
            _print_preview(preview)
            if args.allow_duplicate:
                print(
                    "\nWARNING: --allow-duplicate is active. Inspect X and "
                    "prior receipts before continuing."
                )
            try:
                confirmation = input_func(
                    "\nType PUBLISH to submit this exact text to X: "
                )
            except (EOFError, KeyboardInterrupt):
                print("\nPublication cancelled. No post was submitted.")
                return 2
            if confirmation != "PUBLISH":
                print("Publication cancelled. No post was submitted.")
                return 2
            submission_approved = True
            receipt = service.publish(
                args.run_id,
                approved=True,
                approved_content_hash=preview.content_hash,
                approved_account_reference=preview.account_reference,
                allow_duplicate=args.allow_duplicate,
            )
        finally:
            http.close()
        assert receipt.external_post_id is not None
        assert receipt.published_at is not None
        print("\nX publication succeeded.")
        print(f"  Post ID: {_terminal_text(receipt.external_post_id)}")
        print(f"  Published at: {_terminal_text(receipt.published_at.isoformat())}")
        print(f"  Receipt ID: {_terminal_text(receipt.receipt_id)}")
        return 0
    except BuildLogError as exc:
        print(f"BuildLog X failed: {_terminal_text(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "publish" and submission_approved:
            print(
                "\nX publication was interrupted. Inspect X and local receipts "
                "before trying again.",
                file=sys.stderr,
            )
        else:
            print("\nBuildLog X operation cancelled.", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildlog x",
        description="Authenticate, preview, and explicitly publish one X post.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    login = subparsers.add_parser("login", help="Authorize X using OAuth 2.0 PKCE.")
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL instead of opening a browser.",
    )
    subparsers.add_parser("status", help="Show safe configuration and token status.")
    subparsers.add_parser("whoami", help="Resolve the authenticated X user.")
    subparsers.add_parser("logout", help="Delete the local X token and OAuth state.")
    preview = subparsers.add_parser(
        "preview",
        help="Show exact X text and duplicate state without publishing.",
    )
    preview.add_argument("run_id")
    publish = subparsers.add_parser(
        "publish",
        help="Publish one text post after exact interactive approval.",
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
        help="Allow identical content only after manual inspection.",
    )
    return parser


def _login(
    settings: XSettings,
    *,
    no_browser: bool,
    browser_opener: Callable[[str], object],
) -> int:
    token_store = FileXTokenStore()
    authorization_store = FileXAuthorizationStore()
    oauth = XOAuthService(settings, token_store, authorization_store)
    try:
        start = oauth.start_authorization()
        if no_browser:
            on_listening = lambda: _show_url(start.authorization_url)
        else:
            on_listening = lambda: _open_url(
                start.authorization_url,
                browser_opener,
            )
        print(
            f"Waiting for X callback at {settings.redirect_uri} for up to "
            f"{int(settings.callback_timeout_seconds)} seconds..."
        )
        callback = wait_for_x_callback(
            settings.redirect_uri,
            timeout_seconds=settings.callback_timeout_seconds,
            on_listening=on_listening,
        )
        token = oauth.complete_authorization(callback)
    finally:
        authorization_store.delete()
    print("\nX authorization stored locally.")
    print(f"  Expires at: {_terminal_text(token.expires_at.isoformat())}")
    print("  Scopes: " + ", ".join(sorted(token.scopes)))
    return 0


def _status(root: Path) -> int:
    status = x_configuration_status(root)
    store = FileXTokenStore()
    token = store.load()
    print("X configuration:")
    print(f"  Ready for login: {_yes_no(status['configuration_ready'])}")
    if status["configuration_issue"]:
        print(f"  Configuration issue: {_terminal_text(status['configuration_issue'])}")
    print(f"  Client ID configured: {_yes_no(status['client_id_configured'])}")
    print(f"  Redirect URI: {_terminal_text(status['redirect_uri'])}")
    print("  Requested scopes: " + ", ".join(status["scopes"]))
    print(f"  Token file: {_terminal_text(store.path)}")
    print(f"  Token exists: {_yes_no(token is not None)}")
    if token is not None:
        print(f"  Token expired: {_yes_no(token.is_expired(now=datetime.now(UTC)))}")
        print(f"  Token expires at: {_terminal_text(token.expires_at.isoformat())}")
        print("  Granted scopes: " + ", ".join(sorted(token.scopes)))
    return 0


def _whoami(settings: XSettings) -> int:
    store = FileXTokenStore()
    http = XHttpClient(timeout_seconds=settings.request_timeout_seconds)
    try:
        identity = XIdentityService(settings, http, store).resolve()
        token = require_valid_x_token(store)
    finally:
        http.close()
    print("Authenticated X user:")
    print(f"  Display name: {_terminal_text(identity.display_name)}")
    print(f"  Username: @{_terminal_text(identity.username)}")
    print(f"  User identifier: {_terminal_text(identity.redacted_user_id)}")
    print(f"  Account reference: {_terminal_text(identity.account_reference)}")
    print(f"  Identity source: {_terminal_text(identity.mapping_source)}")
    print(f"  Token expires at: {_terminal_text(token.expires_at.isoformat())}")
    print("  Scopes: " + ", ".join(sorted(token.scopes)))
    return 0


def _logout() -> int:
    deleted = FileXTokenStore().delete()
    FileXAuthorizationStore().delete()
    print(
        "X local credentials deleted."
        if deleted
        else "No local X token was present."
    )
    return 0


def _publishing_service(
    root: Path,
    x_settings: XSettings,
) -> tuple[PublishingService, XHttpClient]:
    settings = load_settings(root)
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()
    token_store = FileXTokenStore()
    http = XHttpClient(timeout_seconds=x_settings.request_timeout_seconds)
    service = PublishingService(
        x_settings,
        FinalArtifactResolver(repository, allowed_root=settings.runs_dir),
        XIdentityService(x_settings, http, token_store),
        XTextPublisher(x_settings, http, token_store),
        repository,
        platform=PublicationPlatform.X,
        platform_name="X",
        content_validator=validate_x_content,
    )
    return service, http


def _print_preview(preview: PublicationPreview) -> None:
    print("X publication preview:")
    print(f"  Run: {_terminal_text(preview.run_id)}")
    print(f"  Artifact: {_terminal_text(preview.artifact_path)}")
    print(f"  Account: {_terminal_text(preview.account_display_name)}")
    print(f"  Account reference: {_terminal_text(preview.account_reference)}")
    print(f"  Weighted length: {x_weighted_length(preview.content)}/280")
    print(f"  Content SHA-256: {_terminal_text(preview.content_hash)}")
    print(f"  Identical successful publication: {_yes_no(preview.duplicate_found)}")
    print(
        "  Matching indeterminate attempt: "
        f"{_yes_no(preview.indeterminate_found)}"
    )
    print("  Network publication from preview: no")
    print("\n--- exact content ---\n")
    print(preview.content)
    print("\n--- end content ---")


def _show_url(url: str) -> None:
    print("Open this one-time X authorization URL:")
    print(url)


def _open_url(url: str, browser_opener: Callable[[str], object]) -> None:
    try:
        opened = browser_opener(url)
    except Exception:
        opened = False
    if opened is False:
        _show_url(url)


def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def _terminal_text(value: object) -> str:
    return redact_linkedin_secrets(value)
