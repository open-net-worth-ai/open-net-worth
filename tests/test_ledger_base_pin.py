"""
Historical-rate PIN (ADR 2026-07-09): a foreign-currency transaction can carry an explicit
base-currency value (the SGD amount the user actually transacted at) that the write choke
honors and locks, instead of auto-converting at the app's FX cache. Editable after adding: a
fresh base_amount re-pins; changing the native amount without a fresh base drops the pin so it
recomputes; editing an unrelated field (note) preserves the pin.

Offline — monkeypatched stores.
"""

from __future__ import annotations

import uuid

import pytest

from backend.services import ledger as svc

_FX = {"SGD": 1.0, "VND": 1 / 20408, "USD": 1.35}  # the app's cache rate ≈ 20,408 VND/S$


class _TxnStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def find_by_id(self, tid):
        return self._rows.get(tid)

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

    def delete(self, tid, *, conn=None):
        return self._rows.pop(tid, None) is not None

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
        return {"id": aid, "currency": "VND"} if aid else None

    def all(self):
        # update_transaction()'s _is_opening_txn() check calls opening_txn_ids(), which reads
        # every account's opening_txn_id via accounts.all() — none of this file's accounts have
        # an opening, so an empty roster is the faithful fake (no row here is ever a Starting
        # Balance row).
        return []


@pytest.fixture
def store(monkeypatch):
    s = _TxnStore()
    monkeypatch.setattr(svc, "transactions", s)
    monkeypatch.setattr(svc, "accounts", _AcctStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # The create path converts via the global FX loader (rates=None) — set it directly so a
    # VND→SGD conversion resolves in-test.
    monkeypatch.setattr("backend.core.fx._fx_rates_loader", lambda: dict(_FX))
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: dict(_FX))
    import backend.services.corrections as corr

    monkeypatch.setattr(corr, "record_correction", lambda old, new: None)
    return s


def _income(explicit_base=None):
    d = {
        "kind": "income",
        "date": "2026-04-18",
        "description": "reimbursement",
        "to_account_id": "vnd",
        "to_amount": 197_125_000.0,
        "to_currency": "VND",
    }
    if explicit_base is not None:
        d["base_amount"] = explicit_base  # the user's "Value in SGD" — no flag any more
    return d


def test_create_honors_explicit_base_amount(store):
    row = svc.create_transaction(_income(explicit_base=9500.0))
    assert row["base_amount"] == 9500.0  # the user's value, NOT the cache's ~9659
    assert row["fx_rate"] == pytest.approx(9500.0 / 197_125_000.0, abs=1e-6)  # stored at 6dp
    assert "base_amount_pinned" not in row  # the flag is gone


def test_create_without_explicit_base_auto_converts(store):
    row = svc.create_transaction(_income())
    assert row["base_amount"] == pytest.approx(197_125_000.0 / 20408, abs=0.5)  # ≈ 9659
    assert "base_amount_pinned" not in row


def test_edit_unrelated_field_PRESERVES_base(store):
    # THE fix: editing the payee / note / etc. must NOT re-price the historical value.
    row = svc.create_transaction(_income(explicit_base=9500.0))
    before = (row["base_amount"], row["base_currency"], row["fx_rate"])
    updated = svc.update_transaction(row["id"], {"description": "reimbursement (edited)"})
    assert updated["description"] == "reimbursement (edited)"
    assert (updated["base_amount"], updated["base_currency"], updated["fx_rate"]) == before


def test_edit_native_amount_recomputes(store):
    row = svc.create_transaction(_income(explicit_base=9500.0))
    updated = svc.update_transaction(row["id"], {"to_amount": 100_000_000.0})
    # the leg changed → base re-stamps at today's cache rate for the new amount
    assert updated["base_amount"] == pytest.approx(100_000_000.0 / 20408, abs=0.5)


def test_edit_value_in_sgd_overrides(store):
    row = svc.create_transaction(_income())  # auto-converted ≈ 9659
    updated = svc.update_transaction(row["id"], {"base_amount": 9500.0})  # user sets "Value in SGD"
    assert updated["base_amount"] == 9500.0
    assert updated["fx_rate"] == pytest.approx(9500.0 / 197_125_000.0, abs=1e-6)


def test_preserve_keeps_base_currency_after_a_base_change(store, monkeypatch):
    # Regression: removing the pin's force-block must NOT let a preserved edit relabel a stale
    # stamp under the new base. Create a VND row under SGD base, switch the app base to USD, then
    # edit an unrelated field — the SGD stamp must survive byte-identical (the read-side lock keys
    # off base_currency; a mislabel to USD would make it wrongly trust an SGD value).
    row = svc.create_transaction(_income())
    b0 = (row["base_amount"], row["base_currency"], row["fx_rate"])
    assert row["base_currency"] == "SGD"
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    updated = svc.update_transaction(row["id"], {"description": "edited under a new base"})
    assert (updated["base_amount"], updated["base_currency"], updated["fx_rate"]) == b0
