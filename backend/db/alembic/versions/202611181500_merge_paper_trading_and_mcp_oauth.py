"""Merge point: ``main`` added ``202611161200_mcp_oauth_server`` on top of
``202611131900_merge_followed_portfolios_and_bank_data_sync`` while this branch added
``202611151500_paper_trading`` to that same revision.

Verified data-safe in either order before merging. ``mcp_oauth_server`` only creates its four
``mcp_oauth_*`` tables and one index; ``paper_trading`` only creates its own ``paper_*`` tables.
Neither reads or writes the other's names, so a database stamped at either head upgrades
correctly through this merge. No operations of its own — the same pattern as
``202611131900_merge_followed_portfolios_and_bank_data_sync``.

Revision ID: 202611181500_merge_paper_trading_and_mcp_oauth
Revises: 202611151500_paper_trading, 202611161200_mcp_oauth_server  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-11-18
"""

from __future__ import annotations

revision = "202611181500_merge_paper_trading_and_mcp_oauth"
down_revision = (
    "202611151500_paper_trading",
    "202611161200_mcp_oauth_server",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
