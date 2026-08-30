"""Focused tests for macOS development build identity and provenance."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from soloscale.local_ui import (
    DesktopBuildIdentity,
    _desktop_build_identity,
    _home_page,
    _page,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY_ROOT / "scripts" / "build_macos_app.sh"
INFO_TEMPLATE = REPOSITORY_ROOT / "desktop" / "macos" / "Info.plist.template"
BUILD_ENVIRONMENT_KEYS = {
    "SOLOSCALE_APP_BUNDLE_NAME",
    "SOLOSCALE_BUILD_KIND",
    "SOLOSCALE_BUILD_NUMBER",
    "SOLOSCALE_BUNDLE_IDENTIFIER",
    "SOLOSCALE_DISPLAY_NAME",
    "SOLOSCALE_VERSION",
}


def _printed_build_identity(
    script: Path = BUILD_SCRIPT,
    **overrides: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    for key in BUILD_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    environment.update(overrides)
    result = subprocess.run(
        ["bash", str(script), "--print-build-identity"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def _git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def test_build_script_reads_branch_and_short_sha_from_its_worktree() -> None:
    identity = _printed_build_identity()
    expected_branch = _git_output("symbolic-ref", "--quiet", "--short", "HEAD")
    expected_commit = _git_output("rev-parse", "--short=7", "HEAD")

    assert identity["git_branch"] == (expected_branch or "unknown")
    assert identity["git_commit"] == (expected_commit or "unknown")
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    if expected_branch:
        assert expected_branch not in script
    if expected_commit:
        assert expected_commit not in script


def test_build_script_reports_unknown_without_git_metadata(tmp_path: Path) -> None:
    copied_script = tmp_path / "scripts" / BUILD_SCRIPT.name
    copied_script.parent.mkdir()
    shutil.copy2(BUILD_SCRIPT, copied_script)

    identity = _printed_build_identity(copied_script)

    assert identity["git_branch"] == "unknown"
    assert identity["git_commit"] == "unknown"


def test_development_identity_differs_and_production_identity_is_unchanged() -> None:
    development = _printed_build_identity()
    production = _printed_build_identity(SOLOSCALE_BUILD_KIND="production")
    with INFO_TEMPLATE.open("rb") as stream:
        template = plistlib.load(stream)

    assert development["build_kind"] == "development"
    assert development["bundle_identifier"] == "local.soloscale.desktop.dev"
    assert development["display_name"] == "SoloScale AI OS Dev"
    assert development["app_bundle_name"] == "SoloScale AI OS Dev"
    assert production["build_kind"] == "production"
    assert production["bundle_identifier"] == "local.soloscale.desktop"
    assert production["display_name"] == "SoloScale AI OS"
    assert production["app_bundle_name"] == "SoloScale AI OS"
    assert template["CFBundleIdentifier"] == production["bundle_identifier"]
    assert template["CFBundleDisplayName"] == production["display_name"]


def test_advanced_page_renders_build_provenance_and_keeps_path_out_of_home(
    tmp_path: Path,
) -> None:
    bundle_path = str(tmp_path / "SoloScale AI OS Dev.app")
    identity = DesktopBuildIdentity(
        app_version="0.5.0-dev.8",
        build_number="8",
        build_kind="development",
        bundle_id="local.soloscale.desktop.dev",
        display_name="SoloScale AI OS Dev",
        git_branch="codex/resume-intelligence-high-recall",
        git_commit="8c97a26",
        bundle_path=bundle_path,
    )

    advanced = _page(
        None,
        tmp_path / "data",
        {},
        "en",
        build_identity=identity,
    )

    assert "SoloScale AI OS Dev" in advanced
    assert "0.5.0-dev.8 (build 8)" in advanced
    assert "codex/resume-intelligence-high-recall" in advanced
    assert "8c97a26" in advanced
    assert "local.soloscale.desktop.dev" in advanced
    assert bundle_path in advanced
    assert "embedded in the app bundle at build time" in advanced
    assert bundle_path not in _home_page("en", data_root=tmp_path / "data")


def test_unknown_metadata_is_truthful_and_unrelated_environment_is_ignored(
    tmp_path: Path,
) -> None:
    identity = _desktop_build_identity(
        {
            "OPENAI_API_KEY": "sk-private-must-not-render",
            "SOLOSCALE_DESKTOP_DISPLAY_NAME": "SoloScale AI OS Dev",
        }
    )
    page = _page(None, tmp_path / "data", {}, "en", build_identity=identity)

    assert identity.git_branch == "unknown"
    assert identity.git_commit == "unknown"
    assert page.count("unknown") >= 4
    assert "sk-private-must-not-render" not in page
