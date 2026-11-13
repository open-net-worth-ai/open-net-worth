"""The ``bank_data_sync`` flag is declared once, ships dark, and reads off by default."""

from __future__ import annotations

from backend.core.feature_flags import FLAGS
from backend.services.feature_flags import enabled


def test_declared_off_by_default():
    flag = next(f for f in FLAGS if f.name == "bank_data_sync")
    assert flag.default is False
    assert flag.kind == "release"
    assert flag.remove_by > flag.added


def test_reads_off_unless_turned_on(monkeypatch):
    monkeypatch.delenv("ONW_FLAG_BANK_DATA_SYNC", raising=False)
    assert enabled("bank_data_sync") is False
