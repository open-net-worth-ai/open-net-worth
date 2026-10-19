"""Config stores join portable export/import, ``/api/reset``, and boot seeding (plan 4d,
task 6) — the last Wave-D task before the security gate.

Extends ``tests/db/test_planning_portable.py``/``tests/db/test_investments_portable.py`` to
the ten config stores — ``payees``, ``automations``, ``alerts``, ``dashboard_layout``,
``memory``, ``news_sources``, ``statement_formats``, ``signals``, plus ``memory_settings``/
``preferred_models`` (two stores sharing ONE ``settings`` table) — sourced from
``backend.db.config_io`` via ``backend.db.portable_bridge``. A per-store SPLIT along the
portable axis, not one wave-wide combination (see ``portable_bridge.py``'s own module
docstring, PLAN 4D TASK 6 section):

1. **SIX join BRIDGED**, undoable, no cross-file FK — ``payees``/``automations``/``alerts``
   (Tier-1, ``book_id=DEFAULT_BOOK_ID``), ``dashboard_layout`` (dict-shaped per-book
   singleton), ``memory``/``news_sources`` (Tier-2/3, no ``book_id``). All six ride the
   GENERIC per-file ``services.portability._apply_bridged_import`` loop and were ALREADY LIVE
   in ``core.portable.PORTABLE_FILES`` before this task touched anything — the load-bearing
   safety check this task's brief calls out explicitly (the exact silent-data-loss bug class
   Waves A/B caught for ``income_events.json``). ``intel_signals`` was originally this wave's
   SEVENTH bridged config store; migration 202608301500 (RFC intel-signals-data-model) split
   its table into ``intel_stories``/``signal_timeline_entries``/``daily_digests`` and removed
   the ``"intel_signals.json"`` config-bridge entry outright — its OWN bespoke bridge
   (``"intel_stories.json"``, sourced from ``backend.db.intel_signals_io``, NOT
   ``config_io``) is covered by ``tests/db/test_portability_db_bridge.py`` instead, not here.
2. **FOUR are wipe-only, never bridged** — ``memory_settings``, ``preferred_models``,
   ``statement_formats``, ``signals``. ``/api/reset`` clears them (config/cache — "reset
   means reset"), correctly ABSENT from ``PORTABLE_FILES`` (no backup, no re-import).
   ``signals`` is ALSO checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``, added in
   plan 4d task 1 — BEFORE this task); its wipe fn fires the hook regardless (uniform with
   every sibling), the exclusion alone makes the capture a structural no-op, mirroring
   ``corrections``' own precedent.

``memory_settings``/``preferred_models`` are the one new wrinkle: two config STORES sharing
one physical ``settings`` table, keyed by ``namespace``. Each gets its own filename-keyed wipe
fn that touches ONLY its own row — proven directly below, not just asserted.

NO money-guard tests this wave (``config_io.py``'s own module docstring — none of the eleven
stores holds a net-worth-bearing amount) — the corrupt-bundle rollback test instead uses a
duplicate-id structural conflict (a real ``IntegrityError``), the one failure mode this
domain's un-guarded ``import_payload_list`` can actually hit.

``tool_grants`` note (2026-08-14, owner ruling): this wave originally shipped a
``tool_grants`` store here too (a fifth wipe-only entry). Revision 0040 (part of a LATER,
unrelated wave — the native-permission-enforcement merge, not this one) absorbs the real
``tool_grants`` TABLE into ``tool_permissions`` and DROPS it; per the owner's "delete rather
than deprecate" ruling, revision 0016's own FROM-FILES cutover job for it was removed
outright rather than preserved for an upgrade path that cannot apply here (see
``tests/db/test_configs_cutover.py``). So ``tool_grants``/``tool_grants.json`` are ABSENT
from every constant and test below — there is nothing left in this wave's own scope to seed,
bridge, or wipe under that name. ``tool_permissions.json`` is its successor's OWN wipe-only
entry, outside this wave entirely — covered by ``tests/db/test_tool_permission_store.py``.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.core.errors import ValidationError
from backend.db import config_io as cio
from backend.db import portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    alerts,
    automations,
    dashboard_layouts,
    memory,
    news_sources,
    notification_log,
    payees,
    settings,
    statement_formats,
)
from backend.services import portability as svc

ALL_CONFIG_FILES = (
    "payees.json",
    "automations.json",
    "alerts.json",
    "memory.json",
    "statement_formats.json",
    "news_sources.json",
    "signals.json",
    "memory_settings.json",
    "preferred_models.json",
    "dashboard_layout.json",
)

# The six bridged (portable) config filenames — exported/imported through the bundle,
# reachable by /api/reset via BRIDGED's own loop.
BRIDGED_CONFIG_FILES = (
    "payees.json",
    "automations.json",
    "alerts.json",
    "memory.json",
    "news_sources.json",
    "dashboard_layout.json",
)

# The four wipe-only config filenames — /api/reset clears them, but none carries an
# export/import role (module docstring, PLAN 4D TASK 6 section, point 2).
WIPE_ONLY_CONFIG_FILES = (
    "memory_settings.json",
    "preferred_models.json",
    "statement_formats.json",
    "signals.json",
)

assert set(BRIDGED_CONFIG_FILES) | set(WIPE_ONLY_CONFIG_FILES) == set(ALL_CONFIG_FILES)

# ── Fixtures — small but non-trivial (mirrors test_configs_cutover.py's own shapes, task
# 5's proven-valid round-trip fixtures, kept byte-identical to that file for cross-wave
# consistency) ──────────────────────────────────────────────────────────────────────────

PAYEES = [
    {
        "id": "pay_1",
        "name": "Anthropic",
        "default_categories": {"expense": "cat_software"},
        "autocategorize": True,
        "hidden": False,
        "aliases": ["anthropic", "anthropic pbc"],
        "rules": [{"match_type": "contains", "value": "ANTHROPIC"}],
        "last_used": "2026-08-01",
        "created_at": "2026-06-01T00:00:00Z",
    },
    {
        "id": "pay_2",
        "name": "café ☕ payee",
        "default_categories": {},
        "autocategorize": True,
        "hidden": False,
        "aliases": [],
        "rules": [],
        "last_used": None,
        "created_at": "2026-06-02T00:00:00Z",
    },
]

AUTOMATIONS = [
    {
        "id": "automation_1",
        "name": "Categorize rent",
        "description": "",
        "instructions": "Set rent category automatically",
        "status": "active",
        "trigger": {"kind": "event", "event": "transaction.created"},
        "trigger_revision": "triggerrev_1",
        "execution": {"kind": "lucius"},
        "permission_mode": "manual",
        "always_allowed": [{"src": "tool", "desc": "categorize", "tools": ["update_transactions"]}],
        "model": "claude-haiku-4-5",
        "effort": "medium",
        "max_runs": None,
        "run_count": 3,
        "next_run_at": None,
        "last_run_at": "2026-08-01T00:00:00Z",
        "last_status": "completed",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "provenance": {},
        "stats": {},
    }
]

ALERTS = [
    {
        "id": "alert_1",
        "ticker": "AAPL",
        "instrument_id": "inst_1",
        "op": ">=",
        "value": 250.0,
        "note": "sell target",
        "label": "AAPL high",
        "asset_class": "equity",
        "created_at": "2026-06-01T00:00:00Z",
    }
]

MEMORY = [
    {
        "id": "mem_1",
        "tier": "durable",
        "source": "explicit",
        "text": "Prefers SGD as base currency",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
        "expires_at": None,
    }
]

STATEMENT_FORMATS = [
    {
        "id": "fmt_1",
        "fingerprint": "sha256:abc123",
        "kind": "tabular",
        "source_label": "Meridian Bank CSV",
        "origin": "inferred",
        "schema": {"date": 0, "amount": 1, "description": 2},
        "instructions": "",
        "account_ids": ["acc_1"],
        "version": 1,
        "uses": 5,
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "last_used_at": "2026-08-01T00:00:00Z",
    }
]

NEWS_SOURCES = [
    {
        "id": "src_ft_companies",
        "label": "FT Companies",
        "kind": "outlet",
        "transport": "poll",
        "url": "https://ft.com/companies/rss",
        "resolver": "",
        "scope": "firehose",
        "match": "server_tokens",
        "cadence": "fast",
        "via": "rss:ft_companies",
        "enabled": True,
        "min_interval_sec": 300,
        "markets": [],
        "provenance": "builtin",
        "notes": "",
        "added_at": "2026-06-01T00:00:00Z",
    }
]

# signals: NEWEST-FIRST on disk (prepend order) — the ORDER FIX fixture, distinct
# timestamps so direction is actually provable.
SIGNALS_NEWEST_FIRST = [
    {
        "id": "sig_3",
        "created_at": "2026-08-03T00:00:00",
        "type": "news",
        "severity": "info",
        "title": "third (newest)",
        "summary": "",
        "portfolio_impact": "",
        "action_items": [],
        "affected_tickers": ["AAPL"],
        "sources": [],
        "source_urls": [],
        "read": False,
        "trigger": "scheduled",
    },
    {
        "id": "sig_2",
        "created_at": "2026-08-02T00:00:00",
        "type": "news",
        "severity": "info",
        "title": "second",
        "summary": "",
        "portfolio_impact": "",
        "action_items": [],
        "affected_tickers": [],
        "sources": [],
        "source_urls": [],
        "read": False,
        "trigger": "scheduled",
    },
    {
        "id": "sig_1",
        "created_at": "2026-08-01T00:00:00",
        "type": "news",
        "severity": "info",
        "title": "first (oldest)",
        "summary": "",
        "portfolio_impact": "",
        "action_items": [],
        "affected_tickers": [],
        "sources": [],
        "source_urls": [],
        "read": True,
        "trigger": "scheduled",
    },
]

MEMORY_SETTINGS = {"enabled": False}
PREFERRED_MODELS = {"models": ["anthropic:claude-sonnet-4-6", "openai:gpt-5"]}
DASHBOARD_LAYOUT = {
    "activeViewId": "view_1",
    "views": [
        {
            "viewId": "view_1",
            "name": "Main",
            "widgets": [
                {
                    "instanceId": "widg_1",
                    "defId": "net_worth",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 2,
                    "config": {},
                }
            ],
        }
    ],
}


def _seed_six_bridged(conn) -> None:
    cio.import_payload_list(conn, payees, PAYEES, book_id=DEFAULT_BOOK_ID)
    cio.import_payload_list(conn, automations, AUTOMATIONS, book_id=DEFAULT_BOOK_ID)
    cio.import_payload_list(conn, alerts, ALERTS, book_id=DEFAULT_BOOK_ID)
    cio.import_payload_list(conn, memory, MEMORY)
    cio.import_payload_list(conn, news_sources, NEWS_SOURCES)
    cio.import_dashboard(conn, DASHBOARD_LAYOUT)


def _seed_four_wipe_only(conn) -> None:
    """Seeds every wipe-only store directly (``WIPE_ONLY_CONFIG_FILES``)."""
    cio.import_payload_list(conn, statement_formats, STATEMENT_FORMATS)
    cio.import_payload_list(conn, notification_log, SIGNALS_NEWEST_FIRST, reverse=True)
    cio.import_namespaced(conn, "memory_settings", MEMORY_SETTINGS)
    cio.import_namespaced(conn, "preferred_models", PREFERRED_MODELS)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors test_planning_portable.py's/test_investments_portable
    .py's own identical helper."""
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


# ── The registry itself: six bridged + wipe-able, four wipe-only ───────────────────────


def test_six_config_filenames_are_bridged_with_wipe_fns():
    for filename in BRIDGED_CONFIG_FILES:
        assert filename in portable_bridge.BRIDGED, f"{filename} missing from BRIDGED"
        assert filename in portable_bridge._WIPE_FNS, f"{filename} missing from _WIPE_FNS"
        bridge = portable_bridge.BRIDGED[filename]
        assert callable(bridge.export_fn)
        assert callable(bridge.import_fn)


def test_four_wipe_only_config_filenames_are_not_bridged():
    """memory_settings/preferred_models/statement_formats/signals are wipe-only —
    reachable by /api/reset (a _WIPE_FNS entry) but with no export/import role at all (no
    BRIDGED entry), mirroring asset_briefs.json's own wipe-only shape for a different
    underlying reason (module docstring)."""
    for filename in WIPE_ONLY_CONFIG_FILES:
        assert filename not in portable_bridge.BRIDGED, f"{filename} must not be bridged"
        assert filename in portable_bridge._WIPE_FNS, f"{filename} missing from _WIPE_FNS"
    # tool_permissions.json — outside this wave's own scope (module docstring) — is the
    # SAME shape (wipe-only, never bridged), checked here for cross-reference only; its
    # own coverage lives in tests/db/test_tool_permission_store.py.
    assert "tool_permissions.json" not in portable_bridge.BRIDGED
    assert "tool_permissions.json" in portable_bridge._WIPE_FNS


def test_six_config_files_are_live_in_portable_files():
    """THE load-bearing safety check this task's brief names explicitly — the exact
    silent-data-loss bug class Waves A/B caught (income_events.json): every bridged store
    /api/reset's wipe_all unconditionally reaches MUST also be in core.portable.
    PORTABLE_FILES, or its pre-wipe backup silently omits it. Direct, unnarrowed-manifest
    proof — not merely asserted true by a test that injects it into a local copy."""
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in BRIDGED_CONFIG_FILES:
        assert filename in names, f"{filename} expected LIVE in PORTABLE_FILES"


def test_four_wipe_only_files_are_absent_from_portable_files():
    """The four wipe-only config filenames are correctly ABSENT from PORTABLE_FILES
    (wiped, not backed up) — that is intended (module docstring, point 2), not an
    oversight, so pin it as a real assertion rather than leaving it implicit."""
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in WIPE_ONLY_CONFIG_FILES:
        assert filename not in names, f"{filename} expected to stay OUT of PORTABLE_FILES"


def test_config_files_constant_matches_the_bridge_registry():
    """config_io.CONFIG_FILES (task 5) names the ELEVEN filenames the FROZEN revision 0016
    cutover absorbed — historical and permanent, never edited for a later change (that
    module's own docstring: "not on the write path... survive as test-pinned cross-checks
    that the cutover covers exactly this set"). "intel_signals.json" is the one name in
    that frozen record no longer reachable through EITHER BRIDGED or _WIPE_FNS today: its
    table split into intel_stories/signal_timeline_entries/daily_digests at revision
    202608301500 (RFC intel-signals-data-model) and the config-bridge entry was removed
    outright — the historical absorb-scope record and the live bridge registry are allowed
    to diverge on exactly this one name. Every one of the other ten still lines up exactly,
    split along the bridged/wipe-only line this module's docstring describes."""
    assert set(cio.CONFIG_FILES) == set(ALL_CONFIG_FILES) | {"intel_signals.json"}
    for filename in ALL_CONFIG_FILES:
        assert filename in portable_bridge._WIPE_FNS
    for filename in BRIDGED_CONFIG_FILES:
        assert filename in portable_bridge.BRIDGED
    for filename in WIPE_ONLY_CONFIG_FILES:
        assert filename not in portable_bridge.BRIDGED
    assert "intel_signals.json" not in portable_bridge.BRIDGED
    assert "intel_signals.json" not in portable_bridge._WIPE_FNS


# ── Export: DB-sourced, under the original filenames ──────────────────────────────────


def test_export_bundle_contains_the_six_live_config_files_db_sourced(db_isolated):
    with get_engine().begin() as conn:
        _seed_six_bridged(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert set(BRIDGED_CONFIG_FILES) <= names
        for filename in WIPE_ONLY_CONFIG_FILES:
            assert filename not in names, f"{filename} must never travel in an export bundle"
        assert json.loads(zf.read("payees.json")) == PAYEES
        assert json.loads(zf.read("automations.json")) == AUTOMATIONS
        assert json.loads(zf.read("alerts.json")) == ALERTS
        assert json.loads(zf.read("memory.json")) == MEMORY
        assert json.loads(zf.read("news_sources.json")) == NEWS_SOURCES
        assert json.loads(zf.read("dashboard_layout.json")) == DASHBOARD_LAYOUT
        payees_bytes = zf.read("payees.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly, like every
    # other bridged store's own equivalent assertion.
    with get_engine().connect() as conn:
        expected = json.dumps(
            cio.export_payload_list(conn, payees), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert payees_bytes == expected


# ── Import: representative bundle (incl. the dict-shaped dashboard_layout), old-bundle
# restore, absent-resets-to-empty ──────────────────────────────────────────────────────


def test_config_bundle_round_trips_through_bridge(db_isolated):
    """The representative bundle: five generic list stores + the one dict store."""
    with get_engine().begin() as conn:
        _seed_six_bridged(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= set(BRIDGED_CONFIG_FILES)
    assert result["row_counts"]["payees.json"] == len(PAYEES)
    assert result["row_counts"]["automations.json"] == len(AUTOMATIONS)
    assert result["row_counts"]["alerts.json"] == len(ALERTS)
    assert result["row_counts"]["memory.json"] == len(MEMORY)
    assert result["row_counts"]["news_sources.json"] == len(NEWS_SOURCES)
    assert result["row_counts"]["dashboard_layout.json"] == 1

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == PAYEES
        assert cio.export_payload_list(conn, automations) == AUTOMATIONS
        assert cio.export_payload_list(conn, alerts) == ALERTS
        assert cio.export_payload_list(conn, memory) == MEMORY
        assert cio.export_payload_list(conn, news_sources) == NEWS_SOURCES
        assert cio.export_dashboard(conn) == DASHBOARD_LAYOUT


def test_import_synthetic_old_bundle_lands_rows_for_list_and_dict_stores(db_isolated):
    """A hand-built bundle stands in for one exported BEFORE these stores were migrated —
    old and new bundles are handled identically, by filename alone (mirrors
    test_planning_portable.py's/test_investments_portable.py's identical-shaped test)."""
    bundle = _build_bundle(
        {
            "payees.json": json.dumps(PAYEES, ensure_ascii=False).encode(),
            "alerts.json": json.dumps(ALERTS).encode(),
            "dashboard_layout.json": json.dumps(DASHBOARD_LAYOUT).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert result["row_counts"]["payees.json"] == len(PAYEES)
    assert result["row_counts"]["alerts.json"] == len(ALERTS)
    assert result["row_counts"]["dashboard_layout.json"] == 1

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == PAYEES
        assert cio.export_payload_list(conn, alerts) == ALERTS
        assert cio.export_dashboard(conn) == DASHBOARD_LAYOUT


def test_reimport_replaces_existing_config_data_entirely(db_isolated):
    with get_engine().begin() as conn:
        cio.import_payload_list(conn, alerts, ALERTS, book_id=DEFAULT_BOOK_ID)

    new_alerts = [{**ALERTS[0], "id": "alert_2", "value": 300.0}]
    bundle = _build_bundle({"alerts.json": json.dumps(new_alerts).encode()})
    result = svc.apply_import(bundle)

    assert result["row_counts"]["alerts.json"] == len(new_alerts)
    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, alerts) == new_alerts


def test_import_absent_config_file_wipes_that_store_to_empty(db_isolated):
    with get_engine().begin() as conn:
        cio.import_payload_list(conn, alerts, ALERTS, book_id=DEFAULT_BOOK_ID)

    bundle = _build_bundle({"payees.json": json.dumps(PAYEES, ensure_ascii=False).encode()})
    # alerts.json intentionally absent.
    result = svc.apply_import(bundle)

    assert "alerts.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, alerts) == []


@pytest.mark.parametrize("filename", WIPE_ONLY_CONFIG_FILES)
def test_bundle_containing_a_wipe_only_config_file_is_rejected_as_unknown(db_isolated, filename):
    """Any of the four wipe-only filenames in a hand-built bundle gets the SAME treatment
    any other non-portable filename already gets (mirrors evidence.json's/asset_briefs.
    json's own precedent) — config/cache must never be accepted as a bundle entry."""
    bundle = _build_bundle({filename: json.dumps({"x": 1}).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any(filename in e and "unknown" in e for e in report["errors"])

    with pytest.raises(ValidationError):
        svc.apply_import(bundle)


def test_corrupt_config_bundle_import_rolls_back_atomically(db_isolated):
    """config_io has NO money guard (module docstring) — the realistic failure mode for
    this domain is a structural PK conflict, not a non-finite amount. A payees.json entry
    repeating an id raises IntegrityError at INSERT time and the whole store's
    transaction — wipe included — rolls back untouched, mirroring
    test_corrupt_planning_bundle_import_rolls_back_atomically's shape with this domain's
    own realistic corruption."""
    with get_engine().begin() as conn:
        cio.import_payload_list(conn, payees, PAYEES, book_id=DEFAULT_BOOK_ID)

    corrupt = [PAYEES[0], {**PAYEES[0]}]  # same id twice -> PK violation on insert
    bundle = _build_bundle({"payees.json": json.dumps(corrupt, ensure_ascii=False).encode()})

    with pytest.raises(IntegrityError):
        svc.apply_import(bundle)

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == PAYEES


# ── /api/reset — every one of the eleven config tables, bridged or wipe-only ──────────


def test_reset_wipes_all_config_and_settings_tables(db_isolated):
    """Also seeds and checks ``tool_permissions`` — outside this wave's own scope (module
    docstring), but reached by the SAME ``reset_bridged_stores()`` call under test, so
    proving the full reset surface here is worth the one extra store."""
    from backend.db.tool_permission_store import ToolPermissionStore
    from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission

    with get_engine().begin() as conn:
        _seed_six_bridged(conn)
        _seed_four_wipe_only(conn)
    set_tool_permission(CORE_CONNECTOR_ID, "add_transactions", "always_allow")

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == []
        assert cio.export_payload_list(conn, automations) == []
        assert cio.export_payload_list(conn, alerts) == []
        assert cio.export_dashboard(conn) == {"activeViewId": None, "views": []}
        assert cio.export_payload_list(conn, memory) == []
        assert cio.export_payload_list(conn, news_sources) == []
        assert cio.export_payload_list(conn, statement_formats) == []
        assert cio.export_payload_list(conn, notification_log) == []
        assert cio.export_namespaced(conn, "memory_settings") == {}
        assert cio.export_namespaced(conn, "preferred_models") == {}
        # the settings table itself is fully empty, not just its two known namespaces
        assert conn.execute(select(settings)).all() == []
    # reset means reset: the owner's decision is gone and every tool is back at its seed
    # (the wipe empties the table; the same call re-seeds it).
    rows = ToolPermissionStore(engine=get_engine()).all()
    assert rows and all(row["source"] == "seed" for row in rows)
    assert ToolPermissionStore(engine=get_engine()).get_level("core", "add_transactions") == (
        "needs_approval"
    )


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors test_planning_portable.py's/
    test_investments_portable.py's own identical helper."""
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


def test_reset_endpoint_wipes_config_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    with get_engine().begin() as conn:
        cio.import_payload_list(conn, payees, PAYEES, book_id=DEFAULT_BOOK_ID)
        cio.import_namespaced(conn, "memory_settings", MEMORY_SETTINGS)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == []
        assert cio.export_namespaced(conn, "memory_settings") == {}


def test_reset_backup_snapshot_contains_config_rows(db_isolated, tmp_path, monkeypatch):
    """Regression-shaped proof (mirrors test_reset_backup_snapshot_contains_planning_rows/
    test_reset_backup_snapshot_contains_portfolio_rows) that /api/reset's pre-wipe backup
    loop (api/setup.py::reset_all_data, iterating core.portable.PORTABLE_FILES) actually
    captures the six bridged config stores' real rows — not the income_events-shaped gap
    those waves found, since all six were already live here, but worth proving positively
    rather than merely by registry inspection."""
    from backend.paths import (
        ALERTS_JSON,
        AUTOMATIONS_JSON,
        DASHBOARD_LAYOUT_JSON,
        MEMORY_JSON,
        NEWS_SOURCES_JSON,
        PAYEES_JSON,
    )

    with get_engine().begin() as conn:
        _seed_six_bridged(conn)

    client, data = _reset_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [
            *portable.PORTABLE_FILES,
            PAYEES_JSON,
            AUTOMATIONS_JSON,
            ALERTS_JSON,
            MEMORY_JSON,
            NEWS_SOURCES_JSON,
            DASHBOARD_LAYOUT_JSON,
        ],
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    assert json.loads((backup_dir / "payees.json").read_text()) == PAYEES
    assert json.loads((backup_dir / "automations.json").read_text()) == AUTOMATIONS
    assert json.loads((backup_dir / "alerts.json").read_text()) == ALERTS
    assert json.loads((backup_dir / "memory.json").read_text()) == MEMORY
    assert json.loads((backup_dir / "news_sources.json").read_text()) == NEWS_SOURCES
    assert json.loads((backup_dir / "dashboard_layout.json").read_text()) == DASHBOARD_LAYOUT
    for filename in WIPE_ONLY_CONFIG_FILES:
        assert not (backup_dir / filename).exists(), f"{filename} must never be backed up"

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == []


# ── settings: memory_settings/preferred_models share ONE table, wiped independently ────


def test_memory_settings_and_preferred_models_wipe_is_scoped_by_namespace(db_isolated):
    """memory_settings and preferred_models share ONE physical settings table, keyed by
    namespace — each gets its own filename-keyed wipe fn that touches ONLY its own row. A
    naive whole-table-truncate wipe fn would fail this test by also deleting the sibling
    namespace's row."""
    with get_engine().begin() as conn:
        cio.import_namespaced(conn, "memory_settings", MEMORY_SETTINGS)
        cio.import_namespaced(conn, "preferred_models", PREFERRED_MODELS)

    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "memory_settings.json")

    with get_engine().connect() as conn:
        assert cio.export_namespaced(conn, "memory_settings") == {}
        assert cio.export_namespaced(conn, "preferred_models") == PREFERRED_MODELS  # untouched


def test_dashboard_layout_wipe_uses_the_right_pk_column(db_isolated):
    """Sanity check for dashboard_layout's non-generic-'id'-value shape: its PK column IS
    named book_id (a per-book singleton, like plan_state/watchlists). A wrong pk_name
    would raise AttributeError on the first seeded row rather than silently doing the
    wrong thing."""
    with get_engine().begin() as conn:
        cio.import_dashboard(conn, DASHBOARD_LAYOUT)

    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "dashboard_layout.json")

    with get_engine().connect() as conn:
        assert conn.execute(select(dashboard_layouts)).all() == []


# ── tool_permissions/signals: wiped, but a checkpoint no-op ─────────────────────────────


def test_tool_permissions_and_signals_wipe_fires_hook_but_checkpoint_exclusion_makes_it_a_structural_no_op(
    db_isolated,
):
    """tool_permissions/signals' wipe fires fire_db_write_hook (uniform with every
    sibling — _capture_then_delete), but ai.checkpoint._EXCLUDED_TABLES makes the capture
    a structural no-op — a /rewind must never resurrect a revoked/blocked permission or a
    dismissed signal. Reset still wipes both regardless ("reset means reset"). Mirrors
    test_corrections_wipe_fires_hook_but_checkpoint_exclusion_makes_it_a_structural_no_op's
    shape, at the reset layer, for BOTH excluded tables at once.

    Ported from ``tool_grants`` (absorbed into ``tool_permissions`` at revision 0040,
    which inherits this exact exclusion unchanged — see the module docstring and
    ``tests/db/test_tool_permission_store.py``)."""
    from backend.db.tool_permission_store import ToolPermissionStore
    from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission

    assert "tool_permissions" in ckpt._EXCLUDED_TABLES
    assert "notification_log" in ckpt._EXCLUDED_TABLES

    with get_engine().begin() as conn:
        cio.import_payload_list(conn, notification_log, SIGNALS_NEWEST_FIRST, reverse=True)
        cio.import_payload_list(
            conn, payees, PAYEES, book_id=DEFAULT_BOOK_ID
        )  # a NON-excluded sibling
    set_tool_permission(CORE_CONNECTOR_ID, "add_transactions", "always_allow")

    turn_ctx, token = ckpt.begin_turn(
        "sess-config-t6-excl", anchor_history_len=0, message_preview="r"
    )
    try:
        svc.reset_bridged_stores()
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, notification_log) == []
        assert cio.export_payload_list(conn, payees) == []
    # reset means reset: the owner's decision is gone and every tool is back at its seed
    # (the wipe empties the table; the same call re-seeds it).
    rows = ToolPermissionStore(engine=get_engine()).all()
    assert rows and all(row["source"] == "seed" for row in rows)
    assert ToolPermissionStore(engine=get_engine()).get_level("core", "add_transactions") == (
        "needs_approval"
    )

    assert entry is not None
    assert "payees" in entry["db_tables"], "a non-excluded config wipe must be captured"
    assert "tool_permissions" not in entry["db_tables"], (
        "tool_permissions' capture must be a structural no-op, not merely untested"
    )
    assert "notification_log" not in entry["db_tables"], (
        "notification_log's capture must be a structural no-op, not merely untested"
    )


# ── Undoable config imports (this task's binding requirement) ─────────────────────────


def test_config_import_is_undoable_via_checkpoint_restore(db_isolated):
    """At least one config store's import is undoable: the write_log before-images a
    checkpoint turn collects let restore() revert it. Mirrors test_planning_import_is_
    undoable_via_checkpoint_restore's/test_investment_import_is_undoable_via_checkpoint_
    restore's minimal pattern."""
    with get_engine().begin() as conn:
        cio.import_payload_list(conn, alerts, ALERTS, book_id=DEFAULT_BOOK_ID)

    new_alerts = [{**ALERTS[0], "id": "alert_2", "value": 275.0}]
    bundle = _build_bundle({"alerts.json": json.dumps(new_alerts).encode()})

    turn_ctx, token = ckpt.begin_turn(
        "sess-config-t6-undo", anchor_history_len=0, message_preview="i"
    )
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "alerts" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, alerts) == new_alerts

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert cio.export_payload_list(conn, alerts) == ALERTS


# ── Seeding: the builtin-news-source seed writes the DB ────────────────────────────────


def test_first_boot_seeds_builtin_news_sources_into_db(db_isolated):
    """services.news_sources.ensure_builtin_sources() (INT9 Phase A) already writes
    through NewsSourceStore, which is DB-backed since plan 4d task 2 — this task doesn't
    change it, only proves it still works against a fresh, migrated, source-less DB, the
    same "verify, don't modify" bar the brief sets for this seed."""
    from backend.db.news_source_store import NewsSourceStore
    from backend.services.news_sources import ensure_builtin_sources

    store = NewsSourceStore()
    assert store.all() == []

    result = ensure_builtin_sources()

    assert result["seeded"] > 0
    seeded = NewsSourceStore().all()
    assert len(seeded) == result["seeded"]
    assert all(row["provenance"] == "builtin" for row in seeded)

    # Idempotent — a second boot re-ensure is a true no-op, never a duplicate insert.
    again = ensure_builtin_sources()
    assert again == {"seeded": 0}
    assert len(NewsSourceStore().all()) == len(seeded)
