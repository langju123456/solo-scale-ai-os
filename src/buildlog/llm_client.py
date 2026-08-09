"""LiteLLM client wrapper for BuildLog model calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from buildlog.config import Settings
from buildlog.exceptions import ModelResponseError, StructuredOutputError
from buildlog.observer import ActiveStep, PendingLLMCall, get_active_step
from buildlog.structured_diagnostics import capture_failed_structured_output

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ResultT = TypeVar("ResultT")


class LLMClient:
    """Small LiteLLM wrapper for text and structured JSON responses."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete_text(self, prompt: str) -> str:
        """Return a plain text model completion."""
        return self._observed_completion(prompt, _parse_text_response)

    def complete_json(self, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """Return a Pydantic-validated JSON model completion."""

        def parse(response: object) -> SchemaT:
            try:
                return _parse_json_response(response, schema)
            except StructuredOutputError:
                self._capture_failed_structured_output(response)
                raise

        return self._observed_completion(
            prompt,
            parse,
        )

    def _capture_failed_structured_output(self, response: object) -> None:
        if (
            not self._settings.capture_failed_structured_output
            or self._settings.environment != "development"
        ):
            return
        active = get_active_step()
        if active is None:
            return
        try:
            content = _extract_content(response)
            capture_failed_structured_output(
                active.observer.run_dir,
                active.step_name,
                content,
            )
        except Exception as exc:
            active.observer.record_observability_issue(
                "could not capture failed structured output: "
                f"{type(exc).__name__}"
            )

    def _observed_completion(
        self,
        prompt: str,
        parser: Callable[[object], ResultT],
    ) -> ResultT:
        active = get_active_step()
        pending = _start_observation(active, prompt)
        provider_end_ns = _monotonic_ns(active)
        provider_ended_at = _now(active)
        usage: dict[str, int | None] | None = None
        finish_reason: str | None = None
        try:
            response = self._completion(prompt)
            provider_end_ns = _monotonic_ns(active)
            provider_ended_at = _now(active)
            usage = _extract_usage(response)
            finish_reason = _extract_finish_reason(response)
            result = parser(response)
        except Exception as exc:
            provider_end_ns = _monotonic_ns(active)
            provider_ended_at = _now(active)
            _finish_observation(
                active,
                pending,
                provider_end_ns=provider_end_ns,
                provider_ended_at=provider_ended_at,
                usage=usage,
                finish_reason=finish_reason,
                error=exc,
            )
            raise
        _finish_observation(
            active,
            pending,
            provider_end_ns=provider_end_ns,
            provider_ended_at=provider_ended_at,
            usage=usage,
            finish_reason=finish_reason,
            error=None,
        )
        return result

    def _completion(self, prompt: str) -> object:
        try:
            from litellm import completion

            kwargs: dict[str, object] = {
                "model": self._settings.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self._settings.temperature,
                "max_tokens": self._settings.max_tokens,
            }
            if self._settings.api_base:
                kwargs["api_base"] = self._settings.api_base
            return completion(**kwargs)
        except Exception as exc:
            raise ModelResponseError(f"model call failed: {exc}") from exc


def _parse_text_response(response: object) -> str:
    content = _extract_content(response)
    if not content.strip():
        raise ModelResponseError("model returned empty content")
    return content.strip()


def _parse_json_response(response: object, schema: type[SchemaT]) -> SchemaT:
    raw = _parse_text_response(response)
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"model returned invalid JSON: {exc.msg}"
        ) from exc

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(
            "model returned schema-invalid structured output"
        ) from exc


def _start_observation(
    active: ActiveStep | None,
    prompt: str,
) -> PendingLLMCall | None:
    if active is None:
        return None
    try:
        return active.observer.start_llm_call(prompt)
    except Exception as exc:
        active.observer.record_observability_issue(
            f"could not start LLM call observation: {exc}"
        )
        return None


def _finish_observation(
    active: ActiveStep | None,
    pending: PendingLLMCall | None,
    *,
    provider_end_ns: int,
    provider_ended_at: datetime,
    usage: dict[str, int | None] | None,
    finish_reason: str | None,
    error: Exception | None,
) -> None:
    if active is None:
        return
    try:
        active.observer.finish_llm_call(
            pending,
            provider_end_ns=provider_end_ns,
            provider_ended_at=provider_ended_at,
            usage=usage,
            finish_reason=finish_reason,
            error=error,
        )
    except Exception as exc:
        active.observer.record_observability_issue(
            f"could not finish LLM call observation: {exc}"
        )


def _now(active: ActiveStep | None) -> datetime:
    if active is None:
        return datetime.now(UTC)
    return active.observer.clock.now()


def _monotonic_ns(active: ActiveStep | None) -> int:
    if active is None:
        return perf_counter_ns()
    return active.observer.clock.monotonic_ns()


def _extract_usage(response: object) -> dict[str, int | None] | None:
    usage = _read_value(response, "usage")
    if usage is None:
        return None
    return {
        "prompt_tokens": _optional_int(_read_value(usage, "prompt_tokens")),
        "completion_tokens": _optional_int(
            _read_value(usage, "completion_tokens")
        ),
        "total_tokens": _optional_int(_read_value(usage, "total_tokens")),
    }


def _extract_finish_reason(response: object) -> str | None:
    try:
        choices = _read_value(response, "choices")
        reason = _read_value(choices[0], "finish_reason")
    except (IndexError, TypeError):
        return None
    return str(reason) if reason is not None else None


def _read_value(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_content(response: object) -> str:
    try:
        choices = _read_value(response, "choices")
        message = _read_value(choices[0], "message")
        content = _read_value(message, "content")
    except (IndexError, TypeError) as exc:
        raise ModelResponseError(
            "model response did not contain message content"
        ) from exc
    if content is None:
        raise ModelResponseError(
            "model response did not contain message content"
        )
    return str(content)


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text
