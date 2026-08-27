"""Container startup sequence: migrate, verify, then replace with the web server."""

from __future__ import annotations

import os
from pathlib import Path

from buildlog.config import load_settings
from buildlog.migration import upgrade_database, verify_database_revision


def main() -> None:
    root = Path.cwd()
    settings = load_settings(root)
    config_path = Path(os.getenv("BUILDLOG_ALEMBIC_CONFIG", root / "alembic.ini"))
    if settings.schema_management == "migrations":
        upgrade_database(settings.database_url, config_path)
        verify_database_revision(settings.database_url, config_path)
    os.execvp(
        "buildlog",
        ["buildlog", "web", "--host", "0.0.0.0", "--port", "8000"],
    )


if __name__ == "__main__":
    main()
