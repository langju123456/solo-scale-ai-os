"""Repository boundary for publication receipts and duplicate detection."""

from __future__ import annotations

from typing import Protocol

from buildlog.publishing_models import PublishReceipt, PublicationPlatform


class PublishingRepository(Protocol):
    """Minimal persistence operations required by downstream publishing."""

    def save_publish_receipt(self, receipt: PublishReceipt) -> None:
        """Persist one publication attempt receipt."""

    def get_publish_receipt(self, receipt_id: str) -> PublishReceipt | None:
        """Return one receipt or ``None``."""

    def find_successful_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest matching successful publication."""

    def find_indeterminate_publication(
        self,
        *,
        platform: PublicationPlatform,
        account_reference: str,
        content_hash: str,
    ) -> PublishReceipt | None:
        """Return the latest matching unresolved publication attempt."""
