"""GoCardless and Enable Banking, the rest of the no-AI sync's PSD2 issues (ADR
2026-11-13, Amendments 14-15). Synthetic ACME data; no network."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services.aggregation import enable_banking as eb


@pytest.fixture(autouse=True)
def isolate_stores(db_isolated):
    """Every test runs against a fresh, isolated database."""


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


# ── Enable Banking asks for the bank's longest consent ───────────────────────────────


def _asked_days(monkeypatch, aspsps: list[dict], bank: str) -> float:
    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb.httpx, "get", lambda *a, **k: _Resp(200, {"aspsps": aspsps}))
    sent: dict = {}

    def _post(url, json, **kw):
        sent.update(json)
        return _Resp(200, {"url": "https://bank.example/a", "authorization_id": "A"})

    monkeypatch.setattr(eb.httpx, "post", _post)
    eb.start_authorization(bank, "FI", "")
    until = datetime.fromisoformat(sent["access"]["valid_until"])
    return (until - datetime.now(timezone.utc)).total_seconds() / 86400


def test_the_banks_own_maximum_consent_is_asked_for(monkeypatch):
    days = _asked_days(
        monkeypatch, [{"name": "ACME Bank", "maximum_consent_validity": 15552000}], "ACME Bank"
    )
    assert 179.9 < days <= 180


def test_a_maximum_beyond_the_ceiling_is_capped(monkeypatch):
    days = _asked_days(
        monkeypatch, [{"name": "ACME Bank", "maximum_consent_validity": 31536000}], "ACME Bank"
    )
    assert 179.9 < days <= 180


def test_a_bank_with_no_stated_maximum_gets_ninety_days(monkeypatch):
    assert 89.9 < _asked_days(monkeypatch, [{"name": "ACME Bank"}], "ACME Bank") <= 90
    assert 89.9 < _asked_days(monkeypatch, [{"name": "Other"}], "ACME Bank") <= 90


def test_a_failed_bank_lookup_still_starts_the_link_at_ninety_days(monkeypatch):
    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb.httpx, "get", lambda *a, **k: _Resp(500, {}))
    sent: dict = {}
    monkeypatch.setattr(
        eb.httpx,
        "post",
        lambda url, json, **kw: sent.update(json) or _Resp(200, {"url": "https://bank.example/a"}),
    )
    eb.start_authorization("ACME Bank", "FI", "")
    until = datetime.fromisoformat(sent["access"]["valid_until"])
    assert 89.9 < (until - datetime.now(timezone.utc)).total_seconds() / 86400 <= 90


# ── Enable Banking reads only what one account needs ─────────────────────────────────


def _two_banks(monkeypatch) -> tuple[EnableBankingConfigStore, list[str]]:
    """ACME Bank (U1, U2, U3) and ACME Savings (V1, V2): every path the code calls is logged."""
    store = EnableBankingConfigStore()
    store.update({"application_id": "APP", "private_key": "KEY"})
    store.add_bank(
        "SESS-A",
        aspsp="ACME Bank",
        country="FI",
        valid_until=None,
        linked_at="2026-11-01T00:00:00+00:00",
        account_ids=["U1", "U2", "U3"],
    )
    store.add_bank(
        "SESS-B",
        aspsp="ACME Savings",
        country="FI",
        valid_until=None,
        linked_at="2026-11-01T00:00:00+00:00",
        account_ids=["V1", "V2"],
    )
    sessions = {"SESS-A": ["U1", "U2", "U3"], "SESS-B": ["V1", "V2"]}
    calls: list[str] = []

    def _get(token, path, params):
        calls.append(path)
        if path.startswith("/sessions/"):
            uids = sessions[path.rsplit("/", 1)[1]]
            return _Resp(
                200,
                {
                    "status": "AUTHORIZED",
                    "accounts": uids,
                    "accounts_data": [{"uid": u, "identification_hash": f"H-{u}"} for u in uids],
                },
            )
        if path.endswith("/details"):
            return _Resp(200, {"name": "ACME", "currency": "EUR"})
        if path.endswith("/balances"):
            return _Resp(
                200,
                {"balances": [{"balance_type": "CLBD", "balance_amount": {"amount": "10.00"}}]},
            )
        return _Resp(200, {"transactions": []})

    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb, "_safe_get", _get)
    return store, calls


def test_a_one_account_read_asks_for_one_session_and_one_details(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "V2")
    assert [a["provider_account_id"] for a in window.accounts] == ["V2"]
    assert calls.count("/sessions/SESS-A") == 0  # another bank is never touched
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/V2/details"]


def test_listing_every_account_still_reads_every_bank(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    window = eb.fetch_window(store, "2026-11-10", "2026-11-10", None, accounts_only=True)
    assert len(window.accounts) == 5
    assert len([c for c in calls if c.endswith("/details")]) == 5


def test_a_renewed_accounts_stable_id_is_still_found_with_a_filtered_read(monkeypatch):
    """The renewed session hands the account a new uid; the stable id is the first one."""
    store, calls = _two_banks(monkeypatch)
    bank_a = store.bank(store.banks()[0]["key"])
    assert (
        eb._stable_account_id(store, bank_a, {"uid": "U1", "identification_hash": "H-U1"}) == "U1"
    )
    store.add_bank(
        "SESS-A2",
        aspsp="ACME Bank",
        country="FI",
        valid_until=None,
        linked_at="2026-11-05T00:00:00+00:00",
        account_ids=["U1", "U2", "U3"],
    )
    original = eb._safe_get

    def _get(token, path, params):
        if path == "/sessions/SESS-A2":
            return _Resp(
                200,
                {
                    "status": "AUTHORIZED",
                    "accounts": ["N1"],
                    "accounts_data": [{"uid": "N1", "identification_hash": "H-U1"}],
                },
            )
        return original(token, path, params)

    monkeypatch.setattr(eb, "_safe_get", _get)
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "U1")
    assert [a["provider_account_id"] for a in window.accounts] == ["U1"]
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/N1/details"]


def test_the_other_banks_are_read_only_when_the_account_is_not_found_first(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    store.set_account_ids(store.banks()[1]["key"], [])  # no bank is known to hold V1
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "V1")
    assert [a["provider_account_id"] for a in window.accounts] == ["V1"]
    assert "/sessions/SESS-A" in calls  # tried first, no match, so the next bank is read
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/V1/details"]
