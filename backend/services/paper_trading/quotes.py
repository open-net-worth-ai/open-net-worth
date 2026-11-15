"""Quotes for paper assets — the one place the paper book does price I/O. Batched per kind:
one Yahoo snapshot + market-status call for every exchange-traded symbol, one ``fetch_live``
for crypto, the FX map for currencies, a short daily history for fund NAVs. Nothing here
records into the real book's price history (``record_history=False``).

A QA process (``ONW_QA_MODE``) prices from ``qa_quotes`` instead: deterministic, offline, and
with an open/closed switch the end-to-end run flips.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.core.config import get_settings
from backend.core.fx import known_rate
from backend.services import fx_cache, prices
from backend.services.paper_trading import qa_quotes
from backend.services.paper_trading.kinds.manual import proxied_price
from backend.services.paper_trading.model import PaperAsset, PaperQuote

logger = logging.getLogger(__name__)

_EXCHANGE_KINDS = ("listed", "index", "metal", "fund_nav")


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _exchange_quotes(assets: list[PaperAsset], now: datetime) -> dict[str, PaperQuote]:
    symbols = sorted({a.symbol for a in assets if a.symbol})
    if not symbols:
        return {}
    try:
        snaps = prices.fetch_quote_snapshots(symbols)
        status = prices.market_status(symbols, with_reopens=True)
    except Exception:  # a provider outage means "no price yet", never a failed page
        logger.info("paper: exchange quotes unavailable", exc_info=True)
        return {}
    out: dict[str, PaperQuote] = {}
    for a in assets:
        snap, st = snaps.get(a.symbol), status.get(a.symbol) or {}
        if not snap:
            continue
        qpu = a.quote_per_unit or 1.0
        state = str(st.get("state") or snap.get("market_state") or "UNKNOWN").upper()
        out[a.key] = PaperQuote(
            last=float(snap["price"]) * qpu,
            bid=float(snap["bid"]) * qpu if snap.get("bid") else None,
            ask=float(snap["ask"]) * qpu if snap.get("ask") else None,
            currency=(snap.get("currency") or a.currency).upper(),
            # Unknown age stays unknown: it is never fresh, so an order waits rather than fill.
            as_of=_parse_ts(snap.get("quote_time") or st.get("quote_time")),
            basis="synthetic" if a.kind == "index" else "quote",
            source="Yahoo",
            session_state=state,
            is_open=bool(st.get("is_open")),
            next_open=_parse_ts(st.get("reopens_at")),
            timezone=str(st.get("timezone") or snap.get("exchange_tz") or ""),
            avg_volume=snap.get("avg_volume"),
        )
    return out


def _fund_navs(assets: list[PaperAsset], quotes: dict[str, PaperQuote]) -> None:
    for a in assets:
        try:
            points = prices.fetch_history(a.symbol, "1M", a.price_source or "yahoo")
        except Exception:
            points = []
        navs = {str(p["date"])[:10]: float(p["price"]) for p in points if p.get("price")}
        base = quotes.get(a.key)
        if base is None and not navs:
            continue
        last_day = max(navs) if navs else None
        quotes[a.key] = PaperQuote(
            last=navs[last_day] if last_day else (base.last if base else None),
            currency=base.currency if base else a.currency,
            as_of=base.as_of if base else None,
            basis="nav",
            source="Yahoo NAV",
            session_state="NAV",
            is_open=True,
            timezone="America/New_York",
            navs=navs,
        )


def _crypto_quotes(assets: list[PaperAsset], now: datetime) -> dict[str, PaperQuote]:
    ids = sorted({a.symbol for a in assets if a.symbol})
    if not ids:
        return {}
    try:
        live = prices.fetch_live(ids, [], record_history=False)
    except Exception:
        logger.info("paper: crypto quotes unavailable", exc_info=True)
        return {}
    out: dict[str, PaperQuote] = {}
    for a in assets:
        price = (live.get("prices") or {}).get(a.symbol)
        if not price:
            continue
        out[a.key] = PaperQuote(
            last=float(price),
            currency=a.currency,
            as_of=_parse_ts((live.get("as_of") or {}).get(a.symbol)),
            basis="quote",
            source=str((live.get("providers") or {}).get(a.symbol) or "CoinGecko"),
            session_state="OPEN",
            is_open=True,
            timezone="UTC",
        )
    return out


def fx_rate(paid: str, bought: str, now: datetime | None = None) -> PaperQuote | None:
    """The price of one unit of ``bought`` in ``paid``, from the live FX map (falling back to
    the app's FX cache), as a quote the fx adapter fills against."""
    paid, bought = paid.upper(), bought.upper()
    now = now or datetime.now(timezone.utc)
    if get_settings().onw_qa_mode:
        return qa_quotes.fx_quote(paid, bought, now)
    if paid == bought:
        return PaperQuote(last=1.0, currency=paid, as_of=now, basis="quote", source="par")
    rates = dict(fx_cache.load_fx_cache())
    try:
        live = prices.fetch_live([], [paid, bought], record_history=False, allow_stale=True)
        rates.update({k: v for k, v in (live.get("fx") or {}).items() if v})
    except Exception:
        logger.info("paper: live FX unavailable; using the cached rates", exc_info=True)
    rate = known_rate(bought, paid, rates)
    if not rate:
        return None
    return PaperQuote(
        last=float(rate),
        currency=paid,
        as_of=now,
        basis="quote",
        source="Yahoo FX",
        session_state="OPEN",
        is_open=True,
    )


def fx_rates(currencies: set[str], home: str) -> dict[str, float]:
    """{ccy: price of one unit in home} — for valuing cash and holdings in the home currency."""
    out: dict[str, float] = {home.upper(): 1.0}
    for c in currencies:
        if c and c.upper() != home.upper():
            q = fx_rate(home, c)
            if q and q.last:
                out[c.upper()] = float(q.last)
    return out


def _manual_quotes(
    assets: list[PaperAsset], marks: dict[str, dict], now: datetime
) -> dict[str, PaperQuote]:
    out: dict[str, PaperQuote] = {}
    proxies = [a for a in assets if a.extra.get("proxy_instrument_id")]
    proxy_now: dict[str, float] = {}
    if proxies:
        from backend.services.paper_trading import assets as assets_mod

        targets = [
            assets_mod.resolve_key(f"inst:{a.extra['proxy_instrument_id']}") for a in proxies
        ]
        got = fetch(targets, now)
        proxy_now = {t.key.split(":", 1)[1]: q.last for t in targets if (q := got.get(t.key))}
    for a in assets:
        mark = marks.get(a.key)
        if not mark:
            continue
        price = proxied_price(
            mark["price"],
            mark.get("proxy_price"),
            proxy_now.get(str(a.extra.get("proxy_instrument_id") or "")),
        )
        out[a.key] = PaperQuote(
            last=price,
            currency=a.currency,
            as_of=_parse_ts(mark.get("at")),
            basis="yours",
            source="your price",
            session_state="OPEN",
            is_open=True,
        )
    return out


def fetch(
    assets: list[PaperAsset], now: datetime, *, marks: dict[str, dict] | None = None
) -> dict[str, PaperQuote]:
    """{asset_key: PaperQuote} for every asset a price could be found for. An asset with no
    quote is simply absent — the caller says "no price yet", never invents one."""
    if not assets:
        return {}
    marks = marks or {}
    by_kind: dict[str, list[PaperAsset]] = {}
    for a in assets:
        by_kind.setdefault(a.kind, []).append(a)
    out: dict[str, PaperQuote] = {}
    out.update(_manual_quotes(by_kind.get("manual", []), marks, now))
    if get_settings().onw_qa_mode:
        out.update(qa_quotes.quotes([a for a in assets if a.kind not in ("manual", "fx")], now))
        return out
    exchange = [a for k in _EXCHANGE_KINDS for a in by_kind.get(k, [])]
    out.update(_exchange_quotes(exchange, now))
    if by_kind.get("fund_nav"):
        _fund_navs(by_kind["fund_nav"], out)
    out.update(_crypto_quotes(by_kind.get("crypto", []), now))
    return out
