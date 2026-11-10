"""ibkr_flex_display_name — the built-in IBKR connector reads "IBKR Flex", as its directory card does.

The built-in ``ibkr`` connector (Flex Web Service report import) was stored as "Interactive
Brokers", while Browse & add already called it "IBKR Flex". Once IBKR's own hosted MCP server
could be added — and named itself "Interactive Brokers" from its website — Lucius's @-mention
list showed two rows with the same name. Upgrade renames the stored row, only while it still
carries the old default (a name the user changed is kept). Downgrade restores the old default
the same way.

Revision ID: 202610291900_ibkr_flex_display_name
Revises: 202610290100_investment_theses
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610291900_ibkr_flex_display_name"
down_revision = "202610290100_investment_theses"
branch_labels = None
depends_on = None

_OLD = "Interactive Brokers"
_NEW = "IBKR Flex"


def _rename(old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text("UPDATE connectors SET name = :new WHERE id = 'ibkr' AND name = :old"),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    _rename(_OLD, _NEW)


def downgrade() -> None:
    _rename(_NEW, _OLD)
