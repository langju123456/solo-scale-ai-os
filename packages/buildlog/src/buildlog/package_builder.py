"""Build one local, target-aware publishing package from a reviewed run."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from buildlog.asset_planner import PlannedAssets
from buildlog.card_renderer import CardRenderer, RenderedCard
from buildlog.exceptions import PackageBuildError
from buildlog.hashing import sha256_file
from buildlog.input_loader import load_iteration
from buildlog.models import Iteration
from buildlog.package_models import (
    PackageAsset,
    PackageCaption,
    PackageSource,
    PublishingPackageManifest,
)
from buildlog.publication_content import (
    FinalArtifactResolver,
    ResolvedPublicationArtifact,
)
from buildlog.repository import RunRepository


class AssetPlanner(Protocol):
    """Planning capability required by the Package Builder."""

    def plan(self, iteration: Iteration, caption: str) -> PlannedAssets:
        """Return one validated card plan."""


@dataclass(frozen=True)
class PackageBuildResult:
    """Paths and metadata returned after a local package build."""

    package_dir: Path
    manifest_path: Path
    caption_path: Path
    asset_paths: tuple[Path, ...]
    manifest: PublishingPackageManifest
    reused_existing: bool = False


@dataclass(frozen=True)
class _ResolvedRunInput:
    iteration: Iteration
    artifact_id: str
    content_hash: str


class PublishingPackageBuilder:
    """Plan, render, and package one completed BuildLog run locally."""

    def __init__(
        self,
        repository: RunRepository,
        final_resolver: FinalArtifactResolver,
        planner: AssetPlanner,
        renderer: CardRenderer,
    ) -> None:
        self._repository = repository
        self._final_resolver = final_resolver
        self._planner = planner
        self._renderer = renderer

    def build(
        self,
        run_id: str,
        output_root: Path,
        *,
        reviewed: bool,
    ) -> PackageBuildResult:
        """Create one local package without invoking any publisher adapter."""
        if not reviewed:
            raise PackageBuildError(
                "package generation requires explicit confirmation that the "
                "source run has been human-reviewed"
            )

        final = self._final_resolver.resolve(run_id)
        run_input = self._resolve_run_input(run_id, final)
        planned = self._planner.plan(run_input.iteration, final.content)
        package_id = _package_id(
            final,
            run_input,
            planned,
            self._renderer.template_version,
        )
        package_dir = output_root.resolve() / package_id
        if package_dir.exists():
            return _load_existing_package(package_dir, package_id)

        output_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = output_root / f".{package_id}.{uuid4().hex}.tmp"
        try:
            temporary_dir.mkdir(parents=False, exist_ok=False)
            caption_path = temporary_dir / "caption.md"
            caption_path.write_text(final.content + "\n", encoding="utf-8")
            rendered = self._renderer.render(
                planned.plan,
                temporary_dir / "assets",
            )
            manifest = _manifest(
                package_id,
                run_input,
                final,
                planned,
                rendered,
                caption_path,
                temporary_dir,
                self._renderer,
            )
            manifest_path = temporary_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_dir.rename(package_dir)
        except PackageBuildError:
            _remove_temporary(temporary_dir)
            raise
        except OSError as exc:
            _remove_temporary(temporary_dir)
            raise PackageBuildError(f"could not write publishing package: {exc}") from exc
        except Exception:
            _remove_temporary(temporary_dir)
            raise

        return _result(package_dir, manifest, reused_existing=False)

    def _resolve_run_input(
        self,
        run_id: str,
        final: ResolvedPublicationArtifact,
    ) -> _ResolvedRunInput:
        input_artifacts = [
            artifact
            for artifact in self._repository.list_artifacts(run_id)
            if artifact.artifact_type == "input"
        ]
        if len(input_artifacts) != 1:
            raise PackageBuildError(
                "reviewed run must contain exactly one indexed input artifact"
            )
        artifact = input_artifacts[0]
        path = Path(artifact.file_path)
        if not path.is_file():
            raise PackageBuildError(f"run input artifact is missing: {path}")
        resolved_path = path.resolve()
        if resolved_path.parent != final.artifact_path.parent:
            raise PackageBuildError(
                "run input and final artifact must belong to the same run directory"
            )
        try:
            actual_hash = sha256_file(resolved_path)
        except OSError as exc:
            raise PackageBuildError(
                f"could not read run input artifact: {resolved_path}"
            ) from exc
        if actual_hash != artifact.content_hash:
            raise PackageBuildError(
                "run input does not match its indexed SHA-256 hash"
            )
        return _ResolvedRunInput(
            iteration=load_iteration(resolved_path),
            artifact_id=artifact.id,
            content_hash=actual_hash,
        )


def _package_id(
    final: ResolvedPublicationArtifact,
    run_input: _ResolvedRunInput,
    planned: PlannedAssets,
    template_version: str,
) -> str:
    identity = {
        "target": "linkedin",
        "run_id": final.run_id,
        "input_hash": run_input.content_hash,
        "caption_hash": final.content_hash,
        "plan": planned.plan.model_dump(mode="json"),
        "planner": planned.provenance.model_dump(mode="json"),
        "template_version": template_version,
    }
    digest = sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"pkg-{digest[:20]}"


def _manifest(
    package_id: str,
    run_input: _ResolvedRunInput,
    final: ResolvedPublicationArtifact,
    planned: PlannedAssets,
    rendered: list[RenderedCard],
    caption_path: Path,
    package_root: Path,
    renderer: CardRenderer,
) -> PublishingPackageManifest:
    assets = [
        PackageAsset(
            position=card.position,
            type=card.spec.type,
            file=card.path.relative_to(package_root).as_posix(),
            sha256=sha256_file(card.path),
            width=renderer.width,
            height=renderer.height,
            alt_text=card.alt_text,
            spec=card.spec,
        )
        for card in rendered
    ]
    return PublishingPackageManifest(
        schema_version="1",
        package_id=package_id,
        target="linkedin",
        review_status="pending",
        source=PackageSource(
            run_id=final.run_id,
            iteration_id=run_input.iteration.id,
            input_artifact_id=run_input.artifact_id,
            input_hash=run_input.content_hash,
            caption_artifact_id=final.artifact_id,
            caption_hash=final.content_hash,
        ),
        planner=planned.provenance,
        caption=PackageCaption(
            file=caption_path.relative_to(package_root).as_posix(),
            sha256=sha256_file(caption_path),
        ),
        assets=assets,
        template_version="v1",
        created_at=datetime.now(UTC),
    )


def _load_existing_package(
    package_dir: Path,
    package_id: str,
) -> PackageBuildResult:
    manifest_path = package_dir / "manifest.json"
    try:
        manifest = PublishingPackageManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise PackageBuildError(
            f"existing package is incomplete or invalid: {package_dir}"
        ) from exc
    if manifest.package_id != package_id:
        raise PackageBuildError(f"existing package identity mismatch: {package_dir}")
    files = [(manifest.caption.file, manifest.caption.sha256)] + [
        (asset.file, asset.sha256) for asset in manifest.assets
    ]
    for relative_path, expected_hash in files:
        path = (package_dir / relative_path).resolve()
        if not path.is_relative_to(package_dir.resolve()) or not path.is_file():
            raise PackageBuildError(
                f"existing package file is missing or unsafe: {relative_path}"
            )
        if sha256_file(path) != expected_hash:
            raise PackageBuildError(
                f"existing package file hash mismatch: {relative_path}"
            )
    return _result(package_dir, manifest, reused_existing=True)


def _result(
    package_dir: Path,
    manifest: PublishingPackageManifest,
    *,
    reused_existing: bool,
) -> PackageBuildResult:
    return PackageBuildResult(
        package_dir=package_dir,
        manifest_path=package_dir / "manifest.json",
        caption_path=package_dir / manifest.caption.file,
        asset_paths=tuple(package_dir / asset.file for asset in manifest.assets),
        manifest=manifest,
        reused_existing=reused_existing,
    )


def _remove_temporary(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
