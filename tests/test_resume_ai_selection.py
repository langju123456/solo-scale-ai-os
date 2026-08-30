"""Focused canonical per-run Resume AI selection coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from soloscale.deepseek_provider import (
    DeepSeekStatus,
    save_deepseek_settings,
)
from soloscale.local_ui import (
    OllamaReadiness,
    _apply_resume_ai_selection,
    _load_ai_provider_preference,
    _resolve_resume_ai_selection,
    _resume_gateway_from_selection,
    _save_ai_provider_preference,
    _user_page,
)
from soloscale.model_gateway import ModelProviderId


def _stable_local_readiness(
    monkeypatch: pytest.MonkeyPatch, *, ready: bool = False
) -> None:
    monkeypatch.setattr(
        "soloscale.local_ui._ollama_readiness",
        lambda preference: OllamaReadiness(ready, ready, ready),
    )


def _ready_deepseek(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, model: str = "deepseek-v4-pro"
) -> None:
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: True
    )
    save_deepseek_settings(
        tmp_path,
        model_id=model,
        reasoning_effort="high",
        thinking_enabled=True,
        api_key_configured=True,
        status=DeepSeekStatus.READY,
    )


def test_global_default_deepseek_is_the_resume_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stable_local_readiness(monkeypatch)
    _ready_deepseek(tmp_path, monkeypatch)
    _save_ai_provider_preference(
        tmp_path,
        provider=ModelProviderId.DEEPSEEK.value,
        deepseek_model="deepseek-v4-pro",
        deepseek_reasoning_effort="high",
        deepseek_thinking=True,
    )

    page = _user_page(None, tmp_path, {}, "en")

    assert "AI service for this run" in page
    assert "Use global default — DeepSeek · DeepSeek V4 Pro · High — READY" in page
    assert 'name="resume_ai_selection"' in page
    assert "SoloScale Hosted AI · Recommended" not in page
    assert "GPT-5.6 Sol Expert Review" not in page
    assert "I approve one expert-review request using my OpenAI API account" not in page
    assert "This review uses your OpenAI API account" not in page


def test_global_default_openai_is_the_resume_default_and_has_scoped_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stable_local_readiness(monkeypatch)
    monkeypatch.setattr(
        "soloscale.local_ui.openai_api_key_is_configured", lambda: True
    )
    preference = _save_ai_provider_preference(
        tmp_path,
        provider=ModelProviderId.OPENAI_COMPATIBLE.value,
        openai_model="gpt-5.6-sol",
    )
    openai_choice = f"{ModelProviderId.OPENAI_COMPATIBLE.value}:{preference.openai_model}"

    page = _user_page(
        None,
        tmp_path,
        {
            "resume_ai_selection": "default",
            "expert_review_mode": "ai",
            "expert_ai_selection": openai_choice,
        },
        "en",
    )

    assert "Use global default — OpenAI · GPT-5.6 Sol — READY" in page
    assert "AI Expert Review" in page
    assert "This review uses your OpenAI API account." in page
    assert "GPT-5.6 Sol Expert Review" not in page


def test_run_override_does_not_mutate_global_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stable_local_readiness(monkeypatch)
    _ready_deepseek(tmp_path, monkeypatch)
    original = _save_ai_provider_preference(
        tmp_path,
        provider=ModelProviderId.DEEPSEEK.value,
        deepseek_model="deepseek-v4-pro",
        deepseek_reasoning_effort="high",
        deepseek_thinking=True,
    )

    default = _resolve_resume_ai_selection(tmp_path, None)
    override = _resolve_resume_ai_selection(
        tmp_path, "deepseek:deepseek-v4-flash"
    )
    run_form: dict[str, str] = {}
    _apply_resume_ai_selection(run_form, override)

    assert default.provider is ModelProviderId.DEEPSEEK
    assert default.model == "deepseek-v4-pro"
    assert override.provider is ModelProviderId.DEEPSEEK
    assert override.model == "deepseek-v4-flash"
    assert run_form == {
        "generation_mode": "deepseek",
        "provider_model": "deepseek-v4-flash",
        "provider_reasoning_effort": "high",
        "provider_thinking": "true",
    }
    assert _load_ai_provider_preference(tmp_path) == original


def test_provider_model_ownership_is_enforced_and_unready_selection_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stable_local_readiness(monkeypatch)
    monkeypatch.setattr(
        "soloscale.local_ui.deepseek_api_key_is_configured", lambda: False
    )
    _save_ai_provider_preference(
        tmp_path,
        provider=ModelProviderId.DEEPSEEK.value,
        deepseek_model="deepseek-v4-pro",
    )

    with pytest.raises(ValueError, match="not available"):
        _resolve_resume_ai_selection(tmp_path, "deepseek:gpt-5.6-sol")

    selected = _resolve_resume_ai_selection(tmp_path, "default")
    assert selected.readiness == "NOT_CONFIGURED"
    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        _resume_gateway_from_selection(selected, tmp_path)
    assert selected.provider is ModelProviderId.DEEPSEEK
    assert selected.model == "deepseek-v4-pro"
