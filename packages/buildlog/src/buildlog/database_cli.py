"""Database lifecycle commands used by operators and deployment automation."""

from __future__ import annotations

import argparse
from pathlib import Path

from buildlog.config import load_settings
from buildlog.migration import database_revision, upgrade_database


def database_main(argv: list[str] | None = None) -> int:
    """Apply or inspect schema migrations without starting the web process."""
    parser = argparse.ArgumentParser(description="Manage the BuildLog database schema.")
    parser.add_argument("command", choices=("upgrade", "status"))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("alembic.ini"),
        help="Path to alembic.ini (default: ./alembic.ini)",
    )
    args = parser.parse_args(argv)
    settings = load_settings(Path.cwd())
    if args.command == "upgrade":
        upgrade_database(settings.database_url, args.config)
        print("Database schema upgraded to head.")
        return 0
    current, expected = database_revision(settings.database_url, args.config)
    print(f"Current revision: {current or 'unversioned'}")
    print(f"Expected revision: {expected}")
    return 0 if current == expected else 1
