"""The "Unattended script runs" switch is removed; its stored key goes with it.

`app_config.script_skills_unattended_enabled` decided whether `run_script` could run in a job nobody
was sitting in front of. That is a decision by context, which the tool's permission value and the
approval mode already make (ADR `2026-10-11-permissions-are-one-value-per-tool`): `run_script` is
an ordinary tool, seeded Ask, and it follows exactly the same chain in an Automation as anywhere
else. Only the two feature switches stay — `code_execution_enabled` and `script_skills_enabled`.

The key is dropped from the `app_config` row so nothing stores a value nothing reads. Nothing is
carried over: the switch only ever REFUSED, so there is no permission to preserve.

Deterministic and idempotent (a second run finds no key); the downgrade is a safe no-op.

Revision ID: 202610201500_script_skills_unattended_switch_is_removed
Revises: 202610201400_run_code_always_allow_is_a_permission
Create Date: 2026-10-20
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202610201500_script_skills_unattended_switch_is_removed"
down_revision = "202610201400_run_code_always_allow_is_a_permission"
branch_labels = None
depends_on = None

_FLAG = "script_skills_unattended_enabled"
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
    conn.execute(_UPDATE, {"payload": json.dumps({k: v for k, v in payload.items() if k != _FLAG})})


def downgrade() -> None:
    """No-op — the switch is retired."""
