"""Persist autonomous trading workspaces and execution history."""

import sqlalchemy as sa
from alembic import op

revision = "202611041900_trading_workspace"
down_revision = "202611021300_merge_ynab_sync_and_news_deliveries"
branch_labels = None
depends_on = None

NAMES = (
    "trading_workspaces",
    "trading_runs",
    "trading_accounts",
    "trading_orders",
    "trading_fills",
    "trading_events",
    "trading_comparisons",
    "trading_control",
    "trading_grants",
)


def upgrade():
    for name in NAMES:
        op.create_table(
            name,
            sa.Column("book_id", sa.Text(), sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("scope_id", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        )
        op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)
        op.create_index(f"ix_{name}_book_scope", name, ["book_id", "scope_id"])
    control = sa.table(
        "trading_control",
        sa.column("book_id"),
        sa.column("seq"),
        sa.column("id"),
        sa.column("scope_id"),
        sa.column("payload", sa.JSON()),
    )
    op.bulk_insert(
        control,
        [
            {
                "book_id": "book_000000000000",
                "seq": 0,
                "id": "trading_lock",
                "scope_id": "",
                "payload": {},
            }
        ],
    )


def downgrade():
    for name in reversed(NAMES):
        op.drop_table(name)
