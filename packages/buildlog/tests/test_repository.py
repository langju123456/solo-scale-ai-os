"""Tests for SQLAlchemy metadata persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, inspect

from buildlog.domain import (
    ArtifactRecord,
    EvaluationRecord,
    IterationRecord,
    ProjectRecord,
    PromptVersionRecord,
    RunRecord,
)
from buildlog.exceptions import PersistenceError
from buildlog.persistence_models import Base, PublishReceiptTable
from buildlog.publishing_models import (
    PublishReceipt,
    PublicationPlatform,
    PublicationStatus,
)
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository

EXPECTED_TABLES = {
    "artifact_dependencies",
    "artifacts",
    "error_observations",
    "evaluations",
    "iterations",
    "llm_call_observations",
    "projects",
    "prompt_versions",
    "publish_receipts",
    "run_observations",
    "runs",
    "step_observations",
    "workflow_jobs",
}


def test_database_creation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    assert set(inspect(repository.engine).get_table_names()) == EXPECTED_TABLES


def test_initialize_adds_receipts_to_existing_schema(tmp_path: Path) -> None:
    repository = SQLAlchemyRunRepository(
        f"sqlite:///{tmp_path / 'legacy-buildlog.db'}"
    )
    legacy_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name != "publish_receipts":
            table.to_metadata(legacy_metadata)
    legacy_metadata.create_all(repository.engine)
    before = set(inspect(repository.engine).get_table_names())

    repository.initialize()

    after = set(inspect(repository.engine).get_table_names())
    assert "publish_receipts" not in before
    assert after == EXPECTED_TABLES
    assert before == EXPECTED_TABLES - {"publish_receipts"}


def test_publish_receipt_schema_excludes_credentials_and_post_content(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    columns = {
        column["name"]
        for column in inspect(repository.engine).get_columns("publish_receipts")
    }

    assert {
        "content",
        "commentary",
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization_code",
        "authorization",
    }.isdisjoint(columns)
    assert {"content_hash", "account_reference", "external_post_id"} <= columns


def test_run_persistence(tmp_path: Path) -> None:
    repository = _repository_with_run(tmp_path)

    repository.complete_run("run-001", True, datetime.now(UTC))
    stored = repository.get_run("run-001")

    assert stored is not None
    assert stored.iteration_id == "iteration-001"
    assert stored.status == "completed"
    assert stored.revision_performed
    assert stored.planner_prompt_version_id == "planner-v1-hash"


def test_evaluation_persistence(tmp_path: Path) -> None:
    repository = _repository_with_run(tmp_path)
    repository.save_evaluation(
        EvaluationRecord(
            id="evaluation-001",
            run_id="run-001",
            technical_accuracy=8,
            specificity=7,
            readability=9,
            reader_value=8,
            evidence_coverage=7,
            feedback={
                "unsupported_claims": [],
                "revision_instructions": ["Tighten the ending."],
            },
        )
    )

    stored = repository.get_evaluation("run-001")

    assert stored is not None
    assert stored.technical_accuracy == 8
    assert stored.feedback["revision_instructions"] == ["Tighten the ending."]


def test_artifact_relationships(tmp_path: Path) -> None:
    repository = _repository_with_run(tmp_path)
    repository.save_artifact(
        ArtifactRecord(
            id="artifact-001",
            run_id="run-001",
            artifact_type="final",
            file_path="/tmp/run-001/06_final.md",
            content_hash="a" * 64,
        )
    )

    artifacts = repository.list_artifacts("run-001")

    assert len(artifacts) == 1
    assert artifacts[0].run_id == "run-001"
    assert artifacts[0].artifact_type == "final"
    assert artifacts[0].content_hash == "a" * 64


def test_publish_receipt_persistence_and_duplicate_query(tmp_path: Path) -> None:
    repository = _repository_with_run(tmp_path)
    repository.save_artifact(
        ArtifactRecord(
            id="run-001:final",
            run_id="run-001",
            artifact_type="final",
            file_path="/tmp/run-001/06_final.md",
            content_hash="a" * 64,
        )
    )
    receipt = PublishReceipt(
        receipt_id="receipt-001",
        attempt_id="attempt-001",
        run_id="run-001",
        artifact_id="run-001:final",
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        content_hash="b" * 64,
        status=PublicationStatus.SUCCEEDED,
        external_post_id="urn:li:share:123",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        api_endpoint="https://api.linkedin.com/rest/posts",
        api_version="202607",
        http_status=201,
    )

    repository.save_publish_receipt(receipt)
    latest_receipt = receipt.model_copy(
        update={
            "receipt_id": "receipt-002",
            "attempt_id": "attempt-002",
            "external_post_id": "urn:li:share:456",
            "created_at": receipt.created_at + timedelta(seconds=1),
            "published_at": receipt.created_at + timedelta(seconds=1),
        }
    )
    repository.save_publish_receipt(latest_receipt)

    stored = repository.get_publish_receipt("receipt-001")
    duplicate = repository.find_successful_publication(
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        content_hash="b" * 64,
    )
    assert stored is not None
    assert stored.external_post_id == "urn:li:share:123"
    assert stored.created_at.tzinfo is UTC
    assert stored.published_at is not None
    assert stored.published_at.tzinfo is UTC
    assert duplicate is not None
    assert duplicate.receipt_id == "receipt-002"
    assert duplicate.external_post_id == "urn:li:share:456"
    assert (
        repository.find_successful_publication(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="another-account",
            content_hash="b" * 64,
        )
        is None
    )
    assert (
        repository.find_successful_publication(
            platform=PublicationPlatform.LINKEDIN,
            account_reference="account-ref",
            content_hash="c" * 64,
        )
        is None
    )


def test_corrupt_publish_receipt_is_reported_as_persistence_error(
    tmp_path: Path,
) -> None:
    repository = _repository_with_run(tmp_path)
    repository.save_artifact(
        ArtifactRecord(
            id="run-001:final",
            run_id="run-001",
            artifact_type="final",
            file_path="/tmp/run-001/06_final.md",
            content_hash="a" * 64,
        )
    )
    receipt = PublishReceipt(
        receipt_id="receipt-001",
        attempt_id="attempt-001",
        run_id="run-001",
        artifact_id="run-001:final",
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        content_hash="b" * 64,
        status=PublicationStatus.SUCCEEDED,
        external_post_id="urn:li:share:123",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        api_endpoint="https://api.linkedin.com/rest/posts",
        api_version="202607",
        http_status=201,
    )
    repository.save_publish_receipt(receipt)
    with repository._publishing._sessions.begin() as session:  # noqa: SLF001
        row = session.get(PublishReceiptTable, receipt.receipt_id)
        assert row is not None
        row.api_version = "corrupt-version"

    with pytest.raises(PersistenceError, match="load publication receipt"):
        repository.get_publish_receipt(receipt.receipt_id)
    with pytest.raises(PersistenceError, match="query publication receipts"):
        repository.find_successful_publication(
            platform=PublicationPlatform.LINKEDIN,
            account_reference=receipt.account_reference,
            content_hash=receipt.content_hash,
        )


def test_invalid_publish_receipt_is_revalidated_before_persistence(
    tmp_path: Path,
) -> None:
    repository = _repository_with_run(tmp_path)
    repository.save_artifact(
        ArtifactRecord(
            id="run-001:final",
            run_id="run-001",
            artifact_type="final",
            file_path="/tmp/run-001/06_final.md",
            content_hash="a" * 64,
        )
    )
    valid = PublishReceipt(
        receipt_id="receipt-001",
        attempt_id="attempt-001",
        run_id="run-001",
        artifact_id="run-001:final",
        platform=PublicationPlatform.LINKEDIN,
        account_reference="account-ref",
        content_hash="b" * 64,
        status=PublicationStatus.SUCCEEDED,
        external_post_id="urn:li:share:123",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        api_endpoint="https://api.linkedin.com/rest/posts",
        api_version="202607",
        http_status=201,
    )
    invalid = valid.model_copy(update={"external_post_id": None})

    with pytest.raises(PersistenceError, match="persist publication receipt"):
        repository.save_publish_receipt(invalid)

    assert repository.get_publish_receipt(valid.receipt_id) is None


def _repository(tmp_path: Path) -> SQLAlchemyRunRepository:
    repository = SQLAlchemyRunRepository(f"sqlite:///{tmp_path / 'buildlog.db'}")
    repository.initialize()
    return repository


def _repository_with_run(tmp_path: Path) -> SQLAlchemyRunRepository:
    repository = _repository(tmp_path)
    repository.save_project(ProjectRecord(id="project-001", name="Project"))
    repository.save_iteration(
        IterationRecord(
            id="iteration-001",
            project_id="project-001",
            title="Iteration",
            goal="Ship a small pipeline.",
            context="Local development.",
            problem="Metadata was file-only.",
            audience="Engineers",
            raw_input={"id": "iteration-001"},
        )
    )
    prompt_ids: dict[str, str] = {}
    for name in ("planner", "writer", "evaluator", "reviser"):
        prompt_id = f"{name}-v1-hash"
        prompt_ids[name] = prompt_id
        repository.save_prompt_version(
            PromptVersionRecord(
                id=prompt_id,
                prompt_name=name,
                version="v1",
                file_path=f"/tmp/{name}_v1.md",
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
    return repository
