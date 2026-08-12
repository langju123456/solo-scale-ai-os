from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from soloscale.content_models import (
    ClaimStatus,
    ContentBrief,
    ContentClaim,
    ContentDrafts,
    ContentRun,
    StoryboardScene,
)
from soloscale.resume_workspace import (
    ResumeWorkspaceStorageError,
    _atomic_private_write,
    _ensure_private_directory,
    _reject_symlink_ancestry,
)

_CONTENT_RUN_ID = re.compile(r"content-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{10}")
_PRIVATE_PATH = re.compile(
    r"(?:file://|(?:^|[\s'\"])/(?:Users|home|private|var|tmp)/|[A-Za-z]:\\)", re.I
)
_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{12,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{12,})"
)
_DOWNLOADS = {
    "linkedin.md": "02_linkedin.md",
    "x-thread.md": "03_x_thread.md",
    "video-script.md": "04_video_script.md",
    "storyboard.json": "05_storyboard.json",
    "publish-pack.json": "06_publish_pack.json",
    "provenance.json": "07_provenance.json",
}


class ContentWorkspaceError(ValueError):
    """Raised when a content draft would cross the local review boundary."""


def parse_claim_ledger(raw: str) -> list[ContentClaim]:
    """Parse `STATUS | claim | receipt | limits` lines into a strict claim ledger."""

    claims: list[ContentClaim] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", maxsplit=3)]
        if len(parts) < 2:
            raise ContentWorkspaceError(
                f"Claim line {line_number} must use STATUS | claim | receipt | limits"
            )
        try:
            status = ClaimStatus(parts[0].upper())
        except ValueError:
            raise ContentWorkspaceError(
                f"Claim line {line_number} has an unknown status"
            ) from None
        receipt = parts[2] if len(parts) >= 3 and parts[2] else None
        limits = parts[3] if len(parts) >= 4 and parts[3] else None
        try:
            claim = ContentClaim(
                id=f"CLAIM-{len(claims) + 1:02d}",
                text=parts[1],
                status=status,
                receipt=receipt,
                limits=limits,
            )
        except ValidationError as exc:
            raise ContentWorkspaceError(
                f"Claim line {line_number} does not satisfy the content contract"
            ) from exc
        claims.append(claim)
    if not claims:
        raise ContentWorkspaceError("At least one claim is required")
    if len(claims) > 8:
        raise ContentWorkspaceError("A content run accepts at most 8 claims")
    return claims


def _reject_private_output(value: str, *, field: str) -> None:
    if _PRIVATE_PATH.search(value):
        raise ContentWorkspaceError(f"{field} contains a private absolute path")
    if _SECRET.search(value):
        raise ContentWorkspaceError(f"{field} contains a credential-like value")


def _validate_public_fields(brief: ContentBrief) -> None:
    values = {
        "topic": brief.topic,
        "audience": brief.audience,
        "call_to_action": brief.call_to_action,
        "source_label": brief.source_label,
    }
    for claim in brief.claims:
        values[f"{claim.id}.text"] = claim.text
        values[f"{claim.id}.receipt"] = claim.receipt or ""
        values[f"{claim.id}.limits"] = claim.limits or ""
    for field, value in values.items():
        _reject_private_output(value, field=field)


def _status_heading(status: ClaimStatus, language: str) -> str:
    if language == "中文":
        return {
            ClaimStatus.VERIFIED: "已验证",
            ClaimStatus.OBSERVED: "已观察",
            ClaimStatus.HYPOTHESIS: "待验证假设",
            ClaimStatus.PLANNED: "下一步计划",
        }[status]
    return {
        ClaimStatus.VERIFIED: "Verified",
        ClaimStatus.OBSERVED: "Observed",
        ClaimStatus.HYPOTHESIS: "Hypothesis",
        ClaimStatus.PLANNED: "Planned",
    }[status]


def _claim_line(claim: ContentClaim, language: str) -> str:
    label = _status_heading(claim.status, language)
    return f"- [{label} · {claim.id}] {claim.text}"


def _render_linkedin(brief: ContentBrief) -> str:
    first_claim = brief.claims[0]
    grouped = {
        status: [
            claim
            for claim in brief.claims[1:]
            if claim.status is status
        ]
        for status in ClaimStatus
    }
    first_label = _status_heading(first_claim.status, brief.language)
    opening = f"{first_claim.text}\n[{first_label} · {first_claim.id}]"
    if brief.language == "中文":
        sections = [opening, "", f"我正在记录：{brief.topic}", ""]
        section_titles = {
            ClaimStatus.VERIFIED: "现在能被证据支持的事实：",
            ClaimStatus.OBSERVED: "目前的个人观察：",
            ClaimStatus.HYPOTHESIS: "仍需验证的假设：",
            ClaimStatus.PLANNED: "接下来会做的实验：",
        }
        limit_title = "明确边界："
        proof_title = "证据索引（发布前请换成可公开链接）："
    else:
        sections = [opening, "", f"I am documenting: {brief.topic}", ""]
        section_titles = {
            ClaimStatus.VERIFIED: "What the evidence supports today:",
            ClaimStatus.OBSERVED: "What I have observed:",
            ClaimStatus.HYPOTHESIS: "What remains a hypothesis:",
            ClaimStatus.PLANNED: "What I will test next:",
        }
        limit_title = "Explicit boundaries:"
        proof_title = "Evidence map (replace private receipts with public links before posting):"
    for status in ClaimStatus:
        claims = grouped[status]
        if not claims:
            continue
        sections.extend(
            [
                section_titles[status],
                *[_claim_line(claim, brief.language) for claim in claims],
                "",
            ]
        )
    limits = [claim for claim in brief.claims if claim.limits]
    if limits:
        sections.extend(
            [limit_title, *[f"- [{claim.id}] {claim.limits}" for claim in limits], ""]
        )
    receipts = [claim for claim in brief.claims if claim.receipt]
    if receipts:
        sections.extend(
            [proof_title, *[f"- {claim.id}: {claim.receipt}" for claim in receipts], ""]
        )
    sections.append(brief.call_to_action)
    return "\n".join(sections).strip() + "\n"


def _render_x_thread(brief: ContentBrief) -> list[str]:
    first_claim = brief.claims[0]
    first_label = _status_heading(first_claim.status, brief.language)
    posts = [f"{first_claim.text}\n[{first_label} · {first_claim.id}]"]
    for claim in brief.claims[1:]:
        label = _status_heading(claim.status, brief.language)
        post = f"[{label} · {claim.id}] {claim.text}"
        if claim.limits:
            post += (
                f"\nLimit: {claim.limits}"
                if brief.language == "English"
                else f"\n边界：{claim.limits}"
            )
        if len(post) > 280:
            raise ContentWorkspaceError(f"{claim.id} is too long for an X post")
        posts.append(post)
    proof_note = (
        "Proof links: see the attached claim ledger. Human review is still required."
        if brief.language == "English"
        else "证据链接见附带的 claim ledger；发布前仍需人工复核。"
    )
    posts.extend([proof_note, brief.call_to_action])
    if any(len(post) > 280 for post in posts):
        raise ContentWorkspaceError("Hook or call to action is too long for X")
    return [f"{index}/{len(posts)} {post}" for index, post in enumerate(posts, start=1)]


def _render_storyboard(brief: ContentBrief) -> list[StoryboardScene]:
    scenes: list[StoryboardScene] = []
    for index, claim in enumerate(brief.claims, start=1):
        start = (index - 1) * 6
        limit = f" · Limit: {claim.limits}" if claim.limits else ""
        scenes.append(
            StoryboardScene(
                id=f"SCENE-{index:02d}",
                start_second=start,
                end_second=start + 6,
                purpose=("Hook" if index == 1 else _status_heading(claim.status, "English")),
                visual=(
                    "HookCard with SourceBadge"
                    if index == 1
                    else "Evidence card with source badge and restrained motion"
                ),
                voiceover=claim.text,
                on_screen_text=f"{claim.status.value} · {claim.id}{limit}",
                claim_ids=[claim.id],
            )
        )
    start = len(scenes) * 6
    scenes.append(
        StoryboardScene(
            id=f"SCENE-{len(scenes) + 1:02d}",
            start_second=start,
            end_second=start + 6,
            purpose="CTA",
            visual="CTA card; no unsupported metric",
            voiceover=brief.call_to_action,
            on_screen_text=brief.call_to_action,
            claim_ids=[],
        )
    )
    return scenes


def _render_video_script(brief: ContentBrief, scenes: list[StoryboardScene]) -> str:
    lines = [f"# {brief.topic}", "", f"Audience: {brief.audience}", ""]
    for scene in scenes:
        lines.extend(
            [
                f"## {scene.start_second:02d}–{scene.end_second:02d}s · {scene.purpose}",
                "",
                f"- Voiceover: {scene.voiceover}",
                f"- Visual: {scene.visual}",
                f"- On-screen: {scene.on_screen_text}",
                f"- Claim anchors: {', '.join(scene.claim_ids) if scene.claim_ids else 'none'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def build_content_drafts(brief: ContentBrief) -> ContentDrafts:
    _validate_public_fields(brief)
    storyboard = _render_storyboard(brief)
    return ContentDrafts(
        linkedin=_render_linkedin(brief),
        x_thread=_render_x_thread(brief),
        video_script=_render_video_script(brief, storyboard),
        storyboard=storyboard,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_run_dir(data_root: Path) -> tuple[str, Path]:
    _reject_symlink_ancestry(data_root)
    _ensure_private_directory(data_root, parents=True)
    runs_root = data_root / "content-runs"
    _ensure_private_directory(runs_root)
    for _ in range(8):
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"content-{timestamp}-{uuid4().hex[:10]}"
        run_dir = runs_root / run_id
        if run_dir.exists() or run_dir.is_symlink():
            continue
        _ensure_private_directory(run_dir)
        return run_id, run_dir
    raise ContentWorkspaceError("Could not allocate a non-overwriting content run")


def run_content_workspace(*, data_root: Path, brief: ContentBrief) -> ContentRun:
    """Build a deterministic, private content candidate without publishing it."""

    drafts = build_content_drafts(brief)
    try:
        run_id, run_dir = _new_run_dir(data_root.absolute())
    except ResumeWorkspaceStorageError as exc:
        raise ContentWorkspaceError(str(exc)) from exc
    created_at = datetime.now(UTC).isoformat()
    artifact_paths = [
        "00_input.json",
        "01_claim_ledger.json",
        "02_linkedin.md",
        "03_x_thread.md",
        "04_video_script.md",
        "05_storyboard.json",
        "06_publish_pack.json",
        "07_provenance.json",
        "08_verification.json",
        "run.json",
    ]
    brief_payload = brief.model_dump(mode="json")
    drafts_payload = drafts.model_dump(mode="json")
    publish_pack = {
        "status": "DRAFT_REQUIRES_HUMAN_APPROVAL",
        "topic": brief.topic,
        "channels": ["LinkedIn", "X", "Short video"],
        "drafts": drafts_payload,
        "publication_performed": False,
        "next_gate": "Human fact-check and explicit per-channel publish approval",
    }
    provenance = {
        "source_label": brief.source_label,
        "claim_ledger_sha256": _sha256(brief_payload["claims"]),
        "claims": brief_payload["claims"],
        "boundary": (
            "Citation membership and operator classification are recorded; semantic support "
            "and public suitability still require human review."
        ),
    }
    verification = {
        "status": "PASS",
        "claim_count": len(brief.claims),
        "every_claim_has_anchor": all(claim.id for claim in brief.claims),
        "verified_and_observed_have_receipts": all(
            claim.receipt
            for claim in brief.claims
            if claim.status in {ClaimStatus.VERIFIED, ClaimStatus.OBSERVED}
        ),
        "private_path_scan_passed": True,
        "credential_shape_scan_passed": True,
        "network_used": False,
        "model_used": False,
        "publication_performed": False,
    }
    run = ContentRun(
        run_id=run_id,
        created_at=created_at,
        brief=brief,
        drafts=drafts,
        artifact_paths=artifact_paths,
        limitations=[
            "Drafts are deterministic editorial candidates, not semantic fact-check results.",
            "Receipts may still need public-safe URLs before posting.",
            "No account connection or publishing action was performed.",
        ],
    )
    artifacts = {
        "00_input.json": _canonical_json(brief_payload),
        "01_claim_ledger.json": _canonical_json({"claims": brief_payload["claims"]}),
        "02_linkedin.md": drafts.linkedin,
        "03_x_thread.md": "\n\n".join(drafts.x_thread) + "\n",
        "04_video_script.md": drafts.video_script,
        "05_storyboard.json": _canonical_json({"scenes": drafts_payload["storyboard"]}),
        "06_publish_pack.json": _canonical_json(publish_pack),
        "07_provenance.json": _canonical_json(provenance),
        "08_verification.json": _canonical_json(verification),
        "run.json": _canonical_json(run.model_dump(mode="json")),
    }
    try:
        for name in artifact_paths:
            _atomic_private_write(run_dir / name, artifacts[name])
    except (OSError, ResumeWorkspaceStorageError) as exc:
        raise ContentWorkspaceError("Private content artifacts could not be saved") from exc
    return run


def content_run_directory(data_root: Path, run_id: str) -> Path:
    if _CONTENT_RUN_ID.fullmatch(run_id) is None:
        raise ContentWorkspaceError("Content run id is invalid")
    root = data_root.absolute()
    try:
        _reject_symlink_ancestry(root)
    except ResumeWorkspaceStorageError as exc:
        raise ContentWorkspaceError(str(exc)) from exc
    run_dir = root / "content-runs" / run_id
    try:
        metadata = run_dir.lstat()
    except FileNotFoundError:
        raise ContentWorkspaceError("Content run is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or run_dir.is_symlink():
        raise ContentWorkspaceError("Content run is unsafe")
    return run_dir


def load_content_run(data_root: Path, run_id: str) -> ContentRun:
    run_dir = content_run_directory(data_root, run_id)
    path = run_dir / "run.json"
    if path.is_symlink() or not path.is_file():
        raise ContentWorkspaceError("Content run receipt is unavailable")
    try:
        return ContentRun.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise ContentWorkspaceError("Content run receipt is invalid") from exc


def content_download(data_root: Path, run_id: str, download_name: str) -> tuple[str, bytes]:
    artifact_name = _DOWNLOADS.get(download_name)
    if artifact_name is None:
        raise ContentWorkspaceError("Content artifact is not downloadable")
    run_dir = content_run_directory(data_root, run_id)
    path = run_dir / artifact_name
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise ContentWorkspaceError("Content artifact is unavailable") from None
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ContentWorkspaceError("Content artifact is unsafe")
    return artifact_name, path.read_bytes()
