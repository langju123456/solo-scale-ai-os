"""SQLAlchemy query projection for publication receipts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from buildlog.exceptions import PersistenceError
from buildlog.persistence_models import PublishReceiptTable
from buildlog.publishing_models import (
    PublishReceipt,
    PublicationPlatform,
    PublicationStatus,
)


class SQLAlchemyPublishingRepository:
    """Persist and query publication receipts without storing post content."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def save_publish_receipt(self, receipt: PublishReceipt) -> None:
        """Persist one immutable publication receipt."""
        try:
            validated = PublishReceipt.model_validate(
                receipt.model_dump(mode="python")
            )
            row = PublishReceiptTable(
                id=validated.receipt_id,
                attempt_id=validated.attempt_id,
                run_id=validated.run_id,
                artifact_id=validated.artifact_id,
                platform=validated.platform.value,
                account_reference=validated.account_reference,
                content_hash=validated.content_hash,
                status=validated.status.value,
                external_post_id=validated.external_post_id,
                created_at=validated.created_at,
                published_at=validated.published_at,
                api_endpoint=validated.api_endpoint,
                api_version=validated.api_version,
                http_status=validated.http_status,
                error_category=validated.error_category,
                safe_error_message=validated.safe_error_message,
                duplicate_of_receipt_id=validated.duplicate_of_receipt_id,
            )
            with self._sessions.begin() as session:
                session.add(row)
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise PersistenceError(
                f"could not persist publication receipt: {exc}"
            ) from exc

    def get_publish_receipt(self, receipt_id: str) -> PublishReceipt | None:
        """Return one publication receipt."""
        try:
            with self._sessions() as session:
                row = session.get(PublishReceiptTable, receipt_id)
                return _receipt(row) if row is not None else None
        except (SQLAlchemyError, ValueError) as exc:
            raise PersistenceError(
                f"could not load publication receipt: {exc}"
            ) from exc

    def find_successful_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest successful matching receipt."""
        return self._find_publication(
            platform=platform,
            account_reference=account_reference,
            content_hash=content_hash,
            status=PublicationStatus.SUCCEEDED,
        )

    def find_indeterminate_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest matching unresolved publication attempt."""
        return self._find_publication(
            platform=platform,
            account_reference=account_reference,
            content_hash=content_hash,
            status=PublicationStatus.INDETERMINATE,
        )

    def _find_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
        status: PublicationStatus,
    ) -> PublishReceipt | None:
        statement = (
            select(PublishReceiptTable)
            .where(
                PublishReceiptTable.platform == platform.value,
                PublishReceiptTable.account_reference == account_reference,
                PublishReceiptTable.content_hash == content_hash,
                PublishReceiptTable.status == status.value,
            )
            .order_by(
                PublishReceiptTable.published_at.desc(),
                PublishReceiptTable.created_at.desc(),
                PublishReceiptTable.id.desc(),
            )
            .limit(1)
        )
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
                return _receipt(row) if row is not None else None
        except (SQLAlchemyError, ValueError) as exc:
            raise PersistenceError(
                f"could not query publication receipts: {exc}"
            ) from exc


def _receipt(row: PublishReceiptTable) -> PublishReceipt:
    return PublishReceipt(
        receipt_id=row.id,
        attempt_id=row.attempt_id,
        run_id=row.run_id,
        artifact_id=row.artifact_id,
        platform=PublicationPlatform(row.platform),
        account_reference=row.account_reference,
        content_hash=row.content_hash,
        status=PublicationStatus(row.status),
        external_post_id=row.external_post_id,
        created_at=_as_utc(row.created_at),
        published_at=(
            _as_utc(row.published_at)
            if row.published_at is not None
            else None
        ),
        api_endpoint=row.api_endpoint,
        api_version=row.api_version,
        http_status=row.http_status,
        error_category=row.error_category,
        safe_error_message=row.safe_error_message,
        duplicate_of_receipt_id=row.duplicate_of_receipt_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
