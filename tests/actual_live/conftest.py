"""Opt-in live suite: the pinned Actual server, the official client as referee, made-up budgets.

Skipped unless ``ONW_ACTUAL_LIVE=1``, so ``make check`` and a bare ``pytest`` need neither Node nor
a download. Run it with ``make actual-smoke``, which installs what it needs into a cache folder and
points ``ONW_ACTUAL_NODE_DIR`` at it. Data isolation is the root ``tests/conftest.py`` plus
``db_isolated``: every path is a throwaway directory, and each test has a new book and a new budget.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.actual_live.harness import Server
from tests.actual_live.world import World

LIVE = os.environ.get("ONW_ACTUAL_LIVE") == "1"


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="live Actual suite: make actual-smoke")
    for item in items:
        if "actual_live" in item.nodeid:
            item.add_marker(pytest.mark.allow_stray_threads)  # the server's front outlives a test
            if not LIVE:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def actual_server(tmp_path_factory):
    node_dir = Path(os.environ["ONW_ACTUAL_NODE_DIR"])
    server = Server(node_dir, tmp_path_factory.mktemp("actual-server"))
    server.start()
    try:
        server.bootstrap()
        yield server
    finally:
        server.stop()


@pytest.fixture
def world(db_isolated, actual_server):
    return World.prepared(actual_server)
