"""PRD acceptance criterion 1 — a paper portfolio has no effect on real figures.

A property over random operation sequences (seeded, so a failure replays exactly): create,
trade every phase-1 kind, wait for the open, cancel, mark, apply dividends/splits/freezes,
start over, archive and delete paper portfolios — then every table of the REAL book is
byte-identical to before, and so are the real figures read from it (net worth, cash on hand,
a thesis's held state). Every real figure is derived from those tables, so identical tables
mean identical net worth and its history, cash on hand, Ready to Assign, Investments, Intel
risk signals, theses and the My holdings watchlist. ``hypothesis`` is not a dependency of this
repo; ``random.Random(seed)`` over many seeds gives the same guarantee, reproducibly."""

from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import select

from backend.core.errors import OpenNetWorthError
from backend.db.engine import get_engine
from backend.db.tables import metadata
from backend.services import accounts, investment_theses
from backend.services import ledger as ledger_service
from backend.services.paper_trading import engine, qa_quotes, service
from backend.services.paper_trading.service import apply_paper_change

PAPER_TABLES = {t for t in metadata.tables if t.startswith("paper_")}
ASSET_KEYS = (
    "inst:inst_acme",
    "inst:inst_acmej",
    "inst:inst_coin",
    "inst:inst_fund",
    "inst:inst_gold",
    "inst:inst_idx",
)


def _real_tables() -> dict[str, list]:
    out = {}
    with get_engine().connect() as conn:
        for name, table in sorted(metadata.tables.items()):
            if name in PAPER_TABLES:
                continue
            rows = conn.execute(select(table)).mappings().all()
            out[name] = sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in rows)
    return out


def _real_figures() -> dict:
    nw = ledger_service.net_worth("USD")
    return {
        "net_worth": json.dumps(nw, sort_keys=True, default=str),
        "held": sorted(investment_theses.held_instrument_ids()),
    }


def _seed_real_book() -> None:
    accounts.create_account(
        {"name": "Everyday", "type": "cash", "currency": "USD", "initial_balance": 4200}
    )
    accounts.create_account(
        {"name": "Card", "type": "credit", "currency": "USD", "initial_balance": -300}
    )
    investment_theses.apply_thesis_change(
        {
            "op": "set",
            "instrument_id": "inst_acme",
            "fields": {"reason": "Synthetic thesis", "buy_below": 105, "buy_above": 90},
        }
    )


def _random_op(rng: random.Random, live: list[str]) -> None:
    op = rng.choice(
        [
            "create",
            "buy",
            "buy",
            "sell",
            "fx",
            "manual",
            "limit",
            "tick",
            "market",
            "cancel",
            "lifecycle",
            "mark",
            "start_over",
            "archive",
            "delete",
        ]
    )
    pid = rng.choice(live) if live else None
    if op == "create" or not pid:
        start = rng.choice(["cash", "cash", "holdings"])
        row = apply_paper_change(
            {
                "op": "create",
                "start_with": start,
                "home_currency": "USD",
                "cash": rng.choice([1000, 10_000, 250_000]),
            }
        )["portfolio"]
        live.append(row["id"])
        return
    order = {
        "asset_key": rng.choice(ASSET_KEYS),
        "side": "buy",
        "quantity": rng.choice([0.5, 1, 3, 40]),
    }
    if op == "sell":
        order["side"] = "sell"
    elif op == "fx":
        order = {"asset_key": "ccy:EUR", "side": "buy", "notional": rng.choice([50, 500])}
    elif op == "manual":
        order = {
            "asset": {"kind": "manual", "name": "Flat", "currency": "USD"},
            "side": "buy",
            "quantity": 1,
            "price": rng.choice([500, 5000]),
        }
    elif op == "limit":
        order.update(
            order_type="limit",
            limit_price=rng.choice([1, 90, 10_000]),
            tif=rng.choice(["day", "gtc"]),
        )
    if op in ("buy", "sell", "fx", "manual", "limit"):
        apply_paper_change({"op": "place", "portfolio_id": pid, "order": order})
    elif op == "tick":
        engine.tick()
    elif op == "market":
        qa_quotes.set_state(
            rng.choice(["open", "closed"]), {"ACME": rng.choice([80.0, 100.0, 120.0])}
        )
    elif op == "cancel":
        opens = [o for o in service.portfolio_orders(pid) if o["status"] == "open"]
        if opens:
            apply_paper_change({"op": "cancel", "order_id": rng.choice(opens)["id"]})
    elif op == "lifecycle":
        key = rng.choice(ASSET_KEYS)
        apply_paper_change(
            {
                "op": "lifecycle",
                "portfolio_id": pid,
                "events": [
                    rng.choice(
                        [
                            {
                                "kind": "dividend",
                                "asset_key": key,
                                "cash_delta": {"USD": 3.0},
                                "idem_key": f"div:{key}:{rng.randint(1, 3)}",
                            },
                            {
                                "kind": "split",
                                "asset_key": key,
                                "payload": {"ratio": 2.0},
                                "idem_key": f"split:{key}:{rng.randint(1, 3)}",
                            },
                            {
                                "kind": "freeze",
                                "asset_key": key,
                                "payload": {"reason": "Merger"},
                                "idem_key": f"freeze:{key}",
                            },
                        ]
                    )
                ],
            }
        )
    elif op == "mark":
        ctrs = [k for k in service.derive(pid).marks]
        if ctrs:
            apply_paper_change(
                {
                    "op": "mark",
                    "portfolio_id": pid,
                    "asset_key": ctrs[0],
                    "price": rng.choice([400, 6000]),
                }
            )
    elif op == "start_over":
        live.append(apply_paper_change({"op": "start_over", "id": pid})["portfolio"]["id"])
        live.remove(pid)
    elif op == "archive":
        apply_paper_change({"op": "archive", "id": pid})
        live.remove(pid)
    elif op == "delete":
        apply_paper_change({"op": "delete", "id": pid})
        live.remove(pid)


@pytest.mark.parametrize("seed", range(12))
def test_random_paper_operations_leave_the_real_book_identical(paper_db, seed):
    _seed_real_book()
    tables_before, figures_before = _real_tables(), _real_figures()
    rng = random.Random(seed)
    live: list[str] = []
    done = 0
    for _ in range(40):
        try:
            _random_op(rng, live)
            done += 1
        except OpenNetWorthError:
            pass  # a refused paper op (no cover, archived, nothing held…) is a valid outcome
    assert done >= 20, "the sequence should mostly run"
    assert _real_tables() == tables_before
    assert _real_figures() == figures_before
