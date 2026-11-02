"""Tables for the scheduled two-way YNAB sync (state + meta, mirror, baselines, review list).

Schema-only, ``book_payload_table`` shape (written directly so the revision stays correct after
any later change to the live ORM table). Nothing credential-bearing: the YNAB token stays in
``connector_secrets``.

Revision ID: 202610211300_ynab_two_way_sync
Revises: 202610171500_trade_cash_legs_say_it_in_the_description
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610211300_ynab_two_way_sync"
down_revision = "202610211200_import_runs"
branch_labels = None
depends_on = None

_TABLES = (
    "ynab_sync_state",
    "ynab_sync_mirror",
    "ynab_sync_baselines",
    "ynab_sync_conflicts",
)


def upgrade() -> None:
    for name in _TABLES:
        op.create_table(
            name,
            sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_TABLES):
        op.drop_table(name)
