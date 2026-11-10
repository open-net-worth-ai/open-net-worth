"""Merge point: ``main`` added ``202611021200_news_deliveries`` on top of
``202610292000_positions_drop_note`` while this branch joined the YNAB import and sync revisions
to that same revision at ``202611021200_merge_ynab_sync_and_main_chains``.

Verified data-safe in either order before merging. ``news_deliveries`` only CREATES its own table
and indexes; the YNAB chain only creates ``import_runs`` and the ``ynab_sync_*`` tables and theirs.
Neither reads or writes the other's names, so a database stamped at either head upgrades correctly
through this merge. No operations of its own — the same pattern as
``202611021200_merge_ynab_sync_and_main_chains``.

Revision ID: 202611021300_merge_ynab_sync_and_news_deliveries
Revises: 202611021200_merge_ynab_sync_and_main_chains, 202611021200_news_deliveries  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-11-02
"""

from __future__ import annotations

revision = "202611021300_merge_ynab_sync_and_news_deliveries"
down_revision = (
    "202611021200_merge_ynab_sync_and_main_chains",
    "202611021200_news_deliveries",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
