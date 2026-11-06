"""The Actual sync's other tables: the twins of ``ynab_sync_state``, ``_mirror`` and ``_conflicts``.

``actual_sync_baselines`` came with the import. Schema-only, ``book_payload_table`` shape (written
directly so the revision stays correct after any later change to the live ORM table). Nothing
credential-bearing: the sign-in stays in ``connector_secrets``.

Revision ID: 202611051800_actual_budget_sync
Revises: 202611051000_actual_sync_baselines
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611051800_actual_budget_sync"
down_revision = "202611051000_actual_sync_baselines"
branch_labels = None
depends_on = None

_TABLES = ("actual_sync_state", "actual_sync_mirror", "actual_sync_conflicts")


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
