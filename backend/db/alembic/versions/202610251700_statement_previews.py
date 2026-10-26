"""statement_previews — the finished review of a statement, persisted with the statement.

``parse_status = ready`` used to mean only that the PARSE was cached: opening the review then
re-ran the matcher, anomaly detection and ledger check over the whole ledger (minutes on a
real book) and the preview a background read had just built was discarded. ``decision_preview``
now persists its final response here before the record can read ``ready``, and a reopen returns
it as-is (ADR ``2026-10-25-ready-means-the-review-is-persisted``).

One row per statement, overwritten in place, FK'd ``ON DELETE CASCADE`` so deleting a statement
drops its review for free (the ``statement_parse_progress`` precedent). Checkpoint-EXCLUDED and
not portable: a derived cache with no rewind/export role. A statement read before this revision
has no row and recomputes on its next open, exactly as before.

Revision ID: 202610251700_statement_previews
Revises: 202610171500_trade_cash_legs_say_it_in_the_description
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610251700_statement_previews"
down_revision = "202610171500_trade_cash_legs_say_it_in_the_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "statement_previews",
        sa.Column(
            "statement_id",
            sa.Text,
            sa.ForeignKey("statements_meta.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fmt", sa.Text, nullable=False),
        sa.Column("instr_hash", sa.Text, nullable=True),
        sa.Column("account_arg", sa.Text, nullable=True),
        sa.Column("resolved_account_id", sa.Text, nullable=True),
        sa.Column("preview", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("statement_previews")
