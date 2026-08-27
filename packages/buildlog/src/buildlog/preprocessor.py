"""Deterministic normalization for iteration inputs."""

from __future__ import annotations

import re

from buildlog.models import Decision, Iteration


def normalize_iteration(iteration: Iteration) -> Iteration:
    """Normalize whitespace and remove exact duplicate list entries."""
    data = iteration.model_dump()
    data["id"] = _normalize_text(data["id"])
    data["title"] = _normalize_text(data["title"])
    data["goal"] = _normalize_text(data["goal"])
    data["context"] = _normalize_text(data["context"])
    data["problem"] = _normalize_text(data["problem"])
    data["result"] = _normalize_text(data["result"])
    data["audience"] = _normalize_text(data["audience"])
    data["actions"] = _dedupe([_normalize_text(item) for item in data["actions"]])
    data["trade_offs"] = _dedupe([_normalize_text(item) for item in data["trade_offs"]])
    data["lessons"] = _dedupe([_normalize_text(item) for item in data["lessons"]])
    data["evidence"] = _dedupe([_normalize_text(item) for item in data["evidence"]])
    data["decisions"] = [_normalize_decision(decision) for decision in iteration.decisions]
    return Iteration.model_validate(data)


def _normalize_decision(decision: Decision) -> dict[str, object]:
    return {
        "decision": _normalize_text(decision.decision),
        "reason": _normalize_text(decision.reason),
        "alternatives_considered": _dedupe(
            [_normalize_text(item) for item in decision.alternatives_considered]
        ),
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
