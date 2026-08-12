"""Constrained draft revision stage."""

from __future__ import annotations

from buildlog.config import Settings
from buildlog.llm_client import LLMClient
from buildlog.models import Evaluation, Iteration
from buildlog.prompt_loader import load_prompt


def revise_draft(
    iteration: Iteration,
    draft: str,
    evaluation: Evaluation,
    client: LLMClient,
    settings: Settings,
) -> str:
    """Revise a draft once using evaluator feedback."""
    prompt = load_prompt(
        settings.prompts_dir,
        "reviser",
        settings.prompt_version,
    ).format(
        iteration_json=iteration.model_dump_json(indent=2),
        draft=draft,
        evaluation_json=evaluation.model_dump_json(indent=2),
    )
    return client.complete_text(prompt)
