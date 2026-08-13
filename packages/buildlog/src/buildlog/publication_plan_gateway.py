"""Private durable state for composite publication plans.

Configured OAuth/network wiring intentionally lives outside this module.  Callers
inject an adapter factory after a human has approved the operational boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, Field, field_validator

from buildlog.publication_content import normalize_publication_content

PublicationPlanChannel = Literal["linkedin", "x"]


class PublicationPlanError(RuntimeError):
    """A composite plan cannot safely proceed."""


class PlanImage(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    alt_text: str = Field(min_length=1)
    filename: str = Field(min_length=1)


class PublicationPlan(BaseModel):
    plan_id: str = Field(min_length=1)
    platform: PublicationPlanChannel
    text_parts: list[str] = Field(min_length=1, max_length=12, repr=False)
    image: PlanImage
    source_package_id: str = Field(min_length=1)
    source_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["staged", "publishing", "succeeded", "failed"] = "staged"
    account_reference: str | None = None
    post_receipt_ids: list[str] = Field(default_factory=list)
    external_post_ids: list[str] = Field(default_factory=list)

    @field_validator("text_parts")
    @classmethod
    def validate_parts(cls, values: list[str]) -> list[str]:
        cleaned = [normalize_publication_content(value) for value in values]
        if any(not value for value in cleaned):
            raise ValueError("publication plan text parts must not be blank")
        return cleaned


class PublicationPlanPreview(BaseModel):
    plan_id: str
    plan_hash: str
    platform: PublicationPlanChannel
    account_reference: str
    account_display_name: str = Field(repr=False)
    parts: list[str] = Field(repr=False)
    image: PlanImage
    source_package_id: str
    source_receipt_hash: str
    duplicate_found: bool
    indeterminate_found: bool
    network_publish_will_occur: Literal[False] = False


class PublicationPlanResult(BaseModel):
    plan_id: str
    plan_hash: str
    platform: PublicationPlanChannel
    account_reference: str
    post_receipt_ids: list[str]
    external_post_ids: list[str]
    status: Literal["succeeded"]


class PublicationPlanAdapter(Protocol):
    """Injectable operational seam; implementations own identity and HTTP."""

    def preview(self, plan: PublicationPlan) -> PublicationPlanPreview:
        """Resolve account and duplicate state without upload or publication."""

    def publish(
        self,
        plan: PublicationPlan,
        *,
        approved_account_reference: str,
    ) -> PublicationPlanResult:
        """Publish a fresh plan, durably recording each upload and post."""


class PublicationPlanAdapterFactory(Protocol):
    def __call__(self, platform: PublicationPlanChannel) -> PublicationPlanAdapter:
        """Return one isolated platform adapter."""


class PublicationPlanGateway:
    """Stage immutable plans and delegate preview/publication to an injected adapter."""

    def __init__(
        self,
        *,
        data_root: Path,
        config_root: Path,
        platform: PublicationPlanChannel,
        adapter_factory: PublicationPlanAdapterFactory | None = None,
    ) -> None:
        self.data_root = data_root.absolute()
        self.config_root = config_root.absolute()
        self.platform = platform
        self.adapter_factory = adapter_factory
        self.plans_root = self.data_root / "publication-plans"
        self.plans_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.plans_root.chmod(0o700)

    def stage(
        self,
        *,
        text_parts: list[str],
        image_path: Path,
        alt_text: str,
        source_package_id: str,
        source_receipt_hash: str,
    ) -> PublicationPlan:
        self._validate_shape(text_parts)
        self._validate_source_hash(source_receipt_hash)
        plan_id = f"plan-{uuid4()}"
        image = self._copy_image(image_path, alt_text, plan_id=plan_id)
        normalized_parts = [normalize_publication_content(part) for part in text_parts]
        plan_hash = _plan_hash(
            self.platform,
            normalized_parts,
            image,
            source_package_id,
            source_receipt_hash,
        )
        plan = PublicationPlan(
            plan_id=plan_id,
            platform=self.platform,
            text_parts=normalized_parts,
            image=image,
            source_package_id=source_package_id,
            source_receipt_hash=source_receipt_hash,
            plan_hash=plan_hash,
        )
        self._copy_parts(plan)
        self._save(plan)
        return plan

    def preview(self, plan_id: str) -> PublicationPlanPreview:
        plan = self._load_verified(plan_id)
        if plan.status != "staged":
            raise PublicationPlanError("only an unsubmitted plan can be previewed")
        return self._adapter().preview(plan)

    def publish(
        self,
        plan_id: str,
        *,
        confirmation: str,
        approved_plan_hash: str,
        approved_account_reference: str,
    ) -> PublicationPlanResult:
        plan = self._load_verified(plan_id)
        if confirmation != "PUBLISH":
            raise PublicationPlanError("publication requires the exact confirmation PUBLISH")
        if plan.status != "staged":
            raise PublicationPlanError("a submitted plan must not be retried or resumed")
        if plan.plan_hash != approved_plan_hash:
            raise PublicationPlanError("publication plan changed after preview")
        preview = self._adapter().preview(plan)
        if preview.account_reference != approved_account_reference:
            raise PublicationPlanError("authenticated account changed after preview")
        if preview.duplicate_found:
            raise PublicationPlanError("a publication-plan part was already published")
        if preview.indeterminate_found:
            raise PublicationPlanError("a publication-plan part has an unresolved attempt")
        plan.status = "publishing"
        plan.account_reference = preview.account_reference
        self._save(plan)
        try:
            result = self._adapter().publish(
                plan,
                approved_account_reference=approved_account_reference,
            )
        except Exception:
            plan.status = "failed"
            self._save(plan)
            raise
        plan.status = "succeeded"
        plan.post_receipt_ids = result.post_receipt_ids
        plan.external_post_ids = result.external_post_ids
        self._save(plan)
        return result

    def _copy_image(self, image_path: Path, alt_text: str, *, plan_id: str) -> PlanImage:
        cleaned_alt = alt_text.strip()
        if not cleaned_alt:
            raise PublicationPlanError("image alt text must not be blank")
        if image_path.is_symlink() or not image_path.is_file():
            raise PublicationPlanError("publication image must be a regular file")
        try:
            with Image.open(image_path) as image:
                if image.format != "PNG":
                    raise PublicationPlanError("publication image must be a PNG")
                width, height = image.size
        except OSError as exc:
            raise PublicationPlanError("publication image could not be read") from exc
        plan_dir = self._plan_dir(plan_id)
        plan_dir.mkdir(mode=0o700)
        copied = plan_dir / "image.png"
        shutil.copyfile(image_path, copied)
        if os.name == "posix":
            copied.chmod(0o600)
        return PlanImage(
            sha256=hashlib.sha256(copied.read_bytes()).hexdigest(),
            mime_type="image/png",
            width=width,
            height=height,
            alt_text=cleaned_alt,
            filename="image.png",
        )

    def _load_verified(self, plan_id: str) -> PublicationPlan:
        path = self._plan_dir(plan_id) / "plan.json"
        try:
            plan = PublicationPlan.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:
            raise PublicationPlanError("publication plan could not be loaded") from exc
        image_path = self._plan_dir(plan_id) / plan.image.filename
        if (
            image_path.is_symlink()
            or not image_path.is_file()
            or hashlib.sha256(image_path.read_bytes()).hexdigest() != plan.image.sha256
        ):
            raise PublicationPlanError("staged image changed after plan creation")
        for index, expected_text in enumerate(plan.text_parts, start=1):
            part_path = self._part_path(plan.plan_id, index)
            try:
                actual_text = part_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise PublicationPlanError("staged publication text is unavailable") from exc
            if (
                part_path.is_symlink()
                or normalize_publication_content(actual_text) != expected_text
            ):
                raise PublicationPlanError("staged publication text changed after plan creation")
        expected = _plan_hash(
            plan.platform,
            plan.text_parts,
            plan.image,
            plan.source_package_id,
            plan.source_receipt_hash,
        )
        if expected != plan.plan_hash:
            raise PublicationPlanError("publication plan integrity check failed")
        self._validate_shape(plan.text_parts)
        return plan

    def _adapter(self) -> PublicationPlanAdapter:
        if self.adapter_factory is None:
            from buildlog.configured_publication_plan import (
                ConfiguredPublicationPlanAdapter,
            )

            return ConfiguredPublicationPlanAdapter(
                data_root=self.data_root,
                config_root=self.config_root,
                platform=self.platform,
            )
        return self.adapter_factory(self.platform)

    def _copy_parts(self, plan: PublicationPlan) -> None:
        for index, content in enumerate(plan.text_parts, start=1):
            path = self._part_path(plan.plan_id, index)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _part_path(self, plan_id: str, index: int) -> Path:
        if index < 1 or index > 12:
            raise PublicationPlanError("invalid publication-plan part index")
        return self._plan_dir(plan_id) / f"part-{index:02d}.md"

    def _save(self, plan: PublicationPlan) -> None:
        self._atomic_json(self._plan_dir(plan.plan_id) / "plan.json", plan.model_dump())

    def _atomic_json(self, path: Path, payload: object) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        if os.name == "posix":
            temporary.chmod(0o600)
        temporary.replace(path)

    def _plan_dir(self, plan_id: str) -> Path:
        if not plan_id.startswith("plan-") or "/" in plan_id or "\\" in plan_id:
            raise PublicationPlanError("invalid plan identifier")
        return self.plans_root / plan_id

    def _validate_shape(self, text_parts: list[str]) -> None:
        if self.platform == "linkedin" and len(text_parts) != 1:
            raise PublicationPlanError("LinkedIn image plans require exactly one text part")
        if self.platform == "x" and not 1 <= len(text_parts) <= 12:
            raise PublicationPlanError("X image threads require one to twelve text parts")

    @staticmethod
    def _validate_source_hash(value: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise PublicationPlanError("source receipt hash must be SHA-256")


def _plan_hash(
    platform: str,
    parts: list[str],
    image: PlanImage,
    source_package_id: str,
    source_receipt_hash: str,
) -> str:
    payload = {
        "platform": platform,
        "parts": parts,
        "image": image.model_dump(mode="json"),
        "source_package_id": source_package_id,
        "source_receipt_hash": source_receipt_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
