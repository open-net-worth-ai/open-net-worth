"""Authoritative account and run valuations, separate from the operator's real book."""

from __future__ import annotations

from backend.db.trading_store import TradingStore
from backend.services.trading.common import ACTIVE_ORDER_STATES, amount, instant
from backend.services.trading.market import environment_time, latest_quote


def account_snapshot(run, account, *, conn=None):
    orders = TradingStore("orders").list_scope(run["id"], conn=conn)
    active = [
        order
        for order in orders
        if order["account_id"] == account["id"] and order["status"] in ACTIVE_ORDER_STATES
    ]
    reserved = sum((amount(row["reserved_cash"]) for row in active), amount(0))
    positions = {}
    for key, position in account["positions"].items():
        held = sum(
            (amount(row["reserved_quantity"]) for row in active if row["instrument_key"] == key),
            amount(0),
        )
        quote = latest_quote(run, position["instrument"])
        positions[key] = {
            **position,
            "reserved_quantity": str(held),
            "available_quantity": str(amount(position["quantity"]) - held),
            "market_price": quote["price"] if quote else None,
            "market_value": float(amount(quote["price"]) * amount(position["quantity"]))
            if quote
            else None,
            "unrealized_pnl": float(
                amount(quote["price"]) * amount(position["quantity"])
                - amount(position["cost_basis"])
            )
            if quote
            else None,
        }
    return {
        **account,
        "positions": positions,
        "reserved_cash": str(reserved),
        "available_cash": str(amount(account["cash"]) - reserved),
    }


def get_account(run_id, account_id=None):
    run = TradingStore("runs").required(run_id)
    accounts = TradingStore("accounts").list_scope(run_id)
    return {
        "items": [
            account_snapshot(run, account)
            for account in accounts
            if account_id is None or account["id"] == account_id
        ],
        "total": len(accounts),
    }


def calculate_performance(run, *, conn=None):
    equity, initial, complete = amount(0), amount(0), True
    cash_total, reserved_total = amount(0), amount(0)
    valuation_time = environment_time(run)
    for ccy, cash in run["initial_capital"].items():
        initial += amount(cash) * amount(run["fx_rates"][ccy])
    for account in TradingStore("accounts").list_scope(run["id"], conn=conn):
        rate = amount(run["fx_rates"][account["currency"]])
        equity += amount(account["cash"]) * rate
        cash_total += amount(account["cash"]) * rate
        reserved_total += amount(account_snapshot(run, account, conn=conn)["reserved_cash"]) * rate
        for position in account["positions"].values():
            quantity = amount(position["quantity"])
            if not quantity:
                continue
            quote = latest_quote(run, position["instrument"])
            if quote is None:
                complete = False
                continue
            age = (valuation_time - instant(quote["timestamp"])).total_seconds()
            if (
                run["mode"] != "backtest"
                and age > run["workspace_snapshot"]["mandate"]["max_quote_age_seconds"]
            ):
                complete = False
            equity += quantity * amount(quote["price"]) * rate
    history = run.get("valuation_history", [])
    peak = max(initial, amount(run.get("lifetime_peak_equity", initial)))
    drawdown = amount(run.get("lifetime_max_drawdown_pct", 0))
    points = (
        [{"equity": str(equity)}]
        if "lifetime_peak_equity" in run
        else [*history, {"equity": str(equity)}]
    )
    for point in points:
        value = amount(point["equity"])
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak * 100 if peak else amount(0))
    fills = TradingStore("fills").list_scope(run["id"], conn=conn)
    fees_complete = all(fill.get("fees_complete", True) for fill in fills)
    complete = complete and fees_complete
    fees = sum(
        (
            amount(fill["fees"]) * amount(run["fx_rates"][fill["instrument"]["currency"]])
            for fill in fills
        ),
        amount(0),
    )
    return {
        "initial_equity": float(initial),
        "equity": float(equity) if complete else None,
        "pnl": float(equity - initial) if complete else None,
        "return_pct": float((equity / initial - 1) * 100) if complete and initial else None,
        "max_drawdown_pct": float(drawdown) if complete else None,
        "lifetime_peak_equity": str(peak),
        "fees": float(fees),
        "fees_complete": fees_complete,
        "cash": float(cash_total),
        "reserved_cash": float(reserved_total),
        "available_cash": float(cash_total - reserved_total),
        "model_cost_usd": float(amount(run["model_cost_usd"])),
        "base_currency": run["base_currency"],
        "valuation_complete": complete,
        "history": history,
        "as_of": valuation_time.isoformat(),
    }


def get_performance(run_id):
    return calculate_performance(TradingStore("runs").required(run_id))


def snapshot_performance(conn, run):
    value = calculate_performance(run, conn=conn)
    if value["valuation_complete"]:
        history = [
            *run.get("valuation_history", []),
            {"timestamp": run["current_time"], "equity": value["equity"]},
        ]
        return TradingStore("runs").update_by_id(
            run["id"],
            {
                "valuation_history": history[-20000:],
                "lifetime_peak_equity": value["lifetime_peak_equity"],
                "lifetime_max_drawdown_pct": str(value["max_drawdown_pct"]),
            },
            conn=conn,
        )
    return run
