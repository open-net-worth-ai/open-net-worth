"""The ``bank_data_sync`` flag is declared once and reads on by default."""

from __future__ import annotations

from backend.core.feature_flags import FLAGS
from backend.services.feature_flags import enabled


def test_declared_on_by_default():
    flag = next(f for f in FLAGS if f.name == "bank_data_sync")
    assert flag.default is True
    assert flag.kind == "release"
    assert flag.remove_by > flag.added


def test_reads_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("ONW_FLAG_BANK_DATA_SYNC", raising=False)
    assert enabled("bank_data_sync") is True
