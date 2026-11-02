"""import_runs: the stored report of every provider import / verify / preview run.

Schema-only: a brand-new table, no prior file or data to carry over. The columns are what the
history list and the polls look up (provider, kind, status, archived_at, started_at, ...);
``detail`` holds the heavy parts a list never reads. The YNAB migration job used to keep its state in memory and lose it on
a restart or a reload; each run is now a row, readable (and archivable) from Imports & sync.

Checkpoint-EXCLUDED, non-restorable and wiped by Reset (``db/portable_bridge.py``). It holds no
credential.

Revision ID: 202610211200_import_runs
Revises: 202610171500_trade_cash_legs_say_it_in_the_description
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610211200_import_runs"
down_revision = "202610171500_trade_cash_legs_say_it_in_the_description"
branch_labels = None
depends_on = None

_TABLE = "import_runs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("finished_at", sa.Text, nullable=True),
        sa.Column("plan_id", sa.Text, nullable=False),
        sa.Column("plan_name", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("archived_at", sa.Text, nullable=True),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column("progress", sa.JSON, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("detail", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_provider_started", _TABLE, ["provider", "started_at"])
    op.create_index(f"ix_{_TABLE}_status", _TABLE, ["status"])


def downgrade() -> None:
    op.drop_table(_TABLE)
