"""Environment-backed configuration for BuildLog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the BuildLog pipeline."""

    model: str
    model_digest: str | None
    api_base: str | None
    temperature: float
    max_tokens: int
    threshold_accuracy: int
    threshold_specificity: int
    threshold_readability: int
    threshold_value: int
    threshold_evidence: int
    prompt_version: str
    prompts_dir: Path
    runs_dir: Path
    database_url: str
    environment: str = "development"
    capture_failed_structured_output: bool = False
    web_api_key: str | None = None
    web_worker_enabled: bool = True
    web_worker_poll_seconds: float = 1.0
    web_job_stale_seconds: int = 900
    web_job_max_attempts: int = 2
    web_jobs_dir: Path | None = None
    web_rate_limit_per_minute: int = 120
    trust_azure_auth: bool = False
    schema_management: str = "create_all"
    object_store_backend: str = "none"
    azure_storage_account_url: str | None = None
    azure_storage_connection_string: str | None = None
    azure_storage_container: str = "buildlog-artifacts"


def load_settings(project_root: Path | None = None) -> Settings:
    """Load settings from environment variables and defaults."""
    import os

    root = project_root or Path.cwd()
    load_dotenv(root / ".env")
    prompts_dir = Path(os.getenv("BUILDLOG_PROMPTS_DIR", root / "prompts"))
    runs_dir = Path(os.getenv("BUILDLOG_RUNS_DIR", root / "runs"))
    jobs_dir = Path(os.getenv("BUILDLOG_WEB_JOBS_DIR", root / ".buildlog" / "jobs"))
    return Settings(
        model=os.getenv("BUILDLOG_MODEL", "ollama_chat/qwen3"),
        model_digest=os.getenv("BUILDLOG_MODEL_DIGEST") or None,
        api_base=os.getenv("BUILDLOG_API_BASE", "http://127.0.0.1:11434"),
        temperature=float(os.getenv("BUILDLOG_TEMPERATURE", "0.4")),
        max_tokens=int(os.getenv("BUILDLOG_MAX_TOKENS", "2200")),
        threshold_accuracy=int(os.getenv("BUILDLOG_EVAL_THRESHOLD_ACCURACY", "8")),
        threshold_specificity=int(os.getenv("BUILDLOG_EVAL_THRESHOLD_SPECIFICITY", "7")),
        threshold_readability=int(os.getenv("BUILDLOG_EVAL_THRESHOLD_READABILITY", "7")),
        threshold_value=int(os.getenv("BUILDLOG_EVAL_THRESHOLD_VALUE", "7")),
        threshold_evidence=int(os.getenv("BUILDLOG_EVAL_THRESHOLD_EVIDENCE", "7")),
        prompt_version=os.getenv("BUILDLOG_PROMPT_VERSION", "v1"),
        prompts_dir=prompts_dir,
        runs_dir=runs_dir,
        database_url=os.getenv("BUILDLOG_DATABASE_URL", f"sqlite:///{root / 'buildlog.db'}"),
        environment=os.getenv("BUILDLOG_ENV", "development").strip().lower(),
        capture_failed_structured_output=_env_bool(
            os.getenv("BUILDLOG_CAPTURE_FAILED_STRUCTURED_OUTPUT", "false")
        ),
        web_api_key=os.getenv("BUILDLOG_WEB_API_KEY") or None,
        web_worker_enabled=_env_bool(os.getenv("BUILDLOG_WEB_WORKER_ENABLED", "true")),
        web_worker_poll_seconds=float(
            os.getenv("BUILDLOG_WEB_WORKER_POLL_SECONDS", "1.0")
        ),
        web_job_stale_seconds=int(
            os.getenv("BUILDLOG_WEB_JOB_STALE_SECONDS", "900")
        ),
        web_job_max_attempts=int(os.getenv("BUILDLOG_WEB_JOB_MAX_ATTEMPTS", "2")),
        web_jobs_dir=jobs_dir,
        web_rate_limit_per_minute=int(
            os.getenv("BUILDLOG_WEB_RATE_LIMIT_PER_MINUTE", "120")
        ),
        trust_azure_auth=_env_bool(os.getenv("BUILDLOG_TRUST_AZURE_AUTH", "false")),
        schema_management=os.getenv("BUILDLOG_SCHEMA_MANAGEMENT", "create_all")
        .strip()
        .lower(),
        object_store_backend=os.getenv("BUILDLOG_OBJECT_STORE_BACKEND", "none")
        .strip()
        .lower(),
        azure_storage_account_url=os.getenv("BUILDLOG_AZURE_STORAGE_ACCOUNT_URL")
        or None,
        azure_storage_connection_string=os.getenv(
            "BUILDLOG_AZURE_STORAGE_CONNECTION_STRING"
        )
        or None,
        azure_storage_container=os.getenv(
            "BUILDLOG_AZURE_STORAGE_CONTAINER", "buildlog-artifacts"
        ),
    )


def _env_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value}")
