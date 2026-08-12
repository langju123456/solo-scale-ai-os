"""Resolve the exact human-reviewed text eligible for publication."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from buildlog.linkedin_errors import PublicationValidationError
from buildlog.repository import RunRepository
from buildlog.review_policy import HUMAN_REVIEW_WARNING
from buildlog.terminal_safety import is_unsafe_terminal_character


@dataclass(frozen=True)
class ResolvedPublicationArtifact:
    """One completed final artifact and its normalized publishable content."""

    run_id: str
    artifact_id: str
    artifact_path: Path
    content: str = field(repr=False)
    content_hash: str


class FinalArtifactResolver:
    """Resolve only an existing completed run's indexed final artifact."""

    def __init__(
        self,
        repository: RunRepository,
        allowed_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.allowed_root = (
            allowed_root.resolve() if allowed_root is not None else None
        )

    def resolve(self, run_id: str) -> ResolvedPublicationArtifact:
        """Return validated publishable content without rerunning generation."""
        run = self.repository.get_run(run_id)
        if run is None:
            raise PublicationValidationError(f"BuildLog run does not exist: {run_id}")
        if run.status != "completed":
            raise PublicationValidationError(
                f"BuildLog run is not completed: {run_id} ({run.status})"
            )
        final_artifacts = [
            artifact
            for artifact in self.repository.list_artifacts(run_id)
            if artifact.artifact_type == "final"
        ]
        if len(final_artifacts) != 1:
            raise PublicationValidationError(
                "BuildLog run must contain exactly one indexed final artifact: "
                f"{run_id}"
            )
        artifact = final_artifacts[0]
        path = Path(artifact.file_path)
        if not path.is_file():
            raise PublicationValidationError(
                f"Final artifact file is missing: {path}"
            )
        resolved_path = path.resolve()
        if (
            self.allowed_root is not None
            and not resolved_path.is_relative_to(self.allowed_root)
        ):
            raise PublicationValidationError(
                "Final artifact is outside the configured runs directory: "
                f"{resolved_path}"
            )
        path = resolved_path
        try:
            raw_bytes = path.read_bytes()
            raw_content = raw_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PublicationValidationError(
                f"Final artifact could not be read: {path}"
            ) from exc
        actual_artifact_hash = hashlib.sha256(raw_bytes).hexdigest()
        if actual_artifact_hash != artifact.content_hash:
            raise PublicationValidationError(
                "Final artifact does not match its indexed SHA-256 hash. "
                f"The run trace may have changed: {path}"
            )
        unnormalized_content = _remove_review_footer(raw_content)
        unsafe_character = next(
            (
                character
                for character in unnormalized_content
                if is_unsafe_terminal_character(character)
                and character not in {"\n", "\t"}
            ),
            None,
        )
        if unsafe_character is not None:
            raise PublicationValidationError(
                "Final artifact contains an unsafe control character "
                f"(U+{ord(unsafe_character):04X}): {path}"
            )
        content = normalize_publication_content(unnormalized_content)
        if not content:
            raise PublicationValidationError(
                f"Final artifact contains no publishable content: {path}"
            )
        return ResolvedPublicationArtifact(
            run_id=run_id,
            artifact_id=artifact.id,
            artifact_path=path,
            content=content,
            content_hash=publication_content_hash(content),
        )


def extract_publishable_content(value: str) -> str:
    """Remove only the exact known review footer and normalize line endings."""
    return normalize_publication_content(_remove_review_footer(value))


def _remove_review_footer(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.endswith(HUMAN_REVIEW_WARNING):
        normalized = normalized[: -len(HUMAN_REVIEW_WARNING)]
    return normalized


def normalize_publication_content(value: str) -> str:
    """Normalize content deterministically without changing visible wording."""
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def publication_content_hash(value: str) -> str:
    """Return SHA-256 for normalized publication content."""
    normalized = normalize_publication_content(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
