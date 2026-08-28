import hashlib
import json
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import soloscale.youtube_publishing as youtube
from soloscale.youtube_publishing import (
    GoogleYouTubeProvider,
    YouTubePublishingJobManager,
    YouTubeUploadRequest,
    YouTubeUploadResult,
    load_youtube_accounts,
    load_youtube_receipt,
    normalize_upload_request,
    save_authorized_channel,
    validate_client_secret,
)

CHANNEL_ID = "UCabcdefghijk"


def _client_secret(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "not-a-real-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _distribution_package(data_root: Path, run_id: str) -> None:
    run_dir = data_root / "content-runs" / run_id
    run_dir.mkdir(parents=True)
    video = run_dir / "21_creator_video_youtube.mp4"
    video.write_bytes(b"synthetic-mp4")
    (run_dir / "26_distribution_package.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "artifacts": {
                    "video": {"sha256": hashlib.sha256(video.read_bytes()).hexdigest()}
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "27_youtube_upload.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "title": "Synthetic title",
                "description": "Synthetic description",
                "visibility": "private",
            }
        ),
        encoding="utf-8",
    )


def _wait(
    manager: YouTubePublishingJobManager,
    data_root: Path,
    job_id: str,
) -> youtube.YouTubeJobSnapshot:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = manager.get(data_root, job_id)
        assert snapshot is not None
        if snapshot.phase in {"SUCCESS", "FAILED"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("YouTube job did not finish")


class FakeProvider:
    def authorize(self, client_secret_path: Path) -> tuple[str, str, str]:
        assert validate_client_secret(client_secret_path)
        return (
            CHANNEL_ID,
            "SoloScale",
            json.dumps({"token": "redacted", "refresh_token": "redacted"}),
        )

    def upload(
        self,
        *,
        credentials: object,
        video_path: Path,
        request: YouTubeUploadRequest,
        progress: Callable[[int | None], None],
    ) -> YouTubeUploadResult:
        assert credentials is not None
        assert video_path.read_bytes() == b"synthetic-mp4"
        assert request.channel_id == CHANNEL_ID
        progress(50)
        return YouTubeUploadResult(
            video_id="video-123",
            video_url="https://youtu.be/video-123",
            uploaded_at="2026-08-28T12:00:00+00:00",
        )


def test_client_configuration_and_multichannel_store_are_private(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    selected = _client_secret(data_root / "integrations/youtube/client_secret.json")

    assert validate_client_secret(selected) == selected
    first = save_authorized_channel(
        data_root,
        channel_id=CHANNEL_ID,
        channel_title="SoloScale",
        credential_json=json.dumps({"token": "one"}),
    )
    second = save_authorized_channel(
        data_root,
        channel_id="UCzyxwvutsrqp",
        channel_title="AI Vision",
        credential_json=json.dumps({"token": "two"}),
    )

    assert {item.channel_id for item in load_youtube_accounts(data_root)} == {
        first.channel_id,
        second.channel_id,
    }
    token = data_root / "integrations/youtube" / first.token_file
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert stat.S_IMODE(token.parent.stat().st_mode) == 0o700
    accounts_payload = json.loads(
        (data_root / "integrations/youtube/accounts.json").read_text(encoding="utf-8")
    )
    assert all(
        set(item) == {"channel_id", "channel_title", "token_file", "connected_at"}
        for item in accounts_payload["accounts"]
    )
    assert "redacted" not in json.dumps(accounts_payload)


def test_background_connect_and_upload_create_receipt_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _client_secret(data_root / "integrations/youtube/client_secret.json")
    manager = YouTubePublishingJobManager(provider=FakeProvider())
    try:
        started = time.monotonic()
        connect_job = manager.start_connect(data_root=data_root)
        assert time.monotonic() - started < 0.2
        assert _wait(manager, data_root, connect_job.job_id).phase == "SUCCESS"

        run_id = "content-20260828T120000Z-deadbeef00"
        _distribution_package(data_root, run_id)
        monkeypatch.setattr(youtube, "load_channel_credentials", lambda *_: object())
        request = normalize_upload_request(
            run_id=run_id,
            channel_id=CHANNEL_ID,
            title="Synthetic title",
            description="Synthetic description",
            tags="AI, SoloScale",
            privacy_status="private",
        )
        upload_job = manager.start_upload(data_root=data_root, request=request)
        complete = _wait(manager, data_root, upload_job.job_id)

        assert complete.phase == "SUCCESS"
        assert complete.progress_percent == 100
        receipt = load_youtube_receipt(data_root, run_id, CHANNEL_ID)
        assert receipt is not None
        assert receipt["video_id"] == "video-123"
        assert receipt["privacy_status"] == "private"
        assert receipt["automatic_retry_count"] == 0
        assert "token" not in json.dumps(receipt)
    finally:
        manager.shutdown()


def test_google_provider_builds_resumable_videos_insert_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class Operation:
        def next_chunk(self) -> tuple[None, dict[str, str]]:
            return None, {"id": "video-456"}

    class Videos:
        def insert(self, **kwargs: object) -> Operation:
            captured.update(kwargs)
            return Operation()

    class Service:
        def videos(self) -> Videos:
            return Videos()

    def build_client(*args: object, **kwargs: object) -> Service:
        captured["build_args"] = args
        captured["build_kwargs"] = kwargs
        return Service()

    def media_file_upload(*args: object, **kwargs: object) -> object:
        captured["media_args"] = args
        captured["media_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        youtube,
        "_google_modules",
        lambda: (
            object(),
            object(),
            object(),
            (build_client, media_file_upload),
            Exception,
            Exception,
        ),
    )
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    result = GoogleYouTubeProvider().upload(
        credentials=object(),
        video_path=video,
        request=YouTubeUploadRequest(
            run_id="content-20260828T120000Z-deadbeef00",
            channel_id=CHANNEL_ID,
            title="Title",
            description="Description",
            tags=("AI",),
            privacy_status="unlisted",
        ),
        progress=lambda _: None,
    )

    assert captured["part"] == "snippet,status"
    assert captured["body"] == {
        "snippet": {"title": "Title", "description": "Description", "tags": ["AI"]},
        "status": {"privacyStatus": "unlisted"},
    }
    assert captured["media_kwargs"] == {
        "chunksize": 8 * 1024 * 1024,
        "resumable": True,
    }
    assert result.video_id == "video-456"
