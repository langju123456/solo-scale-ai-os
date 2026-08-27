"""Tests for artifact resolution, approval, deduplication, receipts, and events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from buildlog.domain import (
    ArtifactRecord,
    IterationRecord,
    ProjectRecord,
    PromptVersionRecord,
    RunRecord,
)
from buildlog.linkedin_config import LinkedInSettings
from buildlog.linkedin_errors import (
    DuplicatePublicationBlockedError,
    IndeterminatePublicationError,
    IndeterminatePublicationBlockedError,
    LinkedInBadRequestError,
    LinkedInNetworkError,
    LinkedInRequestTimeoutError,
    LinkedInServerError,
    PublicationApprovalRequiredError,
    PublicationReceiptPersistenceError,
    PublicationValidationError,
    MissingTokenError,
)
from buildlog.exceptions import PersistenceError
from buildlog.event_writer import AppendOnlyRunEventWriter
from buildlog.hashing import sha256_file
from buildlog.linkedin_identity import LinkedInIdentity
from buildlog.publication_content import (
    HUMAN_REVIEW_WARNING,
    FinalArtifactResolver,
    normalize_publication_content,
    publication_content_hash,
)
from buildlog.publishing_models import (
    PublicationPlatform,
    PublicationStatus,
    PublishReceipt,
    PublishResult,
)
from buildlog.publishing_observability import PublishingEventRecorder
from buildlog.publishing_service import PublishingService
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


class FakeIdentityService:
    """Return one fixed authenticated identity."""

    def resolve(self) -> LinkedInIdentity:
        return LinkedInIdentity(
            subject="member-123",
            display_name="Ju L",
            person_urn="urn:li:person:member-123",
            account_reference="account-ref",
        )


class FakePublisher:
    """Return or raise the configured publication outcome."""

    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome
        self.requests = []

    def publish(self, request):
        self.requests.append(request)
        if self.outcome is not None:
            raise self.outcome
        return PublishResult(
            platform=request.platform,
            account_reference=request.account_reference,
            run_id=request.run_id,
            status=PublicationStatus.SUCCEEDED,
            content_hash=request.content_hash,
            external_post_id="urn:li:share:123",
            occurred_at=_now(),
            http_status=201,
            api_endpoint="https://api.linkedin.com/rest/posts",
            api_version="202607",
        )


def _indeterminate_from(cause: Exception) -> IndeterminatePublicationError:
    error = IndeterminatePublicationError("submission outcome is unknown")
    error.__cause__ = cause
    return error


def test_final_artifact_resolution_strips_only_review_footer(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)

    artifact = FinalArtifactResolver(repository).resolve("run-001")

    assert artifact.content == "Real engineering content."
    assert HUMAN_REVIEW_WARNING not in artifact.content
    assert final_path.read_text(encoding="utf-8").endswith(HUMAN_REVIEW_WARNING)
    assert artifact.content_hash == publication_content_hash(artifact.content)


def test_final_artifact_requires_existing_completed_run(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import RunTable

        row = session.get(RunTable, "run-001")
        assert row is not None
        row.status = "failed"

    with pytest.raises(PublicationValidationError, match="not completed"):
        FinalArtifactResolver(repository).resolve("run-001")
    with pytest.raises(PublicationValidationError, match="does not exist"):
        FinalArtifactResolver(repository).resolve("missing-run")


def test_final_artifact_requires_existing_nonempty_file(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.file_path = str(tmp_path / "runs" / "run-001" / "missing.md")

    with pytest.raises(PublicationValidationError, match="file is missing"):
        FinalArtifactResolver(repository).resolve("run-001")

    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.file_path = str(final_path)
        final_path.write_text(HUMAN_REVIEW_WARNING, encoding="utf-8")
        row.content_hash = sha256_file(final_path)

    with pytest.raises(PublicationValidationError, match="no publishable content"):
        FinalArtifactResolver(repository).resolve("run-001")


def test_final_artifact_requires_valid_utf8(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    final_path.write_bytes(b"\xff\xfe")
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.content_hash = sha256_file(final_path)

    with pytest.raises(PublicationValidationError, match="could not be read"):
        FinalArtifactResolver(repository).resolve("run-001")


def test_final_artifact_requires_exactly_one_indexed_final() -> None:
    run = RunRecord(
        id="run-001",
        iteration_id="iteration-001",
        model="model",
        planner_prompt_version_id="planner",
        writer_prompt_version_id="writer",
        evaluator_prompt_version_id="evaluator",
        reviser_prompt_version_id="reviser",
        status="completed",
    )

    class AmbiguousRepository:
        def get_run(self, _run_id):
            return run

        def list_artifacts(self, _run_id):
            return [
                ArtifactRecord(
                    id="final-one",
                    run_id=run.id,
                    artifact_type="final",
                    file_path="/tmp/one.md",
                    content_hash="a" * 64,
                ),
                ArtifactRecord(
                    id="final-two",
                    run_id=run.id,
                    artifact_type="final",
                    file_path="/tmp/two.md",
                    content_hash="b" * 64,
                ),
            ]

    with pytest.raises(PublicationValidationError, match="exactly one"):
        FinalArtifactResolver(AmbiguousRepository()).resolve(run.id)


def test_content_normalization_and_hash_are_stable() -> None:
    left = "Line one  \r\nLine two\n"
    right = "Line one\nLine two"

    assert normalize_publication_content(left) == right
    assert publication_content_hash(left) == publication_content_hash(right)


def test_final_artifact_tampering_is_rejected(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    final_path.write_text("Unindexed replacement.", encoding="utf-8")

    with pytest.raises(PublicationValidationError, match="indexed SHA-256"):
        FinalArtifactResolver(repository).resolve("run-001")


def test_final_artifact_unsafe_control_character_is_rejected(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    final_path.write_text(
        "Visible text.\x1b[2J" + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.content_hash = sha256_file(final_path)

    with pytest.raises(PublicationValidationError, match="U\\+001B"):
        FinalArtifactResolver(repository).resolve("run-001")


def test_final_artifact_directional_override_is_rejected(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    final_path.write_text(
        "Visible text.\u202ehidden" + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.content_hash = sha256_file(final_path)

    with pytest.raises(PublicationValidationError, match="U\\+202E"):
        FinalArtifactResolver(repository).resolve("run-001")


@pytest.mark.parametrize("unsafe_character", ["\x0b", "\x0c", "\x85"])
def test_final_artifact_line_separator_controls_are_rejected_before_normalizing(
    tmp_path: Path,
    unsafe_character: str,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    final_path.write_text(
        f"Visible{unsafe_character}hidden" + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.content_hash = sha256_file(final_path)

    with pytest.raises(
        PublicationValidationError,
        match=f"U\\+{ord(unsafe_character):04X}",
    ):
        FinalArtifactResolver(repository).resolve("run-001")


def test_final_artifact_outside_runs_root_is_rejected(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    outside_path = tmp_path / "outside.md"
    outside_path.write_text(final_path.read_text(encoding="utf-8"), encoding="utf-8")
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.file_path = str(outside_path)
        row.content_hash = sha256_file(outside_path)

    with pytest.raises(PublicationValidationError, match="outside"):
        FinalArtifactResolver(
            repository,
            allowed_root=tmp_path / "runs",
        ).resolve("run-001")


def test_preview_is_complete_and_never_publishes(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    preview = service.preview("run-001")

    assert preview.content == "Real engineering content."
    assert preview.artifact_path == str(final_path)
    assert preview.account_display_name == "Ju L"
    assert "Real engineering content." not in repr(preview)
    assert not preview.duplicate_found
    assert not preview.network_request_will_occur
    assert publisher.requests == []
    events = _events(final_path.parent)
    assert events[-1]["event_type"] == "publish_previewed"
    assert "content" not in events[-1]["payload"]


def test_identity_failure_is_observable_without_publication(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()

    class FailingIdentityService:
        def resolve(self):
            raise MissingTokenError("missing token")

    service = _service(
        repository,
        publisher,
        identity_service=FailingIdentityService(),
    )

    with pytest.raises(MissingTokenError):
        service.preview("run-001")

    event = _events(final_path.parent)[-1]
    assert event["event_type"] == "linkedin_identity_failed"
    assert event["payload"]["error_category"] == "missing_token"
    assert publisher.requests == []


def test_approval_gate_stops_before_identity_or_network(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    with pytest.raises(PublicationApprovalRequiredError):
        service.publish("run-001", approved=False)

    assert publisher.requests == []


def test_approval_must_be_bound_to_a_preview(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    with pytest.raises(
        PublicationApprovalRequiredError,
        match="fresh preview",
    ):
        service.publish("run-001", approved=True)

    assert publisher.requests == []


def test_successful_publish_persists_receipt_and_event(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    receipt = _approved_publish(service)

    assert receipt.status is PublicationStatus.SUCCEEDED
    assert receipt.external_post_id == "urn:li:share:123"
    assert repository.get_publish_receipt(receipt.receipt_id) is not None
    assert publisher.requests[0].content == "Real engineering content."
    assert _events(final_path.parent)[-1]["event_type"] == "publish_succeeded"


def test_successful_duplicate_is_blocked_by_default(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    content_hash = publication_content_hash("Real engineering content.")
    repository.save_publish_receipt(
        _receipt(
            content_hash=content_hash,
            status=PublicationStatus.SUCCEEDED,
        )
    )
    publisher = FakePublisher()
    service = _service(repository, publisher)

    with pytest.raises(DuplicatePublicationBlockedError):
        _approved_publish(service)

    assert publisher.requests == []


def test_explicit_duplicate_override_records_prior_receipt(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    prior = _receipt(
        content_hash=publication_content_hash("Real engineering content."),
        status=PublicationStatus.SUCCEEDED,
    )
    repository.save_publish_receipt(prior)
    service = _service(repository, FakePublisher())

    receipt = _approved_publish(service, allow_duplicate=True)

    assert receipt.duplicate_of_receipt_id == prior.receipt_id


def test_duplicate_override_links_to_latest_prior_outcome(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    content_hash = publication_content_hash("Real engineering content.")
    successful = _receipt(
        content_hash=content_hash,
        status=PublicationStatus.SUCCEEDED,
    )
    indeterminate = _receipt(
        content_hash=content_hash,
        status=PublicationStatus.INDETERMINATE,
    ).model_copy(
        update={
            "receipt_id": "receipt-indeterminate-latest",
            "attempt_id": "attempt-indeterminate-latest",
            "created_at": datetime(2026, 7, 30, tzinfo=UTC),
        }
    )
    repository.save_publish_receipt(successful)
    repository.save_publish_receipt(indeterminate)
    service = _service(repository, FakePublisher())

    receipt = _approved_publish(service, allow_duplicate=True)

    assert receipt.duplicate_of_receipt_id == indeterminate.receipt_id


def test_indeterminate_attempt_blocks_until_explicit_override(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    prior = _receipt(
        content_hash=publication_content_hash("Real engineering content."),
        status=PublicationStatus.INDETERMINATE,
    )
    repository.save_publish_receipt(prior)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    preview = service.preview("run-001")
    assert preview.indeterminate_found
    assert preview.indeterminate_receipt_id == prior.receipt_id

    with pytest.raises(
        IndeterminatePublicationBlockedError,
        match="inspect LinkedIn",
    ):
        service.publish(
            "run-001",
            approved=True,
            approved_content_hash=preview.content_hash,
            approved_account_reference=preview.account_reference,
        )
    assert publisher.requests == []
    assert _events(final_path.parent)[-1]["event_type"] == (
        "publish_indeterminate_blocked"
    )

    receipt = _approved_publish(service, allow_duplicate=True)
    assert receipt.status is PublicationStatus.SUCCEEDED
    assert receipt.duplicate_of_receipt_id == prior.receipt_id


@pytest.mark.parametrize(
    ("error", "status", "event_type", "error_category"),
    [
        (
            LinkedInBadRequestError("invalid", status_code=400),
            PublicationStatus.FAILED,
            "publish_failed",
            "linkedin_http_400",
        ),
        (
            IndeterminatePublicationError("timeout after submission"),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "indeterminate",
        ),
        (
            _indeterminate_from(
                LinkedInNetworkError("connection ended"),
            ),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "linkedin_network",
        ),
        (
            _indeterminate_from(
                LinkedInRequestTimeoutError("request timed out"),
            ),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "linkedin_request_timeout",
        ),
        (
            LinkedInNetworkError("connection ended"),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "linkedin_network",
        ),
        (
            LinkedInRequestTimeoutError("request timed out"),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "linkedin_request_timeout",
        ),
        (
            LinkedInServerError("server error", status_code=503),
            PublicationStatus.INDETERMINATE,
            "publish_result_indeterminate",
            "linkedin_http_503",
        ),
    ],
)
def test_failed_and_indeterminate_attempts_persist_receipts(
    tmp_path: Path,
    error: Exception,
    status: PublicationStatus,
    event_type: str,
    error_category: str,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    service = _service(repository, FakePublisher(error))

    with pytest.raises(type(error)):
        _approved_publish(service)

    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == status.value
        assert row.error_category == error_category
        assert "secret" not in (row.safe_error_message or "")
    run = repository.get_run("run-001")
    assert run is not None
    assert run.status == "completed"
    assert _events(final_path.parent)[-1]["event_type"] == event_type


def test_failure_receipt_removes_post_content_and_author_identity(
    tmp_path: Path,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    leaked_message = (
        "Rejected Real engineering content. for "
        "urn:li:person:member-123"
    )
    service = _service(
        repository,
        FakePublisher(
            LinkedInBadRequestError(leaked_message, status_code=400),
        ),
    )

    with pytest.raises(LinkedInBadRequestError):
        _approved_publish(service)

    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert "Real engineering content." not in row.safe_error_message
        assert "urn:li:person:member-123" not in row.safe_error_message
        assert row.safe_error_message.count("<redacted>") == 2


def test_interrupt_during_submission_is_persisted_as_indeterminate(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)

    class InterruptedPublisher(FakePublisher):
        def publish(self, request):
            self.requests.append(request)
            raise KeyboardInterrupt

    publisher = InterruptedPublisher()
    service = _service(repository, publisher)

    with pytest.raises(
        IndeterminatePublicationError,
        match="interrupted",
    ) as captured:
        _approved_publish(service)

    assert isinstance(captured.value.__cause__, KeyboardInterrupt)
    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == PublicationStatus.INDETERMINATE.value
        assert row.error_category == "indeterminate"
    assert _events(final_path.parent)[-1]["event_type"] == (
        "publish_result_indeterminate"
    )


def test_publishing_event_recorder_redacts_secret_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    recorder = PublishingEventRecorder("run-001", run_dir)

    assert recorder.emit(
        "publish_failed",
        {
            "access_token": "access-secret",
            "detail": "Authorization: Bearer bearer-secret",
            "content_hash": "a" * 64,
            "nested": [
                {
                    "clientSecret": "nested-secret",
                    "post_content": "private post body",
                }
            ],
            "text": "alternate private post body",
            "author_urn": "urn:li:person:member-123",
            "subject": "member-123",
            "authorization_url": "https://example.test/login?state=oauth-secret",
        },
    )

    raw = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "access-secret" not in raw
    assert "bearer-secret" not in raw
    assert "nested-secret" not in raw
    assert "private post body" not in raw
    assert "alternate private post body" not in raw
    assert "urn:li:person:member-123" not in raw
    assert "member-123" not in raw
    assert "oauth-secret" not in raw
    assert "a" * 64 in raw


@pytest.mark.parametrize(
    "write_failure",
    [OSError("events unavailable"), RuntimeError("writer bug")],
)
def test_observability_write_failure_does_not_change_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    write_failure: Exception,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    def fail_event_write(*_args, **_kwargs):
        raise write_failure

    monkeypatch.setattr(AppendOnlyRunEventWriter, "append", fail_event_write)

    preview = service.preview("run-001")
    receipt = service.publish(
        "run-001",
        approved=True,
        approved_content_hash=preview.content_hash,
        approved_account_reference=preview.account_reference,
    )

    assert receipt.status is PublicationStatus.SUCCEEDED
    assert repository.get_publish_receipt(receipt.receipt_id) is not None
    assert len(publisher.requests) == 1
    assert "publication behavior is unchanged" in caplog.text


def test_terminal_event_interrupt_does_not_hide_publication_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)
    original_append = AppendOnlyRunEventWriter.append

    def interrupt_terminal_event(self, event_type, **kwargs):
        if event_type == "publish_succeeded":
            raise KeyboardInterrupt
        return original_append(self, event_type, **kwargs)

    monkeypatch.setattr(
        AppendOnlyRunEventWriter,
        "append",
        interrupt_terminal_event,
    )

    receipt = _approved_publish(service)

    assert receipt.status is PublicationStatus.SUCCEEDED
    assert repository.get_publish_receipt(receipt.receipt_id) is not None
    assert "publication behavior is unchanged" in caplog.text


def test_observability_initialization_failure_does_not_change_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    publisher = FakePublisher()
    service = _service(repository, publisher)

    def fail_event_writer(*_args, **_kwargs):
        raise RuntimeError("writer initialization bug")

    monkeypatch.setattr(
        "buildlog.publishing_observability.AppendOnlyRunEventWriter",
        fail_event_writer,
    )

    preview = service.preview("run-001")
    receipt = service.publish(
        "run-001",
        approved=True,
        approved_content_hash=preview.content_hash,
        approved_account_reference=preview.account_reference,
    )

    assert receipt.status is PublicationStatus.SUCCEEDED
    assert repository.get_publish_receipt(receipt.receipt_id) is not None
    assert len(publisher.requests) == 1


@pytest.mark.parametrize(
    "persistence_failure",
    [PersistenceError("database unavailable"), RuntimeError("repository bug")],
)
def test_success_with_receipt_failure_warns_against_republishing(
    tmp_path: Path,
    persistence_failure: Exception,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)

    class ReceiptFailingRepository:
        def get_run(self, run_id):
            return repository.get_run(run_id)

        def list_artifacts(self, run_id):
            return repository.list_artifacts(run_id)

        def find_successful_publication(self, **kwargs):
            return repository.find_successful_publication(**kwargs)

        def find_indeterminate_publication(self, **kwargs):
            return repository.find_indeterminate_publication(**kwargs)

        def save_publish_receipt(self, receipt):
            raise persistence_failure

    service = _service(ReceiptFailingRepository(), FakePublisher())

    with pytest.raises(
        PublicationReceiptPersistenceError,
        match="Do not publish again",
    ):
        _approved_publish(service)

    events = _events(final_path.parent)
    assert events[-1]["event_type"] == "publish_receipt_failed"
    assert events[-1]["payload"]["external_post_id"] == "urn:li:share:123"


def test_interrupt_during_success_receipt_save_warns_against_republishing(
    tmp_path: Path,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)

    class InterruptedRepository:
        def get_run(self, run_id):
            return repository.get_run(run_id)

        def list_artifacts(self, run_id):
            return repository.list_artifacts(run_id)

        def find_successful_publication(self, **kwargs):
            return repository.find_successful_publication(**kwargs)

        def find_indeterminate_publication(self, **kwargs):
            return repository.find_indeterminate_publication(**kwargs)

        def save_publish_receipt(self, receipt):
            raise KeyboardInterrupt

    service = _service(InterruptedRepository(), FakePublisher())

    with pytest.raises(
        PublicationReceiptPersistenceError,
        match="Do not publish again",
    ) as captured:
        _approved_publish(service)

    assert isinstance(captured.value.__cause__, KeyboardInterrupt)
    assert _events(final_path.parent)[-1]["event_type"] == (
        "publish_receipt_failed"
    )


@pytest.mark.parametrize(
    ("publication_error", "expected_status"),
    [
        (
            LinkedInBadRequestError("invalid", status_code=400),
            PublicationStatus.FAILED,
        ),
        (
            IndeterminatePublicationError("response missing"),
            PublicationStatus.INDETERMINATE,
        ),
    ],
)
def test_unsuccessful_receipt_failure_blocks_retry(
    tmp_path: Path,
    publication_error: Exception,
    expected_status: PublicationStatus,
) -> None:
    repository, final_path = _repository_with_final(tmp_path)

    class ReceiptFailingRepository:
        def get_run(self, run_id):
            return repository.get_run(run_id)

        def list_artifacts(self, run_id):
            return repository.list_artifacts(run_id)

        def find_successful_publication(self, **kwargs):
            return repository.find_successful_publication(**kwargs)

        def find_indeterminate_publication(self, **kwargs):
            return repository.find_indeterminate_publication(**kwargs)

        def save_publish_receipt(self, receipt):
            raise PersistenceError("database unavailable")

    service = _service(
        ReceiptFailingRepository(),
        FakePublisher(publication_error),
    )

    with pytest.raises(
        PublicationReceiptPersistenceError,
        match="Do not retry",
    ):
        _approved_publish(service)

    event = _events(final_path.parent)[-1]
    assert event["event_type"] == "publish_receipt_failed"
    assert event["payload"]["publication_status"] == expected_status.value


def test_content_change_after_preview_requires_new_approval(tmp_path: Path) -> None:
    repository, final_path = _repository_with_final(tmp_path)
    service = _service(repository, FakePublisher())
    preview = service.preview("run-001")
    final_path.write_text(
        "Changed after preview." + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        from buildlog.persistence_models import ArtifactTable

        row = session.get(ArtifactTable, "run-001:final")
        assert row is not None
        row.content_hash = sha256_file(final_path)

    with pytest.raises(
        PublicationApprovalRequiredError,
        match="changed after preview",
    ):
        service.publish(
            "run-001",
            approved=True,
            approved_content_hash=preview.content_hash,
            approved_account_reference=preview.account_reference,
        )


def test_account_change_after_preview_requires_new_approval(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)

    class SwitchingIdentityService:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self) -> LinkedInIdentity:
            self.calls += 1
            suffix = "first" if self.calls == 1 else "second"
            return LinkedInIdentity(
                subject=f"member-{suffix}",
                display_name=f"Account {suffix}",
                person_urn=f"urn:li:person:member-{suffix}",
                account_reference=f"account-{suffix}",
            )

    publisher = FakePublisher()
    service = _service(
        repository,
        publisher,
        identity_service=SwitchingIdentityService(),
    )
    preview = service.preview("run-001")

    with pytest.raises(
        PublicationApprovalRequiredError,
        match="account changed after preview",
    ):
        service.publish(
            "run-001",
            approved=True,
            approved_content_hash=preview.content_hash,
            approved_account_reference=preview.account_reference,
        )

    assert publisher.requests == []


def test_publisher_result_mismatch_is_indeterminate(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)

    class MismatchedPublisher(FakePublisher):
        def publish(self, request):
            result = super().publish(request)
            return result.model_copy(update={"content_hash": "0" * 64})

    service = _service(repository, MismatchedPublisher())

    with pytest.raises(
        IndeterminatePublicationError,
        match="inconsistent success result",
    ):
        _approved_publish(service)

    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == PublicationStatus.INDETERMINATE.value
        assert row.external_post_id == "urn:li:share:123"


def test_invalid_publisher_result_type_is_indeterminate(tmp_path: Path) -> None:
    repository, _final_path = _repository_with_final(tmp_path)

    class InvalidPublisher(FakePublisher):
        def publish(self, request):
            self.requests.append(request)
            return {"status": "succeeded"}

    service = _service(repository, InvalidPublisher())

    with pytest.raises(
        IndeterminatePublicationError,
        match="invalid result type",
    ):
        _approved_publish(service)

    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == PublicationStatus.INDETERMINATE.value
        assert row.external_post_id is None


def test_unvalidated_publisher_result_is_revalidated_and_indeterminate(
    tmp_path: Path,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)

    class UnsafePublisher(FakePublisher):
        def publish(self, request):
            self.requests.append(request)
            return PublishResult.model_construct(
                platform=request.platform,
                account_reference=request.account_reference,
                run_id=request.run_id,
                status=PublicationStatus.SUCCEEDED,
                content_hash=request.content_hash,
                external_post_id="urn:li:share:123\u202e456",
                occurred_at=_now(),
                http_status=201,
                api_endpoint="https://api.linkedin.com/rest/posts",
                api_version="202607",
            )

    service = _service(repository, UnsafePublisher())

    with pytest.raises(
        IndeterminatePublicationError,
        match="invalid result",
    ):
        _approved_publish(service)

    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == PublicationStatus.INDETERMINATE.value
        assert row.external_post_id is None


def test_unexpected_publisher_exception_is_indeterminate(
    tmp_path: Path,
) -> None:
    repository, _final_path = _repository_with_final(tmp_path)
    service = _service(
        repository,
        FakePublisher(RuntimeError("unexpected adapter failure")),
    )

    with pytest.raises(
        IndeterminatePublicationError,
        match="failed unexpectedly",
    ) as captured:
        _approved_publish(service)

    assert isinstance(captured.value.__cause__, RuntimeError)
    with repository._publishing._sessions() as session:  # noqa: SLF001
        from buildlog.persistence_models import PublishReceiptTable

        row = session.query(PublishReceiptTable).one()
        assert row.status == PublicationStatus.INDETERMINATE.value
        assert row.error_category == "indeterminate"
        assert "unexpected adapter failure" not in row.safe_error_message


def _service(
    repository,
    publisher: FakePublisher,
    *,
    identity_service=None,
) -> PublishingService:
    settings = LinkedInSettings(
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
        api_version="202607",
    )
    return PublishingService(
        settings,
        FinalArtifactResolver(repository),
        identity_service or FakeIdentityService(),
        publisher,
        repository,
    )


def _approved_publish(
    service: PublishingService,
    *,
    allow_duplicate: bool = False,
) -> PublishReceipt:
    preview = service.preview("run-001")
    return service.publish(
        "run-001",
        approved=True,
        approved_content_hash=preview.content_hash,
        approved_account_reference=preview.account_reference,
        allow_duplicate=allow_duplicate,
    )


def _repository_with_final(
    tmp_path: Path,
) -> tuple[SQLAlchemyRunRepository, Path]:
    repository = SQLAlchemyRunRepository(f"sqlite:///{tmp_path / 'buildlog.db'}")
    repository.initialize()
    repository.save_project(ProjectRecord(id="project-001", name="BuildLog"))
    repository.save_iteration(
        IterationRecord(
            id="iteration-001",
            project_id="project-001",
            title="Iteration",
            goal="Generate grounded content.",
            context="Local test.",
            problem="No publication boundary.",
            audience="Engineers",
            raw_input={"id": "iteration-001"},
        )
    )
    prompt_ids = {}
    for name in ("planner", "writer", "evaluator", "reviser"):
        prompt_id = f"{name}-v2-hash"
        prompt_ids[name] = prompt_id
        repository.save_prompt_version(
            PromptVersionRecord(
                id=prompt_id,
                prompt_name=name,
                version="v2",
                file_path=f"/tmp/{name}_v2.md",
                content_hash=name.ljust(64, "0"),
            )
        )
    repository.save_run(
        RunRecord(
            id="run-001",
            iteration_id="iteration-001",
            model="ollama_chat/qwen3:8b",
            planner_prompt_version_id=prompt_ids["planner"],
            writer_prompt_version_id=prompt_ids["writer"],
            evaluator_prompt_version_id=prompt_ids["evaluator"],
            reviser_prompt_version_id=prompt_ids["reviser"],
        )
    )
    repository.complete_run("run-001", False, _now())
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    final_path = run_dir / "06_final.md"
    final_path.write_text(
        "Real engineering content." + HUMAN_REVIEW_WARNING,
        encoding="utf-8",
    )
    repository.save_artifact(
        ArtifactRecord(
            id="run-001:final",
            run_id="run-001",
            artifact_type="final",
            file_path=str(final_path),
            content_hash=sha256_file(final_path),
        )
    )
    return repository, final_path


def _receipt(
    *,
    content_hash: str,
    status: PublicationStatus,
) -> PublishReceipt:
    succeeded = status is PublicationStatus.SUCCEEDED
    return PublishReceipt(
        receipt_id="prior-receipt",
        attempt_id="prior-attempt",
        run_id="run-001",
        artifact_id="run-001:final",
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        content_hash=content_hash,
        status=status,
        external_post_id="urn:li:share:999" if succeeded else None,
        created_at=_now(),
        published_at=_now() if succeeded else None,
        api_endpoint="https://api.linkedin.com/rest/posts",
        api_version="202607",
        http_status=201 if succeeded else None,
    )


def _events(run_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _now() -> datetime:
    return datetime(2026, 7, 29, tzinfo=UTC)
