"""Portable-bundle bridge for DB-backed reference-data stores (plan 2, task 7).

Closes the silent-export-omission gap Task 3's review found and Task 5 compounded:
after a store's JSON file is renamed away by its cutover migration,
``core.portable.PORTABLE_FILES`` still names it by that ORIGINAL filename, so
export/import/``​/api/reset`` need a seam that sources/sinks the DB instead of the
now-dead path — ``backend.db.portable_bridge``.

Only ``price_history.json``/``instruments.json`` are LIVE (present in
``PORTABLE_FILES``, so the bundle pipeline actually reaches them). ``evidence.json``
is wired into ``portable_bridge.BRIDGED`` but deliberately DORMANT — not in
``PORTABLE_FILES`` — per the task 7 review (2026-08-07): a real evidence export
measures ~108MB, over this module's 100MB per-entry import cap, so a bundle
containing it would export fine and then 422 on every subsequent import, including
importing its own just-made export. See
docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md for the full ruling
(including why a bridged import fires NO write-hook).

``bridged_manifest`` narrows ``core.portable.PORTABLE_FILES`` down to just the two
LIVE bridged filenames, at throwaway ``tmp_path`` "dead paths" — mirrors
``tests/test_portability_service.py``'s sandbox-narrowing pattern, and makes "wrote
no dead file" assertions deterministic (a real path that could never accidentally
exist rather than the shared test DATA_DIR).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import func, select

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.db import (
    evidence_io,
    instruments_io,
    intel_signals_io,
    portable_bridge,
    price_history_io,
)
from backend.db.engine import get_engine
from backend.db.tables import (
    book_members,
    books,
    daily_digests,
    evidence_subjects,
    intel_stories,
    json_cutovers,
    signal_timeline_entries,
    users,
    write_log,
)
from backend.services import portability as svc

EVIDENCE_FIXTURE = [
    {
        "id": "ev_a",
        "subjects": ["AAPL"],
        "domain": "example.com",
        "expires_at": "2026-09-01T00:00:00+00:00",
        "first_seen": "2026-08-01T00:00:00+00:00",
        "title": "Apple coverage",
    },
    {
        "id": "ev_b",
        "subjects": ["MSFT"],
        "domain": "example.org",
        "expires_at": "2026-09-02T00:00:00+00:00",
        "first_seen": "2026-08-02T00:00:00+00:00",
        "title": "Microsoft coverage",
    },
]

PRICE_HISTORY_FIXTURE = {
    "AAPL": [
        {"date": "2026-08-01", "price": 190.0, "source": "yahoo"},
        {"date": "2026-08-02", "price": 191.5, "source": "yahoo"},
    ],
    "bitcoin": [{"date": "2026-08-01", "price": 61000.0, "source": "coingecko"}],
}

INSTRUMENTS_FIXTURE = [
    {
        "id": "inst_a",
        "symbol": "AAPL",
        "asset_class": "equity",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "name": "Apple Inc",
    },
    {
        "id": "inst_b",
        "symbol": "BTC",
        "asset_class": "crypto",
        "price_source": "coingecko",
        "price_source_id": "bitcoin",
        "name": "Bitcoin",
    },
]


# Newest-first (index 0 = most recent) — the pre-split on-disk `timeline[]` shape
# (`analyst._update_signal`'s `timeline.insert(0, entry)`) that `export_intel_stories`
# reproduces and `import_intel_stories` must reverse-insert to match (intel_signals_io.py's
# own docstring, ORDER FIX). entry_3 CONFIRMED-supersedes entry_2 (an unconfirmed rumor);
# entry_2's own `superseded=True` reflects that, and entry_2 also `contradicts` entry_1
# (unconfirmed — `status="flagged"`, tables.py's own documented contradicts/status pairing).
INTEL_STORY_FIXTURE = {
    "id": "asset-inst_aapl",
    "type": "asset",
    "instrument_id": "inst_aapl",
    "ticker": "AAPL",
    "label": "AAPL earnings trajectory",
    "status": "active",
    "current_summary": "Q3 beat, guidance raised.",
    "portfolio_impact": "Position up 4% on the print.",
    "action_items": ["Review options collateral"],
    "affected_holdings": None,
    "analyzed": True,
    "read": False,
    "dismissed": False,
    "resolved": False,
    "resolved_reason": None,
    "archived": False,
    "evidence_watermark": "2026-08-15T00:00:00+00:00",
    "created_at": "2026-08-01T00:00:00+00:00",
    "updated_at": "2026-08-20T00:00:00+00:00",
    "timeline": [
        {
            "id": "entry_3",
            "date": "2026-08-15",
            "title": "Earnings confirmed beat",
            "summary": "Q3 print confirms the earlier rumor.",
            "sources": [{"url": "https://example.com/b", "domain": "example.com"}],
            "superseded": False,
            "supersedes": "entry_2",
            "contradicts": None,
            "status": "confirmed",
            "confirmed_at": "2026-08-15T20:00:00+00:00",
        },
        {
            "id": "entry_2",
            "date": "2026-08-10",
            "title": "Rumor of a beat",
            "summary": "Unconfirmed chatter of stronger iPhone demand.",
            "sources": [],
            "superseded": True,
            "supersedes": None,
            "contradicts": "entry_1",
            "status": "flagged",
            "confirmed_at": None,
        },
        {
            "id": "entry_1",
            "date": "2026-08-01",
            "title": "Initial coverage",
            "summary": "Opened story on pre-earnings drift.",
            "sources": [{"url": "https://example.com/a", "domain": "example.com"}],
            "superseded": False,
            "supersedes": None,
            "contradicts": None,
            "status": None,
            "confirmed_at": None,
        },
    ],
}

DAILY_DIGEST_FIXTURE = {
    "date": "2026-08-20",
    "created_at": "2026-08-20T21:00:00+00:00",
    "trigger": "eod",
    "severity": "info",
    "title": "Daily Digest",
    "message": "Portfolio up 1.2% today, led by AAPL.",
    "affected_tickers": ["AAPL", "MSFT"],
    "sources": ["ft", "bloomberg"],
    "source_urls": ["https://ft.com/x"],
    "read": False,
    "period": "1D",
    "pl_base": 1240.5,
    "pl_pct": 1.2,
}


def _seed_intel_stories(conn) -> None:
    intel_signals_io.import_intel_stories(conn, [INTEL_STORY_FIXTURE])


def _seed_daily_digest(conn) -> None:
    from backend.db.daily_digest_store import DailyDigestStore

    DailyDigestStore(engine=get_engine()).upsert(dict(DAILY_DIGEST_FIXTURE), conn=conn)


def _seed_claim(conn) -> None:
    from backend.db.claims_store import ClaimsStore

    ClaimsStore(engine=get_engine()).record_push(
        "news-fact:test-inst",
        story_id="story-1",
        statement="Test Corp announced a tender offer.",
        sources=[{"url": "https://example.com/a", "domain": "example.com"}],
        materiality="new",
        decision_relevant=True,
        confirmed_at=None,
        now="2026-08-20T21:00:00+00:00",
        conn=conn,
    )


def _seed_all(conn) -> None:
    """Seeds evidence too (even though it's dormant) — several tests below use this
    to prove dormancy means "never bundled", not "the DB rows don't exist"."""
    evidence_io.import_evidence_json(conn, EVIDENCE_FIXTURE)
    price_history_io.import_price_history(conn, PRICE_HISTORY_FIXTURE)
    instruments_io.import_instruments(conn, INSTRUMENTS_FIXTURE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle (stands in for a real pre- or post-cutover export) — a
    valid manifest with correct per-entry sha256 so ``inspect_bundle`` accepts it."""
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


@pytest.fixture
def bridged_manifest(tmp_path, monkeypatch):
    """Narrows PORTABLE_FILES to just the two LIVE bridged filenames — evidence is
    deliberately absent here too, matching the real (un-narrowed) manifest shape
    post-ruling, not a hypothetical "what if it were included" set."""
    price_history_path = tmp_path / "price_history.json"
    instruments_path = tmp_path / "instruments.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    files = [price_history_path, instruments_path]
    monkeypatch.setattr(portable, "PORTABLE_FILES", files)
    monkeypatch.setattr(portable, "PORTABLE_DIRS", [sessions])
    # Wave 5E (JSON->DB): sessions are `sessions` table rows now (db/session_store.py),
    # not a SESSIONS_DIR to whole-tree copy — services.portability has no such attribute
    # left to patch; session_bundle_entries() reads the DB directly, isolated by the
    # db_isolated fixture every caller of this fixture already requests.
    monkeypatch.setattr(svc, "_ALLOWED_FILENAMES", {p.name for p in files})
    return {
        "price_history": price_history_path,
        "instruments": instruments_path,
        "sessions": sessions,
    }


# ── The manifest + registry themselves ────────────────────────────────────────


def test_price_history_and_instruments_are_in_the_real_manifest_evidence_is_not():
    """Uses the REAL, un-narrowed manifest — proves the wiring exists by default,
    not just inside this file's own sandbox, and that evidence.json's dormancy is a
    manifest-level fact, not just a test-fixture convention."""
    from backend.paths import EVIDENCE_JSON, INSTRUMENTS_JSON, PRICE_HISTORY_JSON

    names = {p.name for p in portable.PORTABLE_FILES}
    assert PRICE_HISTORY_JSON.name in names
    assert INSTRUMENTS_JSON.name in names
    assert EVIDENCE_JSON.name not in names, (
        "evidence.json must NOT be portable — dormant bridge, task 7 review "
        "(100MB import-entry cap collision on a ~108MB real export)"
    )


def test_bridge_registry_has_the_three_reference_stores_plus_the_money_six():
    """The registry holds the three reference-data stores from plan 2 task 7 (evidence's
    entry wired but not reachable through the manifest-driven bundle pipeline — see the
    dormant-bridge round-trip test below) PLUS the six money-core stores from plan 3 task
    6 (tests/db/test_money_portability.py exercises those six's own behavior — undoable
    imports, the FK-safe group ordering, reset, seeding — in depth; this file's own
    tests below stay scoped to the three reference-data entries, unchanged since task 7)
    PLUS the twelve planning stores from plan 4a task 7 (tests/db/test_planning_portable.py
    exercises those twelve's own behavior — undoable imports, the live/dormant split, reset,
    seeding — in depth; same scoping precedent as the money six's own arrival here) PLUS the
    seven bridged investment stores from plan 4b task 6 (tests/db/test_investments_portable.py
    exercises those seven's own behavior — undoable imports, reset, seeding — in depth, plus
    asset_brief's deliberate ABSENCE from this same registry, wipe-only; same scoping
    precedent again) PLUS the one bridged logs store from plan 4c task 3 (automation_runs —
    tests/db/test_logs_portable.py exercises its own behavior — undoable imports, reset,
    round-trip via the bridge — in depth, plus its 8 excluded siblings' deliberate
    ABSENCE from this same registry, no wipe role at all; same scoping precedent again)
    PLUS SIX (was seven through migration 202608281125) bridged config stores from plan 4d
    task 6 (tests/db/test_configs_portable.py exercises those six's own behavior — undoable
    imports, reset, seeding — in depth, plus the five wipe-only config stores' deliberate
    ABSENCE from this same registry; same scoping precedent again) — the seventh,
    ``intel_signals.json``, was REMOVED (not merely renamed) by migration 202608301500 (RFC
    intel-signals-data-model): the table it bridged split into three PLUS ``intel_stories.json``
    (RFC intel-signals-data-model §3.4), the bespoke bridge that replaces it — composes
    ``intel_stories``+``signal_timeline_entries`` into one bundle entry (this file's own tests
    below exercise its round-trip/reset/undo behavior in depth); ``daily_digests``, the split's
    third table, is deliberately ABSENT from this registry — wipe-only, no bridge role at all,
    same scoping precedent as ``asset_brief``/``pending_pushes`` PLUS the three bridged misc
    stores from plan 4e task 1
    (tests/db/test_misc_portable.py exercises those three's own behavior — undoable
    imports, reset, seeding — in depth, plus pending_pushes' deliberate ABSENCE from this
    same registry, wipe-only; same scoping precedent again) PLUS the owner's financial-profile
    singleton from Wave 5 (profile.json, mirroring plan_state) PLUS two of Wave 5C's five
    runtime-queue/state stores (skills_state.json/actions.json — see portable_bridge.py's own
    WAVE 5C section for why the other three in that wave, dedup_config/harness_state/
    subscriptions, stay wipe-only like pending_pushes above) PLUS budget_engine_state.json
    (revision 0049, .claude/rfcs/2026-08-22-pluggable-budget-engines.md) — a NEW addition, not
    one of the original twelve planning stores, sourced from its own io module and bridged
    directly (see portable_bridge.py's own PLUGGABLE BUDGET ENGINES section) PLUS
    assets.json/asset_categories.json (ADR 2026-09-06-assets-tab-faithful-port) — the
    owned-assets domain, a sibling of commitments.json bridged the same generic
    _planning_bridge way (tests/test_owned_assets_portability.py exercises their own
    round-trip/reset behavior in depth)."""
    assert (
        set(portable_bridge.BRIDGED)
        == {
            "evidence.json",
            "price_history.json",
            "instruments.json",
            "profile.json",
            "skills_state.json",
            "actions.json",
            "accounts.json",
            "institutions.json",  # revision 202609121000 — shared account-institution identity
            "transactions.json",
            "txn_audit.json",
            "import_batches.json",
            "statements.json",
            "merge_journal.json",
            "category_groups.json",
            "categories.json",
            "commitments.json",
            "assets.json",
            "asset_categories.json",
            "envelopes.json",
            "income_events.json",
            "recurring_streams.json",
            "targets.json",
            "corrections.json",
            "match_decisions.json",
            "rejected_identities.json",
            "match_reviews.json",
            "budget_assignments.json",
            "plan_state.json",
            "budget_engine_state.json",
            "positions.json",
            "lots.json",
            "grants.json",
            "vest_events.json",
            "option_contracts.json",
            "investment_theses.json",
            "realized_events.json",
            "watchlist.json",
            "automation_runs.json",
            "payees.json",
            "automations.json",
            "alerts.json",
            "memory.json",
            "news_sources.json",
            "intel_stories.json",
            "dashboard_layout.json",
            "artifacts.json",
            "attachments.json",  # was "uploads.json" — revision 0041, cross-channel-attachments RFC Phase 2
            "receipts.json",  # revision 0042, cross-channel-attachments RFC Phase 4
            "transaction_receipts.json",  # revision 0042, cross-channel-attachments RFC Phase 4
            "email_snapshots.json",
            # RFC connectors-as-data-sources §C — the bank/broker sibling of email_snapshots.
            "connector_snapshots.json",
        }
    )
    for bridge in portable_bridge.BRIDGED.values():
        assert callable(bridge.export_fn)
        assert callable(bridge.import_fn)


def test_dormant_evidence_bridge_round_trips_via_registry_directly(db_isolated):
    """evidence.json is deliberately NOT in PORTABLE_FILES (dormant — see the ADR),
    but its BRIDGED entry stays wired: exercised directly against the registry (no
    bundle/manifest pipeline involved at all), export_fn/import_fn still round-trip
    correctly. Proves flipping it on later is a manifest-only change, not a rebuild.
    """
    bridge = portable_bridge.BRIDGED["evidence.json"]

    with get_engine().begin() as conn:
        count = bridge.import_fn(conn, EVIDENCE_FIXTURE)
    assert count == 2

    with get_engine().connect() as conn:
        assert bridge.export_fn(conn) == EVIDENCE_FIXTURE


def test_intel_stories_bridge_round_trips_a_story_with_timeline_entries(db_isolated):
    """The core TDD deliverable: a story with several timeline entries (incl. a
    superseded/confirmed pair), exported and re-imported through the bridge directly,
    matches the original exactly — byte-for-byte, including the newest-first `timeline[]`
    order (intel_signals_io.py's own ORDER FIX)."""
    bridge = portable_bridge.BRIDGED["intel_stories.json"]

    with get_engine().begin() as conn:
        count = bridge.import_fn(conn, [INTEL_STORY_FIXTURE])
    assert count == 1

    with get_engine().connect() as conn:
        assert bridge.export_fn(conn) == [INTEL_STORY_FIXTURE]
        assert (
            conn.execute(select(func.count()).select_from(signal_timeline_entries)).scalar_one()
            == 3
        )


def test_daily_digests_is_wipe_only_not_bridged():
    """daily_digests (the split's third table) is deliberately NOT a BRIDGED key — wipe-only,
    mirroring asset_briefs.json's/pending_pushes.json's own precedent, for its own reason (a
    delivered-digest record is regenerable notification history, not a book fact)."""
    assert "daily_digests.json" not in portable_bridge.BRIDGED
    assert "daily_digests.json" in portable_bridge._WIPE_FNS


def test_intel_stories_is_live_daily_digests_is_absent_from_portable_files():
    """intel_stories.json must be LIVE (in core.portable.PORTABLE_FILES) — the exact
    silent-data-loss bug class Wave A caught for income_events.json: any bridged store
    /api/reset's wipe_all unconditionally reaches must also be portable, or its pre-wipe
    backup silently omits it. daily_digests.json must stay OUT — wipe-only, not portable,
    by design (not an oversight)."""
    names = {p.name for p in portable.PORTABLE_FILES}
    assert "intel_stories.json" in names
    assert "daily_digests.json" not in names


# ── Export: DB-sourced, under the original filename, byte-compatible ─────────


def test_export_bundle_sources_price_history_and_instruments_evidence_excluded(
    db_isolated, bridged_manifest
):
    with get_engine().begin() as conn:
        _seed_all(conn)  # evidence seeded too — proves its exclusion is structural

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert {"price_history.json", "instruments.json"} <= names
        assert "evidence.json" not in names, (
            "evidence has real DB rows here — its absence from the bundle must come "
            "from PORTABLE_FILES membership, not from having nothing to export"
        )
        assert json.loads(zf.read("price_history.json")) == PRICE_HISTORY_FIXTURE
        assert json.loads(zf.read("instruments.json")) == INSTRUMENTS_FIXTURE
        instruments_bytes = zf.read("instruments.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly
    # (json.dumps(..., ensure_ascii=False, indent=2), .encode("utf-8")) — a bridged
    # entry is byte-compatible with one a pre-cutover JsonStore would have written.
    with get_engine().connect() as conn:
        expected = json.dumps(
            instruments_io.export_instruments(conn), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert instruments_bytes == expected

    # Never read from (or wrote to) the dead file paths.
    assert not bridged_manifest["price_history"].exists()
    assert not bridged_manifest["instruments"].exists()


def test_export_aborts_loudly_when_a_bridged_export_fails(
    db_isolated, bridged_manifest, monkeypatch
):
    """No silent omission: a bridged store that fails to export takes the whole
    export down with it, rather than being skipped like a genuinely-absent file."""

    def _boom(_conn):
        raise RuntimeError("simulated export failure")

    broken = portable_bridge.BRIDGED["price_history.json"]._replace(export_fn=_boom)
    monkeypatch.setitem(portable_bridge.BRIDGED, "price_history.json", broken)

    with pytest.raises(RuntimeError, match="simulated export failure"):
        svc.build_export_bundle()


def test_bundle_containing_evidence_json_entry_is_rejected_as_unknown(
    db_isolated, bridged_manifest
):
    """A bundle carrying an evidence.json entry (e.g. one built by hand, or from the
    brief window before this ruling) is rejected wholesale as an unknown/unsafe
    entry — the SAME treatment any other non-portable filename already gets. There
    is no special-casing needed: this falls straight out of evidence.json no longer
    being in PORTABLE_FILES / _ALLOWED_FILENAMES."""
    bundle = _build_bundle({"evidence.json": json.dumps(EVIDENCE_FIXTURE).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("evidence.json" in e and "unknown" in e for e in report["errors"])

    with pytest.raises(Exception):
        svc.apply_import(bundle)


def test_export_bundle_contains_intel_stories_with_nested_timeline_daily_digests_excluded(
    db_isolated,
):
    """Uses the REAL (un-narrowed) manifest — intel_stories.json is LIVE by default, like
    every other bridged store's own equivalent test (mirrors
    test_configs_portable.py::test_export_bundle_contains_the_seven_live_config_files_db_
    sourced, which also skips the bridged_manifest narrowing fixture). daily_digests.json
    must never appear — a delivered digest has no export role at all."""
    with get_engine().begin() as conn:
        _seed_intel_stories(conn)
        _seed_daily_digest(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert "intel_stories.json" in names
        assert "daily_digests.json" not in names, "a digest must never travel in an export bundle"
        assert json.loads(zf.read("intel_stories.json")) == [INTEL_STORY_FIXTURE]
        story_bytes = zf.read("intel_stories.json")

    # Byte-format check: matches JsonStore._save_raw's own convention, like every other
    # bridged store's own equivalent assertion.
    with get_engine().connect() as conn:
        expected = json.dumps(
            intel_signals_io.export_intel_stories(conn), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert story_bytes == expected


def test_bundle_containing_daily_digests_json_entry_is_rejected_as_unknown(db_isolated):
    """Mirrors test_bundle_containing_evidence_json_entry_is_rejected_as_unknown's shape:
    daily_digests.json is not in PORTABLE_FILES, so a hand-built bundle carrying one gets
    the SAME unknown/unsafe rejection any other non-portable filename already gets."""
    bundle = _build_bundle({"daily_digests.json": json.dumps([DAILY_DIGEST_FIXTURE]).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("daily_digests.json" in e and "unknown" in e for e in report["errors"])

    with pytest.raises(Exception):
        svc.apply_import(bundle)


# ── Import: old-bundle restore, absent-resets-to-empty, correct row counts ───


def test_import_synthetic_old_bundle_lands_rows_in_db_and_writes_no_dead_files(
    db_isolated, bridged_manifest
):
    """A hand-built bundle stands in for a bundle exported BEFORE these stores were
    migrated (plain JsonStore-shaped bytes, no DB involved in producing it) — proves
    old and new bundles are handled identically, by filename alone."""
    bundle = _build_bundle(
        {
            "price_history.json": json.dumps(PRICE_HISTORY_FIXTURE).encode(),
            "instruments.json": json.dumps(INSTRUMENTS_FIXTURE).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= {"price_history.json", "instruments.json"}
    assert result["row_counts"]["instruments.json"] == 2
    # price_history's count is TOTAL POINTS (2 AAPL + 1 bitcoin) from import_fn's own
    # return, not the generic "_row_count" heuristic (which would say 2 = series count).
    assert result["row_counts"]["price_history.json"] == 3

    with get_engine().connect() as conn:
        assert price_history_io.export_price_history(conn) == PRICE_HISTORY_FIXTURE
        assert instruments_io.export_instruments(conn) == INSTRUMENTS_FIXTURE

    assert not bridged_manifest["price_history"].exists()
    assert not bridged_manifest["instruments"].exists()


def test_import_absent_bridged_file_wipes_that_store_to_empty(db_isolated, bridged_manifest):
    """Matches the non-bridged "absent portable file resets to empty" contract —
    restore is point-in-time replace, not a merge, regardless of backing."""
    with get_engine().begin() as conn:
        instruments_io.import_instruments(conn, INSTRUMENTS_FIXTURE)

    bundle = _build_bundle(
        {
            "price_history.json": json.dumps({}).encode(),
            # instruments.json intentionally absent.
        }
    )

    result = svc.apply_import(bundle)

    assert "instruments.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert instruments_io.export_instruments(conn) == []
    assert not bridged_manifest["instruments"].exists()


def test_corrupt_bridged_import_rolls_back_atomically_nothing_partial(
    db_isolated, bridged_manifest
):
    """An unsorted price series fails loudly (price_history_io's own verification)
    and the whole store's transaction — wipe included — rolls back: the pre-import
    data survives completely untouched, not partially replaced."""
    with get_engine().begin() as conn:
        price_history_io.import_price_history(conn, PRICE_HISTORY_FIXTURE)

    unsorted = {
        "AAPL": [
            {"date": "2026-08-02", "price": 1.0, "source": "yahoo"},
            {"date": "2026-08-01", "price": 2.0, "source": "yahoo"},  # out of order
        ]
    }
    bundle = _build_bundle({"price_history.json": json.dumps(unsorted).encode()})

    with pytest.raises(ValueError, match="not sorted strictly ascending"):
        svc.apply_import(bundle)

    with get_engine().connect() as conn:
        assert price_history_io.export_price_history(conn) == PRICE_HISTORY_FIXTURE
    assert not bridged_manifest["price_history"].exists()


def test_bridged_import_never_fires_write_hooks(db_isolated, bridged_manifest, monkeypatch):
    """Checkpoint-interplay ruling (the ADR): a bridged import is an admin restore,
    not an AI-turn mutation — it must fire NEITHER the whole-file hook NOR the
    per-row DB hook, so it stays outside the checkpoint/undo system entirely."""
    from backend.core import io as core_io

    write_hook_calls: list = []
    db_hook_calls: list = []
    monkeypatch.setattr(svc, "fire_write_hook", lambda path: write_hook_calls.append(path))
    monkeypatch.setattr(core_io, "fire_db_write_hook", lambda *a, **k: db_hook_calls.append(a))

    bundle = _build_bundle(
        {
            "price_history.json": json.dumps(PRICE_HISTORY_FIXTURE).encode(),
            "instruments.json": json.dumps(INSTRUMENTS_FIXTURE).encode(),
        }
    )
    svc.apply_import(bundle)

    assert write_hook_calls == []
    assert db_hook_calls == []


def test_round_trip_export_then_import_is_stable(db_isolated, bridged_manifest):
    with get_engine().begin() as conn:
        _seed_all(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert result["row_counts"]["price_history.json"] == 3
    with get_engine().connect() as conn:
        assert price_history_io.export_price_history(conn) == PRICE_HISTORY_FIXTURE
        assert instruments_io.export_instruments(conn) == INSTRUMENTS_FIXTURE
        # Dormant =/= deleted: nothing in a normal export->import cycle ever wipes
        # evidence, since it's never in PORTABLE_FILES to begin with.
        assert evidence_io.export_evidence(conn) == EVIDENCE_FIXTURE


def test_intel_stories_bundle_round_trips_through_apply_import_digest_survives_untouched(
    db_isolated,
):
    """The task's own acceptance shape: a story with several timeline entries PLUS a
    digest, exported and re-imported, matches the original exactly. The story+timeline
    round-trips through the bundle byte-for-byte (intel_stories.json IS portable); the
    digest is untouched by the SAME apply_import call — daily_digests is never in
    PORTABLE_FILES, so a data restore has no file driving it to wipe or restore, unlike
    every actually-portable store above."""
    with get_engine().begin() as conn:
        _seed_intel_stories(conn)
        _seed_daily_digest(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert "intel_stories.json" in result["restored_files"]
    assert result["row_counts"]["intel_stories.json"] == 1

    with get_engine().connect() as conn:
        assert intel_signals_io.export_intel_stories(conn) == [INTEL_STORY_FIXTURE]
        from backend.db.daily_digest_store import DailyDigestStore

        assert DailyDigestStore(engine=get_engine()).get("2026-08-20") == DAILY_DIGEST_FIXTURE


def test_import_absent_intel_stories_file_wipes_that_store_to_empty(db_isolated):
    """Matches the non-narrowed "absent portable file resets to empty" contract every
    other live bridged store gets — a data restore is point-in-time replace, not a merge."""
    with get_engine().begin() as conn:
        _seed_intel_stories(conn)

    bundle = _build_bundle({"payees.json": json.dumps([]).encode()})
    # intel_stories.json intentionally absent.
    result = svc.apply_import(bundle)

    assert "intel_stories.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert intel_signals_io.export_intel_stories(conn) == []
        assert (
            conn.execute(select(func.count()).select_from(signal_timeline_entries)).scalar_one()
            == 0
        )


# ── /api/reset — the DB-backed half ───────────────────────────────────────────


def test_reset_bridged_stores_wipes_all_and_keeps_identity_and_migration_ledger(db_isolated):
    """reset_bridged_stores() is called directly here (not driven by PORTABLE_FILES
    at all — portable_bridge.wipe_all iterates BRIDGED unconditionally), so it still
    wipes evidence's tables even though evidence is portability-dormant: a blank-slate
    reset and a travel-with-you export bundle are different questions (see the ADR)."""
    with get_engine().begin() as conn:
        _seed_all(conn)

    with get_engine().connect() as conn:
        users_before = conn.execute(select(func.count()).select_from(users)).scalar_one()
        books_before = conn.execute(select(func.count()).select_from(books)).scalar_one()
        members_before = conn.execute(select(func.count()).select_from(book_members)).scalar_one()
        cutovers_before = conn.execute(select(func.count()).select_from(json_cutovers)).scalar_one()
        write_log_before = conn.execute(select(func.count()).select_from(write_log)).scalar_one()
    assert users_before == 1
    assert books_before == 1
    assert members_before == 1

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert evidence_io.export_evidence(conn) == []
        assert price_history_io.export_price_history(conn) == {}
        assert instruments_io.export_instruments(conn) == []
        assert conn.execute(select(func.count()).select_from(evidence_subjects)).scalar_one() == 0
        # write_log/json_cutovers are migration/checkpoint machinery, never wiped by reset.
        assert (
            conn.execute(select(func.count()).select_from(json_cutovers)).scalar_one()
            == cutovers_before
        )
        assert (
            conn.execute(select(func.count()).select_from(write_log)).scalar_one()
            == write_log_before
        )
        # Identity seeds (1/1/1) are untouched.
        assert conn.execute(select(func.count()).select_from(users)).scalar_one() == users_before
        assert conn.execute(select(func.count()).select_from(books)).scalar_one() == books_before
        assert (
            conn.execute(select(func.count()).select_from(book_members)).scalar_one()
            == members_before
        )


def test_reset_bridged_stores_wipes_intel_stories_signal_timeline_entries_and_daily_digests(
    db_isolated,
):
    """daily_digests is reached by reset_bridged_stores() even though it is NOT a BRIDGED
    key (portable_bridge.wipe_all's own explicit `wipe(conn, "daily_digests.json")` call,
    mirroring asset_briefs.json's precedent) — "reset means reset" is independent of
    portable-bundle membership."""
    with get_engine().begin() as conn:
        _seed_intel_stories(conn)
        _seed_daily_digest(conn)

    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(intel_stories)).scalar_one() == 1
        assert (
            conn.execute(select(func.count()).select_from(signal_timeline_entries)).scalar_one()
            == 3
        )
        assert conn.execute(select(func.count()).select_from(daily_digests)).scalar_one() == 1

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert intel_signals_io.export_intel_stories(conn) == []
        assert conn.execute(select(func.count()).select_from(intel_stories)).scalar_one() == 0
        assert (
            conn.execute(select(func.count()).select_from(signal_timeline_entries)).scalar_one()
            == 0
        )
        assert conn.execute(select(func.count()).select_from(daily_digests)).scalar_one() == 0


def test_reset_bridged_stores_wipes_claims(db_isolated):
    """Regression test for a security-review finding (2026-08-30): `claims` shipped with NO
    `_WIPE_FNS` entry at all, so `/api/reset` silently left model-derived claim statements
    behind — the same "reset means reset" bug class `income_events.json`/`asset_brief` were
    deliberately fixed for. Mirrors the `daily_digests` sibling test immediately above: claims
    is wipe-only (not a BRIDGED key), reached by `portable_bridge.wipe_all`'s own explicit
    `wipe(conn, "claims.json")` call."""
    from backend.db.tables import claims

    with get_engine().begin() as conn:
        _seed_claim(conn)

    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(claims)).scalar_one() == 1

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(claims)).scalar_one() == 0


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup for the two end-to-end /api/reset tests below."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import paths as bpaths
    from backend.api import setup as setup_api

    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    profile = data / "profile.json"
    watchlist = data / "watchlist.json"
    profile.write_text(json.dumps({"name": "Alex"}))

    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile])
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)
    monkeypatch.setattr(bpaths, "WATCHLIST_JSON", watchlist)

    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app), data


def test_reset_endpoint_wipes_bridged_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    """API-level proof (a TestClient pattern already exists nearby —
    tests/test_portability_api.py): POST /api/reset
    really does clear a DB-backed store, not just the service function in isolation."""
    with get_engine().begin() as conn:
        instruments_io.import_instruments(conn, INSTRUMENTS_FIXTURE)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert instruments_io.export_instruments(conn) == []


def test_reset_backup_snapshot_contains_bridged_rows_evidence_excluded(
    db_isolated, tmp_path, monkeypatch
):
    """Regression test for the reset pre-wipe backup fix: post-migration state (no
    instruments.json/price_history.json file on disk — the DB is the only source),
    seed real rows, hit POST /api/reset, and assert the backup snapshot's
    instruments.json/price_history.json actually CONTAIN those pre-reset rows (not
    silently skipped because shutil.copy2's src.exists() is always False for a
    migrated-away file — the bug the original task 7 commit fixed). evidence.json
    must NOT appear in the backup at all, per the dormant-bridge ruling — it was
    never in PORTABLE_FILES for the backup loop to reach.
    """
    from backend.paths import INSTRUMENTS_JSON, PRICE_HISTORY_JSON

    with get_engine().begin() as conn:
        _seed_all(conn)  # evidence seeded too — proves its exclusion isn't "nothing to back up"

    client, data = _reset_client(tmp_path, monkeypatch)
    # This test's whole point is the bridged-file backup path, so extend the
    # narrowed manifest _reset_client set up with the two live bridged filenames
    # (real Path identities — only .name matters for a bridged lookup).
    monkeypatch.setattr(
        portable, "PORTABLE_FILES", [*portable.PORTABLE_FILES, PRICE_HISTORY_JSON, INSTRUMENTS_JSON]
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    restored_instruments = json.loads((backup_dir / "instruments.json").read_text())
    assert restored_instruments == INSTRUMENTS_FIXTURE

    restored_price_history = json.loads((backup_dir / "price_history.json").read_text())
    assert restored_price_history == PRICE_HISTORY_FIXTURE

    assert not (backup_dir / "evidence.json").exists()


# ── Undoable: a /rewind must be able to revert a signal write ────────────────

REPLACEMENT_STORY = {
    "id": "topic-fed-rate-path",
    "type": "topic",
    "instrument_id": None,
    "ticker": None,
    "label": "Fed rate path",
    "status": "active",
    "current_summary": "Markets pricing a pause.",
    "portfolio_impact": "",
    "action_items": [],
    "affected_holdings": None,
    "analyzed": False,
    "read": False,
    "dismissed": False,
    "resolved": False,
    "resolved_reason": None,
    "archived": False,
    "evidence_watermark": None,
    "created_at": "2026-08-25T00:00:00+00:00",
    "updated_at": "2026-08-25T00:00:00+00:00",
    "timeline": [],
}


def test_intel_stories_import_is_undoable_via_checkpoint_restore(db_isolated):
    """intel_signals was checkpoint-INCLUDED; both new tables preserve that — a /rewind
    must be able to undo a signal write, including a bundle import (mirrors
    test_configs_portable.py::test_config_import_is_undoable_via_checkpoint_restore's
    minimal pattern). Neither intel_stories nor signal_timeline_entries is in
    ai.checkpoint._EXCLUDED_TABLES."""
    assert "intel_stories" not in ckpt._EXCLUDED_TABLES
    assert "signal_timeline_entries" not in ckpt._EXCLUDED_TABLES

    with get_engine().begin() as conn:
        _seed_intel_stories(conn)

    bundle = _build_bundle({"intel_stories.json": json.dumps([REPLACEMENT_STORY]).encode()})

    turn_ctx, token = ckpt.begin_turn(
        "sess-intel-bridge-undo", anchor_history_len=0, message_preview="import"
    )
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "intel_stories" in entry["db_tables"]
    # Captured by the wipe phase (3 pre-existing entries deleted), even though the
    # replacement story's own timeline is empty.
    assert "signal_timeline_entries" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert intel_signals_io.export_intel_stories(conn) == [REPLACEMENT_STORY]

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert intel_signals_io.export_intel_stories(conn) == [INTEL_STORY_FIXTURE]
