"""CLI for building local target-aware publishing packages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from buildlog.asset_planner import LLMAssetPlanner
from buildlog.card_renderer import CardRenderer
from buildlog.config import load_settings
from buildlog.exceptions import BuildLogError
from buildlog.llm_client import LLMClient
from buildlog.package_builder import PublishingPackageBuilder
from buildlog.publication_content import FinalArtifactResolver
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


def package_main(
    argv: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    """Build local publishing packages without calling a publisher adapter."""
    parser = _parser()
    args = parser.parse_args(argv)
    root = (project_root or Path.cwd()).resolve()

    try:
        settings = load_settings(root)
        repository = SQLAlchemyRunRepository(settings.database_url)
        repository.initialize()
        builder = PublishingPackageBuilder(
            repository,
            FinalArtifactResolver(repository, allowed_root=settings.runs_dir),
            LLMAssetPlanner(
                LLMClient(settings),
                settings,
                prompt_version=args.prompt_version,
            ),
            CardRenderer(),
        )
        output_root = (
            args.output_root.resolve()
            if args.output_root is not None
            else root / ".buildlog" / "publishing_packages"
        )
        result = builder.build(
            args.run_id,
            output_root,
            reviewed=args.confirm_reviewed,
        )
    except BuildLogError as exc:
        print(f"BuildLog package failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("BuildLog package cancelled.", file=sys.stderr)
        return 130

    action = "Reused existing" if result.reused_existing else "Created"
    print(f"{action} local publishing package.")
    print(f"Package: {result.package_dir}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Caption: {result.caption_path}")
    print(f"Cards: {len(result.asset_paths)}")
    for path in result.asset_paths:
        print(f"  {path}")
    print("Review status: pending")
    print("Network publication occurred: no")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildlog package",
        description=(
            "Build a local, reviewable publishing package from one completed run."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser(
        "build",
        help="Plan and render one LinkedIn-targeted local package.",
    )
    build.add_argument("run_id")
    build.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="Confirm the source run received human content review.",
    )
    build.add_argument(
        "--output-root",
        type=Path,
        help="Override the default private .buildlog package directory.",
    )
    build.add_argument(
        "--prompt-version",
        default="v1",
        help="Asset planner prompt version (default: v1).",
    )
    return parser
