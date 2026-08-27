"""Programmatic database migration and revision verification."""

from __future__ import annotations

from pathlib import Path


def upgrade_database(database_url: str, config_path: Path) -> None:
    """Upgrade the configured database to the repository head revision."""
    from alembic import command

    command.upgrade(_config(database_url, config_path), "head")


def database_revision(database_url: str, config_path: Path) -> tuple[str | None, str]:
    """Return current and expected schema revisions."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    config = _config(database_url, config_path)
    expected = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    return current, expected


def verify_database_revision(database_url: str, config_path: Path) -> None:
    """Reject serving traffic from a database behind the application schema."""
    current, expected = database_revision(database_url, config_path)
    if current != expected:
        raise RuntimeError(
            f"database schema revision is {current or 'unversioned'}; expected {expected}"
        )


def _config(database_url: str, config_path: Path):
    from alembic.config import Config

    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config
