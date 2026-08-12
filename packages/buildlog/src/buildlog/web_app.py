"""FastAPI application exposing BuildLog as a private internal AI product."""

from __future__ import annotations

import logging
import re
import time
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from buildlog.config import Settings, load_settings
from buildlog.models import Iteration
from buildlog.migration import verify_database_revision
from buildlog.pipeline import run_pipeline
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository
from buildlog.web_models import APIMessage, DashboardMetrics, JobAccepted, RunDetail, RunSummary, WorkflowJob
from buildlog.web_repository import IdempotencyConflict, SQLAlchemyWebRepository, WebPersistenceError
from buildlog.web_security import APIKeyAuthorizer
from buildlog.web_worker import PipelineRunner, WorkflowWorker
from buildlog.web_rate_limit import SlidingWindowRateLimiter

LOGGER = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def create_app(
    settings: Settings | None = None,
    *,
    worker_enabled: bool | None = None,
    pipeline_runner: PipelineRunner = run_pipeline,
) -> FastAPI:
    """Create an application with explicit configuration and replaceable worker I/O."""
    runtime = settings or load_settings(Path.cwd())
    should_run_worker = (
        runtime.web_worker_enabled if worker_enabled is None else worker_enabled
    )
    if runtime.environment == "production" and not runtime.web_api_key:
        raise RuntimeError("BUILDLOG_WEB_API_KEY is required in production")
    if runtime.web_api_key is not None and len(runtime.web_api_key) < 24:
        raise RuntimeError("BUILDLOG_WEB_API_KEY must contain at least 24 characters")

    run_repository = SQLAlchemyRunRepository(runtime.database_url)
    if runtime.schema_management == "migrations":
        config_path = Path.cwd() / "alembic.ini"
        verify_database_revision(runtime.database_url, config_path)
    else:
        run_repository.initialize()
    web_repository = SQLAlchemyWebRepository(run_repository.engine)
    worker = WorkflowWorker(
        runtime,
        run_repository,
        web_repository,
        pipeline_runner=pipeline_runner,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if should_run_worker:
            await worker.start()
        yield
        if should_run_worker:
            await worker.stop()

    app = FastAPI(
        title="BuildLog Internal AI Product",
        version=_package_version(),
        description="Private evidence-to-GTM workflow, run portfolio, and operational dashboard.",
        lifespan=lifespan,
    )
    app.state.settings = runtime
    app.state.run_repository = run_repository
    app.state.web_repository = web_repository
    app.state.worker = worker

    registry = CollectorRegistry()
    request_count = Counter(
        "buildlog_http_requests_total",
        "HTTP requests completed by route and status.",
        ("method", "route", "status"),
        registry=registry,
    )
    request_latency = Histogram(
        "buildlog_http_request_duration_seconds",
        "HTTP request latency by route.",
        ("method", "route"),
        registry=registry,
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    )
    rate_limiter = SlidingWindowRateLimiter(runtime.web_rate_limit_per_minute)

    @app.middleware("http")
    async def operational_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            request_id = str(uuid4())
        started = time.perf_counter()
        remaining = None
        reset_seconds = None
        if request.url.path.startswith("/api/"):
            allowed, remaining, reset_seconds = rate_limiter.check(
                _rate_limit_identity(
                    request,
                    trust_azure_auth=runtime.trust_azure_auth,
                )
            )
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "request rate limit exceeded"},
                    headers={"Retry-After": str(reset_seconds)},
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - started
        request_count.labels(request.method, route_path, response.status_code).inc()
        request_latency.labels(request.method, route_path).observe(elapsed)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'"
        )
        if remaining is not None:
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_seconds)
        LOGGER.info(
            "request_complete method=%s route=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            route_path,
            response.status_code,
            round(elapsed * 1000, 2),
            request_id,
        )
        return response

    @app.exception_handler(WebPersistenceError)
    async def persistence_error_handler(_request: Request, exc: WebPersistenceError):
        status_code = 409 if isinstance(exc, IdempotencyConflict) else 503
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.get("/health/live", response_model=APIMessage, tags=["operations"])
    async def liveness() -> APIMessage:
        return APIMessage(status="ok", version=app.version)

    @app.get("/health/ready", response_model=APIMessage, tags=["operations"])
    def readiness() -> APIMessage:
        try:
            web_repository.ping()
        except WebPersistenceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return APIMessage(
            status="ready",
            version=app.version,
            details={"worker_enabled": should_run_worker},
        )

    authorizer = APIKeyAuthorizer(
        runtime.web_api_key,
        trust_azure_auth=runtime.trust_azure_auth,
    )

    @app.get(
        "/metrics",
        include_in_schema=False,
        dependencies=[Depends(authorizer)],
    )
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authorizer)])

    @api.get("/dashboard", response_model=DashboardMetrics)
    def dashboard() -> DashboardMetrics:
        return web_repository.dashboard()

    @api.get("/runs", response_model=list[RunSummary])
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        status: Annotated[str | None, Query(pattern="^(running|completed|failed)$")] = None,
    ) -> list[RunSummary]:
        return web_repository.list_runs(limit=limit, status=status)

    @api.get("/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str) -> RunDetail:
        result = web_repository.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
        return result

    @api.post("/jobs", response_model=JobAccepted, status_code=202)
    def create_job(
        iteration: Iteration,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobAccepted:
        normalized_key = (idempotency_key or "").strip()
        if not 8 <= len(normalized_key) <= 128:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 8 to 128 characters",
            )
        created = web_repository.create_job(
            input_payload=iteration.model_dump(mode="json"),
            idempotency_key=normalized_key,
        )
        return JobAccepted(job=created.job, created=created.created)

    @api.get("/jobs", response_model=list[WorkflowJob])
    def list_jobs(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[WorkflowJob]:
        return web_repository.list_jobs(limit=limit)

    @api.get("/jobs/{job_id}", response_model=WorkflowJob)
    def get_job(job_id: str) -> WorkflowJob:
        result = web_repository.get_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return result

    app.include_router(api)

    static_dir = Path(__file__).with_name("web_static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def _package_version() -> str:
    try:
        return version("buildlog")
    except PackageNotFoundError:
        return "0.0.0"


def _rate_limit_identity(request: Request, *, trust_azure_auth: bool) -> str:
    if trust_azure_auth:
        principal = request.headers.get("x-ms-client-principal-id", "").strip()
        if principal:
            return f"principal:{principal}"
    authorization = request.headers.get("authorization", "")
    api_key = request.headers.get("x-buildlog-key", "")
    credential = authorization or api_key
    if credential:
        digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:24]
        return f"credential:{digest}"
    host = request.client.host if request.client else "unknown"
    return f"client:{host}"
