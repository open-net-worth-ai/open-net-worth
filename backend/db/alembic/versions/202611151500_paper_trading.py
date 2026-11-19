"""paper_trading — the simulated paper book (ADR 2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path).

Additive: five book-scoped tables written only by ``services.paper_trading`` behind the
``paper_trading`` flag. ``paper_events`` is the append-only truth; orders, events and the daily
value snapshot promote the columns the fill engine queries. Downgrade drops them; every paper
portfolio is lost, and nothing in the real book changes.

Revision ID: 202611151500_paper_trading
Revises: 202611131900_merge_followed_portfolios_and_bank_data_sync
Create Date: 2026-11-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611151500_paper_trading"
down_revision = "202611131900_merge_followed_portfolios_and_bank_data_sync"
branch_labels = None
depends_on = None


def _base(name: str, *extra) -> None:
    op.create_table(
        name,
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
        *extra,
    )
    op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)


def _portfolio_fk() -> sa.Column:
    return sa.Column(
        "portfolio_id",
        sa.Text,
        sa.ForeignKey("paper_portfolios.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    _base("paper_portfolios")
    _base("paper_contracts")
    _base("paper_orders", _portfolio_fk(), sa.Column("status", sa.Text, nullable=False))
    op.create_index("ix_paper_orders_book_status", "paper_orders", ["book_id", "status"])
    op.create_index("ix_paper_orders_portfolio", "paper_orders", ["portfolio_id"])
    _base("paper_events", _portfolio_fk(), sa.Column("idem_key", sa.Text, nullable=True))
    op.create_index("ix_paper_events_portfolio", "paper_events", ["portfolio_id"])
    op.create_index(
        "ux_paper_events_portfolio_idem", "paper_events", ["portfolio_id", "idem_key"], unique=True
    )
    _base("paper_values", _portfolio_fk(), sa.Column("date", sa.Text, nullable=False))
    op.create_index(
        "ux_paper_values_portfolio_date", "paper_values", ["portfolio_id", "date"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_paper_values_portfolio_date", table_name="paper_values")
    op.drop_index("ix_paper_values_book_seq", table_name="paper_values")
    op.drop_table("paper_values")
    op.drop_index("ux_paper_events_portfolio_idem", table_name="paper_events")
    op.drop_index("ix_paper_events_portfolio", table_name="paper_events")
    op.drop_index("ix_paper_events_book_seq", table_name="paper_events")
    op.drop_table("paper_events")
    op.drop_index("ix_paper_orders_portfolio", table_name="paper_orders")
    op.drop_index("ix_paper_orders_book_status", table_name="paper_orders")
    op.drop_index("ix_paper_orders_book_seq", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_index("ix_paper_contracts_book_seq", table_name="paper_contracts")
    op.drop_table("paper_contracts")
    op.drop_index("ix_paper_portfolios_book_seq", table_name="paper_portfolios")
    op.drop_table("paper_portfolios")
