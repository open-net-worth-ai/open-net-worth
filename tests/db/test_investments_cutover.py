"""Investments cutover (plan 4b, task 5): revision 0012 absorbs all 8 investment JSON
sources into their Tier-1/Tier-2 tables (built by revision 0011), one all-or-nothing unit.

Mirrors ``tests/db/test_planning_cutover.py``'s shape: no cross-file consistency
pre-check (no FKs BETWEEN investment tables) and no embedded-child explosion (every
investment table is either a flat payload list or one of the two Tier-2/bespoke shapes).
Covers: exact round-trip per store (direct ``investments_io`` calls, fast) AND through the
real migration chain (monkeypatched sources + ``command.upgrade``); the dict-shaped
``watchlists`` singleton; the Tier-2 ``asset_brief`` cache (no book_id, present-and-absent);
the ``_assert_finite_money`` gate (FR-M7, per-store + the nested ``consumptions[]`` lines);
full absorb + 8 ``json_cutovers`` rows + 8 renames; the up/down/up cycle.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.core.errors import ValidationError
from backend.db import investments_io as iio
from backend.db.tables import (
    asset_brief,
    grants,
    json_cutovers,
    lots,
    option_contracts,
    positions,
    realized_events,
    vest_events,
    watchlists,
)
from tests.db.conftest import sqlite_only

INVESTMENT_STORE_NAMES = (
    "positions",
    "lots",
    "grants",
    "vest_events",
    "option_contracts",
    "realized_events",
    "asset_brief",
    "watchlists",
)


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """Postgres's ``db_url`` is the ONE shared CI server (unlike SQLite's
    fresh-tmp_path-per-test): scrub the public schema first so an earlier test's
    already-applied migration history doesn't make THIS test's ``command.upgrade(...,
    "head")`` a silent no-op. Mirrors ``test_planning_cutover.py``'s identical helper
    verbatim."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures (deliberately small but non-trivial: unicode, nulls, nested dicts,
# empty lists — the round-trip proof needs a diff to actually be possible) ──────

POSITIONS = [
    {
        "id": "pos_1",
        "instrument_id": "instr_aapl",
        "account_id": "acct_broker",
        "quantity": 10.5,
        "cost_basis": 150.25,
        "cost_currency": "USD",
        "vesting_state": "vested",
        "acquired_at": "2025-01-15",
        "note": "core holding",
        "created_at": "2025-01-15T00:00:00Z",
        "updated_at": "2025-01-15T00:00:00Z",
    },
    {
        "id": "pos_2",
        "instrument_id": "instr_rsu_co",
        "account_id": "acct_equity",
        "quantity": 200.0,
        "cost_basis": None,
        "cost_currency": None,
        "vesting_state": "unvested",
        "acquired_at": None,
        "note": "unvested RSU tranche — café ☕ unicode round-trip",
        "created_at": "2026-02-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
    },
]

# The legacy positions.json above still carries a ``note`` (the cutover revision absorbs
# whatever the file holds); revision 202610292000_positions_drop_note then deletes that key from
# every stored position. So at head the store holds the fixture WITHOUT it.
POSITIONS_AT_HEAD = [{k: v for k, v in p.items() if k != "note"} for p in POSITIONS]

LOTS = [
    {
        "id": "lot_1",
        "position_id": "pos_1",
        "instrument_id": "instr_aapl",
        "account_id": "acct_broker",
        "acquired_at": "2025-01-15",
        "original_qty": 10.5,
        "remaining_qty": 10.5,
        "unit_cost": 150.25,
        "cost_currency": "USD",
        "fees": 4.95,
        "fx_rate": 1.35,
        "base_currency": "SGD",
        "kind": "buy",
        "txn_id": "txn_abc",
        "note": "",
        "created_at": "2025-01-15T00:00:00Z",
        "updated_at": "2025-01-15T00:00:00Z",
    }
]

GRANTS = [
    {
        "id": "grant_1",
        "type": "rsu",
        "instrument_id": "instr_rsu_co",
        "grant_date": "2026-01-01",
        "total_units": 400.0,
        "currency": "USD",
        "grant_price_per_unit": 42.0,
        "tax_event": "at_vest",
        "status": "active",
        "release_required": False,
        "locked_position_id": "pos_2",
        "pending_position_id": None,
        "account_id": "acct_equity",
        "tranches": [
            {
                "id": "tr_1",
                "vest_date": "2026-04-01",
                "units": 100.0,
                "kind": "time",
                "condition_note": None,
                "vested": False,
                "vest_event_id": None,
            }
        ],
        "note": "4-year vest",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
]

VEST_EVENTS = [
    {
        "id": "vest_1",
        "grant_id": "grant_1",
        "tranche_id": "tr_1",
        "date": "2026-04-01",
        "units": 100.0,
        "fmv_per_unit": 45.5,
        "fmv_currency": "USD",
        "price_paid_per_unit": 0.0,
        "units_withheld": 30.0,
        "gross_gain_native": 4550.0,
        "txn_id": "txn_vest_1",
        "lot_id": "lot_vest_1",
        "locked_position_id": "pos_2",
        "pending_release": False,
        "expected_release_date": None,
        "released_on": None,
        "pending_position_id": None,
        "idempotency_key": "vest-2026-04-01",
        "source": "manual",
        "base_amount": 6142.5,
        "base_currency": "SGD",
        "note": "",
        "created_at": "2026-04-01T00:00:00Z",
    }
]

OPTION_CONTRACTS = [
    {
        "id": "opt_1",
        "account_id": "acct_broker",
        "underlying_instrument_id": "instr_aapl",
        "option_type": "call",
        "side": "short",
        "status": "open",
        "strike": 180.0,
        "expiry": "2026-09-19",
        "contracts": 2.0,
        "multiplier": 100,
        "premium_per_share": 3.25,
        "premium_currency": "USD",
        "quote_currency": "USD",
        "close_premium_per_share": None,
        "open_date": "2026-07-01",
        "closed_at": None,
        "collateral_account_id": "acct_broker",
        "collateral_amount": 36000.0,
        "strategy_tag": "covered_call",
        "idempotency_key": None,
        "note": "",
        "realized_premium_base": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }
]

REALIZED_EVENTS = [
    {
        "id": "real_1",
        "position_id": "pos_1",
        "instrument_id": "instr_aapl",
        "account_id": "acct_broker",
        "disposed_at": "2026-06-01",
        "quantity": 5.0,
        "proceeds_per_unit": 190.0,
        "proceeds_currency": "USD",
        "disposal_fees": 1.5,
        "method": "fifo",
        "is_taxable": True,
        "cost_basis_known": True,
        "consumptions": [
            {
                "lot_id": "lot_1",
                "qty": 5.0,
                "unit_cost": 150.25,
                "cost_currency": "USD",
                "acquired_at": "2025-01-15",
                "term": "long",
                "realized_gain": 197.5,
            }
        ],
        "realized_gain": 197.5,
        "realized_gain_base": 266.6,
        "base_currency": "SGD",
        "fx_rate": 1.35,
        "txn_id": "txn_sell_1",
        "idempotency_key": None,
        "source": "manual",
        "note": "partial sale — 30% cap gains",
        "created_at": "2026-06-01T00:00:00Z",
    }
]

ASSET_BRIEFS = [
    {
        "instrument_id": "instr_aapl",
        # No "news"/"news_checked_at" key: migration 202608301645_drop_asset_brief_news_fields
        # (RFC intel-signals-data-model §5 — the Asset Detail news feed is a live query over
        # `evidence`, not a stored asset_brief field) runs as part of the SAME "head" chain this
        # fixture is round-tripped through, so a fixture that still carried either key would no
        # longer round-trip byte-identically — not a bug, an intentional later transform.
        "key_issues": [
            {
                "question": "Can services growth offset hardware slowdown?",
                "bullish": {
                    "text": "Services margin expanding",
                    "sources": ["https://example.com/b"],
                },
                "bearish": {"text": "China demand soft", "sources": []},
            }
        ],
        "facts": {
            "ceo": "Tim Cook",
            "employees": 164000,
            "ipo_date": "1980-12-12",
            "industry": "Consumer Electronics",
            "exchange": "NASDAQ",
            "country": "US",
        },
        "peers": [{"name": "Microsoft", "ticker": "MSFT", "exchange": "NASDAQ"}],
        "description": "Consumer electronics and services company.",
        "synthesized_at": "2026-08-01T00:00:00Z",
        "search_provider": "tavily",
    }
]

WATCHLIST = {
    "watchlists": [
        {
            "id": "my_holdings",
            "name": "My holdings",
            "description": "",
            "is_auto_sync": True,
            "kind": "manual",
            "rule": None,
            "order": 0,
            "config": None,
            "muted": False,
        },
        {
            "id": "wl_growth",
            "name": "Growth ideas ☕",
            "description": "unicode round-trip check",
            "is_auto_sync": False,
            "kind": "manual",
            "rule": None,
            "order": 1,
            "config": None,
            "muted": False,
        },
    ],
    "items": [
        {
            "id": "wli_1",
            "type": "ticker",
            "value": "AAPL",
            "label": "Apple",
            "asset_class": "equity",
            "monitor_news": True,
            "added_by": "auto",
            "note": "",
            "search_queries": [],
            "watchlist_ids": ["my_holdings"],
            "instrument_id": "instr_aapl",
        }
    ],
    "global_config": {"enabled": True, "price_check_interval_seconds": 900, "search_budget": 15},
}

# (store name, table, fixture) for the 6 Tier-1 generic stores PLUS asset_brief (Tier-2) —
# all 7 stores whose import AND export are both generic.
_GENERIC_SPECS = [
    ("positions", positions, POSITIONS),
    ("lots", lots, LOTS),
    ("grants", grants, GRANTS),
    ("vest_events", vest_events, VEST_EVENTS),
    ("option_contracts", option_contracts, OPTION_CONTRACTS),
    ("realized_events", realized_events, REALIZED_EVENTS),
    ("asset_brief", asset_brief, ASSET_BRIEFS),
]


def _patch_all_sources_missing(monkeypatch, tmp_path) -> None:
    """Point every cutover source seam — not just this wave's own eight — at a file
    that does not exist, so a test exercising the real migration chain never
    accidentally reads whatever the ambient (session-shared) DATA_DIR happens to hold.
    Delegates to the canonical cross-wave helper (``tests/db/conftest.py``): a bare
    ``command.upgrade(..., "head")`` runs every revision up to head, not just
    investments' own, so a DIFFERENT wave's source (e.g. Wave D's
    ``news_sources.json``, unconditionally reseeded before every test — see the
    canonical helper's docstring) can still get absorbed and collide on a second
    upgrade in the same test unless EVERY source is patched, not just this file's
    eight."""
    from tests.db.conftest import patch_all_cutover_sources_missing

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)


def _write_all_eight_fixtures(tmp_path, monkeypatch) -> dict[str, tuple]:
    """Write every store's fixture to its own file under ``tmp_path`` and monkeypatch the
    matching ``versions_evidence_source`` constant at it. Returns {attr: (filename,
    original)} for assertion convenience."""
    files = {
        "POSITIONS_SOURCE": ("positions.json", POSITIONS),
        "LOTS_SOURCE": ("lots.json", LOTS),
        "GRANTS_SOURCE": ("grants.json", GRANTS),
        "VEST_EVENTS_SOURCE": ("vest_events.json", VEST_EVENTS),
        "OPTION_CONTRACTS_SOURCE": ("option_contracts.json", OPTION_CONTRACTS),
        "REALIZED_EVENTS_SOURCE": ("realized_events.json", REALIZED_EVENTS),
        "ASSET_BRIEF_SOURCE": ("asset_briefs.json", ASSET_BRIEFS),
        "WATCHLIST_SOURCE": ("watchlist.json", WATCHLIST),
    }
    for attr, (filename, fixture) in files.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture, ensure_ascii=False))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    return files


# ── Direct round-trip per store (fixture-scale, no migration chain needed) ──────────


@pytest.mark.parametrize(
    "store,table,fixture", _GENERIC_SPECS, ids=[spec[0] for spec in _GENERIC_SPECS]
)
def test_each_generic_store_round_trips_exactly(db_engine, store, table, fixture):
    id_field = "instrument_id" if store == "asset_brief" else "id"
    with db_engine.begin() as conn:
        n = iio.import_payload_list(conn, table, fixture, id_field=id_field, store=store)
    assert n == len(fixture)
    with db_engine.connect() as conn:
        assert iio.export_payload_list(conn, table) == fixture


def test_generic_import_promotes_book_id_but_payload_stays_verbatim(db_engine):
    with db_engine.begin() as conn:
        iio.import_payload_list(conn, lots, LOTS, store="lots")
    with db_engine.connect() as conn:
        row = conn.execute(select(lots).where(lots.c.id == "lot_1")).one()
    assert row.book_id == "book_000000000000"
    assert row.seq == 0
    assert "book_id" not in row.payload


def test_asset_brief_is_tier_2_no_book_id_keyed_by_instrument_id(db_engine):
    with db_engine.begin() as conn:
        iio.import_payload_list(
            conn, asset_brief, ASSET_BRIEFS, id_field="instrument_id", store="asset_brief"
        )
    with db_engine.connect() as conn:
        row = conn.execute(select(asset_brief).where(asset_brief.c.id == "instr_aapl")).one()
    assert not hasattr(row, "book_id")
    assert row.seq == 0
    assert row.payload["instrument_id"] == "instr_aapl"  # id_field key stays IN payload


def test_missing_source_files_are_a_clean_empty_export_for_every_generic_store(db_engine):
    with db_engine.connect() as conn:
        for _store, table, _fixture in _GENERIC_SPECS:
            assert iio.export_payload_list(conn, table) == []


# ── watchlists: one row per book, verbatim payload ───────────────────────────────────


def test_watchlist_round_trips_the_singleton_dict_verbatim(db_engine):
    with db_engine.begin() as conn:
        n = iio.import_watchlist(conn, WATCHLIST)
    assert n == 1
    with db_engine.connect() as conn:
        assert iio.export_watchlist(conn) == WATCHLIST


def test_watchlist_default_when_no_row_exists(db_engine):
    with db_engine.connect() as conn:
        assert iio.export_watchlist(conn) == {}


def test_watchlist_keyed_by_book_id_not_a_seq_id_payload_shape(db_engine):
    with db_engine.begin() as conn:
        iio.import_watchlist(conn, WATCHLIST)
    with db_engine.connect() as conn:
        row = conn.execute(select(watchlists)).one()
    assert row.book_id == "book_000000000000"
    assert row.payload == WATCHLIST  # verbatim — unicode/None untouched
    assert not hasattr(row, "id")  # watchlists has no id/seq column at all


# ── M1-style: reject non-finite/non-numeric money — the SAME guard the money core's
# import_transactions and planning's import_budget_assignments use (money_io.
# _assert_finite_money, FR-M7), applied proactively across the 5 money-bearing stores. ──


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), "abc"])
@pytest.mark.parametrize(
    "store,table,field,base_row",
    [
        ("positions", positions, "cost_basis", POSITIONS[0]),
        ("positions", positions, "quantity", POSITIONS[0]),
        ("lots", lots, "unit_cost", LOTS[0]),
        ("lots", lots, "fx_rate", LOTS[0]),
        ("lots", lots, "fees", LOTS[0]),
        ("vest_events", vest_events, "fmv_per_unit", VEST_EVENTS[0]),
        ("vest_events", vest_events, "base_amount", VEST_EVENTS[0]),
        ("option_contracts", option_contracts, "strike", OPTION_CONTRACTS[0]),
        ("option_contracts", option_contracts, "collateral_amount", OPTION_CONTRACTS[0]),
        ("realized_events", realized_events, "realized_gain", REALIZED_EVENTS[0]),
        ("realized_events", realized_events, "fx_rate", REALIZED_EVENTS[0]),
    ],
)
def test_import_payload_list_rejects_non_finite_money_per_store(
    db_engine, store, table, field, base_row, bad
):
    row = {**base_row, field: bad}
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            iio.import_payload_list(conn, table, [row], store=store)


@pytest.mark.parametrize("field", ["qty", "unit_cost", "realized_gain"])
@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "abc"])
def test_import_realized_events_rejects_non_finite_consumption_line(db_engine, field, bad):
    row = {
        **REALIZED_EVENTS[0],
        "consumptions": [{**REALIZED_EVENTS[0]["consumptions"][0], field: bad}],
    }
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            iio.import_payload_list(conn, realized_events, [row], store="realized_events")


def test_non_finite_money_aborts_the_whole_store_import(db_engine):
    """One poisoned row must abort the WHOLE store's import — nothing partially written,
    matching import_transactions'/import_budget_assignments' own all-or-nothing FR-M7
    behavior."""
    good = POSITIONS[0]
    bad = {**POSITIONS[1], "id": "pos_bad", "cost_basis": float("inf")}
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            iio.import_payload_list(conn, positions, [good, bad], store="positions")
    with db_engine.connect() as conn:
        assert iio.export_payload_list(conn, positions) == []


def test_import_payload_list_allows_none_and_finite_zero_money_values(db_engine):
    """None (unknown/absent) and finite non-positive values are untouched by the guard —
    only inf/nan/non-numeric are new rejections."""
    row = {**LOTS[0], "id": "lot_zero", "unit_cost": None, "fees": 0.0, "fx_rate": -1.0}
    with db_engine.begin() as conn:
        n = iio.import_payload_list(conn, lots, [row], store="lots")
    assert n == 1
    with db_engine.connect() as conn:
        assert iio.export_payload_list(conn, lots) == [row]


def test_grants_watchlist_asset_brief_are_not_money_guarded_by_design():
    """grant_price_per_unit is 'reference only' (models/equity.py) — the money FACT that
    feeds net worth lives on the vest event, not the grant row itself. Proves the
    _MONEY_FIELDS scope boundary the brief specifies ("grants/watchlists/asset_brief carry
    none") is deliberate, not an oversight: a non-finite value here must NOT raise."""
    row = {**GRANTS[0], "grant_price_per_unit": float("inf")}
    iio._guard_money("grants", [row])


@sqlite_only("SQLite's JSON column stores the non-finite token; Postgres json rejects Infinity")
def test_a_non_finite_grant_price_round_trips_unguarded(db_engine):
    row = {**GRANTS[0], "grant_price_per_unit": float("inf")}
    with db_engine.begin() as conn:
        n = iio.import_payload_list(conn, grants, [row], store="grants")
    assert n == 1
    with db_engine.connect() as conn:
        assert iio.export_payload_list(conn, grants) == [row]


# ── INVESTMENT_PAYLOAD_SPECS / INVESTMENT_FILES constants ───────────────────────────


def test_investment_payload_specs_covers_the_seven_generic_stores():
    specs = {fn: (table, id_field) for fn, table, id_field in iio.INVESTMENT_PAYLOAD_SPECS}
    assert set(specs) == {
        "positions.json",
        "lots.json",
        "grants.json",
        "vest_events.json",
        "option_contracts.json",
        "realized_events.json",
        "asset_briefs.json",
    }
    assert specs["asset_briefs.json"] == (asset_brief, "instrument_id")
    for fn, (_table, id_field) in specs.items():
        if fn != "asset_briefs.json":
            assert id_field == "id"


def test_investment_files_lists_all_eight():
    assert set(iio.INVESTMENT_FILES) == {
        "positions.json",
        "lots.json",
        "grants.json",
        "vest_events.json",
        "option_contracts.json",
        "realized_events.json",
        "asset_briefs.json",
        "watchlist.json",
    }
    assert len(iio.INVESTMENT_FILES) == 8


# ── Full migration chain: monkeypatched sources + command.upgrade(head) ─────────────


def test_all_eight_stores_round_trip_exactly_through_the_real_migration(
    tmp_path, db_url, monkeypatch
):
    files = _write_all_eight_fixtures(tmp_path, monkeypatch)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    exporters = {
        "POSITIONS_SOURCE": lambda c: iio.export_payload_list(c, positions),
        "LOTS_SOURCE": lambda c: iio.export_payload_list(c, lots),
        "GRANTS_SOURCE": lambda c: iio.export_payload_list(c, grants),
        "VEST_EVENTS_SOURCE": lambda c: iio.export_payload_list(c, vest_events),
        "OPTION_CONTRACTS_SOURCE": lambda c: iio.export_payload_list(c, option_contracts),
        "REALIZED_EVENTS_SOURCE": lambda c: iio.export_payload_list(c, realized_events),
        "ASSET_BRIEF_SOURCE": lambda c: iio.export_payload_list(c, asset_brief),
        "WATCHLIST_SOURCE": lambda c: iio.export_watchlist(c),
    }
    with eng.get_engine().connect() as conn:
        for attr, (_filename, original) in files.items():
            expected = POSITIONS_AT_HEAD if attr == "POSITIONS_SOURCE" else original
            assert exporters[attr](conn) == expected, f"{attr} did not round-trip exactly"

    eng.dispose_engine()


def test_asset_brief_absent_is_a_clean_skip_other_seven_still_absorb(tmp_path, db_url, monkeypatch):
    """asset_briefs.json may be absent (it's a cache) — absorbed only if present; the other
    seven sources still absorb normally when only this one is missing."""
    _write_all_eight_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.ASSET_BRIEF_SOURCE",
        tmp_path / "does_not_exist_asset_briefs.json",
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert iio.export_payload_list(conn, asset_brief) == []
        assert iio.export_payload_list(conn, positions) == POSITIONS_AT_HEAD
        assert iio.export_watchlist(conn) == WATCHLIST
        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store == "asset_brief")
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


def test_full_migration_aborts_on_non_finite_money_in_bundle(tmp_path, db_url, monkeypatch):
    """The exact ambiguity-resolution scenario: an untrusted bundle's positions.json carries
    an inf cost_basis (json permits 'Infinity'/1e400-overflow tokens) — the WHOLE migration
    must RAISE via the _assert_finite_money gate, not silently absorb it."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = [{**POSITIONS[0], "cost_basis": float("inf")}]
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(original))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.POSITIONS_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(ValidationError):
        command.upgrade(make_config(db_url), "head")
    eng.dispose_engine()


@pytest.mark.parametrize(
    "attr,filename,bad_payload",
    [
        ("POSITIONS_SOURCE", "positions.json", {"not": "a list"}),  # dict, not list
        ("ASSET_BRIEF_SOURCE", "asset_briefs.json", {"nope": True}),  # dict, not list
        ("WATCHLIST_SOURCE", "watchlist.json", ["not", "a", "dict"]),  # list, not dict
    ],
    ids=["positions-dict-not-list", "asset_brief-dict-not-list", "watchlist-list-not-dict"],
)
def test_wrong_top_level_shape_aborts_the_whole_migration(
    tmp_path, db_url, monkeypatch, attr, filename, bad_payload
):
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / filename
    path.write_text(json.dumps(bad_payload))
    monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError):
        command.upgrade(make_config(db_url), "head")
    eng.dispose_engine()


def test_export_mismatch_after_import_aborts_the_whole_migration(tmp_path, db_url, monkeypatch):
    """``_absorb_one``'s OTHER raise branch — ``export_fn(conn) != original`` AFTER a
    successful import — proven on ``watchlists`` (its own dedicated export function, so
    patching it cannot bleed into any other store's generic export call): the WHOLE
    migration must still RAISE (fail-closed, all-or-nothing), not silently accept the
    drifted store."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = dict(WATCHLIST)
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.WATCHLIST_SOURCE", path)
    monkeypatch.setattr(iio, "export_watchlist", lambda conn: {"drifted": True})
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError, match="round-trip verification FAILED") as exc_info:
        command.upgrade(make_config(db_url), "head")
    assert "watchlists" in str(exc_info.value)
    eng.dispose_engine()


def test_full_migration_absorbs_all_eight_and_renames(tmp_path, db_url, monkeypatch):
    files = _write_all_eight_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    for attr, (filename, _original) in files.items():
        src = tmp_path / filename
        assert not src.exists(), f"{attr}'s source must be renamed away, not left in place"
        assert len(list(tmp_path.glob(f"{filename}.migrated-*"))) == 1

    with eng.get_engine().connect() as conn:
        rows = {r.store: r for r in conn.execute(select(json_cutovers))}
    for store in INVESTMENT_STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_all_eight_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the eight investment files exist — head is reached, zero
    rows, zero cutover-ledger entries for the investment stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for _store, table, _fixture in _GENERIC_SPECS:
            assert iio.export_payload_list(conn, table) == []
        assert iio.export_watchlist(conn) == {}

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(INVESTMENT_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


# ── Up/down/up cycle ──────────────────────────────────────────────────────────────────


def test_up_down_up_cycle_is_reversible(db_url, tmp_path, monkeypatch):
    """Structural reversibility of the SCHEMA CHAIN, isolated from data-absorption
    correctness (already fully proven by the tests above): every one of the eight sources
    is explicitly pointed at a nonexistent file, so both upgrade passes are clean empty
    absorbs — the down step (to 0010, dropping the 8 investment tables via 0011's own
    downgrade; revision 0012's OWN downgrade() is a documented no-op — see that revision)
    has nothing to lose, and re-upgrading cannot collide on a stale ``json_cutovers``
    primary key from a first pass that inserted zero rows. Uses its own throwaway
    ``create_engine`` probes for inspection (never the app singleton) around the
    downgrade step, so there is no question of a stale cached engine."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command
    from sqlalchemy import create_engine, inspect

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from tests.db.conftest import reversible_tip

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, reversible_tip(db_url))  # head's chain refuses a downgrade past it

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "positions" in names and "watchlists" in names and "asset_brief" in names
    finally:
        probe.dispose()

    command.downgrade(cfg, "202608081826_planning_cutover")

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "positions" not in names and "watchlists" not in names
        assert "asset_brief" not in names
    finally:
        probe.dispose()

    command.upgrade(cfg, "head")  # re-absorbs (still nothing to absorb) — must not raise

    eng.dispose_engine()
    with eng.get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == []
        assert iio.export_watchlist(conn) == {}
    eng.dispose_engine()
