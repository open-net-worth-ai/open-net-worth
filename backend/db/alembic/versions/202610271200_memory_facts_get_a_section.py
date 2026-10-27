"""Every memory fact lives in a section; the ones written before sections are ``unsorted``.

A fact now carries ``state`` (``live`` / ``unsorted``), a ``section`` and a ``topic``. A fact
saved before this revision has none of them — only a ``tier`` — and the legacy tier says nothing
about WHERE it belongs, so each is stamped ``state: "unsorted"`` with ``section`` and ``topic``
null, for Lucius to file later. ``tier`` and every other key are left exactly as they were, and a
fact that already has a ``state`` is not touched.

Idempotent; an unreadable payload is skipped, never a failed boot. Downgrade is a no-op.

Revision ID: 202610271200_memory_facts_get_a_section
Revises: 202610251700_statement_previews
Create Date: 2026-10-27
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202610271200_memory_facts_get_a_section"
down_revision = "202610251700_statement_previews"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    conn = op.get_bind()
    stamped = 0
    for row_id, payload in conn.execute(sa.text("SELECT id, payload FROM memory")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or "state" in data:
            continue
        data.update(state="unsorted", section=None, topic=None)
        conn.execute(
            sa.text("UPDATE memory SET payload = :p WHERE id = :i"),
            {"p": json.dumps(data), "i": row_id},
        )
        stamped += 1
    logger.info("memory facts: stamped %d legacy fact(s) unsorted", stamped)


def downgrade() -> None:
    pass
