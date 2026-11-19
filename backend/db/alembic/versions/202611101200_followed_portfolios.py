"""followed_portfolios — public portfolios the user follows (13F funds, House members).

Additive: one book-scoped payload table (``db.followed_portfolio_store``), written only behind
the ``followed_portfolios`` flag. Downgrade drops it; the follows and their sleeves are lost.

Revision ID: 202611101200_followed_portfolios
Revises: 202611061200_lots_txn_id_foreign_key
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611101200_followed_portfolios"
down_revision = "202611061200_lots_txn_id_foreign_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "followed_portfolios",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_followed_portfolios_book_seq", "followed_portfolios", ["book_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_followed_portfolios_book_seq", table_name="followed_portfolios")
    op.drop_table("followed_portfolios")
