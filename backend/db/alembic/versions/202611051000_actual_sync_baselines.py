"""The table the Actual import pairs its categories in: ``ynab_sync_baselines``' twin.

Schema-only, ``book_payload_table`` shape (written directly so the revision stays correct after
any later change to the live ORM table). Nothing credential-bearing: the sign-in stays in
``connector_secrets``. The sync's other three tables arrive with the sync.

Revision ID: 202611051000_actual_sync_baselines
Revises: 202611021300_merge_ynab_sync_and_news_deliveries
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611051000_actual_sync_baselines"
down_revision = "202611021300_merge_ynab_sync_and_news_deliveries"
branch_labels = None
depends_on = None

_TABLE = "actual_sync_baselines"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_book_seq", _TABLE, ["book_id", "seq"], unique=True)


def downgrade() -> None:
    op.drop_table(_TABLE)
