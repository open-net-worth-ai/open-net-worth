"""Regression guard for test-order pollution out of ``tests/evals/``.

The defect this pins: ~7 tests across 4 modules failed only when the whole suite ran in ONE
process, passed in isolation, and the failure COUNT moved between runs (15 → 12 → 7-8).
Every one traced to the tool-surface harness under ``tests/evals/``, which mutates process
globals to boot a real instance and never put them back. Full inventory and the per-leak
"who dirties it / who depends on it clean" mapping is the module docstring of
``tests/evals/conftest.py``.

THE GUARD IS IN TWO HALVES, deliberately:

1. **At the boundary** — ``tests/evals/conftest.py``'s fixture diffs ``os.environ`` around
   every eval test and fails, naming the variable, if the harness changed one it does not
   restore. That is where a NEW leak gets caught, at the test that causes it.

2. **End to end** — this file, the original repro: the harness plus its four measured
   victims in ONE fresh process.

What this file deliberately does NOT do is assert process-wide cleanliness (no
``FX_CACHE_JSON.exists()``, no "the shared ledger is empty") from a module that runs late in
the suite. That was tried and was WRONG: hundreds of unrelated tests legitimately write the
shared cache and data dirs, so such an assertion fails for reasons that have nothing to do
with this lane — an over-broad guard that cries wolf is how a suite gets trained to ignore
red. The invariant only holds at the eval-directory boundary, so that is where it is
asserted.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Stripped from the child's environment so the repro starts from the same pristine state a
# developer's own `pytest` would. These are exactly the keys the harness leaks; inheriting a
# dirty one would make the child fail for the parent's reasons and report it as this lane's
# defect. (DATA_DIR/CACHE_DIR/LOGS_DIR need no stripping — the root conftest overwrites them
# with a fresh mkdtemp before any backend import, so the child never shares the parent's.)
_HARNESS_ENV_KEYS = ("ONW_QA_MODE", "AUTH_DISABLED", "ONW_DISABLE_TELEGRAM", "ONW_EVAL_ROOT")


def test_the_four_victim_modules_pass_when_run_after_the_eval_harness():
    """The original repro, pinned as a test.

    Runs the eval harness and its four measured victims together in ONE fresh process.
    Pre-fix this produced 7 failures; each victim passed when run alone. A subprocess
    because the pollution is process-global — in-process it could only be observed once
    per session, and only if collection ordering cooperated.
    """
    victims = [
        # Run the harness module that owns the deliberate process-global mutations,
        # not every eval test. The other eval modules are not part of this boundary
        # regression and can pollute one another through the same shared fixture,
        # making this guard fail for an unrelated, order-dependent reason.
        "tests/evals/test_tool_surface_harness.py",
        "tests/test_briefing_api.py",
        "tests/test_email_snapshot_api.py",
        "tests/test_price_backfill.py",
        # Not a victim itself: it installs the real FX loader, which is what made the
        # leaked fx_cache.json visible to the recurring-transaction suite. That suite went
        # with Scheduled Tasks (2026-08-02); this stays because installing the real loader
        # is the step that EXPOSES the leak, and the assertion below covers every module in
        # this list, not just the one that used to fail first.
        "tests/test_account_no_autostamp.py",
    ]
    env = {k: v for k, v in os.environ.items() if k not in _HARNESS_ENV_KEYS}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "not integration", *victims],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    assert result.returncode == 0, (
        "eval harness leaked into a later module.\n"
        f"--- stdout ---\n{result.stdout[-4000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
    )


def _env_guard():
    spec = importlib.util.spec_from_file_location(
        "evals_env_guard", REPO_ROOT / "tests" / "evals" / "conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.pytest_runtest_logfinish


def test_the_apps_trust_store_choice_at_boot_is_not_a_leak(monkeypatch):
    """Windows has no platform CA file, so importing backend.main sets SSL_CERT_FILE. The guard's
    assertion runs in a hook, where it ended the whole Windows run 8% of the way through."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr("tests.conftest.PRISTINE_ENV", dict(os.environ))
    monkeypatch.setenv("SSL_CERT_FILE", "/synthetic/cacert.pem")

    _env_guard()("tests/evals/test_synthetic.py::test_synthetic", None)


def test_any_other_new_variable_is_still_a_leak(monkeypatch):
    monkeypatch.setattr("tests.conftest.PRISTINE_ENV", dict(os.environ))
    monkeypatch.setenv("ONW_SYNTHETIC_LEAK", "1")

    with pytest.raises(AssertionError, match="ONW_SYNTHETIC_LEAK"):
        _env_guard()("tests/evals/test_synthetic.py::test_synthetic", None)
