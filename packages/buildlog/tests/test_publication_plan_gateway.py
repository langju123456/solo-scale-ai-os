from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_http import LinkedInHttpResult
from buildlog.linkedin_token_store import LinkedInToken
from buildlog.publication_plan_gateway import (
    PublicationPlanError,
    PublicationPlanGateway,
    PublicationPlanPreview,
    PublicationPlanResult,
)
from buildlog.publication_plan_publishers import (
    build_linkedin_image_post_payload,
    build_x_image_thread_payload,
    build_x_metadata_payload,
    build_x_upload_payload,
)
from buildlog.x_config import XSettings
from buildlog.x_http import XHttpResult
from buildlog.x_token_store import XToken
from PIL import Image
from pydantic import SecretStr


def test_stage_binds_ordered_text_png_and_image_metadata(tmp_path: Path) -> None:
    image_path = _png(tmp_path)
    gateway = PublicationPlanGateway(
        data_root=tmp_path / "data",
        config_root=tmp_path,
        platform="x",
    )
    plan = gateway.stage(
        text_parts=["root  ", "reply"],
        image_path=image_path,
        alt_text=" diagram ",
        source_package_id="package-1",
        source_receipt_hash="a" * 64,
    )

    assert plan.text_parts == ["root", "reply"]
    assert plan.image.mime_type == "image/png"
    assert plan.image.alt_text == "diagram"
    assert (gateway.plans_root / plan.plan_id / "image.png").is_file()
    assert gateway._load_verified(plan.plan_id).plan_hash == plan.plan_hash


def test_stage_rejects_wrong_plan_shape_and_non_png(tmp_path: Path) -> None:
    gateway = PublicationPlanGateway(
        data_root=tmp_path / "data",
        config_root=tmp_path,
        platform="linkedin",
    )
    with pytest.raises(PublicationPlanError, match="exactly one"):
        gateway.stage(
            text_parts=["one", "two"],
            image_path=_png(tmp_path),
            alt_text="alt",
            source_package_id="package-1",
            source_receipt_hash="a" * 64,
        )


def test_preview_and_publish_require_injected_adapter_and_exact_binding(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    gateway = PublicationPlanGateway(
        data_root=tmp_path / "data",
        config_root=tmp_path,
        platform="linkedin",
        adapter_factory=lambda _: adapter,
    )
    plan = gateway.stage(
        text_parts=["post"],
        image_path=_png(tmp_path),
        alt_text="alt",
        source_package_id="package-1",
        source_receipt_hash="a" * 64,
    )
    preview = gateway.preview(plan.plan_id)
    with pytest.raises(PublicationPlanError, match="exact confirmation"):
        gateway.publish(
            plan.plan_id,
            confirmation="yes",
            approved_plan_hash=preview.plan_hash,
            approved_account_reference=preview.account_reference,
        )
    result = gateway.publish(
        plan.plan_id,
        confirmation="PUBLISH",
        approved_plan_hash=preview.plan_hash,
        approved_account_reference=preview.account_reference,
    )

    assert result.external_post_ids == ["123"]
    assert gateway._load_verified(plan.plan_id).status == "succeeded"


def test_payloads_attach_x_image_only_to_root_and_chain_replies() -> None:
    root = build_x_image_thread_payload(
        content="root", media_id="44", reply_to_post_id=None
    )
    reply = build_x_image_thread_payload(
        content="reply", media_id=None, reply_to_post_id="123"
    )

    assert root["media"] == {"media_ids": ["44"]}
    assert "reply" not in root
    assert reply["reply"] == {"in_reply_to_tweet_id": "123"}
    assert "media" not in reply
    assert build_x_metadata_payload("44", "alt") == {
        "id": "44",
        "metadata": {"alt_text": {"text": "alt"}},
    }
    assert build_x_upload_payload(b"png")["media_category"] == "tweet_image"
    assert build_linkedin_image_post_payload(
        author_urn="urn:li:person:abc",
        content="post",
        media_id="urn:li:image:1",
        alt_text="alt",
    )["content"] == {"media": {"id": "urn:li:image:1", "altText": "alt"}}


def test_configured_x_plan_uploads_root_image_then_chains_durable_replies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    plan_root = tmp_path / "data" / "publication-plans"

    class Store:
        def load(self):
            now = datetime.now(UTC)
            return XToken(
                access_token=SecretStr("token"),
                expires_at=now + timedelta(hours=1),
                obtained_at=now,
                scopes={"tweet.write", "users.read", "media.write"},
            )

    class Http:
        def __init__(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds > 0

        def close(self) -> None:
            events.append("closed")

        def get_me(self, url: str, *, access_token: str) -> XHttpResult:
            assert access_token == "token"
            return XHttpResult(
                status_code=200,
                json_body={"data": {"id": "42", "username": "operator", "name": "Operator"}},
            )

        def upload_media(self, url: str, *, access_token: str, payload):
            events.append("upload")
            assert payload["media_category"] == "tweet_image"
            return XHttpResult(status_code=200, json_body={"data": {"id": "77"}})

        def set_media_metadata(self, url: str, *, access_token: str, payload):
            events.append(("metadata", payload))
            return XHttpResult(status_code=200, json_body={"data": {"id": "77"}})

        def create_post(self, url: str, *, access_token: str, payload):
            post_calls = [item for item in events if isinstance(item, tuple) and item[0] == "post"]
            if post_calls:
                progress_files = list(plan_root.glob("plan-*/publication-progress.json"))
                assert len(progress_files) == 1
                assert json.loads(progress_files[0].read_text())["external_post_ids"] == ["100"]
            post_id = str(100 + len(post_calls))
            events.append(("post", payload))
            return XHttpResult(status_code=201, json_body={"data": {"id": post_id}})

    monkeypatch.setattr(
        "buildlog.configured_publication_plan.load_x_settings",
        lambda _: XSettings(client_id="client"),
    )
    monkeypatch.setattr("buildlog.configured_publication_plan.FileXTokenStore", Store)
    monkeypatch.setattr("buildlog.configured_publication_plan.XHttpClient", Http)

    gateway = PublicationPlanGateway(
        data_root=tmp_path / "data",
        config_root=tmp_path,
        platform="x",
    )
    plan = gateway.stage(
        text_parts=["1/2 root", "2/2 reply"],
        image_path=_png(tmp_path),
        alt_text="diagram",
        source_package_id="package-1",
        source_receipt_hash="b" * 64,
    )
    preview = gateway.preview(plan.plan_id)
    result = gateway.publish(
        plan.plan_id,
        confirmation="PUBLISH",
        approved_plan_hash=preview.plan_hash,
        approved_account_reference=preview.account_reference,
    )

    posts = [item[1] for item in events if isinstance(item, tuple) and item[0] == "post"]
    assert result.external_post_ids == ["100", "101"]
    assert posts[0]["media"] == {"media_ids": ["77"]}
    assert posts[1]["reply"] == {"in_reply_to_tweet_id": "100"}
    assert "media" not in posts[1]


def test_configured_linkedin_plan_uploads_receipt_before_image_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    plan_root = tmp_path / "data" / "publication-plans"

    class Store:
        def load(self):
            now = datetime.now(UTC)
            return LinkedInToken(
                access_token=SecretStr("token"),
                expires_at=now + timedelta(hours=1),
                obtained_at=now,
                scopes={"openid", "profile", "w_member_social"},
                scope_source="response",
            )

    class Http:
        def __init__(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds > 0

        def close(self) -> None:
            events.append("closed")

        def get_userinfo(self, url: str, *, access_token: str) -> LinkedInHttpResult:
            return LinkedInHttpResult(200, {}, {"sub": "abc", "name": "Operator"}, "")

        def initialize_image(
            self, url: str, *, access_token: str, api_version: str, owner_urn: str
        ) -> LinkedInHttpResult:
            assert owner_urn == "urn:li:person:abc"
            events.append("initialize")
            return LinkedInHttpResult(
                200,
                {},
                {
                    "value": {
                        "uploadUrl": "https://www.linkedin.com/dms-uploads/image?sig=1",
                        "image": "urn:li:image:abc",
                    }
                },
                "",
            )

        def upload_image(self, url: str, *, access_token: str, content: bytes):
            events.append("upload")
            return LinkedInHttpResult(201, {}, None, "")

        def create_post(self, url: str, *, access_token: str, api_version: str, payload):
            receipts = list(plan_root.glob("plan-*/upload-receipt.json"))
            assert len(receipts) == 1
            assert json.loads(receipts[0].read_text())["status"] == "succeeded"
            events.append(("post", payload))
            return LinkedInHttpResult(201, {"x-restli-id": "urn:li:share:123"}, None, "")

    monkeypatch.setattr(
        "buildlog.configured_publication_plan.load_linkedin_settings",
        lambda _: LinkedInSettings(
            client_id="client",
            client_secret=SecretStr("secret"),
        ),
    )
    monkeypatch.setattr("buildlog.configured_publication_plan.FileTokenStore", Store)
    monkeypatch.setattr("buildlog.configured_publication_plan.LinkedInHttpClient", Http)

    gateway = PublicationPlanGateway(
        data_root=tmp_path / "data",
        config_root=tmp_path,
        platform="linkedin",
    )
    plan = gateway.stage(
        text_parts=["LinkedIn post"],
        image_path=_png(tmp_path),
        alt_text="diagram",
        source_package_id="package-1",
        source_receipt_hash="c" * 64,
    )
    preview = gateway.preview(plan.plan_id)
    result = gateway.publish(
        plan.plan_id,
        confirmation="PUBLISH",
        approved_plan_hash=preview.plan_hash,
        approved_account_reference=preview.account_reference,
    )

    post_payload = next(item[1] for item in events if isinstance(item, tuple) and item[0] == "post")
    assert result.external_post_ids == ["urn:li:share:123"]
    assert post_payload["content"] == {
        "media": {"id": "urn:li:image:abc", "altText": "diagram"}
    }


class _Adapter:
    def preview(self, plan):
        return PublicationPlanPreview(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            platform=plan.platform,
            account_reference="account-1",
            account_display_name="Account",
            parts=plan.text_parts,
            image=plan.image,
            source_package_id=plan.source_package_id,
            source_receipt_hash=plan.source_receipt_hash,
            duplicate_found=False,
            indeterminate_found=False,
        )

    def publish(self, plan, *, approved_account_reference):
        assert approved_account_reference == "account-1"
        return PublicationPlanResult(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            platform=plan.platform,
            account_reference=approved_account_reference,
            post_receipt_ids=["receipt-1"],
            external_post_ids=["123"],
            status="succeeded",
        )


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "image.png"
    Image.new("RGB", (3, 2)).save(path)
    return path
