"""CLI boundary for staging SoloScale artifacts into BuildLog publication controls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from buildlog.config import load_settings
from buildlog.external_artifact import ExternalArtifactError, latest_publication_receipt, stage_soloscale_artifact
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


def external_main(argv: list[str], *, project_root: Path | None = None) -> int:
    root = project_root or Path.cwd()
    parser = argparse.ArgumentParser(prog="buildlog external")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage")
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--source-run-id", required=True)
    stage.add_argument("--channel", choices=("linkedin", "x"), required=True)
    receipt = subparsers.add_parser("receipt")
    receipt.add_argument("run_id")
    args = parser.parse_args(argv)
    settings = load_settings(root)
    try:
        if args.command == "stage":
            repository = SQLAlchemyRunRepository(settings.database_url)
            repository.initialize()
            run_id = stage_soloscale_artifact(
                settings=settings,
                repository=repository,
                source_path=args.source,
                source_run_id=args.source_run_id,
                channel=args.channel,
            )
            print(json.dumps({"buildlog_run_id": run_id}))
            return 0
        result = latest_publication_receipt(settings=settings, run_id=args.run_id)
        print(json.dumps(result or {"buildlog_run_id": args.run_id, "receipt": None}))
        return 0
    except ExternalArtifactError as exc:
        print(f"BuildLog external failed: {exc}")
        return 1
