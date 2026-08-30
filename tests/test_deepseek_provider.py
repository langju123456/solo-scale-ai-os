"""Focused tests for the canonical DeepSeek V4 provider."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from pydantic import BaseModel

from soloscale.deepseek_provider import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_IDS,
    DEEPSEEK_PROVIDER_ID,
    DeepSeekErrorCategory,
    DeepSeekModelGateway,
    DeepSeekProviderError,
    DeepSeekProviderResponse,
    DeepSeekReasoningEffort,
    DeepSeekResponsesHTTPTransport,
    DeepSeekResponsesRequest,
    DeepSeekSettings,
    DeepSeekStatus,
    MockDeepSeekTransport,
    check_deepseek_connection,
    deepseek_display_name,
    deepseek_error_category,
    deepseek_model_capability,
    load_deepseek_settings,
    normalize_reasoning_effort,
    save_deepseek_settings,
)
from soloscale.desktop_credentials import _clear_for_tests
from soloscale.local_ui import (
    _ai_settings_notice,
    _ai_settings_page,
    _apply_deepseek_settings_action,
    _load_ai_provider_preference,
    _save_ai_provider_preference,
)
from soloscale.model_gateway import (
    GatewayConfigurationState,
    ModelProviderId,
    model_gateway_for,
)


class _Answer(BaseModel):
    text: str


class _FakeModelsResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def read(self, size: int = -1) -> bytes:
        del size
        return b"{}"

    def __enter__(self) -> _FakeModelsResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _FakeOpener:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[object] = []

    def __call__(self, request: object, timeout: int = 8) -> _FakeModelsResponse:
        del timeout
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return _FakeModelsResponse(self.status)


class _FailingDeepSeekTransport:
    def __init__(self, category: DeepSeekErrorCategory) -> None:
        self.category = category
        self.requests: list[DeepSeekResponsesRequest] = []

    def send(self, request: DeepSeekResponsesRequest) -> DeepSeekProviderResponse:
        self.requests.append(request)
        raise DeepSeekProviderError("provider unavailable", category=self.category)


def _settings_form(
    action: str,
    *,
    model: str = "deepseek-v4-pro",
    effort: str = "high",
    thinking: bool = True,
) -> dict[str, str]:
    return {
        "action": action,
        "deepseek_model": model,
        "deepseek_reasoning_effort": effort,
        "deepseek_thinking": "true" if thinking else "",
    }


def _settings(
    *,
    model_id: str = DEEPSEEK_MODEL_IDS[0],
    effort: DeepSeekReasoningEffort = DeepSeekReasoningEffort.HIGH,
    thinking: bool = True,
) -> DeepSeekSettings:
    return DeepSeekSettings(
        model_id=model_id,
        reasoning_effort=effort,
        thinking_enabled=thinking,
        api_key_configured=True,
        status=DeepSeekStatus.READY,
    )


def test_provider_registration_and_exact_model_ids() -> None:
    assert ModelProviderId.DEEPSEEK.value == DEEPSEEK_PROVIDER_ID == "deepseek"
    assert set(DEEPSEEK_MODEL_IDS) == {"deepseek-v4-flash", "deepseek-v4-pro"}


def test_display_names_and_capability_truth() -> None:
    assert deepseek_display_name("deepseek-v4-flash") == "DeepSeek V4 Flash"
    assert deepseek_display_name("deepseek-v4-pro") == "DeepSeek V4 Pro"
    capability = deepseek_model_capability("deepseek-v4-pro")
    assert capability.responses_api is True
    assert capability.json_output is True
    assert capability.tool_calling is True
    assert {effort.value for effort in capability.reasoning_efforts} == {
        "low",
        "high",
        "max",
    }
    assert capability.context_limit == 1_000_000


def test_responses_transport_configuration_and_base_url() -> None:
    settings = _settings()
    assert settings.transport == "responses"
    assert settings.base_url == DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    request = DeepSeekResponsesRequest(
        correlation_id=f"deepseek-{'a' * 24}",
        model=DEEPSEEK_MODEL_IDS[0],
        system="system",
        user="user",
        response_json_schema={},
        reasoning_effort=DeepSeekReasoningEffort.HIGH,
        thinking_enabled=True,
    )
    assert request.endpoint == "https://api.deepseek.com/responses"


def test_reasoning_effort_low_high_max_and_alias_rejection() -> None:
    assert normalize_reasoning_effort("low") is DeepSeekReasoningEffort.LOW
    assert normalize_reasoning_effort("HIGH") is DeepSeekReasoningEffort.HIGH
    assert normalize_reasoning_effort("Max") is DeepSeekReasoningEffort.MAX
    with pytest.raises(ValueError):
        normalize_reasoning_effort("medium")
    with pytest.raises(ValueError):
        normalize_reasoning_effort("xhigh")


def test_settings_never_contain_the_api_key(tmp_path: Path) -> None:
    save_deepseek_settings(
        tmp_path,
        model_id=DEEPSEEK_MODEL_IDS[1],
        reasoning_effort=DeepSeekReasoningEffort.MAX,
        thinking_enabled=False,
        api_key_configured=True,
        status=DeepSeekStatus.READY,
    )
    raw = (tmp_path / "settings" / "deepseek-provider.json").read_text(encoding="utf-8")
    assert "api_key" not in raw
    assert "sk-" not in raw
    loaded = load_deepseek_settings(tmp_path, api_key_configured=True)
    assert loaded.status is DeepSeekStatus.READY
    dumped = loaded.model_dump_json()
    assert "sk-" not in dumped
    assert "api_key_configured" in dumped


def test_status_state_machine(tmp_path: Path) -> None:
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=False).status
        is DeepSeekStatus.NOT_CONFIGURED
    )
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.CONFIGURED_NOT_TESTED
    )
    status, category = check_deepseek_connection(
        tmp_path, credential="sk-test", opener=_FakeOpener(200)
    )
    assert status is DeepSeekStatus.READY
    assert category is None
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.READY
    )
    unauthorized = urllib.error.HTTPError(
        "https://api.deepseek.com/models", 401, "Unauthorized", {}, io.BytesIO(b"{}")
    )
    status, category = check_deepseek_connection(
        tmp_path, credential="sk-test", opener=_FakeOpener(error=unauthorized)
    )
    assert status is DeepSeekStatus.CONNECTION_FAILED
    assert category is DeepSeekErrorCategory.AUTHENTICATION_FAILED
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.CONNECTION_FAILED
    )
    assert check_deepseek_connection(tmp_path, credential="") == (
        DeepSeekStatus.NOT_CONFIGURED,
        None,
    )


def test_no_automatic_paid_call_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("network call during provider startup")

    monkeypatch.setattr(
        "soloscale.deepseek_provider.urllib.request.urlopen", forbidden
    )
    load_deepseek_settings(tmp_path, api_key_configured=True)
    save_deepseek_settings(
        tmp_path,
        model_id=DEEPSEEK_MODEL_IDS[0],
        reasoning_effort=DeepSeekReasoningEffort.HIGH,
        thinking_enabled=True,
        api_key_configured=True,
    )
    transport = MockDeepSeekTransport(
        DeepSeekProviderResponse(content=json.dumps({"text": "ok"}))
    )
    DeepSeekModelGateway(
        settings=_settings(), credential="sk-test", transport=transport
    )
    assert transport.requests == []


def test_call_receipt_records_actual_provider_and_model() -> None:
    transport = MockDeepSeekTransport(
        DeepSeekProviderResponse(
            content=json.dumps({"text": "ok"}),
            input_tokens=11,
            output_tokens=5,
            cache_tokens=3,
            request_id="resp-1",
        )
    )
    settings = _settings(
        model_id=DEEPSEEK_MODEL_IDS[1],
        effort=DeepSeekReasoningEffort.MAX,
        thinking=False,
    )
    gateway = DeepSeekModelGateway(
        settings=settings, credential="sk-test", transport=transport
    )
    result = gateway.complete(_Answer, system="system", user="user")
    assert result.text == "ok"
    receipt = gateway.last_receipt
    assert receipt is not None
    assert receipt.provider == "deepseek"
    assert receipt.model == "deepseek-v4-pro"
    assert receipt.reasoning_effort is DeepSeekReasoningEffort.MAX
    assert receipt.thinking_enabled is False
    assert receipt.input_tokens == 11
    assert receipt.output_tokens == 5
    assert receipt.cache_tokens == 3
    assert receipt.status == "SUCCEEDED"
    assert transport.requests[0].model == "deepseek-v4-pro"


def test_no_silent_model_or_provider_fallback() -> None:
    failing = _FailingDeepSeekTransport(DeepSeekErrorCategory.PROVIDER_UNAVAILABLE)
    gateway = DeepSeekModelGateway(
        settings=_settings(model_id=DEEPSEEK_MODEL_IDS[0]),
        credential="sk-test",
        transport=failing,
    )
    with pytest.raises(DeepSeekProviderError) as exc:
        gateway.complete(_Answer, system="system", user="user")
    assert exc.value.category is DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
    assert len(failing.requests) == 1
    assert failing.requests[0].model == "deepseek-v4-flash"
    assert gateway.last_receipt is not None
    assert gateway.last_receipt.status == "FAILED"
    assert (
        gateway.last_receipt.error_category
        is DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
    )


def test_error_category_normalization() -> None:
    assert deepseek_error_category(status=401) is DeepSeekErrorCategory.AUTHENTICATION_FAILED
    assert deepseek_error_category(status=429) is DeepSeekErrorCategory.RATE_LIMITED
    assert deepseek_error_category(status=404) is DeepSeekErrorCategory.MODEL_UNAVAILABLE
    assert deepseek_error_category(status=400) is DeepSeekErrorCategory.REQUEST_INVALID
    assert deepseek_error_category(status=503) is DeepSeekErrorCategory.PROVIDER_UNAVAILABLE
    assert (
        deepseek_error_category(status=None, timed_out=True)
        is DeepSeekErrorCategory.MODEL_TIMEOUT
    )
    assert (
        deepseek_error_category(status=200, error_signal="max_output_tokens")
        is DeepSeekErrorCategory.OUTPUT_LIMIT
    )


def test_credential_redaction_in_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unauthorized(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise urllib.error.HTTPError(
            "https://api.deepseek.com/responses",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(
        "soloscale.deepseek_provider.urllib.request.urlopen", unauthorized
    )
    transport = DeepSeekResponsesHTTPTransport("sk-secret-123")
    request = DeepSeekResponsesRequest(
        correlation_id=f"deepseek-{'b' * 24}",
        model=DEEPSEEK_MODEL_IDS[0],
        system="system",
        user="user",
        response_json_schema={},
        reasoning_effort=DeepSeekReasoningEffort.HIGH,
        thinking_enabled=True,
    )
    with pytest.raises(DeepSeekProviderError) as exc:
        transport.send(request)
    assert "sk-secret-123" not in str(exc.value)
    assert exc.value.category is DeepSeekErrorCategory.AUTHENTICATION_FAILED


def test_existing_provider_regression() -> None:
    assert {
        ModelProviderId.SOLOSCALE_HOSTED.value,
        ModelProviderId.OLLAMA.value,
        ModelProviderId.OPENAI_COMPATIBLE.value,
    } <= {provider.value for provider in ModelProviderId}
    hosted = model_gateway_for(
        ModelProviderId.SOLOSCALE_HOSTED,
        environment={"SOLOSCALE_HOSTED_GATEWAY_ENABLED": "false"},
    )
    assert hosted.descriptor.provider is ModelProviderId.SOLOSCALE_HOSTED
    compatible = model_gateway_for(ModelProviderId.OPENAI_COMPATIBLE)
    assert (
        compatible.descriptor.configuration_state
        is GatewayConfigurationState.NOT_CONFIGURED
    )


def test_ai_settings_page_is_human_readable_without_internal_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type(
            "Readiness",
            (),
            {"ready": False},
        )(),
    )
    page = _ai_settings_page(tmp_path, locale="en", detail="deepseek", desktop_mode=False)
    assert "DeepSeek" in page
    assert "DeepSeek V4 Flash" in page
    assert "DeepSeek V4 Pro" in page
    assert "Not configured" in page
    assert "openai_compatible" not in page
    assert "provider_id=deepseek" in page
    assert "transport=responses" in page
    assert "https://api.deepseek.com" in page
    assert "Low" in page
    assert "High" in page
    assert "Max" in page


def test_deepseek_selection_persists_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type(
            "Readiness",
            (),
            {"ready": False},
        )(),
    )
    _save_ai_provider_preference(
        tmp_path,
        provider="deepseek",
        deepseek_model=DEEPSEEK_MODEL_IDS[1],
        deepseek_reasoning_effort="max",
        deepseek_thinking=False,
    )
    preference = _load_ai_provider_preference(tmp_path)
    assert preference.provider is ModelProviderId.DEEPSEEK
    assert preference.deepseek_model == "deepseek-v4-pro"
    assert preference.deepseek_reasoning_effort == "max"
    assert preference.deepseek_thinking is False
    detail = _ai_settings_page(
        tmp_path, locale="en", detail="deepseek", desktop_mode=False
    )
    assert 'value="deepseek-v4-pro" checked' in detail
    assert 'value="max" selected' in detail
    overview = _ai_settings_page(tmp_path, locale="en")
    assert "DeepSeek V4 Pro" in overview
    assert 'href="/settings/ai/deepseek?lang=en"' in overview


def test_key_save_resets_readiness_and_renders_configured_not_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: True
    )
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type("Readiness", (), {"ready": False})(),
    )
    save_deepseek_settings(
        tmp_path,
        model_id="deepseek-v4-pro",
        reasoning_effort="high",
        thinking_enabled=True,
        api_key_configured=True,
        status=DeepSeekStatus.READY,
    )

    outcome = _apply_deepseek_settings_action(
        _settings_form("prepare"), tmp_path, desktop_mode=True
    )

    assert outcome == "prepared"
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.CONFIGURED_NOT_TESTED
    )
    page = _ai_settings_page(
        tmp_path,
        locale="en",
        detail="deepseek",
        notice=_ai_settings_notice("deepseek-key-saved", "en"),
        desktop_mode=True,
    )
    assert 'data-provider-status="configured_not_tested"' in page
    assert "DeepSeek · Configured, not tested" in page
    assert "DeepSeek · Ready" not in page
    assert "provider=deepseek-key-saved" in page
    assert "configured, not tested" in (
        _ai_settings_notice("deepseek-key-saved", "en") or ""
    )


def test_model_settings_save_persists_and_renders_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: True
    )
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type("Readiness", (), {"ready": False})(),
    )

    outcome = _apply_deepseek_settings_action(
        _settings_form("save"), tmp_path, desktop_mode=True
    )

    assert outcome == "deepseek-settings-saved"
    preference = _load_ai_provider_preference(tmp_path)
    assert preference.deepseek_model == "deepseek-v4-pro"
    assert preference.deepseek_reasoning_effort == "high"
    assert preference.deepseek_thinking is True
    detail = _ai_settings_page(
        tmp_path,
        locale="en",
        detail="deepseek",
        notice=_ai_settings_notice(outcome, "en"),
        desktop_mode=True,
    )
    assert 'value="deepseek-v4-pro" checked' in detail
    assert 'value="high" selected' in detail
    assert 'name="deepseek_thinking" value="true" checked' in detail
    assert "DeepSeek V4 Pro · Reasoning: High · Thinking: Enabled" in detail
    assert "will persist after refresh" in detail
    assert "will persist after refresh" in (
        _ai_settings_notice("deepseek-settings-saved", "en") or ""
    )


def test_explicit_connection_success_persists_ready_and_renders_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    opener = _FakeOpener(200)
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: True
    )
    monkeypatch.setattr(
        "soloscale.local_ui.check_deepseek_connection",
        lambda data_root: check_deepseek_connection(
            data_root, credential="sk-test", opener=opener
        ),
    )
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type("Readiness", (), {"ready": False})(),
    )

    outcome = _apply_deepseek_settings_action(
        _settings_form("test"), tmp_path, desktop_mode=True
    )

    assert outcome == "deepseek-ready"
    assert len(opener.calls) == 1
    assert getattr(opener.calls[0], "full_url", "") == "https://api.deepseek.com/models"
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.READY
    )
    detail = _ai_settings_page(
        tmp_path,
        locale="en",
        detail="deepseek",
        notice=_ai_settings_notice(outcome, "en"),
        desktop_mode=True,
    )
    assert 'data-provider-status="ready"' in detail
    assert "DeepSeek · Ready" in detail
    assert "DeepSeek V4 Pro" in detail
    assert "connection test succeeded" in detail
    assert "connection test succeeded" in (
        _ai_settings_notice("deepseek-ready", "en") or ""
    )
    raw = (tmp_path / "settings" / "deepseek-provider.json").read_text(
        encoding="utf-8"
    )
    assert "sk-test" not in raw


def test_explicit_connection_failure_persists_status_and_actionable_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_for_tests()
    unauthorized = urllib.error.HTTPError(
        "https://api.deepseek.com/models",
        401,
        "Unauthorized",
        Message(),
        io.BytesIO(b"{}"),
    )
    opener = _FakeOpener(error=unauthorized)
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: True
    )
    monkeypatch.setattr(
        "soloscale.local_ui.check_deepseek_connection",
        lambda data_root: check_deepseek_connection(
            data_root, credential="sk-test", opener=opener
        ),
    )
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: type("Readiness", (), {"ready": False})(),
    )

    outcome = _apply_deepseek_settings_action(
        _settings_form("test"), tmp_path, desktop_mode=True
    )

    assert outcome == "deepseek-authentication-failed"
    assert (
        load_deepseek_settings(tmp_path, api_key_configured=True).status
        is DeepSeekStatus.CONNECTION_FAILED
    )
    detail = _ai_settings_page(
        tmp_path,
        locale="en",
        detail="deepseek",
        notice=_ai_settings_notice(outcome, "en"),
        desktop_mode=True,
    )
    assert 'data-provider-status="connection_failed"' in detail
    assert "DeepSeek · Connection failed" in detail
    assert "API key was rejected" in detail
    notice = _ai_settings_notice(outcome, "en") or ""
    assert "API key was rejected" in notice
    assert "sk-test" not in notice
