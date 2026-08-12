"""Schema migration tests for fresh hosted databases."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from buildlog.migration import database_revision, upgrade_database, verify_database_revision


def test_initial_migration_builds_and_versions_complete_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config_path = Path(__file__).parents[1] / "alembic.ini"

    upgrade_database(database_url, config_path)
    current, expected = database_revision(database_url, config_path)
    verify_database_revision(database_url, config_path)

    assert current == expected
    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {
        "alembic_version",
        "runs",
        "artifacts",
        "workflow_jobs",
        "publish_receipts",
        "run_observations",
    }.issubset(tables)
