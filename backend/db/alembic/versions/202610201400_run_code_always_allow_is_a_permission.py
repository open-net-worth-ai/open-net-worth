"""`run_code`'s hidden instance-wide "always allow" becomes an ordinary permission value.

`run_code` used to carry its own approval logic: an in-handler "first run in a conversation asks"
card (`code_run`) and an instance flag, `app_config.code_execution_auto_run`, that skipped it
everywhere. That was a second permission the Permissions list could not show. `run_code` is now an
ordinary tool — seeded Ask because it declares `external_effect=True` — and the ONE permission
value plus the approval mode decide (ADR `2026-10-11-permissions-are-one-value-per-tool`). The
kill switch (`code_execution_enabled`) stays: it is the feature's enable/disable, not an approval.

An owner who had switched the flag on had said "stop asking me about run_code", so that choice is
kept as what it now is: a stored `core:run_code` Always allow, written as theirs (`source: "user"`).
The flag is then removed from the app_config row, so nothing reads it any more. A row the owner
already has for `run_code` is left exactly as it is.

Deterministic: a constant timestamp, no registry (the tool name is a frozen literal). Idempotent: a
second run finds no flag to carry.

Revision ID: 202610201400_run_code_always_allow_is_a_permission
Revises: 202610201300_tool_permissions_carry_a_source
Create Date: 2026-10-20
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202610201400_run_code_always_allow_is_a_permission"
down_revision = "202610201300_tool_permissions_carry_a_source"
branch_labels = None
depends_on = None

_FLAG = "code_execution_auto_run"
_KEY = "core:run_code"
_STAMP = "2026-10-11T00:00:00+00:00"

_perms = sa.table(
    "tool_permissions",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_SELECT = sa.text("SELECT payload FROM runtime_state WHERE namespace = 'app_config'")
_UPDATE = sa.text("UPDATE runtime_state SET payload = :payload WHERE namespace = 'app_config'")


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(_SELECT).first()
    if row is None:
        return
    payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if not isinstance(payload, dict) or _FLAG not in payload:
        return
    carried = bool(payload[_FLAG])
    conn.execute(_UPDATE, {"payload": json.dumps({k: v for k, v in payload.items() if k != _FLAG})})
    if not carried:
        return
    if conn.execute(sa.select(_perms.c.id).where(_perms.c.id == _KEY)).first() is not None:
        return  # the owner already has a value for run_code — theirs wins
    seq = (conn.execute(sa.select(sa.func.max(_perms.c.seq))).scalar() or 0) + 1
    conn.execute(
        sa.insert(_perms).values(
            seq=seq,
            id=_KEY,
            payload={
                "id": _KEY,
                "connector_id": "core",
                "tool": "run_code",
                "level": "always_allow",
                "source": "user",
                "updated_at": _STAMP,
            },
        )
    )


def downgrade() -> None:
    """No-op — the flag is retired."""
