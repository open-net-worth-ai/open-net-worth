"""Stores behind the bank data sync (tables ``bank_sync_settings`` / ``bank_sync_reviews``).

Data access only — ``services/bank_data_sync`` owns every rule. Bookkeeping, never a credential,
and not checkpointed (``ai.checkpoint._policy._EXCLUDED_TABLES``): a turn rollback must not
re-open a review the user decided or flip a connector's sync mode."""

from __future__ import annotations

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import bank_sync_reviews, bank_sync_settings


class BankSyncSettingsStore(BookPayloadTableStore):
    """One row per connector, ``id`` = the provider key."""

    def __init__(self, engine=None):
        super().__init__(bank_sync_settings, engine=engine, checkpointed=False)


class BankSyncReviewStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(bank_sync_reviews, engine=engine, checkpointed=False)

    def for_provider(self, provider: str | None, status: str | None = None) -> list[dict]:
        return [
            r
            for r in self.all()
            if (provider is None or r.get("provider") == provider)
            and (status is None or r.get("status") == status)
        ]
