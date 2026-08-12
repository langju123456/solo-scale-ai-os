"""Command-line entry point for BuildLog."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from buildlog.config import load_settings
from buildlog.exceptions import BuildLogError
from buildlog.linkedin_cli import linkedin_main
from buildlog.package_cli import package_main
from buildlog.pipeline import run_pipeline
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.x_cli import x_main


def main(argv: list[str] | None = None) -> int:
    """Run BuildLog from the command line."""
    command_args = list(argv) if argv is not None else sys.argv[1:]
    if command_args and command_args[0] == "linkedin":
        return linkedin_main(command_args[1:])
    if command_args and command_args[0] == "package":
        return package_main(command_args[1:])
    if command_args and command_args[0] == "x":
        return x_main(command_args[1:])
    if command_args and command_args[0] == "external":
        from buildlog.external_cli import external_main

        return external_main(command_args[1:])
    if command_args and command_args[0] == "web":
        from buildlog.web_cli import web_main

        return web_main(command_args[1:])
    if command_args and command_args[0] == "database":
        from buildlog.database_cli import database_main

        return database_main(command_args[1:])

    parser = argparse.ArgumentParser(
        description="Generate a LinkedIn draft from one iteration JSON file.",
        epilog=(
            "LinkedIn authentication and publishing: "
            "buildlog linkedin --help. Local publishing packages: "
            "buildlog package --help. X authentication and publishing: "
            "buildlog x --help. Hosted internal product: buildlog web --help"
        ),
    )
    parser.add_argument("input_path", type=Path)
    args = parser.parse_args(command_args)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(Path.cwd())

    try:
        repository = SQLAlchemyRunRepository(settings.database_url)
        repository.initialize()
        result = run_pipeline(args.input_path, settings, repository)
    except BuildLogError as exc:
        print(f"BuildLog failed: {exc}", file=sys.stderr)
        return 1

    print("BuildLog completed.\n")
    print("Run:")
    print(result.run_dir)
    print("\nFinal draft:")
    print(result.final_path)
    print("\nEvaluation:")
    for name, score in result.evaluation_scores.items():
        print(f"{name}: {score}")
    print(f"\nRevision performed: {'yes' if result.revision_performed else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
