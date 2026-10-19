"""`web_search` / `fetch_page` belong to the search connector; `x_search` is retired.

ADR `2026-10-11-tool-defaults-follow-what-a-tool-does`. The generic `web_search` and
`fetch_page` call the ACTIVE search provider, so their permission now resolves to that
provider's own tool (`<id>_search` / `<id>_fetch_page`, owner `web_search:<id>`): the search
connector's page is their one control, and a `core:` row of their own could only disagree with
it. `x_search` was `web_search` with `(site:x.com OR site:twitter.com)` appended and is gone.

## What this does to stored data

1. **A restrictive decision is carried, never dropped.** A stored `core:web_search` /
   `core:fetch_page` of `needs_approval` or `blocked` is copied onto the corresponding tool of
   EVERY search provider that has no stored row of its own. Every provider, not just today's
   active one: "which provider is active" depends on live enablement and keys, so it is not a
   fact a frozen migration can pin, and carrying to one provider would silently lift the owner's
   restriction the day they switch providers. A provider's existing row is the owner's and is
   left exactly as it is. A stored `always_allow` is not carried — a read already runs by
   default, so the row said nothing the default doesn't.
2. **The three `core:` rows are then deleted** (`web_search`, `fetch_page`, `x_search`), so no
   independent row can disagree with the connector's. A stored `core:x_search` is simply
   deleted — the tool no longer exists.
3. **Automation `always_allowed` lists** that name `x_search` now name `web_search` (de-duplicated,
   order kept). Skills carry no tool list, so there is nothing to rewrite there.

Deterministic: carried rows keep the decision's own `updated_at`.

`downgrade()` is a no-op: the deleted rows and the rewritten names cannot be told apart from
rows the owner made after this revision.

Revision ID: 202610201200_search_tools_belong_to_the_search_connector
Revises: 202610171500_trade_cash_legs_say_it_in_the_description
Create Date: 2026-10-20
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.search_tools_belong_to_the_search_connector")

revision = "202610201200_search_tools_belong_to_the_search_connector"
down_revision = "202610171500_trade_cash_legs_say_it_in_the_description"
branch_labels = None
depends_on = None

_CORE = "core"
# Frozen: the providers that exist today (`ai/search/base.py::PROVIDER_REGISTRY`).
_PROVIDERS = ("duckduckgo", "tavily", "perplexity", "serper", "google_pse", "firecrawl")
_CARRY = {"web_search": "{id}_search", "fetch_page": "{id}_fetch_page"}
_RETIRED = ("x_search",)
_RESTRICTIVE = ("needs_approval", "blocked")

_perms = sa.table(
    "tool_permissions",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)

_SELECT_AUTOMATIONS = sa.text("SELECT id, payload FROM automations")
_UPDATE_AUTOMATION = sa.text("UPDATE automations SET payload = :payload WHERE id = :id")


def _load(raw: object) -> object:
    return json.loads(raw) if isinstance(raw, str) else raw


def _carry_and_delete_permissions(conn) -> None:
    core_ids = [f"{_CORE}:{tool}" for tool in (*_CARRY, *_RETIRED)]
    old = {
        row_id: _load(payload)
        for row_id, payload in conn.execute(
            sa.select(_perms.c.id, _perms.c.payload).where(_perms.c.id.in_(core_ids))
        )
    }
    if not old:
        return
    existing = {r[0] for r in conn.execute(sa.select(_perms.c.id))}
    next_seq = (conn.execute(sa.select(sa.func.max(_perms.c.seq))).scalar() or 0) + 1
    for tool, template in _CARRY.items():
        payload = old.get(f"{_CORE}:{tool}")
        level = payload.get("level") if isinstance(payload, dict) else None
        if level not in _RESTRICTIVE:
            continue
        for provider in _PROVIDERS:
            owner, name = f"web_search:{provider}", template.format(id=provider)
            key = f"{owner}:{name}"
            if key in existing:
                continue  # the provider's own row is the owner's — never overwritten
            conn.execute(
                sa.insert(_perms).values(
                    seq=next_seq,
                    id=key,
                    payload={
                        "id": key,
                        "connector_id": owner,
                        "tool": name,
                        "level": level,
                        "updated_at": payload.get("updated_at") or "",
                    },
                )
            )
            next_seq += 1
    conn.execute(sa.delete(_perms).where(_perms.c.id.in_(list(old))))


def _rename_in_automations(conn) -> None:
    touched = 0
    for row_id, raw in conn.execute(_SELECT_AUTOMATIONS).fetchall():
        try:
            payload = _load(raw)
        except Exception:  # noqa: BLE001 — one unreadable row must not stop the rest
            log.warning("automation payload %s is unreadable; skipped", row_id)
            continue
        groups = payload.get("always_allowed") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            continue
        changed = False
        for group in groups:
            tools = group.get("tools") if isinstance(group, dict) else None
            if not isinstance(tools, list) or not any(t in _RETIRED for t in tools):
                continue
            group["tools"] = list(
                dict.fromkeys("web_search" if t in _RETIRED else t for t in tools)
            )
            changed = True
        if changed:
            conn.execute(_UPDATE_AUTOMATION, {"payload": json.dumps(payload), "id": row_id})
            touched += 1
    if touched:
        log.info("x_search -> web_search in %d automation(s)", touched)


def upgrade() -> None:
    conn = op.get_bind()
    _carry_and_delete_permissions(conn)
    _rename_in_automations(conn)


def downgrade() -> None:
    """No-op — see the module docstring."""
