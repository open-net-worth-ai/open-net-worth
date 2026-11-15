"""``apply_paper_change`` — THE write choke of the simulated paper book.

Every paper write goes through here, each op inside ONE ``locked_unit_of_work`` over the five
paper tables and nothing else: no account, transaction, position, lot or real figure is in
reach (an import-linter contract forbids the imports). Quotes are fetched BEFORE the lock — a
slow provider must not hold the paper tables — and the book is re-derived and cover re-checked
inside it, so a concurrent order is never double-spent.

Ops: ``create``, ``update``, ``start_over``, ``archive``, ``delete``, ``place``, ``cancel``,
``fill`` (the engine retrying an open order), ``expire``, ``lifecycle`` (corporate actions and
other dated events, idempotent on ``idem_key``), ``mark`` (a price you set), ``snapshot`` (the
end-of-day value row).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.paper_store import (
    ALL_TABLES,
    MAX_ACTIVE_PORTFOLIOS,
    PaperContractStore,
    PaperEventStore,
    PaperOrderStore,
    PaperPortfolioStore,
    PaperValueStore,
)
from backend.services.paper_trading import assets, orders, quotes, thesis
from backend.services.paper_trading import book as book_mod
from backend.services.paper_trading.model import (
    DEFAULT_SETTINGS,
    Fill,
    PaperAsset,
    Reject,
    Wait,
    settings_of,
)

logger = logging.getLogger(__name__)

_CREATORS = ("owner", "lucius")
_SETTING_KEYS = frozenset(DEFAULT_SETTINGS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── reads used by every op ──────────────────────────────────────────────────────────────


def portfolio(pid: str, *, conn: Connection | None = None) -> dict:
    row = PaperPortfolioStore().find_by_id(str(pid or ""), conn=conn)
    if not row:
        raise NotFoundError(f"No paper portfolio '{pid}'.")
    return row


def events(pid: str, *, conn: Connection | None = None) -> list[dict]:
    return PaperEventStore().for_portfolio(pid, conn=conn)


def portfolio_orders(pid: str, *, conn: Connection | None = None) -> list[dict]:
    return PaperOrderStore().for_portfolio(pid, conn=conn)


def derive(pid: str, *, conn: Connection | None = None) -> book_mod.Book:
    return book_mod.derive(events(pid, conn=conn))


def _require_active(row: dict) -> None:
    if row.get("status") != "active":
        raise ConflictError("That paper portfolio is archived. Start a new run to trade.")


# ── event writing ──────────────────────────────────────────────────────────────────────


def _append(pid: str, drafts: list[dict], *, conn: Connection) -> list[dict]:
    """Append events, skipping any whose ``idem_key`` the portfolio already has."""
    store = PaperEventStore()
    written: list[dict] = []
    for d in drafts:
        key = d.get("idem_key")
        if key and store.has_idem_key(pid, key, conn=conn):
            continue
        row = {
            "id": new_id("pevt"),
            "portfolio_id": pid,
            "kind": d["kind"],
            "at": d.get("at") or _now().isoformat(),
            "asset_key": d.get("asset_key"),
            "quantity_delta": float(d.get("quantity_delta") or 0.0),
            "cash_delta": d.get("cash_delta") or {},
            "price": d.get("price"),
            "price_basis": d.get("price_basis"),
            "order_id": d.get("order_id"),
            "fx_rate": d.get("fx_rate"),
            "payload": d.get("payload") or {},
            "idem_key": key,
        }
        store.insert(row, conn=conn)
        written.append(row)
    return written


# ── portfolios ─────────────────────────────────────────────────────────────────────────


def _clean_settings(raw: dict | None) -> dict:
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in _SETTING_KEYS:
            raise ValidationError(f"Unknown fill setting '{k}'.")
        out[k] = v
    for k in ("slippage_bps", "fx_spread_pct", "metal_premium_buy_pct", "metal_discount_sell_pct"):
        if k in out and (not isinstance(out[k], (int, float)) or not 0 <= float(out[k]) <= 1000):
            raise ValidationError(f"{k} must be a number between 0 and 1000.")
    return out


def _created_by(change: dict) -> str:
    origin = str(change.get("origin") or "owner")
    if origin in _CREATORS or origin.startswith("automation:"):
        return origin
    raise ValidationError("origin is owner, lucius or automation:<id>.")


def _create(change: dict) -> dict:
    from backend.services.paper_trading import seed

    home = str(change.get("home_currency") or "").upper() or seed.base_currency()
    start = str(change.get("start_with") or "cash")
    opening = seed.opening(change, home, _now())  # fetched before the lock
    now = _now()
    row = {
        "id": new_id("ppf"),
        "name": (str(change.get("name") or "").strip() or "Paper portfolio")[:80],
        "home_currency": home,
        "benchmark": str(change.get("benchmark") or seed.default_benchmark()),
        "settings": _clean_settings(change.get("settings")),
        "origin": {"kind": start, "ref": change.get("followed_id"), "label": opening["label"]},
        "status": "active",
        "lineage_id": change.get("lineage_id") or new_id("plin"),
        "run_no": int(change.get("run_no") or 1),
        "started_at": now.isoformat(),
        "archived_at": None,
        "created_by": _created_by(change),
        "automation_id": change.get("automation_id"),
        "start_spec": {
            "start_with": start,
            "cash": change.get("cash"),
            "followed_id": change.get("followed_id"),
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    with locked_unit_of_work(*ALL_TABLES) as conn:
        active = [p for p in PaperPortfolioStore().all(conn=conn) if p.get("status") == "active"]
        if len(active) >= MAX_ACTIVE_PORTFOLIOS:
            raise ConflictError(
                f"You can have up to {MAX_ACTIVE_PORTFOLIOS} active paper portfolios. "
                "Archive one first."
            )
        PaperPortfolioStore().insert(row, conn=conn)
        for ctr in opening["contracts"]:
            PaperContractStore().insert(ctr, conn=conn)
        _append(row["id"], opening["events"], conn=conn)
        _snapshot(row, now, conn=conn)
    return row


def _update(change: dict) -> dict:
    patch: dict = {}
    if "name" in change and change["name"] is not None:
        name = str(change["name"]).strip()
        if not name:
            raise ValidationError("A paper portfolio needs a name.")
        patch["name"] = name[:80]
    if change.get("benchmark"):
        patch["benchmark"] = str(change["benchmark"]).strip()
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = portfolio(change.get("id"), conn=conn)
        if change.get("settings") is not None:
            _require_active(row)
            patch["settings"] = {
                **(row.get("settings") or {}),
                **_clean_settings(change["settings"]),
            }
        if change.get("status") == "archived" and row.get("status") == "active":
            patch.update(_archive_patch(row["id"], conn=conn))
        patch["updated_at"] = _now().isoformat()
        return PaperPortfolioStore().update_by_id(row["id"], patch, conn=conn) or row


def _archive_patch(pid: str, *, conn: Connection) -> dict:
    """Cancel what is still open and stamp the archive — the history itself is untouched."""
    store = PaperOrderStore()
    for o in store.for_portfolio(pid, conn=conn):
        if o.get("status") == "open":
            store.update_by_id(
                o["id"], {"status": "cancelled", "state_reason": "Portfolio archived."}, conn=conn
            )
    return {"status": "archived", "archived_at": _now().isoformat()}


def _archive(change: dict) -> dict:
    return _update({"id": change.get("id"), "status": "archived"})


def _start_over(change: dict) -> dict:
    """Archive this run and start the next one the same way it started (same cash, same
    settings, the same copy re-read at today's prices). The old run keeps every order, event
    and value row, readable under Archived."""
    old = portfolio(change.get("id"))
    _require_active(old)
    spec = old.get("start_spec") or {"start_with": "cash", "cash": 10_000.0}
    archived = _archive({"id": old["id"]})
    new = _create(
        {
            **spec,
            "name": old["name"],
            "home_currency": old["home_currency"],
            "benchmark": old.get("benchmark"),
            "settings": old.get("settings") or {},
            "lineage_id": old.get("lineage_id"),
            "run_no": int(old.get("run_no") or 1) + 1,
            "origin": old.get("created_by") or "owner",
            "automation_id": old.get("automation_id"),
        }
    )
    return {"portfolio": new, "archived": archived}


def _delete(change: dict) -> dict:
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = portfolio(change.get("id"), conn=conn)
        for store in (PaperEventStore(), PaperOrderStore(), PaperValueStore()):
            for child in store.for_portfolio(row["id"], conn=conn):
                store.delete(child["id"], conn=conn)
        PaperPortfolioStore().delete(row["id"], conn=conn)
    return row


# ── orders ─────────────────────────────────────────────────────────────────────────────


def _asset_for(change: dict) -> tuple[PaperAsset | None, dict | None]:
    """(the asset, or None for a NEW manual asset + its contract draft)."""
    req = change.get("order") or {}
    if req.get("asset_key"):
        return assets.resolve_key(req["asset_key"]), None
    spec = req.get("asset") or {}
    if spec.get("kind") == "manual" and not spec.get("asset_key"):
        name = str(spec.get("name") or "").strip()
        ccy = str(spec.get("currency") or "").upper()
        if not name or len(ccy) != 3:
            raise ValidationError("A manual asset needs a name and a three-letter currency.")
        ctr = {
            "id": new_id("pctr"),
            "kind": "manual",
            "name": name[:80],
            "currency": ccy,
            "proxy_instrument_id": spec.get("proxy_instrument_id"),
        }
        return assets.from_contract(ctr), ctr
    key = assets.resolve_request(spec)
    return assets.resolve_key(key), None


def _quotes_for(row: dict, asset: PaperAsset, order: dict, bk: book_mod.Book, now: datetime):
    """(asset quote, fx quote) — fetched outside any lock."""
    home = row["home_currency"]
    if asset.kind == "fx":
        return None, quotes.fx_rate(order["from_currency"], asset.currency, now)
    quote = quotes.fetch([asset], now, marks=bk.marks).get(asset.key)
    fxq = quotes.fx_rate(home, asset.currency, now) if asset.currency != home else None
    return quote, fxq


def preview(change: dict) -> dict:
    """What placing this order would do now — no write. Same planner, same quote."""
    from backend.services.paper_trading import views

    row = portfolio(change.get("portfolio_id"))
    asset, _ctr = _asset_for(change)
    order = orders.normalize(change.get("order") or {}, asset, row["home_currency"])
    now = _now()
    bk = derive(row["id"])
    quote, fxq = _quotes_for(row, asset, order, bk, now)
    p = orders.plan(
        portfolio=row,
        settings=settings_of(row),
        book=bk,
        open_orders=portfolio_orders(row["id"]),
        asset=asset,
        order=order,
        quote=quote,
        fx_quote=fxq,
        now=now,
    )
    return views.preview(row, bk, asset, order, p, quote, fxq, now, thesis.stance(asset, p, bk))


def _place(change: dict) -> dict:
    from backend.services.paper_trading import views

    row = portfolio(change.get("portfolio_id"))
    _require_active(row)
    asset, ctr = _asset_for(change)
    order = orders.normalize(change.get("order") or {}, asset, row["home_currency"])
    now = _now()
    quote, fxq = _quotes_for(row, asset, order, derive(row["id"]), now)
    origin = str(change.get("origin") or "owner")
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = portfolio(row["id"], conn=conn)
        _require_active(row)
        bk = derive(row["id"], conn=conn)
        open_orders = [o for o in portfolio_orders(row["id"], conn=conn) if o["status"] == "open"]
        p = orders.plan(
            portfolio=row,
            settings=settings_of(row),
            book=bk,
            open_orders=open_orders,
            asset=asset,
            order=order,
            quote=quote,
            fx_quote=fxq,
            now=now,
        )
        stance = thesis.stance(asset, p, bk)
        record = _order_record(row, asset, order, p, now, origin)
        if isinstance(p.decision, Fill):
            if ctr:
                PaperContractStore().insert(ctr, conn=conn)
            fill, drafts = orders.fill_events(
                order=record,
                asset=asset,
                p=p,
                now=now,
                home_rate=_home_rate(row, asset, fxq),
                thesis=stance,
            )
            record.update(status="filled", fill=fill, state_reason=None)
            PaperOrderStore().insert(record, conn=conn)
            _append(row["id"], drafts, conn=conn)
        else:
            PaperOrderStore().insert(record, conn=conn)
    return {
        "order": record,
        "preview": views.preview(row, bk, asset, order, p, quote, fxq, now, stance),
        "toast": views.toast(record),
    }


def _home_rate(row: dict, asset: PaperAsset, fxq) -> float | None:
    if asset.kind == "fx":
        return None
    if asset.currency == row["home_currency"]:
        return 1.0
    return float(fxq.last) if fxq and fxq.last else None


def _order_record(row, asset, order, p, now, origin) -> dict:
    from backend.services.paper_trading.kinds import kind_for

    d = p.decision
    status = "open" if isinstance(d, Wait) else "rejected" if isinstance(d, Reject) else "filled"
    expires = None
    if status == "open" and order["tif"] == "day" and asset.kind not in ("fund_nav",):
        expires = kind_for(asset).day_expiry(p.session, now).isoformat()
    nav_date = None
    if asset.kind == "fund_nav":
        from backend.services.paper_trading.kinds.fund_nav import nav_date_for

        nav_date = order.get("nav_date") or nav_date_for(now).isoformat()
    return {
        "id": new_id("pord"),
        "portfolio_id": row["id"],
        "asset_key": asset.key,
        "kind": asset.kind,
        "symbol": asset.extra.get("display_symbol") or asset.symbol,
        "name": asset.name,
        **order,
        # A resting sell sized by amount promises a quantity: other sells must not oversell it.
        "quantity": order.get("quantity")
        if order.get("quantity") is not None or order["side"] == "buy"
        else p.quantity,
        "status": status,
        "state_reason": p.next_line if status != "filled" else None,
        "available": d.available if isinstance(d, Reject) else None,
        "placed_at": now.isoformat(),
        "expires_at": expires,
        "nav_date": nav_date,
        "origin": origin,
        "reserve": p.reserve,
        "fill": None,
    }


def _cancel(change: dict) -> dict:
    with locked_unit_of_work(*ALL_TABLES) as conn:
        store = PaperOrderStore()
        order = store.find_by_id(str(change.get("order_id") or ""), conn=conn)
        if not order:
            raise NotFoundError("No such paper order.")
        if order.get("status") != "open":
            raise ConflictError(f"That order is already {order.get('status')}.")
        return store.update_by_id(
            order["id"],
            {"status": "cancelled", "state_reason": "Cancelled.", "reserve": {}},
            conn=conn,
        )


def retry_order(order: dict, quote, fxq, now: datetime) -> dict:
    """The engine's ``fill``: re-plan one open order inside the lock and fill, expire, or
    refresh why it still waits."""
    with locked_unit_of_work(*ALL_TABLES) as conn:
        store = PaperOrderStore()
        current = store.find_by_id(order["id"], conn=conn)
        if not current or current.get("status") != "open":
            return current or order
        row = portfolio(current["portfolio_id"], conn=conn)
        if row.get("status") != "active":
            return store.update_by_id(
                current["id"],
                {"status": "cancelled", "state_reason": "Portfolio archived."},
                conn=conn,
            )
        if current.get("expires_at") and now.isoformat() >= current["expires_at"]:
            return store.update_by_id(
                current["id"],
                {
                    "status": "expired",
                    "state_reason": "Expired at the session close.",
                    "reserve": {},
                },
                conn=conn,
            )
        asset = assets.resolve_key(current["asset_key"], conn=conn)
        bk = derive(row["id"], conn=conn)
        open_orders = [o for o in portfolio_orders(row["id"], conn=conn) if o["status"] == "open"]
        p = orders.plan(
            portfolio=row,
            settings=settings_of(row),
            book=bk,
            open_orders=open_orders,
            asset=asset,
            order=current,
            quote=quote,
            fx_quote=fxq,
            now=now,
        )
        if isinstance(p.decision, Fill):
            fill, drafts = orders.fill_events(
                order=current,
                asset=asset,
                p=p,
                now=now,
                home_rate=_home_rate(row, asset, fxq),
                thesis=thesis.stance(asset, p, bk),
            )
            _append(row["id"], drafts, conn=conn)
            return store.update_by_id(
                current["id"],
                {"status": "filled", "fill": fill, "state_reason": None, "reserve": {}},
                conn=conn,
            )
        if isinstance(p.decision, Reject):
            return store.update_by_id(
                current["id"],
                {"status": "rejected", "state_reason": p.next_line, "reserve": {}},
                conn=conn,
            )
        if p.next_line != current.get("state_reason"):
            return store.update_by_id(current["id"], {"state_reason": p.next_line}, conn=conn)
        return current


# ── marks, lifecycle, snapshots ────────────────────────────────────────────────────────


def _mark(change: dict) -> dict:
    key = str(change.get("asset_key") or "")
    if not key.startswith("ctr:"):
        raise ValidationError("Only an asset you price yourself takes a mark.")
    price = orders._positive(change.get("price"), "Price")
    if price is None:
        raise ValidationError("Enter the price.")
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = portfolio(change.get("portfolio_id"), conn=conn)
        _require_active(row)
        assets.resolve_key(key, conn=conn)
        at = str(change.get("date") or user_today().isoformat())
        written = _append(
            row["id"],
            [
                {
                    "kind": "mark",
                    "at": at,
                    "asset_key": key,
                    "price": price,
                    "price_basis": "yours",
                    "payload": {"note": change.get("note")},
                }
            ],
            conn=conn,
        )
    return {"event": written[0]}


def _lifecycle(change: dict) -> dict:
    """Dated events (dividends, splits, freezes) — each idempotent on its ``idem_key``."""
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = portfolio(change.get("portfolio_id"), conn=conn)
        written = _append(row["id"], list(change.get("events") or []), conn=conn)
    return {"written": written}


def _snapshot(row: dict, now: datetime, *, conn: Connection | None = None) -> dict | None:
    from backend.services.paper_trading import valuation

    bk = derive(row["id"], conn=conn)
    val = valuation.value(row, bk, now, fetch_quotes=conn is None)
    day = user_today().isoformat()
    flow = valuation.flows_home(row, bk, since=day)
    doc = {
        "id": f"{row['id']}:{day}",
        "portfolio_id": row["id"],
        "date": day,
        "value_home": val["value_home"],
        "cash_home": val["cash_home"],
        "net_flow_home": flow,
        "bases": val["by_basis"],
    }
    if val["value_home"] is None:
        return None
    if conn is not None:
        PaperValueStore().upsert(doc, conn=conn)
    else:
        with locked_unit_of_work(*ALL_TABLES) as c:
            PaperValueStore().upsert(doc, conn=c)
    return doc


def snapshot_values(pid: str) -> dict | None:
    row = portfolio(pid)
    if row.get("status") != "active":
        return None
    return _snapshot(row, _now())


def apply_paper_change(change: dict) -> dict:
    """The one writer. Returns ``{"portfolio": …}`` / ``{"order": …}`` / ``{"event": …}`` per op."""
    op = change.get("op")
    if op == "create":
        return {"portfolio": _create(change)}
    if op == "update":
        return {"portfolio": _update(change)}
    if op == "archive":
        return {"portfolio": _archive(change)}
    if op == "start_over":
        return _start_over(change)
    if op == "delete":
        return {"deleted": _delete(change)["id"]}
    if op == "place":
        return _place(change)
    if op == "cancel":
        return {"order": _cancel(change)}
    if op == "mark":
        return _mark(change)
    if op == "lifecycle":
        return _lifecycle(change)
    raise ValidationError(f"Unknown paper change '{op}'.")
