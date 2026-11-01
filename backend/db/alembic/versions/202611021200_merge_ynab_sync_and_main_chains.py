"""Merge point: the YNAB import and sync revisions branched off
``202610171500_trade_cash_legs_say_it_in_the_description`` while ``main`` moved on to
``202610292000_positions_drop_note`` (statement previews, memory, investment theses, IBKR,
positions).

Verified data-safe in either order before merging. The YNAB chain only CREATES tables of its own
(``import_runs`` in ``202610211200_import_runs``; the ``ynab_sync_*`` tables in
``202610211300_ynab_two_way_sync``) plus their indexes, and none of main's revisions reads or
writes those names. A database already stamped at either head therefore upgrades correctly
through this merge. Re-parenting the YNAB chain onto main's head instead would have left a book
already at ``202610211300`` believing it had applied main's revisions. So this revision carries
no operations of its own: the same pattern as ``202609301200_merge_topics_and_main_chains``.

Revision ID: 202611021200_merge_ynab_sync_and_main_chains
Revises: 202610211300_ynab_two_way_sync, 202610292000_positions_drop_note  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-11-02
"""

from __future__ import annotations

revision = "202611021200_merge_ynab_sync_and_main_chains"
down_revision = (
    "202610211300_ynab_two_way_sync",
    "202610292000_positions_drop_note",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
