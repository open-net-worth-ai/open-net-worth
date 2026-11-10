"""Configs cutover (plan 4d, task 5): revision 0016 absorbs 11 config JSON sources into
their Tier-1/Tier-3 tables (built by revision 0015), one all-or-nothing unit.

``tool_grants`` note (2026-08-14, owner ruling): revision 0016 used to carry a twelfth
job absorbing `tool_grants.json` for an instance upgrading from files, ahead of revision
0040 folding those rows into `tool_permissions`. Owner directive — "no backward-
compatibility layers anywhere, build the cleanest solution," "delete rather than
deprecate," "if a migration step exists only to preserve something the owner would
happily re-set in one click, drop the step" — removed that job outright rather than
routing it around the table's later removal from `backend.db.tables` (revision 0040 still
folds forward whatever `tool_grants` rows genuinely exist in a DATABASE — that path is
unaffected and covered by `tests/db/test_tool_permissions_migration.py`; only the
FROM-FILES cutover job is gone). This file therefore no longer references `tool_grants`
in any form.

Mirrors ``test_logs_cutover.py``/``test_planning_cutover.py``'s shape: no cross-file
consistency pre-check (no FKs BETWEEN config tables — the only FK on any of the 11 is the
3 Tier-1 members' ``book_id -> books.id``) and no embedded-child explosion. Covers: exact
round-trip per store (direct ``config_io`` calls, fast) AND through the real migration
chain (monkeypatched sources + ``command.upgrade``); the two namespaced-settings
singletons sharing ONE ``settings`` table; the book-keyed ``dashboard_layout`` singleton;
the ``signals`` prepend-order regression (the ORDER FIX — the one real correctness trap in
this wave, mirroring Wave C's own logs-cutover scar); full absorb + 11 ``json_cutovers``
rows + 11 renames; the up/down/up cycle.

NO money-guard tests this wave (``config_io.py``'s own module docstring) — none of these
11 stores holds a net-worth-bearing amount; every fixture below is verbatim opaque
payload.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.db import config_io as cio
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    alerts,
    automations,
    dashboard_layouts,
    json_cutovers,
    memory,
    news_sources,
    notification_log,
    payees,
    settings,
    statement_formats,
)

CONFIG_STORE_NAMES = (
    "payees",
    "automations",
    "alerts",
    "memory",
    "statement_formats",
    "intel_signals",
    "news_sources",
    "signals",  # json_cutovers.store — stamped by revision 0016 (frozen history), not renamed
    "memory_settings",
    "preferred_models",
    "dashboard_layout",
)


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """Postgres's ``db_url`` is the ONE shared CI server (unlike SQLite's
    fresh-tmp_path-per-test): scrub the public schema first so an earlier test's
    already-applied migration history doesn't make THIS test's ``command.upgrade(...,
    "head")`` a silent no-op. Mirrors ``test_logs_cutover.py``'s identical helper
    verbatim."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures (deliberately small but non-trivial: unicode, nulls, nested dicts — the
# round-trip proof needs a diff to actually be possible) ────────────────────────────────

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

# What the book holds at head: revision 202609281300 drops a payee default that names a category
# which does not exist, and the fresh book these migrations run on has none.
PAYEES_AT_HEAD = [{**payee, "default_categories": {}} for payee in PAYEES]

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
        # The CURRENT canonical spelling. A legacy ``allowed`` here would round-trip as
        # ``always_allowed`` (revision 0061 renames it), which is correct but would make this
        # fidelity check fail for a reason that is not a fidelity problem — the rename has its
        # own end-to-end proof in tests/db/test_automation_always_allowed_migration.py.
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

# Revision 202610271200 files every pre-existing fact as unsorted, with no section or topic yet.
MEMORY_AT_HEAD = [{**fact, "state": "unsorted", "section": None, "topic": None} for fact in MEMORY]

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

INTEL_SIGNALS = [
    {
        "id": "isig_1",
        "ticker": "AAPL",
        "label": "AAPL earnings beat",
        "status": "active",
        "title": "AAPL beats Q3 estimates",
        "severity": "info",
        "created_at": "2026-06-01T00:00:00Z",
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

# signals: NEWEST-FIRST on disk (prepend order, exactly like items.insert(0, new) writes
# it) — the ORDER FIX fixture. Distinct timestamps so direction is actually provable.
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

# (store, table, fixture, id_field, book_id, reverse) — the 7 generic flat-list stores that
# still exist as a table AT HEAD. `intel_signals` (revision 0016's 8th) is deliberately
# excluded here — RFC intel-signals-data-model split/dropped it, so a post-migration,
# HEAD-schema query against it (which every use of this list is) has no table to query.
# Its own cutover-absorption behavior is still covered, mid-chain, by the "through the real
# migration chain" tests below (`INTEL_SIGNALS`/`_INTEL_SIGNALS_LEGACY` remain used there).
_GENERIC_SPECS = [
    ("payees", payees, PAYEES, "id", DEFAULT_BOOK_ID, False),
    ("automations", automations, AUTOMATIONS, "id", DEFAULT_BOOK_ID, False),
    ("alerts", alerts, ALERTS, "id", DEFAULT_BOOK_ID, False),
    ("memory", memory, MEMORY, "id", None, False),
    ("statement_formats", statement_formats, STATEMENT_FORMATS, "id", None, False),
    ("news_sources", news_sources, NEWS_SOURCES, "id", None, False),
    # Label stays "signals" (matches the json_cutovers.store stamp revision 0016 froze in
    # history); the table object is `notification_log` — the table's CURRENT name at head,
    # since revision 0044 renames it after 0016's absorption runs.
    ("signals", notification_log, SIGNALS_NEWEST_FIRST, "id", None, True),
]

_ALL_ELEVEN_SOURCE_ATTRS = (
    "PAYEES_SOURCE",
    "AUTOMATIONS_SOURCE",
    "ALERTS_SOURCE",
    "MEMORY_SOURCE",
    "STATEMENT_FORMATS_SOURCE",
    "INTEL_SIGNALS_SOURCE",
    "NEWS_SOURCES_SOURCE",
    "SIGNALS_SOURCE",
    "MEMORY_SETTINGS_SOURCE",
    "PREFERRED_MODELS_SOURCE",
    "DASHBOARD_LAYOUT_SOURCE",
)


def _patch_all_sources_missing(monkeypatch, tmp_path) -> None:
    """Point every one of the eleven source seams at a file that does not exist, so a
    test exercising the real migration chain never accidentally reads whatever the
    ambient (session-shared) DATA_DIR happens to hold — mirrors
    ``test_logs_cutover.py``'s ``_patch_all_sources_missing``."""
    for attr in _ALL_ELEVEN_SOURCE_ATTRS:
        monkeypatch.setattr(
            f"backend.db.alembic.versions_evidence_source.{attr}",
            tmp_path / f"does_not_exist_{attr}.json",
        )


def _write_all_eleven_fixtures(tmp_path, monkeypatch) -> dict[str, tuple]:
    """Write every store's fixture to its own file under ``tmp_path`` and monkeypatch the
    matching ``versions_evidence_source`` constant at it. Returns ``{attr: (filename,
    original)}`` for assertion convenience."""
    files = {
        "PAYEES_SOURCE": ("payees.json", PAYEES),
        "AUTOMATIONS_SOURCE": ("automations.json", AUTOMATIONS),
        "ALERTS_SOURCE": ("alerts.json", ALERTS),
        "MEMORY_SOURCE": ("memory.json", MEMORY),
        "STATEMENT_FORMATS_SOURCE": ("statement_formats.json", STATEMENT_FORMATS),
        "INTEL_SIGNALS_SOURCE": ("intel_signals.json", INTEL_SIGNALS),
        "NEWS_SOURCES_SOURCE": ("news_sources.json", NEWS_SOURCES),
        "SIGNALS_SOURCE": ("signals.json", SIGNALS_NEWEST_FIRST),
        "MEMORY_SETTINGS_SOURCE": ("memory_settings.json", MEMORY_SETTINGS),
        "PREFERRED_MODELS_SOURCE": ("preferred_models.json", PREFERRED_MODELS),
        "DASHBOARD_LAYOUT_SOURCE": ("dashboard_layout.json", DASHBOARD_LAYOUT),
    }
    for attr, (filename, fixture) in files.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture, ensure_ascii=False))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    return files


# ── Direct round-trip per store (fixture-scale, no migration chain needed) ──────────────


@pytest.mark.parametrize(
    "store,table,fixture,id_field,book_id,reverse",
    _GENERIC_SPECS,
    ids=[spec[0] for spec in _GENERIC_SPECS],
)
def test_each_generic_store_round_trips_exactly_direct(
    db_engine, store, table, fixture, id_field, book_id, reverse
):
    with db_engine.begin() as conn:
        n = cio.import_payload_list(
            conn, table, fixture, id_field=id_field, book_id=book_id, reverse=reverse
        )
    assert n == len(fixture)
    with db_engine.connect() as conn:
        assert cio.export_payload_list(conn, table, reverse=reverse) == fixture


def test_book_id_promoted_but_payload_stays_verbatim_for_tier1_stores(db_engine):
    with db_engine.begin() as conn:
        cio.import_payload_list(conn, payees, PAYEES, book_id=DEFAULT_BOOK_ID)
    with db_engine.connect() as conn:
        row = conn.execute(select(payees).where(payees.c.id == "pay_1")).one()
    assert row.book_id == DEFAULT_BOOK_ID
    assert row.seq == 0
    assert "book_id" not in row.payload


def test_tier2_generic_import_has_no_book_id_column_at_all(db_engine):
    with db_engine.begin() as conn:
        cio.import_payload_list(conn, memory, MEMORY)
    with db_engine.connect() as conn:
        row = conn.execute(select(memory).where(memory.c.id == "mem_1")).one()
    assert not hasattr(row, "book_id")


def test_missing_source_files_are_a_clean_empty_export_for_every_generic_store(db_engine):
    with db_engine.connect() as conn:
        for _store, table, _fixture, _id_field, _book_id, reverse in _GENERIC_SPECS:
            assert cio.export_payload_list(conn, table, reverse=reverse) == []


# ── memory_settings / preferred_models: ONE shared `settings` table, keyed by namespace ─


def test_namespaced_import_export_round_trips_exactly_direct(db_engine):
    with db_engine.begin() as conn:
        n = cio.import_namespaced(conn, "memory_settings", MEMORY_SETTINGS)
    assert n == 1
    with db_engine.connect() as conn:
        assert cio.export_namespaced(conn, "memory_settings") == MEMORY_SETTINGS


def test_namespaced_default_is_empty_dict_when_no_row_exists(db_engine):
    with db_engine.connect() as conn:
        assert cio.export_namespaced(conn, "preferred_models") == {}


def test_namespaced_two_namespaces_coexist_in_the_same_table(db_engine):
    with db_engine.begin() as conn:
        cio.import_namespaced(conn, "memory_settings", MEMORY_SETTINGS)
        cio.import_namespaced(conn, "preferred_models", PREFERRED_MODELS)
    with db_engine.connect() as conn:
        assert cio.export_namespaced(conn, "memory_settings") == MEMORY_SETTINGS
        assert cio.export_namespaced(conn, "preferred_models") == PREFERRED_MODELS
        rows = {r.namespace: r.payload for r in conn.execute(select(settings))}
    assert set(rows) == {"memory_settings", "preferred_models"}


# ── dashboard_layout: per-book singleton, keyed by book_id ──────────────────────────────


def test_dashboard_import_export_round_trips_the_singleton_dict_verbatim(db_engine):
    with db_engine.begin() as conn:
        n = cio.import_dashboard(conn, DASHBOARD_LAYOUT)
    assert n == 1
    with db_engine.connect() as conn:
        assert cio.export_dashboard(conn) == DASHBOARD_LAYOUT


def test_dashboard_default_when_no_row_exists(db_engine):
    with db_engine.connect() as conn:
        assert cio.export_dashboard(conn) == {"activeViewId": None, "views": []}


def test_dashboard_keyed_by_book_id_not_a_seq_id_payload_shape(db_engine):
    with db_engine.begin() as conn:
        cio.import_dashboard(conn, DASHBOARD_LAYOUT)
    with db_engine.connect() as conn:
        row = conn.execute(select(dashboard_layouts)).one()
    assert row.book_id == DEFAULT_BOOK_ID
    assert row.payload == DASHBOARD_LAYOUT
    assert not hasattr(row, "id")  # dashboard_layouts has no id/seq column at all


# ── CONFIG_PAYLOAD_SPECS / CONFIG_FILES constants ────────────────────────────────────


def test_config_payload_specs_covers_the_eight_generic_stores():
    specs = {
        fn: (table, id_field, book_id, reverse)
        for fn, table, id_field, book_id, reverse in cio.CONFIG_PAYLOAD_SPECS
    }
    assert set(specs) == {
        "payees.json",
        "automations.json",
        "alerts.json",
        "memory.json",
        "statement_formats.json",
        "intel_signals.json",
        "news_sources.json",
        "signals.json",
    }
    assert specs["payees.json"] == (payees, "id", DEFAULT_BOOK_ID, False)
    assert specs["automations.json"] == (automations, "id", DEFAULT_BOOK_ID, False)
    assert specs["alerts.json"] == (alerts, "id", DEFAULT_BOOK_ID, False)
    assert specs["signals.json"] == (notification_log, "id", None, True)
    for fn in ("memory.json", "statement_formats.json", "intel_signals.json", "news_sources.json"):
        table, id_field, book_id, reverse = specs[fn]
        assert (id_field, book_id, reverse) == ("id", None, False)


def test_config_files_lists_all_eleven():
    assert set(cio.CONFIG_FILES) == {
        "payees.json",
        "automations.json",
        "alerts.json",
        "memory.json",
        "statement_formats.json",
        "intel_signals.json",
        "news_sources.json",
        "signals.json",
        "memory_settings.json",
        "preferred_models.json",
        "dashboard_layout.json",
    }
    assert len(cio.CONFIG_FILES) == 11


# ── Full migration chain: monkeypatched sources + command.upgrade(head) ─────────────────


def test_each_config_store_round_trips_exactly(tmp_path, db_url, monkeypatch):
    """Brief-required name. Seeds a throwaway source file per store (monkeypatched
    ``*_SOURCE``), runs the real migration to head, and asserts every one of the 10
    STILL-DIRECTLY-EXPORTABLE stores' export equals its source EXACTLY — an ordered list
    for the 7 generic stores, an exact dict for the 3 singletons. This is the ORDER-
    SENSITIVE round-trip check the brief calls the structural safety net: plain ``==`` on
    a Python list is element-by-element/positional, never a set/sorted/count comparison,
    so a store whose import order silently disagreed with its export order would fail
    HERE even independent of the dedicated ``signals`` regression below.

    ``intel_signals`` (the file is still written and still absorbed by revision 0016 —
    the historical cutover step itself is unaffected) is EXCLUDED from the post-migration
    equality check: RFC intel-signals-data-model splits it into three tables at a later
    revision, so by "head" there is no single table left to export back into one ordered
    list for a 1:1 comparison. Its cutover-absorption correctness is covered separately,
    mid-chain, by the "through the real migration chain" tests above.
    """
    files = _write_all_eleven_fixtures(tmp_path, monkeypatch)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    exporters = {
        "PAYEES_SOURCE": lambda c: cio.export_payload_list(c, payees),
        "AUTOMATIONS_SOURCE": lambda c: cio.export_payload_list(c, automations),
        "ALERTS_SOURCE": lambda c: cio.export_payload_list(c, alerts),
        "MEMORY_SOURCE": lambda c: cio.export_payload_list(c, memory),
        "STATEMENT_FORMATS_SOURCE": lambda c: cio.export_payload_list(c, statement_formats),
        "NEWS_SOURCES_SOURCE": lambda c: cio.export_payload_list(c, news_sources),
        "SIGNALS_SOURCE": lambda c: cio.export_payload_list(c, notification_log, reverse=True),
        "MEMORY_SETTINGS_SOURCE": lambda c: cio.export_namespaced(c, "memory_settings"),
        "PREFERRED_MODELS_SOURCE": lambda c: cio.export_namespaced(c, "preferred_models"),
        "DASHBOARD_LAYOUT_SOURCE": lambda c: cio.export_dashboard(c),
    }
    with eng.get_engine().connect() as conn:
        for attr, (_filename, original) in files.items():
            if attr not in exporters:
                continue  # INTEL_SIGNALS_SOURCE — see this test's own docstring
            exported = exporters[attr](conn)
            if attr == "PAYEES_SOURCE":
                original = PAYEES_AT_HEAD
            if attr == "MEMORY_SOURCE":
                original = MEMORY_AT_HEAD
            # Plain ``==`` on two lists is already ORDER-sensitive in Python, so this is the
            # full round-trip AND order check; the signals-specific prepend-order proof lives
            # in test_signals_prepend_order_survives_cutover.
            assert exported == original, f"{attr} did not round-trip exactly"

    eng.dispose_engine()


def test_signals_prepend_order_survives_cutover(tmp_path, db_url, monkeypatch):
    """RED-PROOF (the ORDER FIX, the one real correctness trap this task calls out): the
    source ``signals.json`` is PREPEND-order on disk — newest-first, exactly as the
    pre-rewrite JsonStore-era ``SignalsStore`` wrote it (and as empirically verified
    against the live tree's own ``signals.json`` during this task). Against a cutover
    that omitted ``reverse=True`` (plain ``enumerate(raw)``), the newest pre-existing row
    would land at seq=0, and ``SignalsStore``'s own ``query_signals`` (``ORDER BY seq
    DESC``) would then return the OLDEST row first for every pre-existing row —
    chronological order INVERTED, the exact Wave C logs-cutover blocker.

    Proves:
      (a) ``SignalsStore().query_signals()`` — the real newest-first read path (module
          docstring: "every read... explicitly orders seq DESC") — returns the migrated
          rows in TRUE chronological newest-first order. THIS is the assertion that fails
          RED without ``reverse=True``.
      (b) the io-level ``export_payload_list(..., reverse=True)`` independently
          reproduces the source's newest-first byte order (the round-trip proof the
          migration itself relies on).
      (c) a row appended AFTER the migration continues the seq sequence as the newest —
          seq continuity across the migration boundary.

    NOTE: ``SignalsStore.all()`` is deliberately NOT used for (a) — it is inherited
    unchanged from ``PayloadTableStore.all()`` (``ORDER BY seq`` ASCENDING) and
    ``SignalsStore`` does not override it, so it is oldest-first by construction and
    cannot distinguish this bug at all; only ``query_signals``/``_filtered`` actually read
    ``seq DESC``.
    """
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "signals.json"
    path.write_text(json.dumps(SIGNALS_NEWEST_FIRST, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.SIGNALS_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    from backend.db.notification_log_store import NotificationLogStore

    store = NotificationLogStore()

    # (a) the store's real newest-first read — THIS is the assertion that fails pre-fix.
    ids = [r["id"] for r in store.query_signals(limit=50)]
    assert ids == ["sig_3", "sig_2", "sig_1"], (
        f"chronological order inverted for pre-existing rows: expected newest-first "
        f"[sig_3, sig_2, sig_1], got {ids}"
    )

    # (b) io-level round-trip is still byte-exact for this multi-row prepend case.
    with eng.get_engine().connect() as conn:
        assert cio.export_payload_list(conn, notification_log, reverse=True) == SIGNALS_NEWEST_FIRST

    # (c) seq continuity: a row appended after migration lands newest.
    store.append({"type": "news", "title": "fourth (appended)", "severity": "info"})
    ids_after = [r["id"] for r in store.query_signals(limit=50)]
    assert ids_after[0] not in ("sig_3", "sig_2", "sig_1"), (
        "newly appended row must read back as the newest entry"
    )
    assert ids_after[1:] == ["sig_3", "sig_2", "sig_1"]

    eng.dispose_engine()


def test_namespaced_singletons_round_trip(tmp_path, db_url, monkeypatch):
    """Brief-required name. ``memory_settings`` + ``preferred_models`` — two independent
    namespace rows sharing ONE ``settings`` table — round-trip exactly through the real
    migration, AND the real store classes built on top of the migrated rows read the
    values back correctly (not just the raw io functions)."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    ms_path = tmp_path / "memory_settings.json"
    ms_path.write_text(json.dumps(MEMORY_SETTINGS, ensure_ascii=False))
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.MEMORY_SETTINGS_SOURCE", ms_path
    )
    pm_path = tmp_path / "preferred_models.json"
    pm_path.write_text(json.dumps(PREFERRED_MODELS, ensure_ascii=False))
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.PREFERRED_MODELS_SOURCE", pm_path
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert cio.export_namespaced(conn, "memory_settings") == MEMORY_SETTINGS
        assert cio.export_namespaced(conn, "preferred_models") == PREFERRED_MODELS

    from backend.db.memory_settings_store import MemorySettingsStore
    from backend.db.preferred_models_store import PreferredModelsStore

    assert MemorySettingsStore().enabled() is False
    assert PreferredModelsStore().resolved() == PREFERRED_MODELS

    eng.dispose_engine()


def test_dashboard_singleton_round_trips(tmp_path, db_url, monkeypatch):
    """Brief-required name. ``dashboard_layout`` — the book-keyed singleton — round-trips
    exactly through the real migration, AND ``DashboardLayoutStore.get_state()`` (the real
    read path) returns the migrated views/widgets unchanged."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "dashboard_layout.json"
    path.write_text(json.dumps(DASHBOARD_LAYOUT, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.DASHBOARD_LAYOUT_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert cio.export_dashboard(conn) == DASHBOARD_LAYOUT

    from backend.db.dashboard_layout_store import DashboardLayoutStore

    assert DashboardLayoutStore().get_state() == DASHBOARD_LAYOUT

    eng.dispose_engine()


def test_missing_source_is_skipped(tmp_path, db_url, monkeypatch):
    """Brief-required name. One store (``memory``) has no source file present — it
    absorbs nothing and the migration does not crash; every OTHER store still absorbs
    normally. Mirrors ``test_logs_cutover.py``'s
    ``test_one_store_absent_is_a_clean_skip_other_eight_still_absorb``."""
    files = _write_all_eleven_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.MEMORY_SOURCE",
        tmp_path / "does_not_exist_memory.json",
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert cio.export_payload_list(conn, memory) == []
        assert cio.export_payload_list(conn, payees) == PAYEES_AT_HEAD
        assert cio.export_namespaced(conn, "memory_settings") == MEMORY_SETTINGS
        assert cio.export_dashboard(conn) == DASHBOARD_LAYOUT
        count = conn.execute(
            select(func.count()).select_from(json_cutovers).where(json_cutovers.c.store == "memory")
        ).scalar_one()
    assert count == 0
    assert files  # sanity: fixtures were actually written for the other 10 stores
    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_all_eleven_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the eleven config files exist — head is reached, zero rows,
    zero cutover-ledger entries for the config stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for _store, table, _fixture, _id_field, _book_id, reverse in _GENERIC_SPECS:
            assert cio.export_payload_list(conn, table, reverse=reverse) == []
        assert cio.export_namespaced(conn, "memory_settings") == {}
        assert cio.export_namespaced(conn, "preferred_models") == {}
        assert cio.export_dashboard(conn) == {"activeViewId": None, "views": []}

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(CONFIG_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


@pytest.mark.parametrize(
    "attr,filename,bad_payload",
    [
        ("PAYEES_SOURCE", "payees.json", {"not": "a list"}),  # dict, not list
        ("MEMORY_SETTINGS_SOURCE", "memory_settings.json", ["not", "a", "dict"]),  # list, not dict
    ],
    ids=["payees-dict-not-list", "memory_settings-list-not-dict"],
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
    successful import — proven on ``payees`` by monkeypatching the SHARED generic
    ``export_payload_list`` (every generic store routes through it, so this proves the
    guard fires regardless of which specific store drifts): the WHOLE migration must
    still RAISE (fail-closed, all-or-nothing), not silently accept the drifted store."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = list(PAYEES)
    path = tmp_path / "payees.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.PAYEES_SOURCE", path)
    monkeypatch.setattr(
        cio, "export_payload_list", lambda conn, table, reverse=False: [{"drifted": True}]
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError, match="round-trip verification FAILED") as exc_info:
        command.upgrade(make_config(db_url), "head")
    assert "payees" in str(exc_info.value)
    eng.dispose_engine()


def test_full_migration_absorbs_all_and_renames(tmp_path, db_url, monkeypatch):
    """Brief-required name. All 11 sources present -> all 11 absorbed, all 11 sources
    renamed ``.migrated-*`` (never deleted), 11 ``json_cutovers`` rows with ``renamed=1``
    and a non-empty ``source_hash``."""
    files = _write_all_eleven_fixtures(tmp_path, monkeypatch)
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
    for store in CONFIG_STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


# ── Up/down/up cycle ─────────────────────────────────────────────────────────────────────


def test_up_down_up_cycle_is_reversible(db_url, tmp_path, monkeypatch):
    """Brief-required name. Structural reversibility of the SCHEMA CHAIN, isolated from
    data-absorption correctness (already fully proven above): every one of the eleven
    sources is explicitly pointed at a nonexistent file, so both upgrade passes are clean
    empty absorbs — the down step (to 0014, PAST 0015, dropping the 12 config tables via
    0015's OWN downgrade; revision 0016's own downgrade() is a documented no-op — see that
    revision, and 0014/0010's identical up/down/up tests, which downgrade past their own
    schema revision the same way) has nothing to lose, and re-upgrading cannot collide on
    a stale ``json_cutovers`` primary key from a first pass that inserted zero rows."""
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
        assert "payees" in names and "dashboard_layouts" in names
        assert "settings" in names and "notification_log" in names
    finally:
        probe.dispose()

    command.downgrade(cfg, "202608090747_logs_cutover")

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "payees" not in names
        assert "dashboard_layouts" not in names
        assert "settings" not in names
    finally:
        probe.dispose()

    command.upgrade(cfg, "head")  # re-absorbs (still nothing to absorb) — must not raise

    eng.dispose_engine()
    with eng.get_engine().connect() as conn:
        assert cio.export_payload_list(conn, payees) == []
        assert cio.export_namespaced(conn, "memory_settings") == {}
        assert cio.export_dashboard(conn) == {"activeViewId": None, "views": []}
    eng.dispose_engine()
