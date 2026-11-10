"""Per-test DB isolation. SQLite: a file under tmp_path. Postgres (CI sets
ONW_TEST_DATABASE_URL): the public schema is dropped and recreated per test —
tests in this package therefore must not run in parallel against one server."""

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config


def sqlite_only(reason: str):
    """Skip a test on the Postgres run (``ONW_TEST_DATABASE_URL``). For behavior that exists
    only on SQLite — its writer lock, ``PRAGMA data_version`` — or a legacy cutover that only
    ever carried rows on SQLite installs. ``reason`` says which, so the skip is auditable."""
    return pytest.mark.skipif(
        (os.environ.get("ONW_TEST_DATABASE_URL") or "").startswith("postgres"),
        reason=f"SQLite-only: {reason}",
    )


def reversible_tip(url: str) -> str:
    """The newest revision a test can downgrade FROM and reach any older one: the parent of
    the OLDEST revision that refuses a downgrade (``irreversible = True`` in the revision
    file; ``test_every_refused_downgrade_is_marked_irreversible`` keeps the flag honest).

    A test of an old revision's own ``downgrade()`` upgrades to this tip instead of ``head``,
    downgrades to its revision, then upgrades to ``head`` again — the upgrade pass re-runs the
    irreversible revisions forward, which is the only direction they support."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(make_config(url))
    oldest = None
    for rev in script.walk_revisions():
        if getattr(rev.module, "irreversible", False):
            oldest = rev
    assert oldest is not None and isinstance(oldest.down_revision, str)
    return oldest.down_revision


def seed_stub_accounts(txns: list[dict], *, engine=None) -> None:
    """Test helper (plan 3 task 4 sweep): ``transactions.from_account_id``/
    ``to_account_id`` are now REAL foreign keys to ``accounts.id`` (task 2's own
    binding — RESTRICT semantics, so ``delete_account``'s 409-while-referenced
    invariant is DB-enforced). Countless fixtures across the suite predate that FK
    and use synthetic/label account ids ("meridian", "riverside", "cc", ...) that were never
    real JsonStore-era account rows — the file-backed store never validated
    referential integrity, so a bare label string was a legitimate stand-in. Under
    the real FK, inserting such a transaction now raises ``IntegrityError`` unless
    a matching ``accounts`` row exists first.

    Scans ``txns`` for every distinct ``from_account_id``/``to_account_id`` value
    and creates a minimal stub Account (type="cash", currency "SGD") for each id
    NOT already present — one shared helper instead of ~100
    near-identical per-file seeding loops. A test that needs a SPECIFIC account
    shape (e.g. a liability/credit card) should create that account explicitly
    BEFORE calling this — the existing-id check means this never overwrites a
    caller's own, more specific seed."""
    from backend.db.account_store import AccountStore

    store = AccountStore(engine=engine)
    ids: set[str] = set()
    for t in txns:
        for key in ("from_account_id", "to_account_id"):
            v = t.get(key)
            if v:
                ids.add(v)
    if not ids:
        return
    existing = {a["id"] for a in store.all()}
    for acc_id in sorted(ids - existing):
        store.create({"id": acc_id, "name": acc_id, "type": "cash", "currency": "SGD"})


@pytest.fixture
def db_url(tmp_path):
    return os.environ.get("ONW_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db"


@pytest.fixture(scope="session")
def _sqlite_golden_head(tmp_path_factory):
    """One SQLite file migrated to head, built ONCE per pytest session, so every
    per-test sqlite fixture below can clone it via a file copy instead of replaying
    all 57 Alembic migrations from scratch every time. Every migration's DDL/seed
    data is static (no time.time()/datetime.now() in any version script — swept
    2026-08-25), so a copy of this file is byte-identical to what a fresh
    ``command.upgrade(..., "head")`` produces right now; ~500 files call
    db_isolated/db_engine, most per-test-function, and the replay cost was the
    single biggest driver of full-suite wall-clock time.

    Needs the same ``patch_all_cutover_sources_missing`` protection db_engine/
    db_isolated apply before their own upgrade — done here with a manually-scoped
    ``pytest.MonkeyPatch()`` (undone in the ``finally``) since the built-in
    ``monkeypatch`` fixture is function-scoped and this fixture is not."""
    from alembic import command

    # ``mkdir(exist_ok=True)``, not ``mktemp(..., numbered=False)``: this fixture is
    # registered in TWO conftests — ``tests/db/conftest.py`` defines it and
    # ``tests/conftest.py`` re-exports it so ``db_isolated`` resolves outside ``tests/db/``.
    # pytest treats those as two distinct session-scoped fixtures, so both run, and the
    # second ``mktemp`` raised ``FileExistsError`` on the same fixed name — which then
    # cascaded to every test depending on it (10,155 errors in one full-suite run, 314 in
    # ``tests/db`` + ``tests/dedup`` alone). Whichever instance runs second finds the file
    # already at head, so its ``upgrade`` is a no-op and the template is built exactly once.
    golden_dir = tmp_path_factory.getbasetemp() / "golden-head"
    golden_dir.mkdir(exist_ok=True)
    golden_path = golden_dir / "golden.db"
    golden_url = f"sqlite:///{golden_path}"
    monkey = pytest.MonkeyPatch()
    try:
        patch_all_cutover_sources_missing(monkey, golden_dir)
        command.upgrade(make_config(golden_url), "head")
    finally:
        monkey.undo()
    return golden_path


def _clone_sqlite_head(golden_path: Path, sqlite_url: str) -> None:
    """Copy the golden migrated-to-head template onto ``sqlite_url``'s file.
    Caller must already know ``sqlite_url`` is sqlite:/// (never called for postgres —
    there is no file to copy, and CI's postgres job needs a real per-test upgrade)."""
    target = Path(sqlite_url[len("sqlite:///") :])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(golden_path, target)


def point_engine_at(db_url: str, monkeypatch, golden_path: Path) -> None:
    """Point the ``get_engine()`` singleton at ``db_url``, a clone of the golden head on SQLite.

    The clone lands BEFORE the URL is published: a daemon thread left by an earlier test (the
    tool-call telemetry flusher wakes every 2s) can rebuild the singleton the instant the old one
    is disposed, and on a not-yet-copied path it opens an empty WAL database whose pooled
    connection keeps that empty schema after the copy — ``no such table`` for the whole test."""
    from backend.db import engine as eng

    if db_url.startswith("sqlite:///"):
        _clone_sqlite_head(golden_path, db_url)
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    eng.dispose_engine()


def patch_all_cutover_sources_missing(monkeypatch, tmp_path) -> None:
    """Point EVERY cutover source constant in ``versions_evidence_source`` at a file that
    does not exist, so a bare ``command.upgrade(cfg, "head")`` — unlike the real
    ``ensure_migrated()`` entrypoint, this performs NO isolation of its own — never
    accidentally absorbs whatever the ambient, session-shared ``DATA_DIR`` happens to
    hold. Concretely: ``tests/conftest.py``'s autouse ``_reset_global_fx_and_profile``
    fixture unconditionally (re)writes ``news_sources.json`` as ``"[]"`` before EVERY
    test, so any unpatched ``head`` upgrade absorbs it as a real (if empty)
    ``json_cutovers`` row keyed ``store="news_sources"``; a SECOND such upgrade in the
    same test (an up/down/up cycle, or a bare direct-SQL insert of a test's own row
    followed by an unscoped ``json_cutovers`` read) then either collides on that row's
    ``store`` primary key or silently picks up a row the test never intended to see —
    the two concrete regressions this helper fixes (plan 4d fix-A).

    Enumerates every ``*_SOURCE`` attribute on the module DYNAMICALLY (``dir(module)``,
    not a hand-maintained list) so a future wave's new sources are covered automatically —
    the whole point is that nobody has to remember to extend this when the next wave
    lands. A caller that wants ONE specific source to genuinely absorb still calls
    ``monkeypatch.setattr(..., "SOME_SOURCE", real_path)`` afterward — a later
    ``monkeypatch.setattr`` on the same attribute simply overrides this one."""
    import backend.db.alembic.versions_evidence_source as evsrc

    for name in dir(evsrc):
        if name.endswith("_SOURCE"):
            monkeypatch.setattr(evsrc, name, tmp_path / f"does_not_exist_{name}.json")


@pytest.fixture
def scrubbed_db_url(db_url):
    """``db_url`` with the schema emptied first. On Postgres the URL is ONE shared server
    database that earlier tests leave at head, so a test that walks the chain itself (an
    upgrade to a revision, a downgrade) starts from nothing only with this; SQLite is a fresh
    file per test already."""
    if db_url.startswith("postgresql"):
        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    return db_url


@pytest.fixture
def db_engine(db_url, monkeypatch, tmp_path, _sqlite_golden_head):
    from backend.db import engine as eng

    if db_url.startswith("postgresql"):
        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    point_engine_at(db_url, monkeypatch, _sqlite_golden_head)
    if not db_url.startswith("sqlite:///"):
        from alembic import command

        # MUST run before command.upgrade below — see patch_all_cutover_sources_missing's
        # own docstring for why an unpatched upgrade is unsafe even for a single call.
        patch_all_cutover_sources_missing(monkeypatch, tmp_path)
        command.upgrade(make_config(db_url), "head")
    yield eng.get_engine()
    eng.dispose_engine()


@pytest.fixture(autouse=True)
def _isolated_backup_dir(tmp_path, monkeypatch):
    """Every test under tests/db/ gets its own migrate.BACKUP_DIR — the fix-wave's
    disclosed coupling: ``backend.db.migrate.BACKUP_DIR`` is a MODULE-LEVEL constant
    derived from the real DATA_DIR at import time, so any test that exercises
    ``ensure_migrated``/``_sqlite_backup`` without repointing it would write pre-migrate
    backups into the shared throwaway DATA_DIR rather than its own tmp_path — harmless in
    isolation, but needless cross-test disk sharing that grows unbounded over a long run.
    Autouse makes this structural instead of something each test has to remember; a test
    that patches its own BACKUP_DIR value afterward simply overrides this, and a test that
    already patches it to the SAME tmp_path/"backups"/"db" value is a harmless double-patch."""
    from backend.db import migrate as _migrate

    monkeypatch.setattr(_migrate, "BACKUP_DIR", tmp_path / "backups" / "db")


@pytest.fixture
def db_isolated(db_url, monkeypatch, _sqlite_golden_head):
    """One test-isolation mechanism for anything that reaches ``backend.db.engine.get_engine()``
    — directly or transitively (e.g. ``EvidenceStore()``'s lazy internal resolution). Unlike the
    retired ``tests/db_test_engine.py::isolate_evidence_db`` (which monkeypatched the
    ``get_engine`` NAME and never disposed the engine it created), this redirects the REAL
    singleton via ``ONW_DATABASE_URL`` + ``dispose_engine()`` — the same mechanism ``db_engine``
    above uses — and disposes it again on teardown NO MATTER HOW the test exits, so the singleton
    never dangles pointed at a tmp DB whose directory is about to be deleted (the stale-singleton
    hazard). Usable from ANY test directory, not just tests/db/ — see tests/conftest.py's
    re-export.

    DIALECT-AWARE (Plan-2 final-review C1 — fixed 2026-08-07): this used to hardcode its own
    ``sqlite:///{tmp_path}/test.db`` URL instead of consuming ``db_url``, so the CI Postgres job
    — which sets ``ONW_TEST_DATABASE_URL`` and runs ``tests/db`` against a real server — silently
    NEVER exercised any of the ~100 test functions built on this fixture; they all ran SQLite
    regardless of which dialect CI thought it was proving. ``db_url`` already honors
    ``ONW_TEST_DATABASE_URL`` (sqlite under ``tmp_path`` when unset), so consuming it here is the
    whole fix; on Postgres this additionally scrubs the ``public`` schema first — the identical
    ``DROP SCHEMA public CASCADE; CREATE SCHEMA public`` ``db_engine`` performs above — so each
    test still gets a schema no earlier test left rows in (tests in this package must still not
    run in parallel against one Postgres server; see the module docstring).

    Yields the URL (not the engine): callers that want the engine call ``backend.db.engine.
    get_engine()`` themselves, which is exactly what the production code under test does too.

    SQLite path is a ``_sqlite_golden_head`` clone (file copy), not a fresh migration replay
    — see that fixture's docstring. Postgres is unaffected: still a real per-test upgrade,
    since there is no file to clone there.
    """
    from backend.db import engine as eng

    if db_url.startswith("postgresql"):
        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    try:
        point_engine_at(db_url, monkeypatch, _sqlite_golden_head)
        if not db_url.startswith("sqlite:///"):
            from alembic import command

            command.upgrade(make_config(db_url), "head")
        yield db_url
    finally:
        eng.dispose_engine()
