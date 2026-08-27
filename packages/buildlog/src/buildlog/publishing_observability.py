"""Best-effort publishing events appended to existing BuildLog run traces."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from buildlog.event_writer import AppendOnlyRunEventWriter
from buildlog.linkedin_security import redact_linkedin_secrets

LOGGER = logging.getLogger(__name__)

_FORBIDDEN_NORMALIZED_KEYS = {
    "accesstoken",
    "authorization",
    "authorizationcode",
    "authorizationurl",
    "authorurn",
    "body",
    "clientsecret",
    "commentary",
    "content",
    "idtoken",
    "oauthstate",
    "personurn",
    "postcontent",
    "rawcontent",
    "rawsubject",
    "refreshtoken",
    "subject",
    "text",
}


class PublishingEventRecorder:
    """Append safe publication metadata without affecting publication behavior."""

    def __init__(self, run_id: str, run_dir: Path) -> None:
        try:
            self._writer: AppendOnlyRunEventWriter | None = (
                AppendOnlyRunEventWriter(
                    run_dir / "events.jsonl",
                    run_id,
                    durable=True,
                )
            )
        except Exception:
            self._writer = None

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        preserve_outcome: bool = False,
    ) -> bool:
        """Append one sanitized event and return whether it succeeded."""
        if self._writer is None:
            _warn_event_unavailable(event_type)
            return False
        try:
            self._writer.append(
                event_type,
                step_name=None,
                payload=_sanitize_payload(payload),
            )
        except KeyboardInterrupt:
            if not preserve_outcome:
                raise
            _warn_event_unavailable(event_type)
            return False
        except Exception:
            _warn_event_unavailable(event_type)
            return False
        return True


def _sanitize_payload(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = "".join(
            character
            for character in key.casefold()
            if character.isalnum()
        )
        if normalized_key in _FORBIDDEN_NORMALIZED_KEYS:
            safe[key] = "<redacted>"
        else:
            safe[key] = _sanitize_value(item)
    return safe


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_payload(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact_linkedin_secrets(value)
    return value


def _warn_event_unavailable(event_type: str) -> None:
    LOGGER.warning(
        "Could not append safe publication event %s; publication behavior is "
        "unchanged.",
        redact_linkedin_secrets(event_type),
    )
