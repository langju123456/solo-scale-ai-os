"""HTTP contract tests for the private full-stack application."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buildlog.config import Settings
from buildlog.web_app import create_app


def _settings(tmp_path: Path, *, api_key: str | None = None) -> Settings:
    return Settings(
        model="test-model",
        model_digest=None,
        api_base=None,
        temperature=0.0,
        max_tokens=100,
        threshold_accuracy=8,
        threshold_specificity=7,
        threshold_readability=7,
        threshold_value=7,
        threshold_evidence=7,
        prompt_version="v1",
        prompts_dir=tmp_path / "prompts",
        runs_dir=tmp_path / "runs",
        database_url=f"sqlite:///{tmp_path / 'buildlog.db'}",
        web_api_key=api_key,
        web_worker_enabled=False,
        web_jobs_dir=tmp_path / "jobs",
    )


def _iteration(title: str = "Hosted workflow") -> dict[str, object]:
    return {
        "id": "hosted-workflow-001",
        "title": title,
        "goal": "Expose one reviewed workflow through an internal application.",
        "context": "The existing product was available only through a local CLI.",
        "problem": "GTM users could not inspect or submit work through a browser.",
        "actions": ["Added an authenticated API", "Added a durable workflow queue"],
        "decisions": [
            {
                "decision": "Use a modular monolith",
                "reason": "It preserves transactional simplicity at current scale.",
                "alternatives_considered": ["Independent microservices"],
            }
        ],
        "trade_offs": ["A single worker limits throughput but simplifies recovery."],
        "result": "The workflow can be submitted and observed through HTTP.",
        "lessons": ["Scale architecture only after measuring the bottleneck."],
        "evidence": ["API contract tests", "Persisted idempotent job record"],
        "audience": "GTM engineering leaders",
        "metadata": {"project_id": "buildlog-web"},
    }


def test_health_ui_and_security_headers_are_public(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, api_key="a" * 24), worker_enabled=False)
    with TestClient(app) as client:
        health = client.get("/health/ready")
        page = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ready"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert page.status_code == 200
    assert "Internal AI Operations" in page.text


def test_dashboard_requires_configured_api_key(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, api_key="b" * 24), worker_enabled=False)
    with TestClient(app) as client:
        denied = client.get("/api/v1/dashboard")
        allowed = client.get(
            "/api/v1/dashboard",
            headers={"Authorization": f"Bearer {'b' * 24}"},
        )
        metrics_denied = client.get("/metrics")
        metrics_allowed = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {'b' * 24}"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["total_runs"] == 0
    assert metrics_denied.status_code == 401
    assert metrics_allowed.status_code == 200


def test_job_submission_is_idempotent_and_queryable(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), worker_enabled=False)
    headers = {"Idempotency-Key": "workflow-request-001"}
    with TestClient(app) as client:
        first = client.post("/api/v1/jobs", headers=headers, json=_iteration())
        replay = client.post("/api/v1/jobs", headers=headers, json=_iteration())
        jobs = client.get("/api/v1/jobs")

    assert first.status_code == 202
    assert first.json()["created"] is True
    assert replay.status_code == 202
    assert replay.json()["created"] is False
    assert replay.json()["job"]["id"] == first.json()["job"]["id"]
    assert len(jobs.json()) == 1
    assert jobs.json()[0]["status"] == "queued"


def test_idempotency_key_reuse_with_different_payload_conflicts(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), worker_enabled=False)
    headers = {"Idempotency-Key": "workflow-request-002"}
    with TestClient(app) as client:
        assert client.post("/api/v1/jobs", headers=headers, json=_iteration()).status_code == 202
        conflict = client.post(
            "/api/v1/jobs",
            headers=headers,
            json=_iteration("Different workflow"),
        )

    assert conflict.status_code == 409
    assert "different request payload" in conflict.json()["detail"]


def test_job_submission_validates_contract_and_idempotency_key(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), worker_enabled=False)
    with TestClient(app) as client:
        missing_key = client.post("/api/v1/jobs", json=_iteration())
        invalid_body = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "valid-key-001"},
            json={"title": "missing required evidence"},
        )

    assert missing_key.status_code == 400
    assert invalid_body.status_code == 422


def test_metrics_and_openapi_expose_operational_contracts(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), worker_enabled=False)
    with TestClient(app) as client:
        client.get("/health/live")
        metrics = client.get("/metrics")
        schema = client.get("/openapi.json")

    assert metrics.status_code == 200
    assert "buildlog_http_requests_total" in metrics.text
    assert "/api/v1/jobs" in schema.json()["paths"]
    assert "/health/ready" in schema.json()["paths"]


def test_production_requires_a_strong_api_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    production = Settings(**{**settings.__dict__, "environment": "production"})
    with pytest.raises(RuntimeError, match="required in production"):
        create_app(production, worker_enabled=False)

    weak = Settings(
        **{**settings.__dict__, "environment": "production", "web_api_key": "weak"}
    )
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(weak, worker_enabled=False)


def test_azure_authenticated_identity_can_use_internal_api(tmp_path: Path) -> None:
    base = _settings(tmp_path, api_key="c" * 24)
    settings = Settings(**{**base.__dict__, "trust_azure_auth": True})
    app = create_app(settings, worker_enabled=False)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/dashboard",
            headers={"X-MS-CLIENT-PRINCIPAL-ID": "entra-user-001"},
        )
    assert response.status_code == 200


def test_api_rate_limit_returns_retry_metadata(tmp_path: Path) -> None:
    base = _settings(tmp_path)
    settings = Settings(**{**base.__dict__, "web_rate_limit_per_minute": 1})
    app = create_app(settings, worker_enabled=False)
    with TestClient(app) as client:
        first = client.get("/api/v1/dashboard")
        limited = client.get("/api/v1/dashboard")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-remaining"] == "0"
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_untrusted_azure_header_cannot_bypass_api_key_rate_limit(
    tmp_path: Path,
) -> None:
    base = _settings(tmp_path, api_key="d" * 24)
    settings = Settings(**{**base.__dict__, "web_rate_limit_per_minute": 1})
    app = create_app(settings, worker_enabled=False)
    authorization = {"Authorization": f"Bearer {'d' * 24}"}
    with TestClient(app) as client:
        first = client.get(
            "/api/v1/dashboard",
            headers={**authorization, "X-MS-CLIENT-PRINCIPAL-ID": "forged-1"},
        )
        limited = client.get(
            "/api/v1/dashboard",
            headers={**authorization, "X-MS-CLIENT-PRINCIPAL-ID": "forged-2"},
        )

    assert first.status_code == 200
    assert limited.status_code == 429
