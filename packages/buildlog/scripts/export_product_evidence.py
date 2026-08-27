#!/usr/bin/env python3
"""Export resume-safe product evidence from persisted BuildLog records."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from buildlog.config import load_settings
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.web_repository import SQLAlchemyWebRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--benchmark", type=Path)
    parser.add_argument("--test-count", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(args.project_root)
    run_repository = SQLAlchemyRunRepository(settings.database_url)
    web_repository = SQLAlchemyWebRepository(run_repository.engine)
    dashboard = web_repository.dashboard()
    benchmark = None
    if args.benchmark and args.benchmark.exists():
        benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_database": str(settings.database_url),
        "dashboard": dashboard.model_dump(mode="json"),
        "automated_tests": args.test_count,
        "http_benchmark": benchmark,
        "claim_rules": [
            "Persisted counts are point-in-time product evidence, not customer scale.",
            "HTTP benchmark numbers describe only the recorded host and workload.",
            "Cloud deployment is a verified claim only after a hosted smoke test succeeds.",
            "No revenue, user adoption, or time-saved metric is inferred without measurement.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
