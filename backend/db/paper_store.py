"""Paper trading stores — the five tables of the SIMULATED paper book.

Nothing here is real money: these rows never reach net worth, cash, budgeting or Intel, and no
order in them is ever sent anywhere (ADR
``2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path``). The one writer is
``services.paper_trading.service.apply_paper_change``.

``paper_portfolios`` (``ppf_…``): name, home_currency, benchmark, settings, origin
    {kind: cash|holdings|followed, ref}, status active|archived, lineage_id, run_no, started_at,
    archived_at, created_by, created_at, updated_at. Start over archives a row and inserts
    run_no+1 with the same lineage_id — history is never rewritten.
``paper_contracts`` (``pctr_…``): an asset that is not a shared instrument — phase 1 only
    ``kind: manual`` {name, currency, proxy_instrument_id?}. Holds no quantity.
``paper_orders`` (``pord_…``): portfolio_id, asset_key, side, quantity | notional, order_type
    market|limit|stop|nav, limit_price, stop_price, tif day|gtc, extended_ok, status
    open|filled|cancelled|rejected|expired, state_reason, placed_at, expires_at, origin, note,
    fill {price, basis, source, quote_as_of, session, slippage, spread_or_premium, commission,
    fx_rate, filled_at}. ``portfolio_id`` and ``status`` are real columns.
``paper_events`` (``pevt_…``): the append-only ledger — the single source of truth. portfolio_id,
    at, kind, asset_key, quantity_delta, cash_delta {ccy: amount}, price, price_basis, order_id,
    fx_rate, payload, idem_key (unique per portfolio, so every lifecycle job is re-runnable).
``paper_values`` (``<portfolio_id>:<date>``): the end-of-day snapshot — value_home, cash_home,
    net_flow_home, bases {quote, nav, modelled, synthetic, yours}.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, select

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import (
    paper_contracts,
    paper_events,
    paper_orders,
    paper_portfolios,
    paper_values,
)

MAX_ACTIVE_PORTFOLIOS = 20


class PaperPortfolioStore(BookPayloadTableStore):
    def __init__(self, engine=None) -> None:
        super().__init__(paper_portfolios, id_field="id", engine=engine)


class PaperContractStore(BookPayloadTableStore):
    def __init__(self, engine=None) -> None:
        super().__init__(paper_contracts, id_field="id", engine=engine)


class _PortfolioChildStore(BookPayloadTableStore):
    """A table whose rows belong to one portfolio: ``portfolio_id`` is a real column (FK,
    cascade), kept in step with the payload on every write."""

    _columns: tuple[str, ...] = ("portfolio_id",)

    def _promoted(self, item: dict) -> dict:
        return {c: item.get(c) for c in self._columns}

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        return {**super()._insert_row(seq, id_val, item), **self._promoted(item)}

    def _payload_values(self, item: dict) -> dict:
        return {"payload": item, **self._promoted(item)}

    def for_portfolio(self, portfolio_id: str, *, conn: Connection | None = None) -> list[dict]:
        """Every row of one portfolio, in insertion order."""
        stmt = (
            select(self._table)
            .where(self._table.c.portfolio_id == portfolio_id)
            .order_by(self._table.c.seq)
        )
        with self._use_read(conn) as c:
            return [self._row_item(r) for r in c.execute(stmt)]

    def _use_read(self, conn: Connection | None):
        if conn is not None:
            return _Borrowed(conn)
        return self._eng().connect()


class _Borrowed:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def __enter__(self) -> Connection:
        return self._conn

    def __exit__(self, *exc) -> None:
        return None


class PaperOrderStore(_PortfolioChildStore):
    _columns = ("portfolio_id", "status")

    def __init__(self, engine=None) -> None:
        super().__init__(paper_orders, id_field="id", engine=engine)

    def open_orders(self, *, conn: Connection | None = None) -> list[dict]:
        """Every open order across portfolios — the fill engine's one query per tick."""
        stmt = select(self._table).where(self._table.c.status == "open").order_by(self._table.c.seq)
        with self._use_read(conn) as c:
            return [self._row_item(r) for r in c.execute(stmt)]


class PaperEventStore(_PortfolioChildStore):
    _columns = ("portfolio_id", "idem_key")

    def __init__(self, engine=None) -> None:
        super().__init__(paper_events, id_field="id", engine=engine)

    def has_idem_key(self, portfolio_id: str, idem_key: str, *, conn: Connection) -> bool:
        stmt = select(self._table.c.id).where(
            self._table.c.portfolio_id == portfolio_id, self._table.c.idem_key == idem_key
        )
        return conn.execute(stmt).first() is not None


class PaperValueStore(_PortfolioChildStore):
    _columns = ("portfolio_id", "date")

    def __init__(self, engine=None) -> None:
        super().__init__(paper_values, id_field="id", engine=engine)


ALL_TABLES = (paper_contracts, paper_events, paper_orders, paper_portfolios, paper_values)
