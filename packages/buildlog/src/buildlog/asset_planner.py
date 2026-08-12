"""Grounded LLM planning for publishing-package card specifications."""

from __future__ import annotations

from dataclasses import dataclass

from buildlog.config import Settings
from buildlog.hashing import sha256_file
from buildlog.llm_client import LLMClient
from buildlog.models import Iteration
from buildlog.package_models import AssetPlan, PlannerProvenance
from buildlog.prompt_loader import load_prompt


@dataclass(frozen=True)
class PlannedAssets:
    """One validated AssetPlan and its model/prompt provenance."""

    plan: AssetPlan
    provenance: PlannerProvenance


class LLMAssetPlanner:
    """Create a grounded card plan without generating visual assets."""

    def __init__(
        self,
        client: LLMClient,
        settings: Settings,
        prompt_version: str = "v1",
    ) -> None:
        self._client = client
        self._settings = settings
        self._prompt_version = prompt_version

    def plan(self, iteration: Iteration, caption: str) -> PlannedAssets:
        """Return a validated 3-4 card plan for one reviewed run."""
        prompt_path = (
            self._settings.prompts_dir
            / f"asset_planner_{self._prompt_version}.md"
        ).resolve()
        prompt = load_prompt(
            self._settings.prompts_dir,
            "asset_planner",
            self._prompt_version,
        ).format(
            iteration_json=iteration.model_dump_json(indent=2),
            caption=caption,
        )
        plan = self._client.complete_json(prompt, AssetPlan)
        return PlannedAssets(
            plan=plan,
            provenance=PlannerProvenance(
                model=self._settings.model,
                model_digest=self._settings.model_digest,
                prompt_version=self._prompt_version,
                prompt_hash=sha256_file(prompt_path),
            ),
        )
