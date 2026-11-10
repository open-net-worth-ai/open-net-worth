"""positions_drop_note — a position no longer carries a free-text note.

The field had no purpose of its own: it held either a label that belongs to the thesis
("Speculative") or the provenance of one fill, which belongs to that fill's lot (a position
reading "bought 42" while holding 50). Owner decision 2026-10-29: drop it outright, no move into
theses or lots. Upgrade deletes the ``note`` key from every position payload. Lot notes are a
different field and are untouched.

Downgrade is a no-op: the text is gone, and the code that read it is gone with it.

Revision ID: 202610292000_positions_drop_note
Revises: 202610291900_ibkr_flex_display_name
Create Date: 2026-10-29
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202610292000_positions_drop_note"
down_revision = "202610291900_ibkr_flex_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, payload in conn.execute(sa.text("SELECT id, payload FROM positions")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or "note" not in data:
            continue
        data.pop("note")
        conn.execute(
            sa.text("UPDATE positions SET payload = :payload WHERE id = :id"),
            {"payload": json.dumps(data), "id": row_id},
        )


def downgrade() -> None:
    pass
