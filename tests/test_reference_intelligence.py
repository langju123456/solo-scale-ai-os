from pathlib import Path
from typing import Literal, TypeVar

import pytest
from pydantic import BaseModel

from soloscale.content_models import (
    ClaimStatus,
    ContentBrief,
    ContentClaim,
)
from soloscale.content_ui import ContentFormStatus, content_page, run_content_form
from soloscale.content_workspace import (
    ContentWorkspaceError,
    build_content_drafts,
    content_download,
    run_content_workspace,
    run_content_workspace_with_gateway,
)
from soloscale.editorial_models import ProviderIdentity, ProviderKind
from soloscale.model_gateway import (
    GatewayConfigurationState,
    GatewayDescriptor,
    GatewayTransportScope,
    ModelProviderId,
)
from soloscale.reference_intelligence import extract_content_pattern

_DISTINCTIVE_REFERENCE_PHRASE = (
    "violet compasses only point north when the silent workshop bell rings"
)
_REFERENCE_TEXT = (
    "Most people add another feature when feedback is missing.\n\n"
    "I did that too, but the real problem was deciding what outcome mattered. "
    f"{_DISTINCTIVE_REFERENCE_PHRASE}.\n\n"
    "The useful pattern was simple: show the failure, investigate the cause, "
    "reveal the lesson, then ask one honest question.\n\n"
    "What would you validate before building again?"
)

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


class _RecordingGateway:
    descriptor = GatewayDescriptor(
        provider=ModelProviderId.OLLAMA,
        display_name="Test gateway",
        configuration_state=GatewayConfigurationState.CONFIGURED,
        transport_scope=GatewayTransportScope.LOOPBACK,
        model="test-model",
        base_url="http://127.0.0.1:11434",
    )

    def __init__(self, brief: ContentBrief) -> None:
        self.drafts = build_content_drafts(brief)
        self.users: list[str] = []

    def complete(
        self,
        schema: type[_ResponseT],
        *,
        system: str,
        user: str,
        reasoning_effort: Literal["none", "low"] = "low",
    ) -> _ResponseT:
        del system, reasoning_effort
        self.users.append(user)
        return schema.model_validate(self.drafts.model_dump(mode="json"))


def _brief(reference_text: str) -> tuple[ContentBrief, str]:
    asset, pattern, normalized = extract_content_pattern(
        reference_text,
        title="A builder story pattern",
        visual_notes="talking head, screen recording, screenshots, large captions",
    )
    return (
        ContentBrief(
            topic="A grounded debugging lesson",
            audience="AI builders",
            language="English",
            call_to_action="What would you inspect first?",
            source_label="https://example.test/verified-receipt",
            claims=[
                ContentClaim(
                    id="CLAIM-01",
                    text="The captured trace reached its configured output-token limit.",
                    status=ClaimStatus.VERIFIED,
                    receipt="https://example.test/verified-receipt",
                    limits="The trace does not prove every provider behaves the same way.",
                )
            ],
            reference_asset=asset,
            content_pattern=pattern,
        ),
        normalized,
    )


def test_reference_pattern_guides_a_private_original_video_script(
    tmp_path: Path,
) -> None:
    brief, normalized = _brief(_REFERENCE_TEXT)
    run = run_content_workspace(
        data_root=tmp_path / ".soloscale",
        brief=brief,
        reference_source_text=normalized,
    )
    run_dir = tmp_path / ".soloscale" / "content-runs" / run.run_id

    assert run.network_used is False
    assert run.model_used is False
    assert "Reference pattern applied: contrarian contrast" in run.drafts.video_script
    assert "operator claim ledger only" in run.drafts.video_script
    assert "screen recording" in run.drafts.storyboard[0].visual
    assert "CLAIM-01" in run.drafts.video_script
    assert _DISTINCTIVE_REFERENCE_PHRASE not in run.drafts.video_script
    assert (run_dir / "17_reference_asset.json").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "18_content_pattern.json").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "19_reference_source.txt").read_text().strip() == normalized
    assert _DISTINCTIVE_REFERENCE_PHRASE not in (
        run_dir / "06_publish_pack.json"
    ).read_text()
    assert _DISTINCTIVE_REFERENCE_PHRASE not in (
        run_dir / "07_provenance.json"
    ).read_text()
    assert _DISTINCTIVE_REFERENCE_PHRASE not in (
        run_dir / "run.json"
    ).read_text()
    name, pattern_bytes = content_download(
        tmp_path / ".soloscale", run.run_id, "reference-pattern.json"
    )
    assert name == "18_content_pattern.json"
    assert b'"facts_source": "operator_claim_ledger_only"' in pattern_bytes
    with pytest.raises(ContentWorkspaceError, match="not downloadable"):
        content_download(
            tmp_path / ".soloscale", run.run_id, "reference-source.txt"
        )

    copied = build_content_drafts(brief).model_copy(
        update={
            "video_script": (
                build_content_drafts(brief).video_script
                + f"\n{_DISTINCTIVE_REFERENCE_PHRASE}\n"
            )
        }
    )
    with pytest.raises(ContentWorkspaceError, match="distinctive reference phrase"):
        run_content_workspace(
            data_root=tmp_path / "copied",
            brief=brief,
            drafts=copied,
            provider=ProviderIdentity(
                kind=ProviderKind.OLLAMA,
                provider="test",
                model="test-model",
            ),
            prompt_version="reference-test-v1",
            reference_source_text=normalized,
        )
    assert not (tmp_path / "copied").exists()

    gateway = _RecordingGateway(brief)
    run_content_workspace_with_gateway(
        data_root=tmp_path / "gateway",
        brief=brief,
        gateway=gateway,
        reference_source_text=normalized,
    )
    assert len(gateway.users) == 1
    assert "contrarian contrast" in gateway.users[0]
    assert "A builder story pattern" not in gateway.users[0]
    assert "Reference creator" not in gateway.users[0]
    assert _DISTINCTIVE_REFERENCE_PHRASE not in gateway.users[0]
    assert '"raw_reference_included": false' in gateway.users[0]

    assert brief.reference_asset is not None
    tampered = brief.model_copy(
        update={
            "reference_asset": brief.reference_asset.model_copy(
                update={"raw_sha256": "0" * 64}
            )
        }
    )
    with pytest.raises(ContentWorkspaceError, match="does not match"):
        run_content_workspace_with_gateway(
            data_root=tmp_path / "preflight",
            brief=tampered,
            gateway=gateway,
            reference_source_text=normalized,
        )
    assert len(gateway.users) == 1
    assert not (tmp_path / "preflight").exists()


def test_content_ui_accepts_reference_text_and_shows_only_the_pattern(
    tmp_path: Path,
) -> None:
    form = {
        "topic": "A grounded debugging lesson",
        "audience": "AI builders",
        "language": "English",
        "source_label": "https://example.test/verified-receipt",
        "verified_claims": (
            "The trace reached its configured output-token limit. | "
            "https://example.test/verified-receipt | Synthetic trace only."
        ),
        "observed_claims": "",
        "hypotheses": "",
        "planned": "",
        "call_to_action": "What would you inspect first?",
        "generation_mode": "template",
        "reference_title": "A builder story pattern",
        "reference_author": "Reference creator",
        "reference_text": _REFERENCE_TEXT,
        "reference_visual_notes": "screen recording and large captions",
    }
    result = run_content_form(form, tmp_path / ".soloscale")

    assert result.status is ContentFormStatus.GENERATED
    assert result.run_id is not None
    page = content_page(data_root=tmp_path / ".soloscale", run_id=result.run_id)
    assert "Reference Intelligence" in page
    assert "A builder story pattern" in page
    assert "contrarian contrast" in page
    assert "原文私有" in page
    assert f"/content/downloads/{result.run_id}/reference-pattern.json" in page
    assert _DISTINCTIVE_REFERENCE_PHRASE not in page

    empty_page = content_page(data_root=tmp_path / ".soloscale")
    assert 'name="reference_text"' in empty_page
    assert "第一版不抓取 URL" in empty_page
