"""Command-line launcher for the hosted internal application."""

from __future__ import annotations

import argparse
import logging


def web_main(argv: list[str] | None = None) -> int:
    """Launch the FastAPI application with production-oriented defaults."""
    parser = argparse.ArgumentParser(description="Run the BuildLog internal web product.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    uvicorn.run(
        "buildlog.web_app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0
