#!/usr/bin/env python3
"""Measure one BuildLog read path over HTTP or an in-process ASGI transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/api/v1/dashboard")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--asgi",
        action="store_true",
        help="Benchmark the application in-process without binding a network port.",
    )
    return parser.parse_args()


async def benchmark(args: argparse.Namespace) -> dict[str, object]:
    api_key = os.getenv("BUILDLOG_WEB_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(args.timeout)
    url = f"{args.base_url.rstrip('/')}{args.path}"
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    errors: list[str] = []
    semaphore = asyncio.Semaphore(args.concurrency)

    transport = None
    if args.asgi:
        from buildlog.web_app import create_app

        transport = httpx.ASGITransport(app=create_app(worker_enabled=False))
        url = f"http://buildlog.test{args.path}"
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        transport=transport,
    ) as client:
        for _ in range(args.warmup):
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        async def one_request() -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get(url, headers=headers)
                    statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    latencies.append((time.perf_counter() - started) * 1000)

        wall_started = time.perf_counter()
        await asyncio.gather(*(one_request() for _ in range(args.requests)))
        wall_seconds = time.perf_counter() - wall_started

    successes = sum(count for status, count in statuses.items() if status < 400)
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "target": url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "warmup_requests": args.warmup,
        "wall_seconds": round(wall_seconds, 4),
        "requests_per_second": round(args.requests / wall_seconds, 2),
        "successful_requests": successes,
        "error_rate_percent": round((args.requests - successes) / args.requests * 100, 3),
        "status_counts": {str(key): value for key, value in sorted(statuses.items())},
        "exception_counts": {
            name: errors.count(name) for name in sorted(set(errors))
        },
        "latency_ms": {
            "min": round(min(latencies), 2),
            "p50": round(_percentile(latencies, 50), 2),
            "p95": round(_percentile(latencies, 95), 2),
            "p99": round(_percentile(latencies, 99), 2),
            "max": round(max(latencies), 2),
        },
        "scope": (
            "in-process ASGI read benchmark; excludes network and container overhead"
            if args.asgi
            else "single-node HTTP read benchmark; not a production capacity claim"
        ),
    }


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> None:
    args = parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.warmup < 0:
        raise SystemExit("requests/concurrency must be positive and warmup non-negative")
    result = asyncio.run(benchmark(args))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
