"""memory_topic_summaries — one generated Summary line per memory topic.

Display-only (ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` amendment 8): the idle
sweep writes one row per live topic, keyed by section and the topic's case-folded key, with a
digest of the topic's facts so only a topic whose facts changed is summarised again. Never read
into a model prompt. A new, empty table — downgrade drops it and nothing else.

Revision ID: 202610272100_memory_topic_summaries
Revises: 202610271800_memory_forgotten
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610272100_memory_topic_summaries"
down_revision = "202610271800_memory_forgotten"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_topic_summaries",
        sa.Column("section", sa.Text, primary_key=True),
        sa.Column("topic_key", sa.Text, primary_key=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("facts_digest", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("memory_topic_summaries")
