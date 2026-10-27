"""memory_forgotten — a fact the user forgot stays forgotten; the You topic is "Profile".

Two parts, both from ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` amendment 7:

- A ``memory_forgotten`` table: one row per fact the user told Lucius to forget, keyed by the
  SHA-256 of its normalised text (``ai.memory_structure.fingerprint``). The text itself is never
  stored. The memory write choke refuses an implicit write that matches a row.
- A one-time rename of the stored You topic "About you" (any case) to "Profile". The write choke
  maps the old name on every write from now on; this catches what was stored before. Only a
  fact in section ``you`` is touched, only its ``topic`` key, and an unreadable payload is
  skipped, never a failed boot. Idempotent.

Downgrade drops the table and leaves the topic name as it is (a "Profile" written after the
upgrade cannot be told apart from a renamed one).

Revision ID: 202610271800_memory_forgotten
Revises: 202610271200_memory_facts_get_a_section
Create Date: 2026-10-27
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202610271800_memory_forgotten"
down_revision = "202610271200_memory_facts_get_a_section"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _rename_about_you(conn) -> int:
    renamed = 0
    for row_id, payload in conn.execute(sa.text("SELECT id, payload FROM memory")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("section") != "you":
            continue
        topic = data.get("topic")
        if not isinstance(topic, str) or " ".join(topic.split()).casefold() != "about you":
            continue
        data["topic"] = "Profile"
        conn.execute(
            sa.text("UPDATE memory SET payload = :p WHERE id = :i"),
            {"p": json.dumps(data), "i": row_id},
        )
        renamed += 1
    return renamed


def upgrade() -> None:
    op.create_table(
        "memory_forgotten",
        sa.Column("hash", sa.Text, primary_key=True),
        sa.Column("forgotten_at", sa.Text, nullable=False),
    )
    renamed = _rename_about_you(op.get_bind())
    logger.info("memory facts: renamed %d 'About you' topic(s) to 'Profile'", renamed)


def downgrade() -> None:
    op.drop_table("memory_forgotten")
