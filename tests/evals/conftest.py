"""Containment for the ONE test directory that deliberately mutates process-wide state.

Every test under ``tests/evals/`` drives the real tool-surface harness
(``backend.evals.tool_surface``). Reaching the model-facing tool surface for real means
the harness boots a real instance, and it does that the only way ``core.paths`` allows —
by mutating process globals:

* ``bootstrap._isolate()`` writes ``ONW_QA_MODE=1``, ``AUTH_DISABLED=true``,
  ``ONW_DISABLE_TELEGRAM=1`` and ``ONW_EVAL_ROOT`` into ``os.environ`` and never
  puts them back.
* ``fixture.build()`` calls ``bootstrap.reset_data_dir()``, which WIPES the shared
  ``DATA_DIR``/``CACHE_DIR`` and reseeds a full 6-account / 12-transaction book,
  ``fx_cache.json`` (USD 1.35), ``price_history.json``, instruments and positions.

None of that is wrong for the harness — it is the point of the harness. What was wrong is
that it escaped the directory. This fixture restores each mutation at the exact boundary
the harness stops owning the process.

MEASURED LEAKS THIS CLOSES (each: shared state → who dirties it → who depends on it clean)

1. ``os.environ["ONW_QA_MODE"]="1"``, latched into the ``core.config._settings`` singleton
   (module-global, resolved lazily on FIRST access and never re-read).
   Dirtied by: any test here that calls ``run_one``/``fixture.build``.
   Broke: ``test_price_backfill.py::TestTriggerBackfillAsync::{test_spawns_thread_and_
   calls_backfill,test_duplicate_trigger_coalesces}`` — ``positions._trigger_backfill_
   async`` early-returns under QA mode, so the thread under test never spawned and the
   test timed out on ``done.wait(5)``.

2. ``os.environ["AUTH_DISABLED"]="true"`` (same singleton latch).
   Dirtied by: as above.
   Broke: ``test_email_snapshot_api.py::test_auth_required_when_remote_and_unconfigured``
   — ``require_auth`` waved the remote caller through, so the route answered 404 instead
   of the asserted 401.

3. Shared ``DATA_DIR`` seeded with a full book (``transactions.json`` etc.).
   Read through ``services.ledger.transactions`` — a MODULE-LEVEL singleton constructed at
   import (``ledger.py:48``), so its path is frozen and a test monkeypatching
   ``transaction_store.TRANSACTIONS_JSON`` does NOT redirect it.
   Dirtied by: as above.
   Broke: ``test_briefing_api.py::{test_spending_block_empty_when_no_transactions,
   test_build_context_new_user_stage,test_build_context_no_native_annotation_for_base_
   currency_holdings}`` — the seeded book's July expenses surfaced as
   "Month-to-date spend: SGD 190" where an empty ledger was asserted.

4. Shared ``CACHE_DIR/fx_cache.json`` seeded with ``USD: 1.35``.
   Dirtied by: ``fixture._seed_fx()``.
   Broke: ``test_recurring_transaction.py::test_recurring_transfer_carries_no_category`` —
   a USD 300 transfer stamped ``base_amount=405.0`` instead of 300.0.
   This one is the reason the whole-suite failure COUNT was unstable run to run: the leaked
   rates only bite when ``core.fx._fx_rates_loader`` also happens to be installed at that
   moment, which depends on which other test ran in between. Same leak either way — only
   its visibility moved.

Deliberately NOT a reset-everything hook: it restores only the four things this directory
is proven to mutate, and it lives beside the tests that mutate them, so the blast radius
stays where it belongs. ``tests/test_no_eval_state_leak.py`` is the regression guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Exactly the variables backend/evals/tool_surface/bootstrap.py::_isolate writes.
_EVAL_ENV_KEYS = (
    "ONW_QA_MODE",
    "AUTH_DISABLED",
    "AUTH_PASSWORD",
    "ONW_DISABLE_TELEGRAM",
    "ONW_EVAL_ROOT",
    "DATA_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
)


# pytest's own per-phase bookkeeping — it rewrites this every setup/call/teardown, so it is
# never evidence of a harness leak.
_PYTEST_OWNED_ENV_KEYS = frozenset({"PYTEST_CURRENT_TEST"})


def _restore_env(env_before: dict[str, str | None]) -> None:
    import backend.core.config as _config

    for key, value in env_before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _config._settings = None


def pytest_collection_finish(session):
    """Undo ``_isolate()``'s import-time env writes as soon as collection ends. The fixture
    below only restores in an EVAL test's teardown, so every test that ran before the first
    eval test — and, under xdist, a whole worker that drew no eval test — ran with
    ``AUTH_DISABLED=true`` and ``ONW_QA_MODE=1`` (auth gates answered 200, backfill never
    spawned). Each eval test re-enters isolation itself."""
    from tests.conftest import PRISTINE_ENV

    _restore_env({key: PRISTINE_ENV.get(key) for key in _EVAL_ENV_KEYS})


def _clear_seeded_files(path_str: str) -> None:
    """Remove the seeded FILES under a throwaway data/cache dir, leaving the tree intact.

    ``fixture.build()`` has already destroyed whatever these held (``reset_data_dir``
    rmtree's them first), so there is no prior content to preserve — the faithful
    restoration is the empty dir the root conftest's DATA_DIR redirect establishes and
    that its per-test seeding layers onto.

    Deliberately files-only rather than ``rmtree`` + recreate: several test modules build
    subdirectories under DATA_DIR/CACHE_DIR and keep using them, and removing a directory
    out from under a live handle turns a clean teardown into an unrelated ``OSError`` in
    a distant module. Unlinking the seeded JSON leaves nothing behind that a later test
    can read, without invalidating anyone's directory.
    """
    if not path_str:
        return
    path = Path(path_str)
    # Belt and braces: never let a misconfigured env turn this into a destructive op.
    assert "/.open-net-worth/" not in str(path), f"refusing to clear live tree: {path}"
    try:
        resolved = str(path.resolve())
    except OSError:
        return
    if not resolved.startswith(("/tmp", "/private/tmp", "/private/var/folders", "/var/folders")):
        return
    for entry in path.rglob("*"):
        if entry.is_file() and not _is_sqlite_db_file(entry):
            try:
                entry.unlink()
            except OSError:
                pass


def _is_sqlite_db_file(entry: Path) -> bool:
    """True for onw.db and its WAL-mode sidecars (``-wal``/``-shm``/``-journal``).

    The sqlite db's lifecycle belongs to the DB layer, not this JSON-store sweep:
    ``bootstrap.reset_data_dir()`` already re-migrates it (fresh + empty) after wiping
    DATA_DIR, and ``tests/conftest.py``'s Tier-2 per-test clearing handles anything a
    later test writes into it — the same "one mechanism, not 50 special cases"
    philosophy as that clearing. ``fixture.build()`` never seeds this file, so removing
    it here was never this sweep's job; doing so only destroyed the migration
    ``reset_data_dir()`` had just performed, moments earlier in the same test."""
    from backend.core.paths import DB_FILE

    return entry.name == DB_FILE.name or entry.name.startswith(DB_FILE.name + "-")


@pytest.fixture(autouse=True)
def _contain_eval_harness_globals():
    """Snapshot/restore the process globals the tool-surface harness mutates."""
    import backend.core.config as _config
    import backend.core.fx as _fx
    from backend.evals.tool_surface import bootstrap
    from tests.conftest import PRISTINE_ENV

    # Restore from the ROOT conftest's pristine snapshot, NOT from a snapshot taken here.
    # `bootstrap.py` calls `_isolate()` at module level, so os.environ is already dirty by
    # the time this fixture first runs — these tests were imported at collection.
    env_before = {key: PRISTINE_ENV.get(key) for key in _EVAL_ENV_KEYS}

    # The previous eval test restored the pristine environment, but core.paths remains
    # frozen to the eval root adopted at collection time. Re-enter isolation before every
    # test so reset_data_dir() wipes the same paths the stores actually use; otherwise the
    # seeded ledger accumulates between tests and money assertions drift with test order.
    bootstrap._isolate()
    _config._settings = None

    # 5. The FX RATES LOADER. `backend/evals/tool_surface/fixture.py:230` calls
    # `set_fx_rates_loader(load_fx_cache)` on every build — a PROCESS-WIDE mutable global —
    # and never puts it back. Clearing the seeded fx_cache.json below is not enough: the
    # LOADER stays registered, so a later test's `to_base()` keeps calling the harness's
    # loader against whatever cache file exists by then.
    #
    # This is the second half of the eval-harness state leak, and the half the env fix did
    # not reach. Symptom: net worth computed as 110,346.53 where the test expected
    # 102,277.65 — a WRONG MONEY NUMBER, arriving only when the eval suite happened to be
    # collected first, which is why the failure rotated between files all day.
    fx_loader_before = _fx._fx_rates_loader

    yield

    _fx._fx_rates_loader = fx_loader_before

    # 1+2. Put the environment back to pristine, then un-latch the settings singleton so
    # the NEXT get_settings() re-resolves against that restored env instead of serving the
    # QA-mode / auth-disabled object minted while the harness owned the process. Clearing
    # to None (rather than restoring a saved reference) is the unconditionally correct
    # move here: the saved reference may ITSELF have been latched at collection time, when
    # os.environ was already dirty.
    _restore_env(env_before)

    # 3+4. Drop the seeded book and the seeded fx_cache.json. DATA_DIR/CACHE_DIR are read
    # from the RESTORED env above, so this always targets the suite's throwaway root even
    # if the harness had repointed them mid-test.
    _clear_seeded_files(os.environ.get("DATA_DIR", ""))
    _clear_seeded_files(os.environ.get("CACHE_DIR", ""))


# ── REGRESSION GUARD ─────────────────────────────────────────────────────────
# The restores above cover a FIXED list, so they can only ever undo the leaks we already
# know about. This detects a NEW one by name, failing AT the polluting test instead of
# resurfacing weeks later as an unattributable flake in a module that merely ran after.
#
# Two deliberate design points, each established by experiment rather than reasoning:
#
# 1. It runs in `pytest_runtest_logfinish`, NOT in the fixture teardown above. A
#    fixture's teardown runs BEFORE `monkeypatch` undoes the test's own `setenv`, so a
#    perfectly well-scoped `monkeypatch.setenv("ANTHROPIC_API_KEY", …)` (which
#    test_tool_surface_harness.py does) was reported as a leak. `logfinish` fires after
#    the entire setup/call/teardown protocol, when every fixture has finalized — a guard
#    that punishes correct code is worse than no guard.
#
# 2. It diffs against the ROOT conftest's PRISTINE snapshot, NOT a snapshot taken when
#    the test started. `bootstrap._isolate()` runs at MODULE IMPORT, i.e. during
#    COLLECTION, so a leak it introduces is already present before the first eval test
#    begins; an entry-time baseline contains the leak and reports the variable as
#    unchanged. Verified by injecting a synthetic `os.environ[...] = …` into `_isolate()`:
#    the entry-time diff missed it entirely, the pristine diff names it.
#
# Files need no equivalent detector: _clear_seeded_files removes ALL files under both
# dirs, not an enumerated set, so a newly-seeded file is already covered.
def pytest_runtest_logfinish(nodeid, location):
    from tests.conftest import PRISTINE_ENV

    escaped = {
        key: (PRISTINE_ENV.get(key), os.environ.get(key))
        for key in set(PRISTINE_ENV) | set(os.environ)
        if key not in _EVAL_ENV_KEYS
        and key not in _PYTEST_OWNED_ENV_KEYS
        and PRISTINE_ENV.get(key) != os.environ.get(key)
    }
    assert not escaped, (
        f"{nodeid} changed process environment variable(s) that tests/evals/conftest.py "
        f"does not restore: {escaped}. Add them to _EVAL_ENV_KEYS (and document the leak "
        "in this module's docstring) — an unrestored env var silently reconfigures every "
        "test that runs after this one."
    )
