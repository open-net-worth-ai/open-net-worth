# ── DATA SAFETY (load-bearing) ───────────────────────────────────────────────
# Redirect EVERY user-data + cache path to a throwaway dir BEFORE any backend
# module imports `backend.core.paths` (which resolves DATA_DIR/CACHE_DIR from the
# environment exactly once, at import). This makes it STRUCTURALLY IMPOSSIBLE for a
# test that forgets to monkeypatch its store to read or write the operator's real
# instance data (~/.open-net-worth/data). Per-test `tmp_path` monkeypatching still
# works and layers on top — this is the backstop beneath it. Motivated by a test
# that leaked into the live app_config.json ("My Money" incident). `.env` is loaded
# with override=False, so these set-first values win over any DATA_DIR in .env.
import atexit as _atexit
import os
import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

_TEST_ROOT = _Path(_tempfile.mkdtemp(prefix="onw-pytest-data-"))
# Runs at import time (see above), so there is no pytest session/fixture to hang a
# teardown off of — atexit is the natural counterpart at this scope. Removes ONLY
# this process's own freshly-minted _TEST_ROOT, never anything else's, so it's safe
# alongside concurrent sibling pytest runs sharing the same tmp parent. Best-effort:
# a hard kill (SIGKILL/OOM) skips atexit entirely, which is an accepted residual —
# this fixes the normal-exit leak, not the abnormal-exit one.
_atexit.register(_shutil.rmtree, _TEST_ROOT, ignore_errors=True)
os.environ["DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["CACHE_DIR"] = str(_TEST_ROOT / "cache")
os.environ["LOGS_DIR"] = str(_TEST_ROOT / "logs")
# Guard: never run the suite pointed at a real instance dir.
assert "/.open-net-worth/" not in os.environ["DATA_DIR"], "test DATA_DIR must be a throwaway"

# Model-backed tests need an instance default model, the way a real install has one saved in
# Settings → AI Model. Production never seeds it from the environment (2026-10-10); the suite
# supplies it (`_test_default_model`, below), only while nothing is stored. Set BEFORE the
# pristine snapshot: it is part of every session's baseline environment, not drift, and the
# evals env-drift guard compares against that snapshot.
os.environ["ONW_TEST_DEFAULT_MODEL"] = "anthropic:test-model"

# ── PRISTINE ENV SNAPSHOT (consumed by tests/evals/conftest.py) ───────────────
# Taken HERE, at root-conftest import, because this is the only point guaranteed to run
# before ANY test module is imported. It has to be: `backend/evals/tool_surface/
# bootstrap.py` calls `_isolate()` at MODULE level (`EVAL_ROOT: Path = _isolate()`), so
# merely IMPORTING a tests/evals/ test module — which happens at COLLECTION, before a
# single fixture runs — already writes ONW_QA_MODE=1 and AUTH_DISABLED=true into
# os.environ. A snapshot taken inside a fixture would faithfully preserve the dirt.
# tests/evals/conftest.py restores from this. See that file for the full leak inventory.
PRISTINE_ENV = dict(os.environ)

import pytest

# Run all async tests automatically
pytest_plugins = ["pytest_asyncio"]

# ── db_isolated (+ db_url) re-export ─────────────────────────────────────────────
# tests/db/conftest.py::db_isolated is the ONE test-isolation fixture for anything
# that reaches backend.db.engine.get_engine() (directly or transitively, e.g.
# EvidenceStore()). It has to be usable from test files OUTSIDE tests/db/ (~16 files
# at the tests/ top level), but a conftest.py's fixtures are normally only visible to
# tests at or below its OWN directory. A plain import into this ROOT conftest's module
# namespace is enough: pytest's fixture collection inspects each conftest module's
# namespace for @pytest.fixture-wrapped callables regardless of where they were
# originally defined, so this makes db_isolated visible everywhere — proven by
# tests/db/test_isolation_fixtures.py running from tests/db/ AND by the ~16 adapted
# top-level files (e.g. tests/test_evidence_store.py) running from tests/. The fixture
# body still lives in tests/db/conftest.py, alongside db_engine, which stays
# tests/db/-scoped (not re-exported) — deliberately narrower than pytest_plugins, which
# would also globally expose it.
#
# db_url MUST be re-exported too (Plan-2 final-review C1 fix): db_isolated now takes
# db_url as a fixture PARAMETER (so it honors ONW_TEST_DATABASE_URL, closing the
# dialect-blind-spot this review found), and pytest resolves a fixture's own
# dependencies by NAME against whatever conftest chain the REQUESTING test file sees —
# not against where db_isolated's code happens to live. Without also re-exporting
# db_url here, every one of the ~100 db_isolated call sites outside tests/db/ broke
# with "fixture 'db_url' not found" the moment db_isolated started depending on it
# (caught by the full-suite gate, not tests/db alone, since tests/db is already
# inside db_url's home scope and never needed this).
# seed_stub_accounts (plan 3 task 4 sweep): a plain helper function, not a fixture — see
# tests/db/conftest.py's own docstring. Re-exported here for the same reason db_isolated
# is: importable as `from tests.conftest import seed_stub_accounts` from any test file,
# not just ones under tests/db/.
from tests.db.conftest import (  # noqa: F401
    _sqlite_golden_head,  # noqa: F401 — db_isolated's own dependency, must resolve outside tests/db/ too
    db_isolated,
    db_url,
    seed_stub_accounts,  # noqa: F401
)


def pytest_addoption(parser):
    parser.addoption(
        "--flag",
        action="append",
        default=[],
        metavar="NAME=on|off",
        help="force a feature flag for the whole run (repeatable) — docs/FEATURE_FLAGS.md",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requires ANTHROPIC_API_KEY and makes LLM calls"
    )
    config.addinivalue_line("markers", "flags(**values): force feature flags for this test")
    config.addinivalue_line(
        "markers", "flag_both(name): run this test with the feature flag off and on"
    )
    from backend.core import feature_flags as _ff
    from tests.flags import TEST_FLAGS

    _ff.REGISTRY.update(_ff.build_registry(_ff.FLAGS + TEST_FLAGS))


def pytest_generate_tests(metafunc):
    marker = metafunc.definition.get_closest_marker("flag_both")
    if marker is None:
        return
    (name,) = marker.args
    metafunc.parametrize(
        "_feature_flags",
        [{name: False}, {name: True}],
        ids=[f"{name}=off", f"{name}=on"],
        indirect=True,
    )


@pytest.fixture(autouse=True)
def _feature_flags(request, monkeypatch):
    """Apply ``--flag``, ``@pytest.mark.flags`` and ``flag_both`` (tests/flags.py), most
    specific last so it wins; clear any ``ONW_FLAG_*`` inherited from the shell."""
    from backend.core.feature_flags import ENV_PREFIX, override
    from tests.flags import parse_cli

    for key in [k for k in os.environ if k.startswith(ENV_PREFIX)]:
        monkeypatch.delenv(key)
    values = parse_cli(request.config.getoption("--flag"))
    for marker in reversed(list(request.node.iter_markers("flags"))):
        values.update(marker.kwargs)
    values.update(getattr(request, "param", None) or {})
    with override(**values):
        yield


def pytest_collection_modifyitems(items):
    skip_integration = pytest.mark.skip(reason="ANTHROPIC_API_KEY not set")
    for item in items:
        if "integration" in item.keywords and not os.environ.get("ANTHROPIC_API_KEY"):
            item.add_marker(skip_integration)


# ── DB SCHEMA (load-bearing, mirrors the DATA_DIR redirect above) ─────────────
# EvidenceStore (plan 1, task 7) is the first DB-backed store: EvidenceStore()
# resolves lazily via backend.db.engine.get_engine(), which — absent its own
# isolation — points at DATA_DIR/onw.db (already a throwaway path via the
# redirect above). But unlike a missing JsonStore FILE, which every JSON store
# reads as "empty", a missing/unmigrated sqlite TABLE is a hard error, not an
# empty read. Most evidence-touching tests isolate their own tmp database via
# the db_isolated fixture (tests/db/conftest.py, re-exported above); a few reach
# EvidenceStore() only TRANSITIVELY (e.g. monitoring.collect.gather_rss with no
# store= passed) with nothing in the test file to grep for and isolate explicitly.
# Migrating the shared default path once, here, restores the old "missing = empty"
# default for anything that never claims its own isolation. It is never POPULATED —
# nothing here writes to it; a test that reaches it is cleared below — so this
# changes no test's data, only whether an unisolated read errors.


# Model-backed tests need an instance default model (see ONW_TEST_DEFAULT_MODEL, set with the
# other process-wide test env above the pristine snapshot); this supplies it only while nothing
# is stored. A test of the unconfigured case unsets ONW_TEST_DEFAULT_MODEL.
@pytest.fixture(scope="session", autouse=True)
def _test_default_model():
    from backend.db import model_config_store

    original = model_config_store.ModelConfigStore._read_raw

    def _read_raw(self):
        data = original(self)
        ref = os.environ.get("ONW_TEST_DEFAULT_MODEL", "")
        return data if data or not ref else {"default_model": ref}

    model_config_store.ModelConfigStore._read_raw = _read_raw
    yield
    model_config_store.ModelConfigStore._read_raw = original


@pytest.fixture(scope="session", autouse=True)
def _migrate_default_evidence_db():
    from alembic import command

    from backend.db.alembic_config import make_config
    from backend.db.engine import database_url

    command.upgrade(make_config(database_url()), "head")


# Tier-1 identity (seeded 1/1/1 by revision 0001 — see backend/db/tables.py) plus
# json_cutovers (a permanent absorption LEDGER, not per-test working data: it is
# written once by a migration, not by test bodies, and clearing it per-test would
# make no sense for a row nothing in a test run ever repopulates). Every other table
# registered on backend.db.tables.metadata is Tier-2 world data, cleared below.
_TIER2_CLEAR_EXEMPT = frozenset({"users", "books", "book_members", "json_cutovers"})


def _clear_tier2_tables(engine) -> None:
    """DELETE every non-exempt table, in REVERSE topological order so a child table
    (e.g. evidence_subjects, FK'd to evidence) is always cleared before its parent —
    correct regardless of whether the dialect enforces ON DELETE CASCADE. One mechanism
    for all current AND FUTURE Tier-2 tables (plan 2 adds ~50 more stores): a new table
    registered on ``metadata`` is covered automatically, no new per-table clause needed.
    ``metadata.sorted_tables`` only contains tables this module defines via ``Table(...)``
    — alembic's own bookkeeping table (``alembic_version``) is never in it, so it is
    never at risk here even though it is not in the exempt set.

    DELIBERATELY does NOT skip a table missing from the live schema. Every table on
    ``metadata`` must exist at head — ``tests/db/test_migrations.py``'s ``alembic check``
    is the assertion of that — so a missing one is a real defect (a migration dropped a
    table whose ``Table(...)`` registration was left behind), and this function raising is
    how it gets found. A tolerant "skip what is absent" version was written while chasing
    exactly that bug (revision 0040 dropping ``tool_grants``) and then removed once the
    root cause was fixed: it would have made the NEXT such divergence invisible here, and
    an inspector round-trip per test is not free either."""
    from backend.db import tables as _tables

    with engine.begin() as conn:
        for table in reversed(_tables.metadata.sorted_tables):
            if table.name in _TIER2_CLEAR_EXEMPT:
                continue
            conn.execute(table.delete())


# ── Global-state isolation (prevents cross-test pollution) ────────────────────
# Two known sources of cross-test pollution are reset here automatically:
#
# 1. core.fx._fx_rates_loader — a process-wide mutable singleton.  Tests in
#    test_chat_mentions.py and test_mentions_service.py call
#    fx.set_fx_rates_loader(None) in their teardown without restoring the real
#    loader (lambda: load_fx_cache()).  Any subsequent test that calls to_base()
#    without explicit rates then falls back to {base_currency(): 1.0} only —
#    missing foreign-currency rates → wrong 1:1 pass-through.
#    Fix: save/restore the loader value around every test.
#
# 2. Shared DATA_DIR / profile — a test that seeds base_currency="USD" into the
#    shared profile row and does not clean it up leaves every later test seeing
#    "USD" as the base even when it expects the default "SGD".  Any test that
#    relies on the default base (e.g. test_ingestion_orchestrator) fails if it
#    happens to run
#    after the seed tests.
#    Fix: remove profile.json from the shared DATA_DIR after each test so the
#    next test sees the clean fallback ("SGD").  Per-test tmp_path fixtures that
#    monkeypatch PROFILE_JSON are unaffected — they shadow the shared path.


# _isolate_format_profiles — RETIRED (plan 4d task 2): StatementFormatStore is DB-backed
# now (PayloadTableStore), so there is no per-store `STATEMENT_FORMATS_JSON` module
# attribute left to monkeypatch. Per-test isolation for the shared default engine is now
# the GENERIC `_clear_tier2_tables` mechanism below (`_reset_global_fx_and_profile`'s
# teardown half) — `statement_formats` is registered on `backend.db.tables.metadata` like
# every other Tier-2 table, so it is wiped after every test with no per-store fixture
# needed; a test using `db_isolated`/`db_engine` gets a genuinely fresh table regardless.


@pytest.fixture(autouse=True)
def _sql_console_role_for_postgres_ci(monkeypatch):
    """On a Postgres test run the SQL console needs its dedicated-role URL (it is refused
    without one). CI's single test role stands in for it; the refusal itself is tested in
    tests/db/test_sql_console_postgres_gate.py, which clears this."""
    url = os.environ.get("ONW_TEST_DATABASE_URL") or ""
    if url.startswith("postgres"):
        monkeypatch.setenv("ONW_SQL_CONSOLE_DATABASE_URL", url)


@pytest.fixture(autouse=True)
def _isolate_statement_files(tmp_path, monkeypatch):
    """Give every test its own statement-BYTES dir.

    Root cause of the suite's worst flake, historically: every consumer of the statements
    blob directory (``statement_store.py``, ``money_io.py``, ``portable_bridge.py``, the
    legacy ``backend/paths.py`` re-export shim) used to ``from backend.core.paths import
    STATEMENTS_FILES_DIR``, each binding its OWN module-local name at import time — a test
    that patched only one module's copy left the others still writing into the process-wide
    ``_TEST_ROOT/data/statements``, so 12+ test files that each believed they were isolated
    were in fact sharing one directory (measured: 21 of 29 whole-suite failures, none of
    which reproduced when the file ran alone), and grew into a ~13-file "double-monkeypatch
    dance" once ``money_io``/``portable_bridge`` joined the split (2026-08-22
    consolidate-derived-data-storage RFC, the day the parsed-rows sidecar moved onto
    ``STATEMENTS_FILES_DIR/<id>.parsed.json``).

    2026-08-22, security review fix round 2 (Fix 3): every consumer now calls
    ``backend.core.paths.statements_files_dir()`` — a bare accessor function that reads THIS
    module's own global fresh on every call — instead of importing the constant. There is
    now exactly ONE binding to patch: ``backend.core.paths.STATEMENTS_FILES_DIR`` itself.
    Every consumer sees the patch immediately, resolved at call time. The legacy
    ``backend.paths`` re-export shim still carries its own frozen ``STATEMENTS_FILES_DIR``
    snapshot (``from backend.core.paths import *``, cosmetic backward compat only — nothing
    live reads through it any more) so it is patched too, purely so a stray direct read of
    ``backend.paths.STATEMENTS_FILES_DIR`` never sees a stale value either.

    A flaky suite is worse than a red one: it trains everyone to re-run rather than read,
    and it makes every lane's "N failures" report unfalsifiable.
    """
    import backend.core.paths as _core_paths
    import backend.paths as _paths

    # Deliberately NOT tmp_path/"statements": several test fixtures create exactly that
    # path themselves with a bare .mkdir(), which raises FileExistsError if we get there
    # first. A name no test claims keeps this backstop invisible to tests that already
    # isolate themselves.
    stmts = tmp_path / "_conftest_statement_files"
    stmts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_core_paths, "STATEMENTS_FILES_DIR", stmts)
    monkeypatch.setattr(_paths, "STATEMENTS_FILES_DIR", stmts)


@pytest.fixture(autouse=True)
def _reset_global_fx_and_profile():
    """Save/restore core.fx._fx_rates_loader and clear the shared profile.json
    after each test so neither the FX-loader singleton nor a seeded base_currency
    leak into the next test.

    Also resets the shared CategoryStore (DB-backed, plan 4a task 2) to exactly
    BUILTIN_CATEGORIES BEFORE each test — mirrors the old unconditional
    ``CATEGORIES_JSON.write_text(builtins)`` this replaces: ``replace_all`` (wipe +
    insert), not ``bulk_insert``, so it is idempotent no matter what the table
    already held (a stale custom category from an uncleared prior test, or nothing at
    all) — never a duplicate-id IntegrityError. Targets whatever engine get_engine()
    resolves to AT THIS POINT: correct for the ~majority of callers that never
    isolate their own database, but a test whose OWN fixture requests db_isolated
    redirects the engine AFTER this autouse fixture already ran (autouse fixtures set
    up before explicitly-requested ones at the same scope) — such a test must (and
    several already do, e.g. tests/test_envelope_flip.py's env fixture) reseed
    BUILTIN_CATEGORIES itself, once db_isolated is active."""
    from backend.core import fx as _fx
    from backend.core.paths import PAYEES_JSON as _PAYEES_JSON
    from backend.core.paths import TXN_AUDIT_JSON as _TXN_AUDIT_JSON

    try:
        from backend.db.category_store import CategoryStore as _CategoryStore
        from backend.models.category_defs import BUILTIN_CATEGORIES as _BUILTIN_CATEGORIES

        _CategoryStore().replace_all(list(_BUILTIN_CATEGORIES))
    except Exception:
        pass

    # Reset the shared profile row (DB-backed as of Wave 5, JSON->DB) to empty so a base_currency
    # (or any profile field) seeded by one test starts clean for the next — mirrors the categories
    # reseed above, and REPLACES the old post-test profile.json unlink. base_currency() reads this
    # store now, so an empty row falls back to the "SGD" default exactly as a missing file did.
    # A db_isolated test redirects the engine AFTER this autouse fixture runs, so it seeds its own
    # profile once isolated (same caveat as categories above).
    try:
        from backend.db.profile_store import ProfileStore as _ProfileStore

        _ProfileStore().set_raw({})
    except Exception:
        pass

    # Reset + reseed the shared news-sources STORE so every test starts with the full
    # builtin source set, all enabled — the hot tier gates its fetches on this store
    # (services.news_sources.active_sources), so leaked rows (a test's src_test_* row, a
    # disabled builtin) silently change what a later rss_poller_hot test observes. This
    # used to wipe news_sources.json; since Wave D moved the store into the DB that file
    # write became a NO-OP and the reset silently stopped resetting — rows accumulated
    # across the whole run (found 2026-08-12 chasing order-dependent poller failures).
    try:
        from backend.db.news_source_store import NewsSourceStore as _NewsSourceStore
        from backend.services.news_sources import ensure_builtin_sources as _ensure_sources

        _NewsSourceStore().replace_all([])
        _ensure_sources()
    except Exception:
        pass

    # Reset the hot tier's own in-process state (INT9 Phase B: failure-backoff AND the
    # min_interval "ticks since last attempt" politeness counter) — both live in
    # rss_poller_health.py (split out of rss_poller_hot.py), module-level dicts/counter that
    # persist for the life of the test process, keyed by `via`. `api:gdelt` in particular is
    # reused across many tests; without this reset, a prior test's GDELT fetch could make a
    # LATER single-tick test's GDELT politeness gate see it as "not yet due" — cross-test
    # flakiness that has nothing to do with what that test is actually exercising. Reset via
    # the OWNING module directly (not the rss_poller_hot re-export) — `_hot_tick_counter` is a
    # reassigned int, so setting it through a re-exported name would only rebind rss_poller_
    # hot's own copy, leaving rss_poller_health's real counter (what _due_for_interval actually
    # reads) untouched.
    try:
        import backend.monitoring.rss_poller_health as _rss_health

        _rss_health._source_health.clear()
        _rss_health._last_attempt_tick.clear()
        _rss_health._hot_tick_counter = 0
    except Exception:
        pass

    # Snapshot the loader installed at session start (services.fx_cache registers
    # lambda: load_fx_cache() at import time; we want to put THAT back, not None).
    #
    # The import is LOAD-BEARING, not decorative. On the FIRST test of a session
    # services.fx_cache has not necessarily been imported yet, so this snapshot was None
    # — and the teardown below then pinned None for the WHOLE RUN. Every test after the
    # first got `_load_rates() == {base: 1.0}`, silently passing every non-base currency
    # through 1:1: a test that believed it was exercising real FX conversion via the
    # `rates=None` path was not exercising it at all. Found 2026-08-23 while building the
    # transfer rate-sanity guard, whose refusals fired on the first test of the file and
    # on no test after it. Importing here guarantees there is always a REAL loader to
    # snapshot, whatever order the suite happens to run in.
    import backend.services.fx_cache  # noqa: F401  (registers the loader as a side effect)

    _prior_loader = _fx._fx_rates_loader

    yield

    # Restore loader unconditionally — handles both the set-to-None teardown in
    # test_chat_mentions/test_mentions_service AND any test that installs a custom
    # loader without cleaning up.
    _fx.set_fx_rates_loader(_prior_loader)

    # Clear the shared payees.json so a payee/default-category learned by one test
    # (the create_transaction choke point auto-resolves + learns — P4-PAYEE) does not
    # leak into the next test and silently auto-categorize its transactions. Per-test
    # tmp_path fixtures that monkeypatch payee_registry._store._path are unaffected.
    try:
        _PAYEES_JSON.unlink(missing_ok=True)
    except Exception:
        pass

    # Clear the shared txn_audit.json so provenance events recorded by one test (the
    # create/update/delete ledger choke points now emit them — transaction-provenance RFC)
    # do not accumulate across the suite. Per-test tmp_path fixtures are unaffected.
    try:
        _TXN_AUDIT_JSON.unlink(missing_ok=True)
    except Exception:
        pass

    # Clear the shared accounts.json AND transactions.json — TOGETHER, always. An account's
    # opening balance is a POINTER into the transaction store (`opening_txn_id`, ADR
    # 2026-07-31-opening-balance-is-one-fact), so scrubbing one and leaving the other is a
    # DANGLING pointer, which the ledger deliberately raises `DataIntegrityError` on rather
    # than silently mis-folding a balance — a half-scrubbed pair lets one test's accounts
    # point at another test's transactions. Per-test tmp_path fixtures that
    # monkeypatch the store `_path`s shadow these and are unaffected.
    try:
        from backend.core.paths import ACCOUNTS_JSON as _ACCOUNTS_JSON
        from backend.core.paths import TRANSACTIONS_JSON as _TRANSACTIONS_JSON

        _ACCOUNTS_JSON.unlink(missing_ok=True)
        _TRANSACTIONS_JSON.unlink(missing_ok=True)
    except Exception:
        pass

    # Clear the shared planning-store JSON files (plan 4a task 6 — revision 0010 is
    # what first makes these ABSORBABLE; the leak vector itself is pre-existing, same
    # as accounts/transactions above). Planning stores' OWN reset/import bridge landed
    # in plan 4a task 7 (`backend/db/portable_bridge.py`'s `BRIDGED`/`_WIPE_FNS`): for
    # the eight LIVE planning filenames, `/api/reset` and a full-replace import now
    # `continue` past the raw file-write branch entirely (`portable_bridge.BRIDGED.
    # get(path.name) is not None`), so neither endpoint writes one of these 12 paths
    # directly any more — this cleanup block is now a defensive backstop against some
    # OTHER stray-file source (a test writing one of these paths directly for an
    # unrelated reason, or a future regression reopening the vector), not the live
    # leak the original comment described. Kept unconditional (cheap `unlink(missing_
    # ok=True)`, normally a no-op now) rather than removed, for the same reason the
    # accounts/transactions clear above it was never removed once THEIR bridge landed
    # (plan 3 task 6): `core.portable`'s constants, like `versions_evidence_source`'s,
    # are bound once at import and never see a later per-test DATA_DIR monkeypatch, so
    # IF a file ever did leak here, ANY later test's plain `command.upgrade(...,
    # "head")` (db_engine/db_isolated — neither monkeypatches the 12 SOURCE constants)
    # would silently absorb it into that test's otherwise-fresh database — for
    # `plan_state` (a one-row-per-book singleton) a second absorb elsewhere in the same
    # run collides on the book_id primary key outright. Per-test tmp_path fixtures that
    # monkeypatch `versions_evidence_source`'s SOURCE constants (this file's own
    # `test_planning_cutover.py`) are unaffected.
    try:
        from backend.core.paths import BUDGET_ASSIGNMENTS_JSON as _BUDGET_ASSIGNMENTS_JSON
        from backend.core.paths import CATEGORIES_JSON as _CATEGORIES_JSON
        from backend.core.paths import CATEGORY_GROUPS_JSON as _CATEGORY_GROUPS_JSON
        from backend.core.paths import COMMITMENTS_JSON as _COMMITMENTS_JSON
        from backend.core.paths import CORRECTIONS_JSON as _CORRECTIONS_JSON
        from backend.core.paths import ENVELOPES_JSON as _ENVELOPES_JSON
        from backend.core.paths import INCOME_EVENTS_JSON as _INCOME_EVENTS_JSON
        from backend.core.paths import MATCH_DECISIONS_JSON as _MATCH_DECISIONS_JSON
        from backend.core.paths import MATCH_REVIEWS_JSON as _MATCH_REVIEWS_JSON
        from backend.core.paths import PLAN_STATE_JSON as _PLAN_STATE_JSON
        from backend.core.paths import RECURRING_STREAMS_JSON as _RECURRING_STREAMS_JSON
        from backend.core.paths import TARGETS_JSON as _TARGETS_JSON

        for _planning_json in (
            _CATEGORY_GROUPS_JSON,
            _CATEGORIES_JSON,
            _COMMITMENTS_JSON,
            _ENVELOPES_JSON,
            _INCOME_EVENTS_JSON,
            _RECURRING_STREAMS_JSON,
            _TARGETS_JSON,
            _PLAN_STATE_JSON,
            _CORRECTIONS_JSON,
            _MATCH_DECISIONS_JSON,
            _MATCH_REVIEWS_JSON,
            _BUDGET_ASSIGNMENTS_JSON,
        ):
            _planning_json.unlink(missing_ok=True)
    except Exception:
        pass

    # Clear every Tier-2 table (evidence today; ~50 more stores land on this same
    # mechanism in plan 2) written into the shared default DB (see
    # _migrate_default_evidence_db above) by a test that reached it without its own
    # isolated tmp database. Mirrors the JSON-store clears above, now that these are
    # DB-backed. A test that DID isolate its own database (the db_isolated fixture)
    # never touches this shared DB in the first place, so this is a no-op for it.
    #
    # MUST NOT call get_engine() unconditionally here: pytest teardown is LIFO, and
    # this fixture (autouse, no fixture params) is set up BEFORE db_isolated (which
    # depends on monkeypatch) within a test that uses both — so its teardown runs
    # this code AFTER db_isolated's `finally: dispose_engine()` but BEFORE
    # monkeypatch restores ONW_DATABASE_URL. In that window `_engine is None` and the
    # env var still points at the isolated test's about-to-vanish tmp DB, so a bare
    # get_engine() would lazily REBIND the process-wide singleton to that orphaned
    # tmp path — and nothing disposes it again, so every later test that never
    # requests db_isolated/db_engine would silently inherit it. Guard on the
    # singleton already being bound; an already-disposed engine means there is
    # nothing shared left to clear. Never re-derive a URL from the environment here.
    try:
        from backend.db import engine as _eng

        if _eng._engine is not None:
            _clear_tier2_tables(_eng.get_engine())
    except Exception as exc:  # noqa: BLE001 — teardown must not fail the run; see below
        # REPORTED, never silent (2026-08-14). This catch has to stay broad — a teardown
        # that raises turns one test's cleanup problem into a cascade of unrelated errors,
        # and the LIFO race described above is a legitimate, expected miss. But `pass` made
        # the wrong thing invisible: `_clear_tier2_tables` runs ALL its DELETEs in ONE
        # transaction, so a single bad table aborts the whole cleanup — every Tier-2 table
        # silently stops being cleared while the suite still looks green, and isolation
        # decays until some unrelated test fails for a reason that makes no sense.
        #
        # That is not hypothetical: a table registered on `metadata` but dropped by a
        # migration (`tool_grants`, revision 0040) put the suite in exactly this state, and
        # only `alembic check` and one direct-call test noticed. A warning shows up in
        # pytest's summary, so the next regression of this class is visible on the run that
        # causes it rather than the run that trips over it.
        import warnings

        warnings.warn(
            f"Tier-2 test cleanup FAILED — cross-test isolation is degraded for the rest of "
            f"this run. The whole DELETE transaction rolls back, so NO Tier-2 table was "
            f"cleared. Most likely a table on backend.db.tables.metadata that does not exist "
            f"in the live schema (check `alembic check`). Cause: {exc!r}",
            stacklevel=2,
        )

    # Reset the provenance suppression + scope ContextVars defensively so a test that sets
    # an AuditScope can't leak it into the next test's ledger writes.
    try:
        from backend.core.audit_context import clear_audit_scope as _clear_audit_scope

        _clear_audit_scope()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_live_price_cache():
    """Clear `services.prices._live_price_cache` before AND after each test.

    Module-level, process-lifetime dict (2026-09-01, the extended-hours price-consistency
    pass) — `fetch_live()` writes a resolved live price into it on every real fetch,
    `positions.hydration_context()` reads it to keep `last_known` fresh across the ~25 bare
    `hydrated()` callers. Without a reset, a price one test's `fetch_live` mock (or a stray
    real fetch) writes for a given price_source_id would silently outlive that test and
    change what a LATER, unrelated test's bare `hydrated()`/`ledger.net_worth()` call
    resolves to — the exact cross-test-leak shape `_reset_global_fx_and_profile` above
    exists to prevent for FX/profile state."""
    from backend.services import prices as _prices

    _prices._live_price_cache.clear()
    yield
    _prices._live_price_cache.clear()


@pytest.fixture(autouse=True)
def _reset_tool_offer_scopes():
    """The per-turn tool-offer ContextVars (explicit offer, loaded groups, loaded tool names)
    live in the test thread's own context, which sync tests share for the whole session.
    A test that loads names (or sets an offer) and does not unwind them leaves the NEXT
    ``_active_tools()`` call widened — the cold-start offer-size budget once measured the full
    304-tool registry because of it. Reset after every test, once the modules are loaded."""
    yield
    import sys

    if "backend.ai.tools._extra_groups_scope" not in sys.modules:
        return
    from backend.ai.tools import _skill_scope
    from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names

    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())


# Synthetic cross rates, each "how many SGD one unit buys" (a common pivot, so any currency
# in the table can be the book's base). Round numbers on purpose — nothing here is a market
# quote. A test that asserts a specific converted figure supplies its own table.
SYNTHETIC_FX_VS_SGD = {
    "SGD": 1.0,
    "USD": 1.35,
    "EUR": 1.5,
    "GBP": 1.7,
    "HKD": 0.17,
    "TWD": 0.042,
    "VND": 0.000054,
}


@pytest.fixture
def fx_rates(monkeypatch, tmp_path):
    """Give the test a real FX cache holding ``SYNTHETIC_FX_VS_SGD`` — OPT-IN.

    Since 2026-09-25 a missing rate REFUSES conversion (``MissingFxRateError``) instead of
    assuming 1:1, and the suite's default cache is empty, so a test whose book holds a
    foreign-currency row has no rate unless it supplies one. This writes the table to a
    per-test ``fx_cache.json`` and points ``services.fx_cache`` at it, so EVERY reader sees
    it — the ``core.fx`` loader and the modules that bound ``load_fx_cache`` by name (a
    ``monkeypatch.setattr(fx_cache, "load_fx_cache", ...)`` misses those). A currency outside
    the table stays unavailable, and a test of the refusal simply does not request this.

    The cache stores rates relative to the base (``load_fx_cache`` pins ``rates[base] = 1.0``),
    so the table is rebased on the base in force WHEN THIS RUNS — seed the profile's
    ``base_currency`` first, or call the returned ``seed(base=...)`` again after changing it.
    Returns that ``seed`` callable; ``extra`` adds or overrides SGD-pivot rates.
    """
    import json

    import backend.services.fx_cache as _fx_cache
    from backend.core.fx import base_currency

    path = tmp_path / "fx_cache.json"
    monkeypatch.setattr(_fx_cache, "_FX_CACHE_FILE", path)

    def seed(base: str | None = None, extra: dict[str, float] | None = None) -> None:
        table = {**SYNTHETIC_FX_VS_SGD, **(extra or {})}
        pivot = table[(base or base_currency()).upper()]
        rebased = {ccy: rate / pivot for ccy, rate in table.items()}
        path.write_text(json.dumps({"rates": rebased}), encoding="utf-8")

    seed()
    return seed


@pytest.fixture
def fixed_local_tz():
    """Pin the process's local timezone to a fixed, DST-free +12h UTC offset for the
    duration of a test — used by the naive/aware timestamp-sort regression tests
    (I6 follow-up: `core.timestamps.sort_key`) so a mixed naive-local/aware-UTC
    ordering bug reproduces deterministically regardless of the test runner's own
    timezone. Skips on platforms without `time.tzset` (Windows)."""
    import time as _time

    if not hasattr(_time, "tzset"):
        pytest.skip("time.tzset unavailable on this platform")
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Etc/GMT-12"
    _time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    _time.tzset()


@pytest.fixture(autouse=True)
def _restore_backend_logger():
    """Undo any test's mutation of the ``backend`` logger, so it cannot poison later tests.

    ``configure_logging()`` sets ``backend.propagate = False`` — correct in production, where
    the logger owns its own handler. But ``caplog`` captures by attaching a handler to the
    ROOT logger, so once propagation is off, root never sees another ``backend.*`` record and
    every later ``caplog`` assertion silently sees nothing.

    Anything that boots the real app trips this: ``with TestClient(app)`` runs the lifespan
    in-process, which calls ``configure_logging()``. One such test poisoned the rest of the
    SESSION — ~18 failures across ``test_turn_diagnostics``, ``test_rss_poller_hot``,
    ``test_push_retry`` and the pricing-warning tests, every one of which PASSED in isolation
    and only failed in the full suite. That signature (green alone, red together) is what made
    it survive so long: it reads like flakiness rather than a specific test's side effect.
    Bisected 2026-08-27 to ``tests/test_native_permission_enforcement.py``.

    Two files already carried hand-written save/restore dances for exactly this
    (``tests/db/test_boot_migrates.py``, ``tests/test_recurring_api.py``). Doing it once here,
    for every test, means the next person to add a ``TestClient(app)`` does not have to know.
    """
    import logging as _logging

    logger = _logging.getLogger("backend")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    try:
        yield
    finally:
        logger.handlers, logger.level, logger.propagate = (
            saved[0],
            saved[1],
            saved[2],
        )


@pytest.fixture(autouse=True)
def _no_background_price_backfill(request, monkeypatch):
    """Stop a position-create from spawning a REAL network thread during tests.

    ``services.positions._trigger_backfill_async`` fires a daemon thread that fetches 5 years
    of live closes from Yahoo/CoinGecko. It no-ops under ``ONW_QA_MODE`` — but the suite never
    sets that flag, so every test that creates a position has been starting one. Three distinct
    symptoms, all the same cause:

    * ``no such table`` — the thread outlives its test's tmp database and writes into the
      torn-down engine (``test_positions_stores.py``, full-suite run 2026-08-27);
    * ``partially initialized module 'numpy'`` — the thread races the main thread's import;
    * a demo book whose fabricated price history got EXTENDED with real market closes past its
      anchor, inventing a 20% net-worth cliff and a figure that moved between runs. That one
      took hours to find precisely because a background thread is invisible at the call site.

    Tests that genuinely exercise the trigger mark themselves ``real_price_backfill``.
    """
    if "real_price_backfill" in request.keywords:
        yield
        return
    from backend.services import positions as _positions
    from backend.services import watch_history as _watch_history

    monkeypatch.setattr(_positions, "_trigger_backfill_async", lambda *_a, **_k: None)
    # The Watching pulse's on-demand history fetch (a daemon thread to Yahoo) — same hazard.
    monkeypatch.setattr(_watch_history, "request", lambda *_a, **_k: [])
    yield


@pytest.fixture(autouse=True)
def _no_background_session_title(request, monkeypatch):
    """Stop a chat POST from starting a REAL title call in the background.

    ``POST /api/chat`` on a conversation's opening message schedules
    ``ai.session_titles.schedule_title`` — an asyncio task that calls whatever provider is
    registered on the LLM seam and then writes the session row. In a test that is a model
    call nobody stubbed, racing the test's own teardown of its database. Tests that exercise
    the scheduler itself mark themselves ``real_session_title``.
    """
    if "real_session_title" in request.keywords:
        yield
        return
    from backend.ai import session_titles as _session_titles

    monkeypatch.setattr(_session_titles, "schedule_title", lambda *_a, **_k: False)
    yield
