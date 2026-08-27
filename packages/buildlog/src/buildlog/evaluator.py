"""Draft evaluation stage and threshold logic."""

from __future__ import annotations

from buildlog.config import Settings
from buildlog.llm_client import LLMClient
from buildlog.models import Evaluation, Iteration
from buildlog.prompt_loader import load_prompt


def evaluate_draft(
    iteration: Iteration,
    draft: str,
    client: LLMClient,
    settings: Settings,
) -> Evaluation:
    """Evaluate a draft and return validated scores and feedback."""
    prompt = load_prompt(
        settings.prompts_dir,
        "evaluator",
        settings.prompt_version,
    ).format(
        iteration_json=iteration.model_dump_json(indent=2),
        draft=draft,
    )
    return client.complete_json(prompt, Evaluation)


def passes_thresholds(evaluation: Evaluation, settings: Settings) -> bool:
    """Return whether evaluation scores pass deterministic thresholds."""
    if evaluation.hard_failure:
        return False
    return (
        evaluation.technical_accuracy >= settings.threshold_accuracy
        and evaluation.specificity >= settings.threshold_specificity
        and evaluation.readability >= settings.threshold_readability
        and evaluation.reader_value >= settings.threshold_value
        and evaluation.evidence_coverage >= settings.threshold_evidence
    )
