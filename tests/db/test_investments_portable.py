"""Investment stores join portable export/import, ``/api/reset``, and boot seeding (plan
4b, task 6) — closing the deferred ``/api/reset`` gap Wave B's tasks 2/3 flagged: reset used
to write dead ``positions.json``/``lots.json``/... files (nothing reads them post-cutover)
and never cleared the DB rows for any of the 8 investment tables.

Extends ``tests/db/test_planning_portable.py`` (plan 4a's own bridge, the closest precedent
— same "no cross-file FK" shape) to the EIGHT investment stores — ``positions``, ``lots``,
``grants``, ``vest_events``, ``option_contracts``, ``realized_events``, ``watchlists``,
``asset_brief`` — sourced from ``backend.db.investments_io`` (task 5) via
``backend.db.portable_bridge``. Like planning, NOT like money:

1. **Undoable** — ``investments_io``'s ``import_payload_list``/``import_watchlist`` fire
   ``core.io.fire_db_write_hook`` unconditionally (task 5), so a restore through
   ``ai.checkpoint`` reverts an investment import exactly like a money/planning one.
2. **No cross-file FK between investment tables** (``tables.py``'s own investments-block
   comment — a lot's ``position_id``, a realized event's ``consumptions[].lot_id``, a vest
   event's ``grant_id`` are opaque, Python-filtered ids, never a SQL FK; every table's only
   real FK is ``book_id -> books.id``, which ``wipe_all`` never touches), so all seven
   BRIDGED entries ride the GENERIC per-file ``services.portability._apply_bridged_import``
   loop — no ``apply_money_bundle``-style group orchestration.

THE 8-STORE CLASSIFICATION (this task's controller-mandated deliverable — see the task
report for the full table): SEVEN are REAL, IRREPLACEABLE USER PORTFOLIO DATA — positions/
lots/grants/vest_events/option_contracts/realized_events are the user's holdings and
disposal/vesting history; ``watchlist`` is durable monitoring config (``WATCHLIST_JSON`` was
ALREADY portable pre-task, per ``core/portable.py``). All seven are bridged into ``BRIDGED``
(export/import) AND wired into ``_WIPE_FNS`` (reset). ``asset_brief`` (the eighth) is a
derived per-instrument LLM/search-synthesis CACHE (``asset_brief_store.py``'s own docstring:
"rebuilt by POST /api/intel/assets/{id}/refresh"), checkpoint-EXCLUDED
(``ai.checkpoint._EXCLUDED_TABLES``) and deliberately ABSENT from both ``core.portable.
PORTABLE_FILES`` and ``portable_bridge.BRIDGED`` — wipe-only (``_WIPE_FNS`` still reaches it,
so ``/api/reset`` clears it — a stale brief for a since-reset instance is worse than none),
never exported, never imported, never backed up, exactly like ``evidence.json``/
``price_history.json``'s own cache precedent.

PORTABLE_FILES CHECK (the Wave-A-T7 lesson, applied proactively — confirmed here, not caught
by a review round): all seven portable investment filenames (``POSITIONS_JSON``,
``LOTS_JSON``, ``GRANTS_JSON``, ``VEST_EVENTS_JSON``, ``REALIZED_EVENTS_JSON``,
``OPTION_CONTRACTS_JSON``, ``WATCHLIST_JSON``) were ALREADY members of ``core.portable.
PORTABLE_FILES`` before this task touched anything (tasks 1-5 never needed to edit
``portable.py`` — verified in the task report) — so the pre-wipe backup loop
(``api/setup.py::reset_all_data``, which iterates that exact list) only needed this task's
``BRIDGED``/``_WIPE_FNS`` registration to start actually reaching each store's real DB rows.
``ASSET_BRIEF_JSON`` is correctly ABSENT from that manifest (a cache). A bridged-but-not-
portable REAL-user-data store would be the exact silent-data-loss Blocker Wave A's T7 fix
round 1 caught for ``income_events.json`` — this module's
``test_seven_investment_files_are_live_in_portable_files`` is the direct, unnarrowed-manifest
proof that never happened here, and
``test_reset_backup_snapshot_contains_portfolio_rows`` below is the end-to-end regression
test in the EXACT shape of Wave A's own ``test_reset_backup_snapshot_contains_planning_rows``
(RED before this task's ``BRIDGED`` registration — ``export_bytes_for`` falls through to
``path.exists()``, which is False post-cutover, so the backup silently omits the store
entirely; GREEN after).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.core.errors import ValidationError
from backend.db import investments_io as iio
from backend.db import portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import (
    asset_brief,
    grants,
    lots,
    option_contracts,
    positions,
    realized_events,
    vest_events,
    watchlists,
)
from backend.services import portability as svc

ALL_INVESTMENT_FILES = (
    "positions.json",
    "lots.json",
    "grants.json",
    "vest_events.json",
    "option_contracts.json",
    "realized_events.json",
    "asset_briefs.json",
    "watchlist.json",
)
# LIVE in core.portable.PORTABLE_FILES + bridged for export/import: every investment file
# except the asset_brief cache.
LIVE_INVESTMENT_FILES = tuple(f for f in ALL_INVESTMENT_FILES if f != "asset_briefs.json")

# ── Fixtures — small but non-trivial (mirrors test_investments_cutover.py's own shapes,
# task 5's proven-valid round-trip fixtures, trimmed to one row per store) ────────────────

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
    }
]

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
        "txn_id": None,
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
        "tranches": [],
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
        "news": [
            {
                "title": "Apple hits new high",
                "source": "Reuters",
                "url": "https://example.com/a",
                "published_at": "2026-08-01T00:00:00Z",
                "image": None,
            }
        ],
        "key_issues": [],
        "facts": {"ceo": "Tim Cook"},
        "peers": [],
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


def _seed_seven_bridged(conn) -> None:
    iio.import_payload_list(conn, positions, POSITIONS, store="positions")
    iio.import_payload_list(conn, lots, LOTS, store="lots")
    iio.import_payload_list(conn, grants, GRANTS, store="grants")
    iio.import_payload_list(conn, vest_events, VEST_EVENTS, store="vest_events")
    iio.import_payload_list(conn, option_contracts, OPTION_CONTRACTS, store="option_contracts")
    iio.import_payload_list(conn, realized_events, REALIZED_EVENTS, store="realized_events")
    iio.import_watchlist(conn, WATCHLIST)


def _seed_asset_brief(conn) -> None:
    iio.import_payload_list(
        conn, asset_brief, ASSET_BRIEFS, id_field="instrument_id", store="asset_brief"
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors test_planning_portable.py's/test_money_portability.py's
    own identical helper."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries = {}
        for name, data in files.items():
            zf.writestr(name, data)
            entries[name] = {"sha256": _sha(data)}
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": entries}),
        )
    return buf.getvalue()


# ── The registry itself: seven bridged + wipe-able, asset_brief wipe-only ────────────────


def test_seven_investment_filenames_are_bridged_with_wipe_fns():
    for filename in LIVE_INVESTMENT_FILES:
        assert filename in portable_bridge.BRIDGED, f"{filename} missing from BRIDGED"
        assert filename in portable_bridge._WIPE_FNS, f"{filename} missing from _WIPE_FNS"
        bridge = portable_bridge.BRIDGED[filename]
        assert callable(bridge.export_fn)
        assert callable(bridge.import_fn)


def test_asset_brief_is_wiped_but_not_bridged():
    """asset_briefs.json (task 5's real, absorbed filename — plural, ``core.paths.
    ASSET_BRIEF_JSON.name`` and ``investments_io.INVESTMENT_PAYLOAD_SPECS`` both confirm
    the spelling) is wipe-only: ``/api/reset`` must clear it (a stale brief for a
    since-reset instance is worse than none), but it has no export/import role at all —
    no bundle round-trip, no backup — exactly like evidence.json/price_history.json's own
    cache precedent (portable_bridge.py's module docstring)."""
    assert "asset_briefs.json" not in portable_bridge.BRIDGED
    assert "asset_briefs.json" in portable_bridge._WIPE_FNS


def test_investment_files_constant_matches_the_bridge_registry_minus_asset_brief():
    """investments_io.INVESTMENT_FILES (task 5) names all EIGHT investment filenames; this
    task's BRIDGED reaches exactly seven of them — every one except the cache. Consumes,
    never redefines, task 5's own list."""
    assert set(iio.INVESTMENT_FILES) == set(ALL_INVESTMENT_FILES)
    seven = set(iio.INVESTMENT_FILES) - {"asset_briefs.json"}
    assert seven == set(LIVE_INVESTMENT_FILES)
    for filename in seven:
        assert filename in portable_bridge.BRIDGED


def test_seven_investment_files_are_live_in_portable_files():
    """Direct, unnarrowed-manifest proof of the module docstring's PORTABLE_FILES CHECK —
    the exact check Wave A's T7 fix round 1 caught missing for income_events.json,
    confirmed here proactively rather than by a later review round."""
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in LIVE_INVESTMENT_FILES:
        assert filename in names, f"{filename} expected LIVE in PORTABLE_FILES"
    assert "asset_briefs.json" not in names


# ── Export: DB-sourced, under the original filenames ──────────────────────────────────


def test_export_bundle_contains_the_seven_live_investment_files_db_sourced(db_isolated):
    with get_engine().begin() as conn:
        _seed_seven_bridged(conn)
        _seed_asset_brief(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert set(LIVE_INVESTMENT_FILES) <= names
        assert "asset_briefs.json" not in names, "the cache must never travel in an export bundle"
        assert json.loads(zf.read("positions.json")) == POSITIONS
        assert json.loads(zf.read("lots.json")) == LOTS
        assert json.loads(zf.read("grants.json")) == GRANTS
        assert json.loads(zf.read("vest_events.json")) == VEST_EVENTS
        assert json.loads(zf.read("option_contracts.json")) == OPTION_CONTRACTS
        assert json.loads(zf.read("realized_events.json")) == REALIZED_EVENTS
        assert json.loads(zf.read("watchlist.json")) == WATCHLIST
        positions_bytes = zf.read("positions.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly, like every
    # other bridged store's own equivalent assertion.
    with get_engine().connect() as conn:
        expected = json.dumps(
            iio.export_payload_list(conn, positions), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert positions_bytes == expected


# ── Import: representative bundle (incl. the dict-shaped watchlist singleton, and
# asset_brief seeded alongside to prove the cache survives an /api/data/import untouched) ──────


def test_investment_bundle_round_trips_through_bridge(db_isolated):
    """The representative bundle (ambiguity-resolution item a): all seven bridged stores
    incl. the dict-shaped watchlist singleton, PLUS asset_brief seeded alongside (through
    investments_io directly, never through the bridge — it has no Bridge entry) to prove
    the cache survives an /api/data/import untouched: apply_import's generic loop only ever
    reaches core.portable.PORTABLE_FILES members, and asset_briefs.json is not one."""
    with get_engine().begin() as conn:
        _seed_seven_bridged(conn)
        _seed_asset_brief(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= set(LIVE_INVESTMENT_FILES)
    assert result["row_counts"]["positions.json"] == len(POSITIONS)
    assert result["row_counts"]["lots.json"] == len(LOTS)
    assert result["row_counts"]["grants.json"] == len(GRANTS)
    assert result["row_counts"]["vest_events.json"] == len(VEST_EVENTS)
    assert result["row_counts"]["option_contracts.json"] == len(OPTION_CONTRACTS)
    assert result["row_counts"]["realized_events.json"] == len(REALIZED_EVENTS)
    assert result["row_counts"]["watchlist.json"] == 1

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == POSITIONS
        assert iio.export_payload_list(conn, lots) == LOTS
        assert iio.export_payload_list(conn, grants) == GRANTS
        assert iio.export_payload_list(conn, vest_events) == VEST_EVENTS
        assert iio.export_payload_list(conn, option_contracts) == OPTION_CONTRACTS
        assert iio.export_payload_list(conn, realized_events) == REALIZED_EVENTS
        assert iio.export_watchlist(conn) == WATCHLIST
        # asset_brief is untouched by /api/data/import — not in PORTABLE_FILES, so the generic
        # loop never reaches it. Proves the cache-exclusion end to end, not just by
        # registry inspection.
        assert iio.export_payload_list(conn, asset_brief) == ASSET_BRIEFS


def test_import_synthetic_old_bundle_lands_rows_for_list_and_dict_stores(db_isolated):
    """A hand-built bundle stands in for one exported BEFORE these stores were migrated —
    old and new bundles are handled identically, by filename alone (mirrors
    test_planning_portable.py's/test_money_portability.py's identical-shaped test)."""
    bundle = _build_bundle(
        {
            "positions.json": json.dumps(POSITIONS).encode(),
            "grants.json": json.dumps(GRANTS).encode(),
            "watchlist.json": json.dumps(WATCHLIST, ensure_ascii=False).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert result["row_counts"]["positions.json"] == len(POSITIONS)
    assert result["row_counts"]["grants.json"] == len(GRANTS)
    assert result["row_counts"]["watchlist.json"] == 1

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == POSITIONS
        assert iio.export_payload_list(conn, grants) == GRANTS
        assert iio.export_watchlist(conn) == WATCHLIST


def test_reimport_replaces_existing_investment_data_entirely(db_isolated):
    with get_engine().begin() as conn:
        iio.import_payload_list(conn, lots, LOTS, store="lots")

    new_lots = [{**LOTS[0], "id": "lot_2", "remaining_qty": 3.0}]
    bundle = _build_bundle({"lots.json": json.dumps(new_lots).encode()})
    result = svc.apply_import(bundle)

    assert result["row_counts"]["lots.json"] == len(new_lots)
    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, lots) == new_lots


def test_import_absent_investment_file_wipes_that_store_to_empty(db_isolated):
    with get_engine().begin() as conn:
        iio.import_payload_list(conn, grants, GRANTS, store="grants")

    bundle = _build_bundle({"positions.json": json.dumps(POSITIONS).encode()})
    # grants.json intentionally absent.
    result = svc.apply_import(bundle)

    assert "grants.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, grants) == []


def test_bundle_containing_asset_brief_file_is_rejected_as_unknown(db_isolated):
    """asset_briefs.json in a hand-built bundle gets the SAME treatment any other
    non-portable filename already gets (mirrors evidence.json's own precedent,
    test_bundle_containing_a_dormant_planning_file_is_rejected_as_unknown for planning) —
    a cache must never be accepted as a bundle entry."""
    bundle = _build_bundle({"asset_briefs.json": json.dumps(ASSET_BRIEFS).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("asset_briefs.json" in e and "unknown" in e for e in report["errors"])

    with pytest.raises(ValidationError):
        svc.apply_import(bundle)


def test_corrupt_investment_bundle_import_rolls_back_atomically(db_isolated):
    """A positions entry with a non-finite cost_basis fails loudly
    (investments_io._guard_money, task 5's FINITE-MONEY GUARD) and the whole store's
    transaction — wipe included — rolls back untouched, mirroring
    test_corrupt_planning_bundle_import_rolls_back_atomically."""
    with get_engine().begin() as conn:
        iio.import_payload_list(conn, positions, POSITIONS, store="positions")

    corrupt = [{**POSITIONS[0], "id": "pos_bad", "cost_basis": float("inf")}]
    bundle = _build_bundle({"positions.json": json.dumps(corrupt).encode()})

    with pytest.raises(ValidationError):
        svc.apply_import(bundle)

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == POSITIONS


# ── /api/reset — every one of the eight investment tables, incl. the asset_brief cache ──


def test_reset_wipes_all_eight_investment_tables(db_isolated):
    with get_engine().begin() as conn:
        _seed_seven_bridged(conn)
        _seed_asset_brief(conn)

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == []
        assert iio.export_payload_list(conn, lots) == []
        assert iio.export_payload_list(conn, grants) == []
        assert iio.export_payload_list(conn, vest_events) == []
        assert iio.export_payload_list(conn, option_contracts) == []
        assert iio.export_payload_list(conn, realized_events) == []
        assert iio.export_watchlist(conn) == {}
        assert iio.export_payload_list(conn, asset_brief) == []


def test_watchlists_and_asset_brief_wipes_use_the_right_pk_column(db_isolated):
    """Sanity check for the two non-generic-'id'-flow wipe fns: watchlists' PK column IS
    named book_id (a per-book singleton, like plan_state); asset_brief's PK column is
    payload_table's generic 'id' (holding the instrument_id VALUE — id_field is a
    store-level read/write alias, never a real column name). A wrong pk_name would raise
    AttributeError on the first seeded row rather than silently doing the wrong thing."""
    with get_engine().begin() as conn:
        iio.import_watchlist(conn, WATCHLIST)
        _seed_asset_brief(conn)

    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "watchlist.json")
        portable_bridge.wipe(conn, "asset_briefs.json")

    with get_engine().connect() as conn:
        assert conn.execute(select(watchlists)).all() == []
        assert conn.execute(select(asset_brief)).all() == []


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors test_planning_portable.py's/
    test_money_portability.py's own identical helper."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import paths as bpaths
    from backend.api import setup as setup_api

    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    profile = data / "profile.json"
    profile.write_text(json.dumps({"name": "Alex"}))

    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile])
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)

    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app), data


def test_reset_endpoint_wipes_investment_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    with get_engine().begin() as conn:
        iio.import_payload_list(conn, positions, POSITIONS, store="positions")
        iio.import_watchlist(conn, WATCHLIST)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == []
        assert iio.export_watchlist(conn) == {}


def test_reset_backup_snapshot_contains_portfolio_rows(db_isolated, tmp_path, monkeypatch):
    """THE DATA-LOSS REGRESSION TEST (ambiguity-resolution item c) — the exact shape of
    Wave A T7's own ``test_reset_backup_snapshot_contains_planning_rows``, which itself
    reproduced ``docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md``'s
    original Blocker for price_history/instruments and Plan 3 Task 6's repeat for
    TXN_AUDIT_JSON/MERGE_JOURNAL_JSON: ``/api/reset``'s pre-wipe backup loop
    (``api/setup.py::reset_all_data``) calls ``export_bytes_for(src)`` for every
    ``core.portable.PORTABLE_FILES`` entry — which only reaches a DB-backed store's REAL
    rows once that filename is ALSO a ``portable_bridge.BRIDGED`` key. Positions/lots are
    post-cutover (no file on disk — the DB is the only source of truth since plan 4b task
    2), seeded with real portfolio rows, reset is triggered, and the backup snapshot must
    contain them.

    RED before this task's BRIDGED registration: ``export_bytes_for`` falls through to
    ``path.exists()`` (False post-cutover — no file), returns None, and the backup loop
    silently OMITS positions/lots/grants/vest_events/option_contracts/realized_events/
    watchlist entirely — a self-hoster hitting reset would have their actual portfolio
    holdings destroyed with NO backup and NO undo path (reset opens no checkpoint turn).
    GREEN after — this is the literal RED-then-GREEN proof this task's TDD loop ran."""
    from backend.paths import (
        GRANTS_JSON,
        LOTS_JSON,
        OPTION_CONTRACTS_JSON,
        POSITIONS_JSON,
        REALIZED_EVENTS_JSON,
        VEST_EVENTS_JSON,
        WATCHLIST_JSON,
    )

    with get_engine().begin() as conn:
        _seed_seven_bridged(conn)

    client, data = _reset_client(tmp_path, monkeypatch)
    # This test's whole point is the bridged-file backup path, so extend the narrowed
    # manifest _reset_client set up with the seven live bridged investment filenames (real
    # Path identities — only .name matters for a bridged lookup, exactly like Wave A T7's
    # own equivalent test; tests/conftest.py redirects DATA_DIR to a throwaway temp root
    # before ANY backend module is imported, so these Path objects can never resolve
    # into the live ~/.open-net-worth tree even before the fix is implemented).
    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [
            *portable.PORTABLE_FILES,
            POSITIONS_JSON,
            LOTS_JSON,
            GRANTS_JSON,
            VEST_EVENTS_JSON,
            OPTION_CONTRACTS_JSON,
            REALIZED_EVENTS_JSON,
            WATCHLIST_JSON,
        ],
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    positions_backup = backup_dir / "positions.json"
    lots_backup = backup_dir / "lots.json"
    assert positions_backup.exists(), (
        "positions.json missing from the pre-reset backup — a self-hoster's actual "
        "portfolio holdings would be destroyed with no recovery path"
    )
    assert lots_backup.exists(), (
        "lots.json missing from the pre-reset backup — cost-basis/FIFO lot history would "
        "be destroyed with no recovery path"
    )
    assert json.loads(positions_backup.read_text()) == POSITIONS
    assert json.loads(lots_backup.read_text()) == LOTS
    assert json.loads((backup_dir / "grants.json").read_text()) == GRANTS
    assert json.loads((backup_dir / "vest_events.json").read_text()) == VEST_EVENTS
    assert json.loads((backup_dir / "option_contracts.json").read_text()) == OPTION_CONTRACTS
    assert json.loads((backup_dir / "realized_events.json").read_text()) == REALIZED_EVENTS
    assert json.loads((backup_dir / "watchlist.json").read_text()) == WATCHLIST
    assert not (backup_dir / "asset_briefs.json").exists(), "the cache must never be backed up"

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, positions) == []
        assert iio.export_payload_list(conn, lots) == []


# ── Undoable investment imports (this task's binding requirement) ─────────────────────


def test_investment_import_is_undoable_via_checkpoint_restore(db_isolated):
    """At least one investment store's import is undoable: the write_log before-images a
    checkpoint turn collects let restore() revert it. Mirrors
    test_planning_import_is_undoable_via_checkpoint_restore's minimal pattern (direct
    ai.checkpoint begin_turn/end_turn/restore — the file AND DB write hooks are already
    registered at backend.ai.checkpoint's own module-import time)."""
    with get_engine().begin() as conn:
        iio.import_payload_list(conn, lots, LOTS, store="lots")

    new_lots = [{**LOTS[0], "id": "lot_2", "remaining_qty": 1.0}]
    bundle = _build_bundle({"lots.json": json.dumps(new_lots).encode()})

    turn_ctx, token = ckpt.begin_turn("sess-inv-t6-undo", anchor_history_len=0, message_preview="i")
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "lots" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, lots) == new_lots

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert iio.export_payload_list(conn, lots) == LOTS


# ── Seeding: positions/lots (task 2's _seed_investments_db) + watchlist (task 3's
# _seed_watchlist_db) actually write the DB ────────────────────────────────────────────


def test_first_boot_seeds_default_watchlist_into_db(db_isolated):
    """WatchlistStore self-seeds its default watchlist on first _load() (no main.py seed
    block needed, unlike every _seed_*_db counterpart) — a fresh, migrated, row-less DB's
    very first read must still yield the auto-managed my_holdings singleton."""
    from backend.db.watchlist_store import MY_HOLDINGS_ID, WatchlistStore

    assert any(wl["id"] == MY_HOLDINGS_ID for wl in WatchlistStore().list_watchlists())


def test_restoring_an_older_export_takes_topics_back_out_of_watchlists(db_isolated):
    """ADR 2026-09-27-topics-are-followed-not-listed: an export from before the rule must not
    bring back a topic filed in a list, a duplicate topic, or the topics-only list."""
    import copy

    old = copy.deepcopy(WATCHLIST)
    old["watchlists"].append({**old["watchlists"][1], "id": "wl_i", "name": "Intelligence"})
    old["items"] += [
        {"id": "t1", "type": "topic", "value": "Fed", "watchlist_ids": ["wl_i"]},
        {"id": "t2", "type": "topic", "value": "fed", "watchlist_ids": ["wl_growth"]},
    ]
    with get_engine().begin() as conn:
        iio.import_watchlist(conn, old)
    with get_engine().connect() as conn:
        restored = iio.export_watchlist(conn)
    assert [w["id"] for w in restored["watchlists"]] == ["my_holdings", "wl_growth"]
    topics = [i for i in restored["items"] if i["type"] == "topic"]
    assert [(t["id"], t["watchlist_ids"]) for t in topics] == [("t1", [])]
    assert len(old["items"]) == 3  # the caller's payload is never mutated
