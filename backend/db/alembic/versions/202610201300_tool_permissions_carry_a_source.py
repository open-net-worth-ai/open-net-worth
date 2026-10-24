"""Every permission row now says whether it is still the app's seed or the owner's decision.

Permissions are one value per tool (ADR `2026-10-11-permissions-are-one-value-per-tool`): every
tool has a row, seeded with a default, and a row remembers `source: "seed" | "user"` so a boot
sync can move a seed row to a changed rule without ever touching a decision.

Every row that exists BEFORE this revision was written by the owner (through the confirm card,
Settings → Permissions or a connector's ladder — nothing seeded a row until now), so each one is
stamped `source: "user"`. A row that happens to equal what the seed would be is still the
owner's: it does not follow a later change of the rule. Seeding itself needs the live tool
registry, so it is not done here — the boot sync (`services.tool_permissions.sync_seeds`) inserts
the missing rows, and carries the retired YOLO switch forward as per-tool Always allow rows.

Deterministic: no clock, no registry. Idempotent: a row that already has a `source` is skipped.

Revision ID: 202610201300_tool_permissions_carry_a_source
Revises: 202610201200_search_tools_belong_to_the_search_connector
Create Date: 2026-10-20
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202610201300_tool_permissions_carry_a_source"
down_revision = "202610201200_search_tools_belong_to_the_search_connector"
branch_labels = None
depends_on = None

_SELECT = sa.text("SELECT id, payload FROM tool_permissions")
_UPDATE = sa.text("UPDATE tool_permissions SET payload = :payload WHERE id = :id")


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, raw in conn.execute(_SELECT).fetchall():
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict) or "source" in payload:
            continue
        conn.execute(_UPDATE, {"payload": json.dumps({**payload, "source": "user"}), "id": row_id})


def downgrade() -> None:
    """No-op — the extra payload key is harmless to code that does not read it."""
