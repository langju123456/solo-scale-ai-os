"""Tests for grounded local publishing package generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from buildlog.asset_planner import PlannedAssets
from buildlog.card_renderer import CARD_HEIGHT, CARD_WIDTH, CardRenderer
from buildlog.domain import ArtifactRecord, RunRecord
from buildlog.exceptions import PackageBuildError
from buildlog.hashing import sha256_file
from buildlog.main import main
from buildlog.package_builder import PublishingPackageBuilder
from buildlog.package_models import (
    ArchitectureCardSpec,
    AssetPlan,
    PlannerProvenance,
    TakeawayCardSpec,
    TitleCardSpec,
    TradeoffCardSpec,
)
from buildlog.publication_content import (
    FinalArtifactResolver,
    HUMAN_REVIEW_WARNING,
    publication_content_hash,
)


class FakeRunRepository:
    """Expose one completed run and its indexed artifacts."""

    def __init__(
        self,
        run: RunRecord,
        artifacts: list[ArtifactRecord],
    ) -> None:
        self.run = run
        self.artifacts = artifacts

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.run if run_id == self.run.id else None

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        return list(self.artifacts) if run_id == self.run.id else []


class FakeAssetPlanner:
    """Return one fixed, validated card plan without an LLM call."""

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, iteration, caption: str) -> PlannedAssets:
        self.calls += 1
        assert iteration.id == "local-agent-001"
        assert caption == "Reviewed caption."
        return PlannedAssets(
            plan=_asset_plan(),
            provenance=PlannerProvenance(
                model="test-model",
                model_digest="test-digest",
                prompt_version="v1",
                prompt_hash="a" * 64,
            ),
        )


def test_builds_reviewable_package_with_grounded_manifest(tmp_path: Path) -> None:
    repository, runs_dir = _reviewed_run(tmp_path)
    planner = FakeAssetPlanner()
    builder = PublishingPackageBuilder(
        repository,
        FinalArtifactResolver(repository, allowed_root=runs_dir),
        planner,
        CardRenderer(),
    )

    result = builder.build(
        "run-001",
        tmp_path / ".buildlog" / "publishing_packages",
        reviewed=True,
    )

    assert not result.reused_existing
    assert result.manifest.review_status == "pending"
    assert result.manifest.target == "linkedin"
    assert result.manifest.source.run_id == "run-001"
    assert result.manifest.source.iteration_id == "local-agent-001"
    assert result.manifest.source.caption_hash == publication_content_hash(
        "Reviewed caption."
    )
    assert result.caption_path.read_text(encoding="utf-8") == "Reviewed caption.\n"
    assert [asset.type for asset in result.manifest.assets] == [
        "title",
        "architecture",
        "tradeoff",
        "takeaway",
    ]
    assert all(asset.spec.source_fields for asset in result.manifest.assets)
    assert json.loads(result.manifest_path.read_text(encoding="utf-8"))[
        "schema_version"
    ] == "1"

    from PIL import Image

    for path in result.asset_paths:
        assert path.is_file()
        with Image.open(path) as image:
            assert image.size == (CARD_WIDTH, CARD_HEIGHT)
            assert image.format == "PNG"

    repeated = builder.build(
        "run-001",
        tmp_path / ".buildlog" / "publishing_packages",
        reviewed=True,
    )
    assert repeated.reused_existing
    assert repeated.package_dir == result.package_dir
    assert planner.calls == 2


def test_requires_explicit_review_confirmation(tmp_path: Path) -> None:
    repository, runs_dir = _reviewed_run(tmp_path)
    planner = FakeAssetPlanner()
    builder = PublishingPackageBuilder(
        repository,
        FinalArtifactResolver(repository, allowed_root=runs_dir),
        planner,
        CardRenderer(),
    )

    with pytest.raises(PackageBuildError, match="human-reviewed"):
        builder.build("run-001", tmp_path / "packages", reviewed=False)

    assert planner.calls == 0
    assert not (tmp_path / "packages").exists()


def test_rejects_changed_indexed_input(tmp_path: Path) -> None:
    repository, runs_dir = _reviewed_run(tmp_path)
    input_artifact = next(
        artifact
        for artifact in repository.artifacts
        if artifact.artifact_type == "input"
    )
    Path(input_artifact.file_path).write_text("{}", encoding="utf-8")
    builder = PublishingPackageBuilder(
        repository,
        FinalArtifactResolver(repository, allowed_root=runs_dir),
        FakeAssetPlanner(),
        CardRenderer(),
    )

    with pytest.raises(PackageBuildError, match="indexed SHA-256"):
        builder.build("run-001", tmp_path / "packages", reviewed=True)


def test_asset_plan_rejects_unknown_grounding_field() -> None:
    with pytest.raises(ValidationError, match="unknown source fields"):
        TitleCardSpec(
            type="title",
            title="Grounded package",
            subtitle="One reviewed run.",
            source_fields=["invented_field"],
        )


def test_asset_plan_reduces_dense_architecture_flow_to_five_steps() -> None:
    plan = _asset_plan()
    architecture = plan.cards[1]
    assert isinstance(architecture, ArchitectureCardSpec)
    dense = architecture.model_copy(
        update={"steps": [f"Step {index}" for index in range(1, 8)]}
    )

    normalized = AssetPlan(
        cards=[plan.cards[0], dense, plan.cards[2], plan.cards[3]]
    )

    normalized_architecture = normalized.cards[1]
    assert isinstance(normalized_architecture, ArchitectureCardSpec)
    assert normalized_architecture.steps == [
        "Step 1",
        "Step 3",
        "Step 4",
        "Step 5",
        "Step 7",
    ]


def test_main_dispatches_package_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "buildlog.main.package_main",
        lambda argv: 9 if argv == ["build", "run-001"] else 8,
    )

    assert main(["package", "build", "run-001"]) == 9


def _reviewed_run(
    tmp_path: Path,
) -> tuple[FakeRunRepository, Path]:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-001"
    run_dir.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "valid_iteration.json"
    input_path = run_dir / "00_input.json"
    input_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    final_path = run_dir / "06_final.md"
    final_path.write_text(
        "Reviewed caption." + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    run = RunRecord(
        id="run-001",
        iteration_id="local-agent-001",
        model="test-model",
        planner_prompt_version_id="planner",
        writer_prompt_version_id="writer",
        evaluator_prompt_version_id="evaluator",
        reviser_prompt_version_id="reviser",
        status="completed",
    )
    artifacts = [
        ArtifactRecord(
            id="run-001:input",
            run_id=run.id,
            artifact_type="input",
            file_path=str(input_path),
            content_hash=sha256_file(input_path),
        ),
        ArtifactRecord(
            id="run-001:final",
            run_id=run.id,
            artifact_type="final",
            file_path=str(final_path),
            content_hash=sha256_file(final_path),
        ),
    ]
    return FakeRunRepository(run, artifacts), runs_dir


def _asset_plan() -> AssetPlan:
    return AssetPlan(
        cards=[
            TitleCardSpec(
                type="title",
                title="From evidence to a publishable package",
                subtitle="A reviewed engineering run becomes reusable visual assets.",
                source_fields=["title", "goal"],
            ),
            ArchitectureCardSpec(
                type="architecture",
                title="Keep generation separate from publishing",
                steps=[
                    "Validated iteration",
                    "Grounded asset plan",
                    "Deterministic renderer",
                    "Reviewable package",
                ],
                summary="The publisher receives reviewed assets, not rendering logic.",
                source_fields=["actions", "decisions"],
            ),
            TradeoffCardSpec(
                type="tradeoff",
                title="A narrow package boundary",
                decision="Render locally before adding media upload APIs.",
                benefit="Visual value can be tested without platform coupling.",
                cost="The first package must be uploaded manually.",
                source_fields=["decisions", "trade_offs"],
            ),
            TakeawayCardSpec(
                type="takeaway",
                title="What this baseline should prove",
                items=[
                    "A manifest can preserve package lineage.",
                    "Deterministic cards keep visual output consistent.",
                    "Human review remains the final quality gate.",
                ],
                source_fields=["lessons"],
            ),
        ]
    )
