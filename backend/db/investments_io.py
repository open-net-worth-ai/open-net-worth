"""Investment import/export between the JSON file shapes and the 8 investment tables
(``positions``, ``lots``, ``grants``, ``vest_events``, ``option_contracts``,
``realized_events``, ``watchlists``, ``asset_brief``) — the plan-4b task-5 absorb,
revision 0012.

Mirrors ``planning_io.py`` (Wave A) EXACTLY but stays self-contained (no cross-import):
six of the eight stores are book-scoped flat lists absorbed by the SAME generic
``(book_id, seq, id, payload)`` pair every flat-list store reuses; ``watchlists`` is a
per-book singleton dict (mirrors ``plan_state``); ``asset_brief`` is the one Tier-2
store — a derived cache like ``instruments``, so its rows carry NO ``book_id``.

``book_id`` is a promoted COLUMN, never inside ``payload`` — source rows never carried
it, so ``payload`` stays the exact source dict and ``export == source`` holds regardless
of which book absorbed it (exactly ``money_io``'s/``planning_io``'s own reasoning). Every
Tier-1 ``import_*`` call writes ``book_id=DEFAULT_BOOK_ID`` unconditionally;
``asset_brief`` writes none.

FINITE-MONEY GUARD (FR-M7, applied PROACTIVELY here — the plan-4a security-review lesson
from ``budget_assignments``' M1 fix, not a post-hoc patch this time): investment money
feeds net worth and ``/api/data/import`` is untrusted, so ``import_payload_list`` runs
``money_io._assert_finite_money`` on each store's net-worth-bearing fields BEFORE a row
lands in ``payload`` — an ``inf``/``nan``/non-numeric value would otherwise survive
VERBATIM into the round-trip source of truth and silently poison portfolio math.
``_MONEY_FIELDS`` names exactly which fields per store; ``grants``/``watchlists``/
``asset_brief`` carry none — a grant's own ``grant_price_per_unit`` is reference-only
(``models/equity.py``), the money FACT that actually feeds net worth lives on the vest
event (``fmv_per_unit``/``base_amount``, already covered) and the eventual lot/position.
A bad value REJECTS the whole store's import (nothing partially written), matching
``import_transactions``'s own all-or-nothing FR-M7 behavior — ``realized_events`` also
nests per-lot money in ``consumptions[]``, guarded the same way.

``INVESTMENT_PAYLOAD_SPECS``/``INVESTMENT_FILES`` are consumed by revision 0012 (this
absorb) AND by task 6's portable-export bridge for these DB-backed stores.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.money_io import _assert_finite_money
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    asset_brief,
    grants,
    lots,
    option_contracts,
    positions,
    realized_events,
    vest_events,
    watchlists,
)

_BATCH = 1000

# Net-worth-bearing money fields per store — asserted finite on import (an untrusted
# /api/data/import bundle can carry inf/nan that would survive verbatim into payload and
# poison portfolio math). grants/watchlists/asset_brief carry none — see module
# docstring's FINITE-MONEY GUARD section for why a grant's own row is out of scope.
_MONEY_FIELDS = {
    "positions": ("cost_basis", "quantity"),
    "lots": ("unit_cost", "fees", "fx_rate", "original_qty", "remaining_qty"),
    "vest_events": (
        "fmv_per_unit",
        "price_paid_per_unit",
        "gross_gain_native",
        "base_amount",
        "units",
    ),
    "option_contracts": (
        "strike",
        "premium_per_share",
        "close_premium_per_share",
        "collateral_amount",
        "contracts",
    ),
    "investment_theses": ("stop_below", "buy_below", "buy_above", "fair_value", "trim_above"),
    "realized_events": (
        "proceeds_per_unit",
        "disposal_fees",
        "realized_gain",
        "realized_gain_base",
        "fx_rate",
        "quantity",
    ),
}


def _guard_money(store: str, raw: list[dict]) -> None:
    for field in _MONEY_FIELDS.get(store, ()):  # grants/watchlists/asset_brief -> () -> no-op
        for row in raw:
            _assert_finite_money(row.get(field), field=field, row_id=row.get("id"))
    # realized_events also nests per-lot money in consumptions[] — guard those lines too.
    if store == "realized_events":
        for row in raw:
            for c in row.get("consumptions") or []:
                for f in ("qty", "unit_cost", "realized_gain"):
                    _assert_finite_money(c.get(f), field=f"consumptions.{f}", row_id=row.get("id"))


# ── Generic pair — the 6 Tier-1 flat-list stores PLUS asset_brief (Tier-2). Book-agnostic
# by construction: which columns land is decided entirely by `table is asset_brief`, so one
# pair serves both shapes rather than a Tier-1/Tier-2 fork of near-duplicate functions. ────


def import_payload_list(
    conn: Connection,
    table: Table,
    raw: list[dict],
    *,
    id_field: str = "id",
    store: str | None = None,
) -> int:
    _guard_money(store or table.name, raw)
    book = {} if table is asset_brief else {"book_id": DEFAULT_BOOK_ID}
    rows = [
        {**book, "seq": seq, "id": row.get(id_field), "payload": row} for seq, row in enumerate(raw)
    ]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table) -> list[dict]:
    return [r.payload for r in conn.execute(select(table).order_by(table.c.seq))]


# ── watchlists: one row per book, verbatim payload, keyed by book_id ────────────────
# Mirrors planning_io.import_plan_state/export_plan_state exactly — WatchlistStore hand-
# rolls every method over _get_raw/_save_raw, so it never used the flat-list surface.


def import_watchlist(conn: Connection, raw: dict) -> int:
    import copy

    from backend.db.watchlist_topics import normalize_topics

    # An export from before topics left watchlists must not bring back topics filed in lists.
    payload = copy.deepcopy(raw)
    normalize_topics(payload)
    fire_db_write_hook(watchlists.name, [(DEFAULT_BOOK_ID, None)], conn=conn)
    conn.execute(insert(watchlists).values(book_id=DEFAULT_BOOK_ID, payload=payload))
    return 1


def export_watchlist(conn: Connection) -> dict:
    row = conn.execute(select(watchlists).where(watchlists.c.book_id == DEFAULT_BOOK_ID)).first()
    return row.payload if row else {}  # round-trips the raw file dict verbatim


# ── Filename/table/id_field specs — consumed by revision 0012 AND task 6's portable
# bridge. 7 entries, not 8: watchlist's import is bespoke (dict-shaped singleton), so it
# is addressed by name, not through this generic spec list — mirrors PLANNING_PAYLOAD_
# SPECS excluding match_decisions/budget_assignments/plan_state for the same reason. ───

INVESTMENT_PAYLOAD_SPECS: list[tuple[str, Table, str]] = [
    ("positions.json", positions, "id"),
    ("lots.json", lots, "id"),
    ("grants.json", grants, "id"),
    ("vest_events.json", vest_events, "id"),
    ("option_contracts.json", option_contracts, "id"),
    ("realized_events.json", realized_events, "id"),
    # Tier-2 cache, keyed instrument_id. backend.core.paths.ASSET_BRIEF_JSON's real
    # filename is the plural "asset_briefs.json" — used here verbatim (not the singular
    # spelling) so a future filename-keyed consumer (task 6) never has to special-case it.
    ("asset_briefs.json", asset_brief, "instrument_id"),
]

INVESTMENT_FILES: tuple[str, ...] = tuple(
    fn for fn, _table, _id_field in INVESTMENT_PAYLOAD_SPECS
) + ("watchlist.json",)
