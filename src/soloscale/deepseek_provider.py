"""Canonical DeepSeek V4 provider capability and human-readable service state.

DeepSeek is a first-class AI provider, not an anonymous OpenAI-compatible endpoint. This
module owns one reusable identity: configuration, model selection, capability truth,
credential boundary, request adapter, status, and call receipts. It never performs an
automatic paid call, and it never silently falls back to another provider or model.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, model_validator

from soloscale.models import ContractModel

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)

DEEPSEEK_PROVIDER_ID = "deepseek"
DEEPSEEK_DISPLAY_NAME = "DeepSeek"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_RESPONSES_ENDPOINT = "https://api.deepseek.com/responses"
DEEPSEEK_MODELS_ENDPOINT = "https://api.deepseek.com/models"
DEEPSEEK_MODEL_IDS = ("deepseek-v4-flash", "deepseek-v4-pro")
DEEPSEEK_CONTEXT_LIMIT = 1_000_000
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_ERROR_BYTES = 64 * 1024


class DeepSeekReasoningEffort(StrEnum):
    LOW = "low"
    HIGH = "high"
    MAX = "max"


class DeepSeekStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED_NOT_TESTED = "configured_not_tested"
    READY = "ready"
    CONNECTION_FAILED = "connection_failed"


class DeepSeekErrorCategory(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    REQUEST_INVALID = "request_invalid"
    OUTPUT_LIMIT = "output_limit"
    MODEL_TIMEOUT = "model_timeout"


class DeepSeekProviderError(RuntimeError):
    """Sanitized DeepSeek failure. Credentials are never part of the message."""

    def __init__(self, message: str, *, category: DeepSeekErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class DeepSeekModelCapability(ContractModel):
    model_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    responses_api: Literal[True] = True
    json_output: Literal[True] = True
    tool_calling: Literal[True] = True
    thinking: Literal[True] = True
    reasoning_efforts: list[DeepSeekReasoningEffort] = Field(
        default_factory=lambda: [
            DeepSeekReasoningEffort.LOW,
            DeepSeekReasoningEffort.HIGH,
            DeepSeekReasoningEffort.MAX,
        ]
    )
    context_limit: int = Field(ge=1)


_DEEPSEEK_MODEL_CAPABILITIES: dict[str, DeepSeekModelCapability] = {
    "deepseek-v4-flash": DeepSeekModelCapability(
        model_id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        context_limit=DEEPSEEK_CONTEXT_LIMIT,
    ),
    "deepseek-v4-pro": DeepSeekModelCapability(
        model_id="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        context_limit=DEEPSEEK_CONTEXT_LIMIT,
    ),
}


def deepseek_model_capability(model_id: str) -> DeepSeekModelCapability:
    selected = _DEEPSEEK_MODEL_CAPABILITIES.get(model_id.strip())
    if selected is None:
        raise ValueError("unknown DeepSeek model id")
    return selected


def deepseek_display_name(model_id: str) -> str:
    return deepseek_model_capability(model_id).display_name


def normalize_reasoning_effort(value: object) -> DeepSeekReasoningEffort:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("DeepSeek reasoning effort is required")
    normalized = value.strip().casefold()
    try:
        return DeepSeekReasoningEffort(normalized)
    except ValueError as exc:
        raise ValueError(
            "DeepSeek reasoning effort must be one of low, high, max"
        ) from exc


class DeepSeekSettings(ContractModel):
    """Non-secret provider settings. The API key is never stored in this record."""

    provider_id: Literal["deepseek"] = "deepseek"
    base_url: str = DEEPSEEK_BASE_URL
    transport: Literal["responses"] = "responses"
    model_id: str = Field(min_length=1, max_length=120)
    reasoning_effort: DeepSeekReasoningEffort = DeepSeekReasoningEffort.HIGH
    thinking_enabled: bool = True
    api_key_configured: bool = False
    status: DeepSeekStatus = DeepSeekStatus.NOT_CONFIGURED

    @model_validator(mode="after")
    def validate_deepseek_settings(self) -> DeepSeekSettings:
        if self.model_id not in _DEEPSEEK_MODEL_CAPABILITIES:
            raise ValueError("unknown DeepSeek model id")
        if self.base_url != DEEPSEEK_BASE_URL:
            raise ValueError("DeepSeek base URL must remain canonical")
        if not self.api_key_configured and self.status is not DeepSeekStatus.NOT_CONFIGURED:
            raise ValueError("an unconfigured provider cannot claim a tested status")
        return self


def deepseek_settings_path(data_root: Path) -> Path:
    return Path(data_root) / "settings" / "deepseek-provider.json"


def load_deepseek_settings(
    data_root: Path, *, api_key_configured: bool
) -> DeepSeekSettings:
    path = deepseek_settings_path(data_root)
    if not path.exists():
        return DeepSeekSettings(
            model_id=DEEPSEEK_MODEL_IDS[0],
            api_key_configured=api_key_configured,
            status=(
                DeepSeekStatus.NOT_CONFIGURED
                if not api_key_configured
                else DeepSeekStatus.CONFIGURED_NOT_TESTED
            ),
        )
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("deepseek settings storage is unsafe")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("DeepSeek settings are unavailable") from exc
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek settings are unavailable")
    stored_status = payload.get("status")
    status = (
        DeepSeekStatus(stored_status)
        if isinstance(stored_status, str)
        else DeepSeekStatus.CONFIGURED_NOT_TESTED
    )
    if not api_key_configured:
        status = DeepSeekStatus.NOT_CONFIGURED
    elif status not in {DeepSeekStatus.READY, DeepSeekStatus.CONNECTION_FAILED}:
        status = DeepSeekStatus.CONFIGURED_NOT_TESTED
    settings = DeepSeekSettings(
        model_id=str(payload.get("model_id", DEEPSEEK_MODEL_IDS[0])),
        reasoning_effort=normalize_reasoning_effort(payload.get("reasoning_effort", "high")),
        thinking_enabled=(
            payload.get("thinking_enabled", True)
            if isinstance(payload.get("thinking_enabled"), bool)
            else True
        ),
        api_key_configured=api_key_configured,
        status=status,
    )
    return settings


def save_deepseek_settings(
    data_root: Path,
    *,
    model_id: str,
    reasoning_effort: DeepSeekReasoningEffort | str,
    thinking_enabled: bool,
    api_key_configured: bool,
    status: DeepSeekStatus | None = None,
) -> DeepSeekSettings:
    deepseek_model_capability(model_id)
    selected_effort = normalize_reasoning_effort(
        reasoning_effort.value
        if isinstance(reasoning_effort, DeepSeekReasoningEffort)
        else reasoning_effort
    )
    selected_status = (
        DeepSeekStatus.NOT_CONFIGURED
        if not api_key_configured
        else status or DeepSeekStatus.CONFIGURED_NOT_TESTED
    )
    settings = DeepSeekSettings(
        model_id=model_id,
        reasoning_effort=selected_effort,
        thinking_enabled=thinking_enabled,
        api_key_configured=api_key_configured,
        status=selected_status,
    )
    path = deepseek_settings_path(data_root)
    directory = path.parent
    _reject_symlink_ancestry(directory)
    directory.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
    os.chmod(directory, _DIRECTORY_MODE)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".deepseek-provider-", suffix=".tmp", dir=directory
    )
    temporary = Path(raw_temporary)
    try:
        os.chmod(temporary, _FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            payload = {
                "schema_version": "1.0",
                "provider_id": DEEPSEEK_PROVIDER_ID,
                "base_url": DEEPSEEK_BASE_URL,
                "transport": "responses",
                "model_id": settings.model_id,
                "reasoning_effort": settings.reasoning_effort.value,
                "thinking_enabled": settings.thinking_enabled,
                "status": settings.status.value,
            }
            json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, _FILE_MODE)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return settings


def _reject_symlink_ancestry(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("deepseek settings storage must not contain symlinks")
        current = current.parent


class DeepSeekResponsesRequest(ContractModel):
    correlation_id: str = Field(pattern=r"^deepseek-[a-f0-9]{24}$")
    endpoint: str = DEEPSEEK_RESPONSES_ENDPOINT
    model: str = Field(min_length=1, max_length=120)
    system: str = Field(min_length=1, max_length=200_000)
    user: str = Field(min_length=1, max_length=200_000)
    response_json_schema: dict[str, object]
    reasoning_effort: DeepSeekReasoningEffort
    thinking_enabled: bool
    max_output_tokens: int = Field(default=8_192, ge=1, le=64_000)
    timeout_seconds: int = Field(default=105, ge=1, le=120)

    @model_validator(mode="after")
    def validate_request(self) -> DeepSeekResponsesRequest:
        if self.model not in _DEEPSEEK_MODEL_CAPABILITIES:
            raise ValueError("unknown DeepSeek model id")
        if self.endpoint != DEEPSEEK_RESPONSES_ENDPOINT:
            raise ValueError("DeepSeek Responses endpoint must remain canonical")
        return self


class DeepSeekProviderResponse(ContractModel):
    content: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=200)


class DeepSeekTransport(Protocol):
    def send(self, request: DeepSeekResponsesRequest) -> DeepSeekProviderResponse: ...


class MockDeepSeekTransport:
    """In-memory scripted transport for bounded tests and local demos."""

    def __init__(self, response: DeepSeekProviderResponse) -> None:
        self.response = response
        self.requests: list[DeepSeekResponsesRequest] = []

    def send(self, request: DeepSeekResponsesRequest) -> DeepSeekProviderResponse:
        self.requests.append(request)
        return self.response


def deepseek_error_category(
    *,
    status: int | None,
    error_signal: str | None = None,
    timed_out: bool = False,
) -> DeepSeekErrorCategory:
    signal = (error_signal or "").casefold()
    if timed_out or status == 408 or "timeout" in signal:
        return DeepSeekErrorCategory.MODEL_TIMEOUT
    if status in {401, 403} or any(
        marker in signal
        for marker in ("authentication", "unauthorized", "invalid api key")
    ):
        return DeepSeekErrorCategory.AUTHENTICATION_FAILED
    if status == 429 or "rate" in signal:
        return DeepSeekErrorCategory.RATE_LIMITED
    if status == 404 or "model not found" in signal:
        return DeepSeekErrorCategory.MODEL_UNAVAILABLE
    if status == 400 or any(
        marker in signal
        for marker in ("invalid_request", "invalid json", "schema")
    ):
        return DeepSeekErrorCategory.REQUEST_INVALID
    if any(marker in signal for marker in ("output", "max_output", "context length")):
        return DeepSeekErrorCategory.OUTPUT_LIMIT
    if status is not None and status >= 500:
        return DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
    return DeepSeekErrorCategory.PROVIDER_UNAVAILABLE


class DeepSeekResponsesHTTPTransport:
    """Bounded Responses-API transport. The credential never leaves this object."""

    def __init__(self, credential: str) -> None:
        selected = credential.strip()
        if not selected:
            raise ValueError("DeepSeek credential must not be empty")
        self._credential = selected

    @staticmethod
    def _schema_name(schema: dict[str, object]) -> str:
        raw = str(schema.get("title", "soloscale_response"))
        normalized = "".join(
            character if character.isalnum() or character in "_-" else "_"
            for character in raw
        ).strip("_")
        return (normalized or "soloscale_response")[:64]

    def send(self, request: DeepSeekResponsesRequest) -> DeepSeekProviderResponse:
        body = json.dumps(
            {
                "model": request.model,
                "input": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "stream": False,
                "reasoning": {"effort": request.reasoning_effort.value},
                "max_output_tokens": request.max_output_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": self._schema_name(request.response_json_schema),
                        "schema": request.response_json_schema,
                        "strict": True,
                    }
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        outbound = urllib.request.Request(
            request.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._credential}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "SoloScale/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                outbound, timeout=request.timeout_seconds
            ) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            category = deepseek_error_category(status=exc.code)
            raise DeepSeekProviderError(
                "DeepSeek request failed", category=category
            ) from None
        except TimeoutError as exc:
            raise DeepSeekProviderError(
                "DeepSeek request timed out",
                category=DeepSeekErrorCategory.MODEL_TIMEOUT,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DeepSeekProviderError(
                "DeepSeek provider is unreachable",
                category=DeepSeekErrorCategory.PROVIDER_UNAVAILABLE,
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DeepSeekProviderError(
                "DeepSeek response exceeded the size limit",
                category=DeepSeekErrorCategory.OUTPUT_LIMIT,
            )
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeepSeekProviderError(
                "DeepSeek returned an invalid response envelope",
                category=DeepSeekErrorCategory.REQUEST_INVALID,
            ) from exc
        if not isinstance(envelope, dict):
            raise DeepSeekProviderError(
                "DeepSeek returned an invalid response envelope",
                category=DeepSeekErrorCategory.REQUEST_INVALID,
            )
        content = _first_output_text(envelope)
        usage = envelope.get("usage")
        input_tokens = output_tokens = cache_tokens = None
        if isinstance(usage, dict):
            input_tokens = _optional_usage_int(usage.get("input_tokens"))
            output_tokens = _optional_usage_int(usage.get("output_tokens"))
            details = usage.get("input_tokens_details")
            if isinstance(details, dict):
                cache_tokens = _optional_usage_int(
                    details.get("cached_tokens")
                ) or _optional_usage_int(details.get("cache_read_input_tokens"))
        request_id = envelope.get("id")
        return DeepSeekProviderResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            request_id=str(request_id) if isinstance(request_id, str) else None,
        )


def _first_output_text(envelope: Mapping[str, object]) -> str:
    output = envelope.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text = str(part["text"]).strip()
                        if text:
                            return text
    direct = envelope.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    raise DeepSeekProviderError(
        "DeepSeek returned an empty response",
        category=DeepSeekErrorCategory.REQUEST_INVALID,
    )


def _optional_usage_int(value: object) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    return None


class DeepSeekCallReceipt(ContractModel):
    """Body-free provider receipt; never contains credentials or response text."""

    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(min_length=1, max_length=120)
    reasoning_effort: DeepSeekReasoningEffort
    thinking_enabled: bool
    started_at: datetime
    completed_at: datetime | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    status: Literal["SUCCEEDED", "FAILED"]
    error_category: DeepSeekErrorCategory | None = None


class DeepSeekModelGateway:
    """Explicit DeepSeek gateway. No implicit fallback to another provider or model."""

    def __init__(
        self,
        *,
        settings: DeepSeekSettings,
        credential: str,
        transport: DeepSeekTransport | None = None,
    ) -> None:
        selected = credential.strip()
        if not selected:
            raise ValueError("DeepSeek credential must not be empty")
        if settings.status is DeepSeekStatus.NOT_CONFIGURED:
            raise DeepSeekProviderError(
                "DeepSeek is not configured",
                category=DeepSeekErrorCategory.AUTHENTICATION_FAILED,
            )
        self.settings = settings
        self._credential = selected
        self._transport = transport or DeepSeekResponsesHTTPTransport(selected)
        self.last_receipt: DeepSeekCallReceipt | None = None

    def complete(
        self,
        schema: type[ResponseModelT],
        *,
        system: str,
        user: str,
        reasoning_effort: DeepSeekReasoningEffort | str | None = None,
        thinking_enabled: bool | None = None,
    ) -> ResponseModelT:
        selected_effort = (
            normalize_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else self.settings.reasoning_effort
        )
        selected_thinking = (
            self.settings.thinking_enabled
            if thinking_enabled is None
            else thinking_enabled
        )
        request = DeepSeekResponsesRequest(
            correlation_id=f"deepseek-{secrets.token_hex(12)}",
            model=self.settings.model_id,
            system=system,
            user=user,
            response_json_schema=schema.model_json_schema(),
            reasoning_effort=selected_effort,
            thinking_enabled=selected_thinking,
        )
        started_at = datetime.now(UTC)
        wall_started = time.monotonic()
        try:
            response = self._transport.send(request)
            decoded = json.loads(response.content)
            result = schema.model_validate(decoded)
        except DeepSeekProviderError as exc:
            self.last_receipt = DeepSeekCallReceipt(
                model=self.settings.model_id,
                reasoning_effort=selected_effort,
                thinking_enabled=selected_thinking,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                latency_ms=max(0, int((time.monotonic() - wall_started) * 1000)),
                status="FAILED",
                error_category=exc.category,
            )
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.last_receipt = DeepSeekCallReceipt(
                model=self.settings.model_id,
                reasoning_effort=selected_effort,
                thinking_enabled=selected_thinking,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                latency_ms=max(0, int((time.monotonic() - wall_started) * 1000)),
                status="FAILED",
                error_category=DeepSeekErrorCategory.REQUEST_INVALID,
            )
            raise DeepSeekProviderError(
                "DeepSeek returned output outside the required schema",
                category=DeepSeekErrorCategory.REQUEST_INVALID,
            ) from exc
        self.last_receipt = DeepSeekCallReceipt(
            model=self.settings.model_id,
            reasoning_effort=selected_effort,
            thinking_enabled=selected_thinking,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            latency_ms=max(0, int((time.monotonic() - wall_started) * 1000)),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_tokens=response.cache_tokens,
            status="SUCCEEDED",
        )
        return result


def check_deepseek_connection(
    data_root: Path,
    *,
    credential: str | None = None,
    opener: object | None = None,
) -> tuple[DeepSeekStatus, DeepSeekErrorCategory | None]:
    """Explicit free capability check (GET /models). Never automatic at startup."""

    from soloscale.desktop_credentials import deepseek_api_key

    selected = (credential if credential is not None else deepseek_api_key())
    if selected is None or not selected.strip():
        return DeepSeekStatus.NOT_CONFIGURED, None
    current = load_deepseek_settings(data_root, api_key_configured=True)
    direct_opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
    ).open
    selected_opener = direct_opener if opener is None else opener
    request = urllib.request.Request(
        DEEPSEEK_MODELS_ENDPOINT,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {selected}",
        },
    )
    try:
        with selected_opener(request, timeout=8) as response:  # type: ignore[operator]
            status = int(getattr(response, "status", 200))
            response.read(1)
    except urllib.error.HTTPError as exc:
        category = deepseek_error_category(status=exc.code)
        save_deepseek_settings(
            data_root,
            model_id=current.model_id,
            reasoning_effort=current.reasoning_effort,
            thinking_enabled=current.thinking_enabled,
            api_key_configured=True,
            status=DeepSeekStatus.CONNECTION_FAILED,
        )
        return DeepSeekStatus.CONNECTION_FAILED, category
    except (OSError, TimeoutError, TypeError, urllib.error.URLError):
        save_deepseek_settings(
            data_root,
            model_id=current.model_id,
            reasoning_effort=current.reasoning_effort,
            thinking_enabled=current.thinking_enabled,
            api_key_configured=True,
            status=DeepSeekStatus.CONNECTION_FAILED,
        )
        return DeepSeekStatus.CONNECTION_FAILED, DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
    ready = 200 <= status < 300
    save_deepseek_settings(
        data_root,
        model_id=current.model_id,
        reasoning_effort=current.reasoning_effort,
        thinking_enabled=current.thinking_enabled,
        api_key_configured=True,
        status=DeepSeekStatus.READY if ready else DeepSeekStatus.CONNECTION_FAILED,
    )
    if ready:
        return DeepSeekStatus.READY, None
    return DeepSeekStatus.CONNECTION_FAILED, DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
