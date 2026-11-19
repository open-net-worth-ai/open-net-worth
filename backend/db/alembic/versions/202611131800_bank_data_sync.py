"""Bank data sync: each connector's sync settings and its review list.

``bank_sync_settings`` — one row per connector (id = provider key): the mode (``off`` | ``data``),
the app accounts the user unticked, and the Lucius follow-up switch. ``bank_sync_reviews`` — what
the bank changed after booking, and rows the duplicate guard refused, until the user decides.
Schema-only, ``book_payload_table`` shape written out so the revision stays correct after any
later change to the live table. Additive: with the ``bank_data_sync`` flag off nothing reads
either table. No credential is stored here.

Revision ID: 202611131800_bank_data_sync
Revises: 202611131200_bank_syncs_use_book_transactions
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611131800_bank_data_sync"
down_revision = "202611131200_bank_syncs_use_book_transactions"
branch_labels = None
depends_on = None

_TABLES = ("bank_sync_settings", "bank_sync_reviews")


def upgrade() -> None:
    for table in _TABLES:
        op.create_table(
            table,
            sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{table}_book_seq", table, ["book_id", "seq"], unique=True)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
