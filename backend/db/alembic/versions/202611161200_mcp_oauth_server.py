"""mcp_oauth_server — the inbound MCP OAuth server's four tables (flag ``mcp_oauth``).

Additive: ``mcp_oauth_clients`` (owner-created app credentials), ``mcp_oauth_pending`` (staged
authorization requests and their single-use codes), ``mcp_oauth_grants`` (one per approved app)
and ``mcp_oauth_tokens`` (hashed access/refresh tokens). Written only while the flag is on.
Downgrade drops them; every connected app has to reconnect.

Revision ID: 202611161200_mcp_oauth_server
Revises: 202611131900_merge_followed_portfolios_and_bank_data_sync
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611161200_mcp_oauth_server"
down_revision = "202611131900_merge_followed_portfolios_and_bank_data_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("client_id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("secret_hash", sa.Text, nullable=False),
        sa.Column("redirect_uris", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("revoked_at", sa.Text, nullable=True),
    )
    op.create_table(
        "mcp_oauth_pending",
        sa.Column("id_hash", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text, nullable=False),
        sa.Column("client_host", sa.Text, nullable=False),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("code_challenge", sa.Text, nullable=False),
        sa.Column("requested_scopes", sa.Text, nullable=False),
        sa.Column("granted_scopes", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=True),
        sa.Column("grant_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("used_at", sa.Text, nullable=True),
        sa.CheckConstraint(
            "kind IN ('request','code')", name=sa.schema.conv("ck_mcp_oauth_pending_kind")
        ),
    )
    op.create_table(
        "mcp_oauth_grants",
        sa.Column("grant_id", sa.Text, primary_key=True),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text, nullable=False),
        sa.Column("client_host", sa.Text, nullable=False),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("last_used_at", sa.Text, nullable=True),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("revoked_at", sa.Text, nullable=True),
        sa.Column("revoke_reason", sa.Text, nullable=True),
    )
    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("token_hash", sa.Text, primary_key=True),
        sa.Column(
            "grant_id",
            sa.Text,
            sa.ForeignKey("mcp_oauth_grants.grant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("rotated_at", sa.Text, nullable=True),
        sa.Column("replaced_by_hash", sa.Text, nullable=True),
        sa.CheckConstraint(
            "kind IN ('access','refresh')", name=sa.schema.conv("ck_mcp_oauth_tokens_kind")
        ),
    )
    op.create_index("ix_mcp_oauth_tokens_grant", "mcp_oauth_tokens", ["grant_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_tokens_grant", table_name="mcp_oauth_tokens")
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_oauth_grants")
    op.drop_table("mcp_oauth_pending")
    op.drop_table("mcp_oauth_clients")
