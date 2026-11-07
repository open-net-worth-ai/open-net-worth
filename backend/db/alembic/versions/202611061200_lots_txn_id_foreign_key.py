"""``lots.txn_id`` becomes a real column with a foreign key to ``transactions``.

A lot's link to its purchase cash row lived only inside the JSON payload, so nothing could keep it
true: a deleted cash row left the lot pointing at it, and correcting the lot then tried to re-book
a purchase the ledger already held. ADR ``2026-11-06-a-lots-cash-link-is-a-real-foreign-key``.

A link to a row that still EXISTS is copied, soft-deleted rows included (the row is there, and an
import rollback can bring it back; the amend path treats it as no link). A link to a row that is
gone cannot satisfy the key, so it is cleared from the column and the payload. ``lots`` has no
child tables, so SQLite's batch rebuild cascades into nothing. Downgrade drops the column; the
payload still holds the link.

Revision ID: 202611061200_lots_txn_id_foreign_key
Revises: 202611051800_actual_budget_sync
Create Date: 2026-11-06
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "202611061200_lots_txn_id_foreign_key"
down_revision = "202611051800_actual_budget_sync"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_lots = sa.table(
    "lots", sa.column("id", sa.Text), sa.column("payload", sa.JSON), sa.column("txn_id", sa.Text)
)
_transactions = sa.table("transactions", sa.column("id", sa.Text))


def upgrade() -> None:
    with op.batch_alter_table("lots") as batch_op:
        batch_op.add_column(sa.Column("txn_id", sa.Text, nullable=True))
        batch_op.create_index("ix_lots_txn_id", ["txn_id"])
        batch_op.create_foreign_key(
            "fk_lots_txn_id_transactions", "transactions", ["txn_id"], ["id"], ondelete="SET NULL"
        )

    conn = op.get_bind()
    known = set(conn.execute(sa.select(_transactions.c.id)).scalars())
    cleared = 0
    for lot_id, payload in conn.execute(sa.select(_lots.c.id, _lots.c.payload)).all():
        link = (payload or {}).get("txn_id")
        if not link:
            continue
        if link in known:
            conn.execute(_lots.update().where(_lots.c.id == lot_id).values(txn_id=link))
            continue
        cleared += 1
        conn.execute(
            _lots.update().where(_lots.c.id == lot_id).values(payload={**payload, "txn_id": None})
        )
    if cleared:
        logger.info("lots_txn_id_foreign_key: cleared %d link(s) to a missing row", cleared)


def downgrade() -> None:
    with op.batch_alter_table("lots") as batch_op:
        batch_op.drop_constraint("fk_lots_txn_id_transactions", type_="foreignkey")
        batch_op.drop_index("ix_lots_txn_id")
        batch_op.drop_column("txn_id")
