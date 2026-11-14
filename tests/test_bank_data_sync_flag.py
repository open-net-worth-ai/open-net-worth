"""The ``bank_data_sync`` flag is declared once and is on by default (owner, 2026-11-15).

On only offers the sync: every connector still starts with its sync Off until someone sets it up,
so an install that never opens Data import and sync behaves exactly as before.
"""

from __future__ import annotations

import pytest

from backend.core.feature_flags import FLAGS
from backend.services import bank_data_sync
from backend.services.feature_flags import enabled


def test_declared_on_by_default():
    flag = next(f for f in FLAGS if f.name == "bank_data_sync")
    assert flag.default is True
    assert flag.kind == "release"
    assert flag.remove_by > flag.added


def test_reads_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("ONW_FLAG_BANK_DATA_SYNC", raising=False)
    assert enabled("bank_data_sync") is True
    monkeypatch.setenv("ONW_FLAG_BANK_DATA_SYNC", "0")
    assert enabled("bank_data_sync") is False


@pytest.mark.usefixtures("db_isolated")
def test_on_by_default_sets_nothing_up(monkeypatch):
    monkeypatch.delenv("ONW_FLAG_BANK_DATA_SYNC", raising=False)
    assert bank_data_sync.run_if_due() is False
