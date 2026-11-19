"""trading_agents_runs — one row per TradingAgents analysis (flag ``trading_agents``).

Additive: one table written only by ``services.trading_agents.runs.apply_run_change`` behind the
``trading_agents`` flag. Real columns, not a payload, because the cost panel and the monthly cap
aggregate over them. Tier-2 (no ``book_id``): a run is research, never part of the ledger, net
worth or the budget. Downgrade drops the table; every recorded run is lost and nothing in the
real book changes.

Revision ID: 202611191200_trading_agents_runs
Revises: 202611181500_merge_paper_trading_and_mcp_oauth
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611191200_trading_agents_runs"
down_revision = "202611181500_merge_paper_trading_and_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trading_agents_runs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, nullable=True),
        sa.Column("trade_date", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("requested_by", sa.Text, nullable=False),
        sa.Column("session_id", sa.Text, nullable=True),
        sa.Column("analysts", sa.JSON, nullable=False),
        sa.Column("config_snapshot", sa.JSON, nullable=False),
        sa.Column("context_level", sa.Text, nullable=False),
        sa.Column("context_sent", sa.JSON, nullable=False),
        sa.Column("progress", sa.JSON, nullable=False),
        sa.Column("signal", sa.Text, nullable=True),
        sa.Column("decision_text", sa.Text, nullable=True),
        sa.Column("reports", sa.JSON, nullable=True),
        sa.Column("llm_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cache_creation_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("plan_included_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("unpriced_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_by_role", sa.JSON, nullable=False),
        sa.Column("cost_by_model", sa.JSON, nullable=False),
        sa.Column("estimate_usd", sa.Float, nullable=True),
        sa.Column("cap_usd", sa.Float, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("started_at", sa.Text, nullable=True),
        sa.Column("finished_at", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled','stopped_budget',"
            "'stopped_calls','timed_out','interrupted')",
            name="ck_trading_agents_runs_status",
        ),
        sa.CheckConstraint(
            "requested_by IN ('user','lucius','automation')",
            name="ck_trading_agents_runs_requested_by",
        ),
        sa.CheckConstraint(
            "context_level IN ('investments','position','ticker')",
            name="ck_trading_agents_runs_context_level",
        ),
    )
    op.create_index(
        "ix_trading_agents_runs_ticker_created", "trading_agents_runs", ["ticker", "created_at"]
    )
    op.create_index("ix_trading_agents_runs_status", "trading_agents_runs", ["status"])
    op.create_index("ix_trading_agents_runs_created", "trading_agents_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_trading_agents_runs_created", table_name="trading_agents_runs")
    op.drop_index("ix_trading_agents_runs_status", table_name="trading_agents_runs")
    op.drop_index("ix_trading_agents_runs_ticker_created", table_name="trading_agents_runs")
    op.drop_table("trading_agents_runs")
