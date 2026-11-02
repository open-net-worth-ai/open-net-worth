"""news_deliveries: the Intel delivery decider's record of every candidate notification.

Behind the ``intel_delivery_decider`` flag, Lucius decides for each candidate Intel push whether
it goes out now, waits for a roundup, or stays in the Intel tab (ADR
``2026-11-02-intel-decides-news-delivery-code-keeps-safety-rails``). This table is that
decision's ledger and the status machine that stops an item being told twice. Additive: with the
flag off nothing reads or writes it, so upgrade and downgrade touch no existing data.

Revision ID: 202611021200_news_deliveries
Revises: 202610292000_positions_drop_note
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611021200_news_deliveries"
down_revision = "202610292000_positions_drop_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_deliveries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("story_id", sa.Text, nullable=False, server_default=""),
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
        sa.Column("user_set", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("decided_at", sa.Text, nullable=True),
        sa.Column("told_at", sa.Text, nullable=True),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','waiting','told','skipped','superseded','expired')",
            name=sa.schema.conv("ck_news_deliveries_status"),
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('now','later','skip')",
            name=sa.schema.conv("ck_news_deliveries_decision"),
        ),
        sa.CheckConstraint(
            "user_set IN (0,1)", name=sa.schema.conv("ck_news_deliveries_user_set_bool")
        ),
    )
    op.create_index("ix_news_deliveries_seq", "news_deliveries", ["seq"], unique=True)
    op.create_index("ix_news_deliveries_status", "news_deliveries", ["status"])
    op.create_index("ix_news_deliveries_story_id", "news_deliveries", ["story_id"])


def downgrade() -> None:
    op.drop_index("ix_news_deliveries_story_id", table_name="news_deliveries")
    op.drop_index("ix_news_deliveries_status", table_name="news_deliveries")
    op.drop_index("ix_news_deliveries_seq", table_name="news_deliveries")
    op.drop_table("news_deliveries")
