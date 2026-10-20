"""
Transfers stamp base_amount / base_currency / fx_rate at write time (2026-07-09,
ADR foreign-currency-historical-rate-lock, Task 3). Previously a transfer stored
base_amount=None; now it records the base value that MOVED so a later reader
(planning._cash_at_cost's unrealized-FX figure) can reconstruct a cross-currency
boundary transfer exactly instead of approximating.

Pure unit tests for _transfer_base_value + one end-to-end create_transaction path,
both offline (monkeypatched stores; no disk/network/LLM).
"""

from __future__ import annotations

import uuid

import pytest

from backend.services import ledger as svc

# rates relative to SGD base: 1 USD = 1.35 SGD, 1 VND = 1/17000 SGD.
_FX = {"SGD": 1.0, "USD": 1.35, "VND": 1 / 17000}


# ── _transfer_base_value (pure) ──────────────────────────────────────────────


def test_transfer_base_value_prefers_base_currency_from_leg():
    # SGD (base) → VND: the SGD leg is base currency, so the moved value is exactly 100 (no FX).
    ba, src = svc._transfer_base_value(100.0, "SGD", 1_700_000.0, "VND", "SGD", _FX)
    assert ba == 100.0
    assert src == 100.0


def test_transfer_base_value_prefers_base_currency_to_leg():
    # VND → SGD (base): the base-currency leg is the TO leg → moved value is the SGD amount.
    ba, src = svc._transfer_base_value(1_700_000.0, "VND", 100.0, "SGD", "SGD", _FX)
    assert ba == 100.0
    assert src == 100.0


def test_transfer_base_value_converts_from_leg_when_neither_is_base():
    # USD → VND, base SGD: neither leg is base → convert the from-leg. 100 USD * 1.35 = 135 SGD.
    ba, src = svc._transfer_base_value(100.0, "USD", 2_300_000.0, "VND", "SGD", _FX)
    assert ba == pytest.approx(135.0)
    assert src == 100.0


def test_transfer_base_value_none_when_no_leg_amounts():
    assert svc._transfer_base_value(None, "SGD", None, "VND", "SGD", _FX) == (None, None)


# ── end-to-end: create_transaction stamps a transfer ─────────────────────────


class _TxnStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def by_external_ref(self, source, ref):
        from backend.core.txn_sources import ext_ref_pairs

        return next((r for r in self._rows.values() if (source, ref) in ext_ref_pairs(r)), None)

    def has_external_ref(self, source, ref):
        return self.by_external_ref(source, ref) is not None

    def insert(self, row, *, conn=None):
        return self.append(row)

    def append(self, row, *, conn=None):
        row = dict(row)
        if not row.get("id"):
            row["id"] = str(uuid.uuid4())
        self._rows[row["id"]] = row
        return row

    def upsert(self, row, *, conn=None):
        self._rows[row["id"]] = row
        return row

    def transfer_routes(self):
        return frozenset(
            frozenset((t["from_account_id"], t["to_account_id"]))
            for t in self.all()
            if t.get("kind") == "transfer"
            and not t.get("deleted")
            and not t.get("merged_into")
            and t.get("from_account_id")
            and t.get("to_account_id")
        )

    def all(self):
        return list(self._rows.values())

    def query_txns(self, **_):
        return list(self._rows.values())

    def enabled_rules(self):
        return []


class _AcctStore:
    def find_by_id(self, aid):
        return {"id": aid, "currency": "SGD"} if aid else None


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(svc, "transactions", _TxnStore())
    monkeypatch.setattr(svc, "accounts", _AcctStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    import backend.services.corrections as corr

    monkeypatch.setattr(corr, "record_correction", lambda old, new: None)


def test_cross_currency_transfer_stamps_base_amount(patched):
    row = svc.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-07-09",
            "from_account_id": "sgd",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "vnd",
            "to_amount": 1_700_000.0,
            "to_currency": "VND",
        }
    )
    # 100 SGD moved; SGD is base → the base value that moved is exactly 100.
    assert row["base_currency"] == "SGD"
    assert row["base_amount"] == 100.0
    assert row["fx_rate"] == pytest.approx(1.0)
