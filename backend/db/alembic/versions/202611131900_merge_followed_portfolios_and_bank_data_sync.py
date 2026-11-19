"""Merge point: ``main`` added ``202611131200_bank_syncs_use_book_transactions`` and
``202611131800_bank_data_sync`` on top of ``202611061200_lots_txn_id_foreign_key`` while this
branch added ``202611101200_followed_portfolios`` to that same revision.

Verified data-safe in either order before merging. The bank chain rewrites saved bank-sync
Automations whose instructions are exactly an old template text and creates its own bank-sync
tables; ``followed_portfolios`` only creates its own table and index. Neither reads or writes the
other's names, so a database stamped at either head upgrades correctly through this merge. No
operations of its own — the same pattern as ``202611021300_merge_ynab_sync_and_news_deliveries``.

Revision ID: 202611131900_merge_followed_portfolios_and_bank_data_sync
Revises: 202611101200_followed_portfolios, 202611131800_bank_data_sync  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-11-13
"""

from __future__ import annotations

revision = "202611131900_merge_followed_portfolios_and_bank_data_sync"
down_revision = (
    "202611101200_followed_portfolios",
    "202611131800_bank_data_sync",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
