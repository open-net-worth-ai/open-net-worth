"""A budget app's screens build their URLs from ``app.apiPrefix``, which the route consistency test
can't see through. Every ``${app.apiPrefix}/…`` in the frontend is a real route, for both apps."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_route_path_consistency import _collect_app_routes, _route_template

FRONTEND = Path(__file__).parent.parent / "frontend" / "src"
APPS_FILE = FRONTEND / "components/tabs/automations/datasync/budgetApps.ts"
_SUFFIX = re.compile(r"\$\{(?:app|[A-Z_]+_APP)\.apiPrefix\}(/[^`?$\s]*)")
_PREFIX = re.compile(r"apiPrefix: `\$\{API\}(/[a-z-]+)`")
# ``sendSync`` adds these to `${app.apiPrefix}/sync`, in the same words YNAB's page always used.
_SYNC_PATHS = ("/sync/settings", "/sync/run", "/sync/conflicts/{}/resolve")


def _prefixes() -> list[str]:
    return [f"/api{p}" for p in _PREFIX.findall(APPS_FILE.read_text(encoding="utf-8"))]


def _suffixes() -> set[str]:
    found: set[str] = set()
    for path in FRONTEND.rglob("*.ts*"):
        if ".test." not in path.name:
            found |= set(_SUFFIX.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


def test_both_apps_and_their_paths_are_found():
    assert _prefixes() == ["/api/ynab", "/api/actual-budget"]
    assert {"/status", "/budgets", "/sync", "/migration", "/migration/preview"} <= _suffixes()


@pytest.mark.parametrize("prefix", _prefixes())
@pytest.mark.parametrize("suffix", sorted(_suffixes() | set(_SYNC_PATHS)))
def test_every_path_is_a_route_on_both_apps(prefix, suffix):
    routes = {_route_template(r) for r in _collect_app_routes()}
    assert f"{prefix}{suffix}" in routes
