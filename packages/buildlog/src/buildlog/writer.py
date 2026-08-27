"""LinkedIn draft writing stage."""

from __future__ import annotations

from buildlog.config import Settings
from buildlog.llm_client import LLMClient
from buildlog.models import Iteration, StoryPlan
from buildlog.prompt_loader import load_prompt


def write_draft(
    iteration: Iteration,
    plan: StoryPlan,
    client: LLMClient,
    settings: Settings,
) -> str:
    """Write the first LinkedIn draft as Markdown."""
    prompt = load_prompt(
        settings.prompts_dir,
        "writer",
        settings.prompt_version,
    ).format(
        iteration_json=iteration.model_dump_json(indent=2),
        plan_json=plan.model_dump_json(indent=2),
    )
    return client.complete_text(prompt)
