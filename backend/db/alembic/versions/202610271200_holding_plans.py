"""holding_plans — why you own (or would buy) an instrument, moved off the watch item.

A watched ticker's ``intent`` (thesis, buy zone, fair value) lived inside the watchlist item, and
a held ticker's auto "My holdings" item is deleted on a full sell — so a plan stored there died
with the position. Plans now live in their own book-scoped table, one row per instrument (ADR
``2026-10-27-a-holding-plan-lives-on-the-instrument``).

Upgrade creates the table and moves every ticker item's ``intent`` into a plan when the item's
instrument is known: its ``instrument_id``, else exactly one instrument whose ``price_source_id``
matches the ticker, else exactly one carrying it as a bare symbol (held crypto: "BTC" vs
"bitcoin"). An item is left untouched — ``intent`` kept, logged — when no instrument matches,
when a second item maps to an instrument that already got a plan, or when a stored price is not
a number. ``services.holding_plans`` reads a kept intent as a fallback, and the first plan
written for that instrument absorbs it.

Downgrade copies each plan's thesis/buy_below/buy_above/fair_value back onto the matching watch
item and drops the table; role, sell condition, stop and trim are lost on downgrade.

Revision ID: 202610271200_holding_plans
Revises: 202610251700_statement_previews
Create Date: 2026-10-27
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202610271200_holding_plans"
down_revision = "202610251700_statement_previews"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

_INTENT_FIELDS = ("thesis", "buy_below", "buy_above", "fair_value")
_PLAN_FIELDS = (
    "role",
    "thesis",
    "exit_if",
    "stop_below",
    "buy_below",
    "buy_above",
    "fair_value",
    "trim_above",
)

_meta = sa.MetaData()
_watchlists = sa.Table(
    "watchlists", _meta, sa.Column("book_id", sa.Text), sa.Column("payload", sa.JSON)
)
_instruments = sa.Table(
    "instruments", _meta, sa.Column("id", sa.Text), sa.Column("payload", sa.JSON)
)
_plans = sa.Table(
    "holding_plans",
    _meta,
    sa.Column("book_id", sa.Text),
    sa.Column("seq", sa.Integer),
    sa.Column("id", sa.Text),
    sa.Column("payload", sa.JSON),
)


def _instrument_for(item: dict, by_source_id: dict, by_symbol: dict, known: set[str]) -> str | None:
    iid = item.get("instrument_id")
    if iid and iid in known:
        return iid
    key = (item.get("value") or "").strip().lower()
    matches = by_source_id.get(key) or by_symbol.get(key) or []
    return matches[0] if len(matches) == 1 else None


def _intent_fields(intent: dict) -> dict | None:
    """The intent as plan fields, or None when a stored price is not a usable number."""
    out = {}
    for key in _INTENT_FIELDS:
        value = intent.get(key)
        if value in (None, ""):
            continue
        if key == "thesis":
            out[key] = str(value).strip()
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            return None
    return {k: v for k, v in out.items() if v != ""}


def upgrade() -> None:
    op.create_table(
        "holding_plans",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_holding_plans_book_seq", "holding_plans", ["book_id", "seq"], unique=True)

    conn = op.get_bind()
    known: set[str] = set()
    by_source_id: dict[str, list[str]] = {}
    by_symbol: dict[str, list[str]] = {}
    for iid, payload in conn.execute(sa.select(_instruments.c.id, _instruments.c.payload)):
        known.add(iid)
        sid = ((payload or {}).get("price_source_id") or "").strip().lower()
        if sid:
            by_source_id.setdefault(sid, []).append(iid)
        sym = ((payload or {}).get("symbol") or "").strip().lower()
        if sym:
            by_symbol.setdefault(sym, []).append(iid)

    now = datetime.now(timezone.utc).isoformat()
    seq = 0
    for book_id, payload in conn.execute(sa.select(_watchlists.c.book_id, _watchlists.c.payload)):
        payload = dict(payload or {})
        items = [dict(i) for i in payload.get("items") or []]
        planned: set[str] = set()
        changed = False
        for item in items:
            intent = item.get("intent")
            if (item.get("type") or "ticker") != "ticker" or not isinstance(intent, dict):
                continue
            fields = _intent_fields(intent)
            iid = _instrument_for(item, by_source_id, by_symbol, known)
            if iid is None or fields is None or iid in planned:
                log.warning(
                    "holding_plans: kept the intent on watch item %s (%s)",
                    item.get("id"),
                    "no single matching instrument"
                    if iid is None
                    else "a stored price is not a number"
                    if fields is None
                    else "its instrument already has a plan from another item",
                )
                continue
            if fields:
                plan = {
                    "id": f"hp_{secrets.token_hex(6)}",
                    "instrument_id": iid,
                    **{k: None for k in _PLAN_FIELDS},
                    **fields,
                    "created_at": now,
                    "updated_at": now,
                }
                conn.execute(
                    _plans.insert().values(book_id=book_id, seq=seq, id=plan["id"], payload=plan)
                )
                seq += 1
                planned.add(iid)
            item.pop("intent", None)
            changed = True
        if changed:
            payload["items"] = items
            conn.execute(
                _watchlists.update().where(_watchlists.c.book_id == book_id).values(payload=payload)
            )


def downgrade() -> None:
    conn = op.get_bind()
    plans_by_book: dict[str, dict[str, dict]] = {}
    for book_id, payload in conn.execute(sa.select(_plans.c.book_id, _plans.c.payload)):
        plans_by_book.setdefault(book_id, {})[payload.get("instrument_id")] = payload
    source_ids = {
        iid: ((p or {}).get("price_source_id") or "").strip().lower()
        for iid, p in conn.execute(sa.select(_instruments.c.id, _instruments.c.payload))
    }
    for book_id, payload in conn.execute(sa.select(_watchlists.c.book_id, _watchlists.c.payload)):
        plans = plans_by_book.get(book_id) or {}
        if not plans:
            continue
        payload = dict(payload or {})
        items = [dict(i) for i in payload.get("items") or []]
        for item in items:
            if (item.get("type") or "ticker") != "ticker":
                continue
            iid = item.get("instrument_id")
            if not iid:
                ticker = (item.get("value") or "").strip().lower()
                iid = next((k for k, v in source_ids.items() if v and v == ticker), None)
            plan = plans.get(iid) if iid else None
            if plan:
                intent = {k: plan[k] for k in _INTENT_FIELDS if plan.get(k) not in (None, "")}
                if intent:
                    item["intent"] = intent
        payload["items"] = items
        conn.execute(
            _watchlists.update().where(_watchlists.c.book_id == book_id).values(payload=payload)
        )
    op.drop_index("ix_holding_plans_book_seq", table_name="holding_plans")
    op.drop_table("holding_plans")
