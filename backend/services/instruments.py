"""instruments_service — instrument resolution (find-or-create from ticker/query).

The SINGLE implementation of resolve_instrument; api/instruments.py's REST route and
ai/tools/portfolio.py's resolve_instrument tool both delegate here (api → services →
db → core) instead of maintaining their own copies.

Identity for *pricing* stays (price_source, price_source_id) — see instrument_store's
module docstring. This module is the dedup FRONT DOOR in front of that identity key:
before minting a new instrument it runs a resolution ladder (find_existing) that also
catches the same real-world asset showing up through a different price vendor or with
no external id at all (docs/adr/2026-07-15-instrument-identity-canonicalization-front-door.md):

  1. Crypto canonicalization — any crypto-looking query, INCLUDING a Yahoo crypto pair
     like "BTC-USD" or "eth-eur", normalizes to the canonical CoinGecko coin (one
     instrument per coin, quoted USD, priced by coingecko) instead of minting a
     separate yahoo-priced instrument per fiat pairing.
  2. The `identifiers` bag is populated at resolve time with whatever's free (isin/mic
     for Yahoo, coingecko_id for CoinGecko) so a later resolve can match on a shared
     identifier even across vendors.
  3. find_existing() tries, in order: (a) exact (price_source, price_source_id) — the
     identity key, unchanged; (b) a shared identifiers-bag value; (c) normalized
     symbol + same asset_class — silently reused for crypto (a symbol collision there
     is essentially always the same coin), but REFUSED (ValidationError) for anything
     else unless the caller passes allow_duplicate_symbol=true, since a stock ticker
     genuinely can collide across exchanges.
  4. A pinned (price_source, price_source_id) identifies the feed entry but never
     carries its facts: the source is still fetched (quote_currency above all), an
     existing row is REFRESHED with what the feed reports, and a mint is REFUSED when
     the source won't confirm the id. The store itself refuses to fabricate a
     quote_currency, so no instrument can ship priced in a currency nobody fetched.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from backend.core import units
from backend.core.errors import ValidationError
from backend.core.quote_units import major_currency
from backend.db.instrument_store import InstrumentStore
from backend.services import coingecko_client

_QUOTE_TYPE_TO_CLASS = {
    "EQUITY": "equity",
    "ETF": "equity",
    "MUTUALFUND": "equity",
    "INDEX": "equity",
    "CRYPTOCURRENCY": "crypto",
    "FUTURE": "commodity",
}
_QUOTE_TYPE_TO_SUBCLASS = {
    "EQUITY": "stock",
    "ETF": "etf",
    "MUTUALFUND": "mutual_fund",
    "INDEX": "index",
    "CRYPTOCURRENCY": "coin",
    "FUTURE": "future",
}

_METAL_SYMBOLS = {
    "gold": ("GC=F", "Gold (spot)"),
    "xau": ("GC=F", "Gold (spot)"),
    "xauusd": ("GC=F", "Gold (spot)"),
    "silver": ("SI=F", "Silver (spot)"),
    "xag": ("SI=F", "Silver (spot)"),
    "xagusd": ("SI=F", "Silver (spot)"),
    "platinum": ("PL=F", "Platinum (spot)"),
    "xpt": ("PL=F", "Platinum (spot)"),
    "palladium": ("PA=F", "Palladium (spot)"),
    "xpd": ("PA=F", "Palladium (spot)"),
}

# Metal codes/names that could also parse as a "BASE-FIAT" pair (e.g. a stray "XAU-USD") —
# excluded from crypto-pair detection so a dash-suffixed metal query never mis-routes to a
# CoinGecko search instead of falling through to the metal table / normal resolution.
_METAL_BASE_CODES = frozenset(_METAL_SYMBOLS)

# Common ISO-4217 fiat codes. Used only to recognize a Yahoo-style crypto pair ("BTC-USD",
# "eth-eur") so it canonicalizes to the underlying coin instead of minting a per-pair
# instrument — not a general currency validator (see profile.base_currency for that).
_FIAT_CODES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CAD",
        "AUD",
        "NZD",
        "SGD",
        "HKD",
        "CNY",
        "CNH",
        "TWD",
        "KRW",
        "INR",
        "IDR",
        "MYR",
        "THB",
        "PHP",
        "VND",
        "BRL",
        "MXN",
        "ZAR",
        "TRY",
        "RUB",
        "ILS",
        "AED",
        "SAR",
        "DKK",
        "NOK",
        "SEK",
        "PLN",
        "CZK",
        "HUF",
        "RON",
        "BGN",
    }
)

_CRYPTO_PAIR_RE = re.compile(r"([A-Za-z0-9]{2,10})-([A-Za-z]{3})")


def _split_crypto_pair(query: str) -> Optional[str]:
    """If `query` looks like a Yahoo crypto pair ("BTC-USD", "eth-eur"), return the base
    coin symbol ("BTC"). None otherwise (including for a dash-suffixed metal code, which
    is left to the metal table / normal resolution).

    This is a SYNTACTIC hint only — the caller still confirms CoinGecko actually
    recognizes the base symbol before treating it as crypto (see resolve_instrument)."""
    m = _CRYPTO_PAIR_RE.fullmatch((query or "").strip())
    if not m:
        return None
    base, quote = m.group(1), m.group(2).upper()
    if quote not in _FIAT_CODES:
        return None
    if base.lower() in _METAL_BASE_CODES:
        return None
    return base.upper()


def _unit_fields(unit: Optional[str], asset_class: Optional[str]) -> dict:
    u = units.normalize_unit(unit)
    if not u:
        return {}
    native = units.NATIVE_PRECIOUS_UNIT if asset_class == "commodity" else u
    return {"unit": u, "quote_per_unit": units.quote_per_unit(u, native)}


_ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def _fetch_isin(query: str) -> str:
    """Best-effort ISIN lookup via yfinance's free (no paid dependency) Ticker.isin.

    A separate network call from fast_info/.info, so it's opportunistic — any failure
    (offline, unsupported ticker, rate limit) just means no ISIN, never a raised error."""
    try:
        import yfinance as yf

        isin = (yf.Ticker(query).isin or "").strip().upper()
        if isin and _ISIN_RE.fullmatch(isin):
            return isin
    except Exception:
        pass
    return ""


def _resolve_yahoo(query: str) -> dict | None:
    try:
        import yfinance as yf

        fi = yf.Ticker(query).fast_info
        price = fi.get("lastPrice") or fi.get("regularMarketPrice")
        # major_currency, not .upper(): Yahoo quotes a London listing in GBp (pence) and Tel
        # Aviv in ILA (agorot) — the instrument is denominated in GBP / ILS, and the price
        # feed converts every quote to that (core/quote_units.py).
        currency = major_currency(fi.get("currency"))
        if not price or not currency:
            # No price OR no quote currency = the source did not confirm this
            # ticker's facts. The old `or "USD"` fabricated a currency (KSTL.DE
            # shipped as a USD instrument while priced in EUR, undervaluing the
            # position by the EUR/USD rate) — unresolved is the only safe answer.
            return None
        qtype = (fi.get("quoteType") or "").upper()
    except Exception:
        return None

    name, exchange = "", ""
    try:
        from backend.services.prices import _fetch_name

        _, name, exchange = _fetch_name(query)
    except Exception:
        pass

    asset_class = _QUOTE_TYPE_TO_CLASS.get(qtype, "equity")
    asset_subclass = _QUOTE_TYPE_TO_SUBCLASS.get(qtype, qtype.lower() or asset_class)
    identifiers: dict = {}
    if exchange:
        identifiers["mic"] = exchange
    isin = _fetch_isin(query)
    if isin:
        identifiers["isin"] = isin
    return {
        "symbol": query.upper().split(".")[0],
        "name": name or query.upper(),
        "asset_class": asset_class,
        "asset_subclass": asset_subclass,
        "quote_currency": currency,
        "price_source": "yahoo",
        "price_source_id": query.upper(),
        "precision": 8 if asset_class == "crypto" else 4,
        "identifiers": identifiers,
    }


def _resolve_coingecko(query: str) -> dict | None:
    try:
        data = coingecko_client.get_json("/search", {"query": query}, timeout=10)
    except Exception:
        return None
    coins = data.get("coins") or []
    if not coins:
        return None
    coins.sort(
        key=lambda c: c.get("market_cap_rank") if c.get("market_cap_rank") is not None else 1e9
    )
    top = coins[0]
    coingecko_id = top.get("id")
    return {
        "symbol": (top.get("symbol") or query).upper(),
        "name": top.get("name") or query,
        "asset_class": "crypto",
        "asset_subclass": "coin",
        "quote_currency": "USD",
        "price_source": "coingecko",
        "price_source_id": coingecko_id,
        "precision": 8,
        # coingecko_id duplicates price_source_id here, but it's what lets the dedup
        # ladder match a coin against a differently-sourced candidate that happens to
        # carry the same coingecko_id in ITS identifiers bag (see find_existing).
        "identifiers": {"coingecko_id": coingecko_id} if coingecko_id else {},
    }


def _resolve_metal(query: str) -> dict | None:
    hit = _METAL_SYMBOLS.get((query or "").strip().lower())
    if not hit:
        return None
    symbol, name = hit
    resolved = _resolve_yahoo(symbol)
    if not resolved:
        return None
    resolved.update({"asset_class": "commodity", "asset_subclass": "precious_metal", "name": name})
    return resolved


def _manual_quote_currency(quote_currency: Optional[str]) -> str:
    """A manual instrument has no price feed, so an explicit quote_currency is the
    ONLY source of truth — refuse rather than fabricate one (the store used to
    default a missing currency to USD, which silently mis-denominated everything
    priced under it)."""
    if not quote_currency:
        raise ValidationError(
            "a manual instrument needs an explicit quote_currency — there is no price "
            "feed to fetch it from, and a fabricated one silently mis-denominates the "
            "whole position",
            field="quote_currency",
        )
    return str(quote_currency).strip().upper()


def _pin_facts(
    price_source: str, price_source_id: str, quote_currency: Optional[str] = None
) -> dict | None:
    """The FACTS a pinned (price_source, price_source_id) implies — quote_currency
    above all, plus whatever else the source knows on a mint (name, asset_class/
    subclass defaults, precision, identifiers).

    A pin identifies the feed entry; it does not carry the entry's facts. Fetching
    nothing and defaulting the rest is how KSTL.DE shipped as USD while priced in
    EUR (owner incident 2026-08-28): every valuation of the position was silently
    wrong by the EUR/USD rate. So: yahoo → a live fetch, None when unconfirmed (the
    caller decides whether None means REFUSE, on a mint, or keep-the-row, on a
    refresh); coingecko → USD by definition of the API (vs=usd), no fetch; manual →
    the caller's explicit quote_currency, refusing when absent; any other source →
    refused, never mint an instrument against a feed we cannot query."""
    src = (price_source or "").lower()
    if src == "yahoo":
        return _resolve_yahoo(price_source_id)
    if src == "coingecko":
        return {"quote_currency": "USD"}
    if src == "manual":
        return {"quote_currency": _manual_quote_currency(quote_currency)}
    raise ValidationError(
        f"unknown price_source {price_source!r} — expected 'yahoo', 'coingecko' or 'manual'",
        field="price_source",
        value=price_source,
    )


def _refresh_pinned_facts(store: InstrumentStore, inst: dict, facts: dict) -> dict:
    """Sync an EXISTING pinned row with what its source currently reports — the
    "still look up and update accordingly" half of a pinned resolve. Only
    FEED-OWNED facts move: quote_currency always; name only when the stored one is
    a placeholder (the symbol/id fallback, never a name a caller chose); feed
    identifiers fill gaps without clobbering row values (they are load-bearing for
    the dedup ladder). Record metadata (asset_class, sector, note, …) stays the
    update_instrument tool's job — resolve fetches facts, update edits the record."""
    updates: dict = {}
    if facts.get("quote_currency") and facts["quote_currency"] != inst.get("quote_currency"):
        updates["quote_currency"] = facts["quote_currency"]
    name = (inst.get("name") or "").strip()
    placeholder = not name or name == inst.get("symbol") or name == inst.get("price_source_id")
    if placeholder and facts.get("name"):
        updates["name"] = facts["name"]
    if facts.get("identifiers"):
        merged = {**facts["identifiers"], **(inst.get("identifiers") or {})}
        if merged != (inst.get("identifiers") or {}):
            updates["identifiers"] = merged
    if not updates:
        return inst
    return store.touch(inst["id"], updates) or inst


# Identifier keys that are genuinely unique enough to imply "same asset" across vendors.
# "mic" (exchange name) is deliberately excluded — two different companies can list on
# the same exchange, so it disambiguates but never identifies on its own.
_IDENTITY_IDENTIFIER_KEYS = ("isin", "figi", "coingecko_id")


def find_existing(
    candidate: dict, store: InstrumentStore, allow_duplicate_symbol: bool = False
) -> dict | None:
    """Resolution ladder run before minting a new instrument. Tries, in order:

      (a) exact (price_source, price_source_id) — the identity key, unchanged.
      (b) a shared value in the `identifiers` bag (isin/figi/coingecko_id) — catches the
          same real-world asset resolved through a different price vendor.
      (c) normalized symbol + same asset_class — for crypto this is silently reused (a
          symbol/class collision on crypto is essentially always the same coin across
          venues); for anything else it's genuinely ambiguous (the same ticker can be two
          different companies on two exchanges), so it's a REFUSAL — ValidationError
          carrying the existing instrument's id/symbol/name — unless the caller passes
          allow_duplicate_symbol=True, meaning "yes, mint a distinct instrument anyway."

    Returns the instrument to reuse, or None if the candidate is genuinely new."""
    price_source = candidate.get("price_source", "")
    price_source_id = candidate.get("price_source_id", "")
    existing = store.find_by_source(price_source, price_source_id)
    if existing:
        return existing

    identifiers = candidate.get("identifiers") or {}
    for key in _IDENTITY_IDENTIFIER_KEYS:
        value = identifiers.get(key)
        if not value:
            continue
        existing = store.find_by_identifier(key, value)
        if existing:
            return existing

    symbol = candidate.get("symbol", "")
    asset_class = candidate.get("asset_class", "")
    match = store.find_by_symbol_and_class(symbol, asset_class)
    if not match:
        return None
    if asset_class == "crypto":
        return match
    if allow_duplicate_symbol:
        return None
    raise ValidationError(
        f"an instrument with symbol {symbol!r} ({asset_class}) already exists "
        f"(id={match['id']}, name={match.get('name')!r}). If this is genuinely a "
        "different asset (e.g. the same ticker on a different exchange), re-resolve "
        "with allow_duplicate_symbol=true to create it as a distinct instrument.",
        field="symbol",
        value=symbol,
        existing_instrument_id=match["id"],
        existing_symbol=match.get("symbol"),
        existing_name=match.get("name"),
    )


def resolve_instrument(
    query: str,
    asset_class: Optional[str] = None,
    price_source: Optional[str] = None,
    price_source_id: Optional[str] = None,
    unit: Optional[str] = None,
    asset_subclass: Optional[str] = None,
    name: Optional[str] = None,
    allow_duplicate_symbol: bool = False,
    quote_currency: Optional[str] = None,
    manual_price: Optional[float] = None,
    require_manual_price: bool = False,
) -> tuple[dict | None, bool]:
    """Find-or-create an instrument from a user query. Returns (instrument, created).

    (None, False) means resolution failed and the caller should ask for clarification
    or pass explicit price_source/price_source_id.

    A pinned price_source+price_source_id is authoritative for IDENTITY only — the
    source is still fetched for its facts, an existing row is refreshed with what it
    reports, and a mint is REFUSED when the source won't confirm the id (see
    _pin_facts). `quote_currency` is consulted ONLY for manual instruments (no
    feed exists); on a feed-backed row the feed's answer always wins.

    Raises ValidationError if the candidate's symbol collides with an existing
    DIFFERENT-vendor instrument of the same asset_class (see find_existing) — pass
    allow_duplicate_symbol=True once the caller has confirmed it's a genuinely
    different asset.

    `manual_price` is the mark a NEW manual instrument is created with (it is valued at
    that and nothing else). With `require_manual_price`, creating a manual instrument
    without one is REFUSED — it would be valued at 0, as the update path already refuses
    (`_manual_price`). An existing manual instrument is returned as is; re-mark it with
    update_instrument."""
    store = InstrumentStore()
    query = (query or "").strip()
    if quote_currency is not None:
        quote_currency = str(quote_currency).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", quote_currency):
            raise ValidationError(
                f"quote_currency {quote_currency!r} is not a 3-letter ISO currency code",
                field="quote_currency",
                value=quote_currency,
            )

    if price_source and price_source_id:
        # An explicit, caller-trusted external id is authoritative for IDENTITY —
        # dedupe on the exact identity key only, no ladder/refusal (a collision here
        # is a legitimate cross-exchange/cross-vendor symbol clash, not an ambiguous
        # guess) — but it does NOT carry the entry's facts. The source is still
        # fetched (see _pin_facts): an existing row gets its feed-owned facts
        # refreshed, and a mint refuses to guess what the source won't confirm.
        existing = store.find_by_source(price_source, price_source_id)
        if existing:
            try:
                facts = _pin_facts(price_source, price_source_id, quote_currency)
            except ValidationError:
                facts = None  # an unconfirmable pin never blocks an existing row
            if facts:
                existing = _refresh_pinned_facts(store, existing, facts)
            uf = _unit_fields(unit, existing.get("asset_class"))
            if uf and existing.get("unit") != uf["unit"]:
                existing = store.touch(existing["id"], uf) or existing
            return existing, False
        facts = _pin_facts(price_source, price_source_id, quote_currency)
        if facts is None:
            # A mint REFUSES an unconfirmed pin outright — an instrument must never exist
            # priced in a currency nobody fetched (the KSTL.DE-as-USD incident minted
            # exactly such a row, by skipping this fetch entirely).
            raise ValidationError(
                f"{price_source} could not confirm {price_source_id!r} (no price or "
                "quote currency) — refusing to mint an instrument nobody can price",
                field="price_source_id",
                value=price_source_id,
            )
        ac = (
            asset_class
            or facts.get("asset_class")
            or ("crypto" if price_source == "coingecko" else "equity")
        )
        payload = {
            "symbol": query or price_source_id,
            "name": name or facts.get("name") or query or price_source_id,
            "asset_class": ac,
            "asset_subclass": asset_subclass or facts.get("asset_subclass") or "",
            "price_source": price_source,
            "price_source_id": price_source_id,
            "quote_currency": facts["quote_currency"],
            **_unit_fields(unit, ac),
        }
        if facts.get("precision") is not None:
            payload["precision"] = facts["precision"]
        if facts.get("identifiers"):
            payload["identifiers"] = facts["identifiers"]
        return store.create(payload), True

    if price_source == "manual" and not price_source_id and query:
        # A manual instrument with no external id never hits the exact-key dedupe above
        # (find_by_source("") is always None on an empty id) — route it through the
        # ladder's symbol+class step instead, so two manual entries for the same thing
        # don't silently duplicate. No feed exists, so the currency must be explicit.
        ac = asset_class or "equity"
        candidate = {
            "symbol": query,
            "name": name or query,
            "asset_class": ac,
            "asset_subclass": asset_subclass or "",
            "price_source": "manual",
            "price_source_id": "",
            "quote_currency": _manual_quote_currency(quote_currency),
            "identifiers": {},
            **_unit_fields(unit, ac),
        }
        existing = find_existing(candidate, store, allow_duplicate_symbol)
        if existing:
            return existing, False
        mark = _new_manual_mark(manual_price, require_manual_price)
        if mark is not None:
            candidate["manual_price"] = mark
        return store.create(candidate), True

    if not query:
        return None, False

    # Crypto canonicalization: a Yahoo-style pair ("BTC-USD", "eth-eur") normalizes to the
    # canonical CoinGecko coin instead of minting a separate yahoo-priced instrument per
    # fiat pairing — but only once CoinGecko actually recognizes the base symbol; otherwise
    # fall through to the normal resolution below (unchanged behavior).
    resolved = None
    base_symbol = _split_crypto_pair(query)
    if base_symbol:
        resolved = _resolve_coingecko(base_symbol)

    if not resolved and asset_class in (None, "commodity"):
        resolved = _resolve_metal(query)

    if not resolved:
        order = ["coingecko", "yahoo"] if asset_class == "crypto" else ["yahoo", "coingecko"]
        for src in order:
            resolved = _resolve_yahoo(query) if src == "yahoo" else _resolve_coingecko(query)
            if resolved:
                break
    if not resolved:
        return None, False
    if asset_class:
        resolved["asset_class"] = asset_class
    if asset_subclass:
        resolved["asset_subclass"] = asset_subclass
    resolved.update(_unit_fields(unit, resolved.get("asset_class")))
    existing = find_existing(resolved, store, allow_duplicate_symbol)
    if existing:
        inst, created = existing, False
    else:
        inst, created = store.create(resolved), True
    if not created and unit:
        uf = _unit_fields(unit, inst.get("asset_class"))
        if uf and inst.get("unit") != uf["unit"]:
            inst = store.touch(inst["id"], uf) or inst
    return inst, created


def resolve_by_ticker(ticker: str) -> tuple[dict | None, bool]:
    """Resolve a /quote/$symbol URL segment to an instrument. Tries an exact,
    vendor-agnostic price_source_id match first — the common case, an instrument
    that already exists (held or previously viewed) — before falling through to
    resolve_instrument's find-or-create ladder for a genuinely new ticker.

    The exact match matters: unlike a bare human symbol (which can collide across
    exchanges/vendors — a "BTC" equity ticker and a "BTC" coin, or two distinct
    "OKLO" listings), price_source_id is the pricing identity and doesn't collide
    in practice, so this can skip the ladder's fuzzy/provider-search path entirely
    when the instrument is already known. Returns (instrument, created) like
    resolve_instrument."""
    ticker = (ticker or "").strip()
    if not ticker:
        return None, False
    store = InstrumentStore()
    existing = store.find_by_price_source_id(ticker)
    if existing:
        return existing, False
    # A held row can carry the bare display symbol instead of the qualified
    # price_source_id: crypto's services.positions._row_ticker substitutes it on
    # purpose ("BTC", not "bitcoin"), and some UI components pass the bare symbol
    # straight through as `ticker` too (e.g. the Commitments funding-source link —
    # fundingValue.ts prefers `r.symbol` over `r.ticker`). Rather than chase every
    # such call site, catch it here: when exactly ONE instrument (any asset class)
    # carries this bare symbol, it's unambiguous and safe to use directly — no
    # network call. When it's shared (e.g. the real "oklo" stock vs its
    # tokenized-wrapper crypto twin — two distinct instruments, same bare symbol)
    # or unknown, this is deliberately NOT a guess: fall through to the full ladder.
    matches = store.find_all_by_symbol(ticker)
    if len(matches) == 1:
        return matches[0], False
    return resolve_instrument(ticker)


# Metadata an edit may touch freely. Valuation fields (quote_currency, price_source,
# price_source_id, unit math, precision, symbol) are NOT plain metadata — they change
# how money is computed — so update_instrument routes them through _rebind_updates,
# which refuses a change that would silently re-denominate stored price history and
# re-establishes the series in the new terms on a key change. (This reverses the old
# "valuation fields are not updatable at all" decision: the KSTL.DE-as-USD incident
# showed a currency label with NO correction path is worse than a controlled re-bind —
# the mislabeled row could be re-resolved forever and would never heal.)
_UPDATABLE_FIELDS = {
    "name",
    "asset_class",
    "asset_subclass",
    "sector",
    "note",
    "dividend_rate_annual",
    # Dividend calendar overrides (ADR 0020) — user may correct a flaky/missing Yahoo value.
    # dividend_meta_updated_at is deliberately excluded (system-maintained).
    "next_ex_date",
    "next_pay_date",
    "last_ex_date",
    "last_dividend_amount",
    "dividend_frequency",
    # Fixed-income terms (bonds-first-class). Metadata, NOT a valuation field — the mark is
    # DERIVED from these by services.fixed_income, never a user-set price. Re-coerced through
    # the store factory below so it is normalized/clamped and nulled on a non-bond.
    "bond_terms",
}

_ASSET_CLASSES = {"equity", "commodity", "crypto", "bond", "cash", "property"}

# Valuation-critical fields an update may change ONLY through _rebind_updates.
_REBIND_FIELDS = (
    "quote_currency",
    "price_source",
    "price_source_id",
    "symbol",
    "precision",
    "unit",
    "manual_price",
)


def _new_manual_mark(manual_price, required: bool) -> float | None:
    """The mark a new manual instrument is created with, validated like `_manual_price`."""
    if manual_price is None:
        if required:
            raise ValidationError(
                "a manual instrument is valued at its manual_price — pass manual_price (the "
                "price per unit in quote_currency; the price paid if there is no newer "
                "valuation), or it would be valued at 0",
                field="manual_price",
            )
        return None
    try:
        price = float(manual_price)
    except (TypeError, ValueError):
        raise ValidationError(
            f"manual_price {manual_price!r} is not a number", field="manual_price"
        ) from None
    if not (math.isfinite(price) and price > 0):
        raise ValidationError("manual_price must be greater than 0", field="manual_price")
    return price


def _manual_price(
    inst: dict, changes: dict, new_source: str, old_source: str
) -> tuple[dict, str | None]:
    """The manual mark. A manual instrument is valued at `manual_price` and nothing else, so
    switching to manual without one valued the holding at 0 (QA 2026-10-13: "pin VWRA at
    139.20" passed the price as price_source_id and zeroed two holdings)."""
    raw = changes.get("manual_price")
    if raw is None:
        switching = new_source == "manual" and old_source != "manual"
        if switching and not (inst.get("manual_price") or 0) > 0:
            return {}, (
                "a manual instrument is valued at its manual_price — pass manual_price (the "
                "price per unit in quote_currency), or it would be valued at 0"
            )
        return {}, None
    if new_source != "manual":
        return {}, (
            "manual_price only values a manual instrument — pass price_source='manual' with "
            "it to pin this price instead of the feed's"
        )
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return {}, f"manual_price {raw!r} is not a number"
    if not price > 0:
        return {}, "manual_price must be greater than 0"
    return {"manual_price": price}, None


def _looks_like_a_price(value: str) -> bool:
    try:
        float(value.replace(",", ""))
    except ValueError:
        return False
    return True


def _rebind_key(store: InstrumentStore, inst: dict, changes: dict) -> tuple[dict, str | None]:
    """The quote_currency / price_source / price_source_id third of a re-bind.

    Price-history points carry NO currency — a series is interpreted through its
    instrument's quote_currency — so a change with stored history would silently
    re-denominate every historical valuation. Therefore a (price_source,
    price_source_id) change re-establishes the series in the new terms (old series
    dropped after commit, the new key fetched — see _reestablish_price_history),
    while a quote_currency-only change is allowed only while NO history is stored
    under the current id; after that, re-resolve with the pin, which refreshes the
    currency from the feed. A feed-backed re-bind always takes its currency from the
    feed — the same fail-closed rule as resolve (KSTL.DE-as-USD, owner incident
    2026-08-28). Returns (updates, None) or (None-so-far, error_message)."""
    old_source = (inst.get("price_source") or "").lower()
    old_id = (inst.get("price_source_id") or "").strip()
    new_source = old_source
    if changes.get("price_source") is not None:
        new_source = str(changes["price_source"]).strip().lower()
        if new_source not in ("yahoo", "coingecko", "manual"):
            return {}, f"price_source must be 'yahoo', 'coingecko' or 'manual', not {new_source!r}"
    new_id = old_id
    if changes.get("price_source_id") is not None:
        new_id = str(changes["price_source_id"]).strip()
        if not new_id:
            return (
                {},
                "price_source_id cannot be empty — pricing identity is (price_source, price_source_id)",
            )
        if new_source == "manual" and _looks_like_a_price(new_id):
            return {}, (
                f"price_source_id {new_id!r} looks like a price, not an id — pass it as "
                "manual_price"
            )
    elif new_source == "manual" and old_source in ("yahoo", "coingecko"):
        # The feed's key no longer prices this instrument; kept, the live quote would still
        # win over the manual mark in valuation.
        new_id = ""
    mark, mark_error = _manual_price(inst, changes, new_source, old_source)
    if mark_error:
        return {}, mark_error
    explicit_cc = ""
    if changes.get("quote_currency") is not None:
        explicit_cc = str(changes["quote_currency"]).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", explicit_cc):
            return {}, f"quote_currency {explicit_cc!r} is not a 3-letter ISO currency code"
    if (new_source, new_id.lower()) == (old_source, old_id.lower()):
        if not explicit_cc:
            return mark, None
        from backend.db.price_history_store import PriceHistoryStore

        if PriceHistoryStore().series(old_id):
            return {}, (
                f"quote_currency is locked once price history exists under {old_id!r} — "
                "stored points are denominated in the old currency and relabelling them "
                "would re-denominate every historical valuation. Re-bind price_source/"
                "price_source_id to a series in the right currency instead."
            )
        return {**mark, "quote_currency": explicit_cc}, None
    other = store.find_by_source(new_source, new_id)
    if other and other["id"] != inst["id"]:
        return {}, (
            f"an instrument already exists with price_source={new_source!r}, "
            f"price_source_id={new_id!r} ({other.get('symbol')!r}, id={other['id']}) — "
            "re-bind this position onto it instead of duplicating the feed key"
        )
    if new_id.lower() == old_id.lower() and new_source != old_source:
        shared = any(
            i["id"] != inst["id"]
            and (i.get("price_source_id") or "").strip().lower() == new_id.lower()
            for i in store.all()
        )
        if shared:
            return {}, (
                f"another instrument also reads price_source_id {new_id!r} — switching "
                "vendor on a shared id would mix differently-denominated price points"
            )
    rebind: dict = {}
    if new_source == "yahoo":
        facts = _resolve_yahoo(new_id)
        if facts is None:
            return {}, (
                f"yahoo could not confirm {new_id!r} (no price or quote currency) — "
                "refusing to re-bind to an unverified id"
            )
        if explicit_cc and explicit_cc != facts["quote_currency"]:
            return {}, (
                f"yahoo prices {new_id!r} in {facts['quote_currency']}, not {explicit_cc} "
                "— the feed owns quote_currency on a yahoo instrument"
            )
        rebind["quote_currency"] = facts["quote_currency"]
    elif new_source == "coingecko":
        if explicit_cc and explicit_cc != "USD":
            return {}, "coingecko instruments are quoted in USD (the API prices vs=usd)"
        rebind["quote_currency"] = "USD"
    else:
        cc = explicit_cc or (inst.get("quote_currency") or "").strip().upper()
        if not cc:
            return {}, (
                "re-binding to manual needs an explicit quote_currency — there is no "
                "feed to fetch it from"
            )
        rebind["quote_currency"] = cc
    rebind["price_source"] = new_source
    rebind["price_source_id"] = new_id
    return {**rebind, **mark}, None


def _rebind_updates(store: InstrumentStore, inst: dict, changes: dict) -> tuple[dict, str | None]:
    """Validate valuation-field changes (quote_currency / price_source / price_source_id
    / symbol / precision / unit) and compute their touch payload. Returns (updates,
    None) or (None-so-far, error_message) — update_instrument shapes the error. No
    store writes happen here; the price-series work follows the commit
    (see _reestablish_price_history)."""
    rebind, error = _rebind_key(store, inst, changes)
    if error:
        return {}, error
    if changes.get("symbol") is not None:
        symbol = str(changes["symbol"]).strip()
        if not symbol:
            return {}, "symbol cannot be empty"
        rebind["symbol"] = symbol
    if changes.get("precision") is not None:
        try:
            precision = int(changes["precision"])
        except (TypeError, ValueError):
            return {}, f"precision must be an integer, not {changes['precision']!r}"
        if not 0 <= precision <= 12:
            return {}, "precision must be between 0 and 12"
        rebind["precision"] = precision
    if changes.get("unit") is not None:
        unit = str(changes["unit"]).strip().lower()
        if not unit:
            rebind["unit"] = ""
            rebind["quote_per_unit"] = 1.0
        else:
            eff_class = str(changes.get("asset_class") or inst.get("asset_class") or "")
            rebind.update(_unit_fields(unit, eff_class))
    return rebind, None


def _reestablish_price_history(store: InstrumentStore, before: dict, updates: dict) -> dict:
    """After a re-bind commits: drop the OLD key's price series when no other instrument
    reads it (it was this instrument's own history, denominated for a key nothing will
    fetch again), then backfill the NEW key from its source so the series exists in the
    new terms. Best-effort — the startup backfill sweep rebuilds it anyway — but done
    eagerly so history-backed views (returns, net-worth history) don't go dark until
    the next sweep. Returns what happened, for the caller to report."""
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services.prices import backfill_history

    old_id = (before.get("price_source_id") or "").strip()
    new_id = (updates.get("price_source_id") or old_id).strip()
    new_source = (updates.get("price_source") or before.get("price_source") or "").lower()
    out: dict = {}
    history = PriceHistoryStore()
    still_read = any(
        i["id"] != before["id"]
        and (i.get("price_source_id") or "").strip().lower() == old_id.lower()
        for i in store.all()
    )
    # A pin to a manual price keeps the feed's history: it is real market data, and
    # re-binding back to the feed finds it again.
    if not still_read and new_source != "manual":
        wiped = history.delete_series(old_id)
        if wiped:
            out["wiped_points"] = wiped
    if new_source in ("yahoo", "coingecko"):
        added = backfill_history(new_id)
        if added:
            out["backfilled_points"] = added
    return out


def update_instrument(instrument_id: str, changes: dict) -> dict:
    """Edit an instrument through InstrumentStore.touch. Two kinds of field:

    METADATA (name / classification / sector / note / dividend rate / bond terms) is
    whitelist-filtered; class/subclass normalized to lowercase; bond_terms re-coerced
    against the effective asset_class. An explicit dividend_rate_annual also stamps
    dividend_rate_source="manual" in the SAME write — this is the only user-facing path
    for that field, so a caller-supplied rate is, by definition, user-confirmed (mirrors
    set_classification stamping classification_source="manual" alongside sector/region).
    VALUATION fields (quote_currency / price_source / price_source_id / symbol /
    precision / unit) change how money is computed, so they go through _rebind_updates —
    which refuses a change that would silently re-denominate stored price history and
    re-establishes the series in the new terms on a key change. Unknown instrument or an
    empty effective change set returns an error dict (the AI-tool callers shape it)."""
    store = InstrumentStore()
    inst = store.find_by_id(instrument_id)
    if not inst:
        return {
            "error": "instrument not found — get the id from resolve_instrument or get_portfolio"
        }
    updates: dict = {}
    for key, value in changes.items():
        if key in _REBIND_FIELDS or value is None:
            continue
        if key not in _UPDATABLE_FIELDS:
            continue
        if key in ("asset_class", "asset_subclass"):
            value = str(value).strip().lower()
            if key == "asset_class" and value not in _ASSET_CLASSES:
                return {"error": f"asset_class must be one of {sorted(_ASSET_CLASSES)}"}
        updates[key] = value
    if "dividend_rate_annual" in updates:
        # The only user-facing path for this field — an explicit value is user-confirmed.
        # Ratchet: this always wins, same as set_classification stamping "manual".
        updates["dividend_rate_source"] = "manual"
    if "bond_terms" in updates:
        # Re-coerce against the EFFECTIVE asset_class (post-edit if it's changing in the same
        # call) so terms are normalized/clamped and dropped to None on a non-bond.
        from backend.db.instrument_store import _coerce_bond_terms

        eff_class = updates.get("asset_class") or inst.get("asset_class")
        updates["bond_terms"] = _coerce_bond_terms(updates["bond_terms"], eff_class)
        # ADR 0020 / MATH-50: quote_per_unit is DERIVED from face_value, never user-set —
        # re-derive it here so an edited face_value re-values every existing position.
        if updates["bond_terms"] is not None:
            updates["quote_per_unit"] = updates["bond_terms"]["face_value"] / 100.0
    rebind, rebind_error = _rebind_updates(store, inst, changes)
    if rebind_error:
        return {"error": rebind_error}
    updates.update(rebind)
    if not updates:
        return {"error": "nothing to update — pass at least one editable field"}
    updated = store.touch(instrument_id, updates) or inst
    report: dict = {}
    if rebind.get("price_source") is not None or rebind.get("price_source_id") is not None:
        report = _reestablish_price_history(store, inst, rebind)
    out = {
        "ok": True,
        "instrument": {
            k: updated.get(k)
            for k in (
                "id",
                "symbol",
                "name",
                "asset_class",
                "asset_subclass",
                "sector",
                "note",
                "dividend_rate_annual",
                "dividend_rate_source",
                "next_ex_date",
                "next_pay_date",
                "last_ex_date",
                "last_dividend_amount",
                "dividend_frequency",
                "bond_terms",
                "quote_currency",
                "price_source",
                "price_source_id",
                "manual_price",
                "unit",
                "quote_per_unit",
                "precision",
            )
        },
    }
    if report:
        out["price_history"] = report
    return out
