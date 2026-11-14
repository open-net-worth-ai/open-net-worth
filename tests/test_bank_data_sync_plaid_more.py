"""Plaid in the no-AI bank sync: a first import waits for Plaid's whole history, a consent end is
recorded per Item and shown like the European banks', and an account's failed read names ITS
bank's error, not the first failing bank's (ADR 2026-11-13, Amendment 14)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.db.plaid_config_store import PlaidConfigStore, bank_key
from backend.services.aggregation import plaid as svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import access

RECENT = (user_today() - timedelta(days=10)).isoformat()
TODAY = user_today().isoformat()


@pytest.fixture(autouse=True)
def isolate(db_isolated):
    """Fresh, isolated DB per test."""


@pytest.fixture
def store():
    s = PlaidConfigStore()
    s.update({"client_id": "cid", "secret": "skey", "enabled": True})
    return s


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


def _item(item_id: str, name: str, **extra) -> dict:
    return {
        "item_id": item_id,
        "access_token": f"access-{item_id}",
        "institution_id": f"ins_{item_id}",
        "institution_name": name,
        **extra,
    }


def _bank(monkeypatch, *, fail: dict | None = None, status: dict | None = None, consent=None):
    """A fake Plaid. ``fail``: access token → error code its reads answer with. ``status``:
    access token → ``transactions_update_status``. Returns the paths called, in order."""
    calls: list[str] = []

    def fake_post(url, json=None, timeout=None):
        path = url.split(".plaid.com", 1)[1]
        token = (json or {}).get("access_token")
        calls.append(f"{path}:{token}")
        if token in (fail or {}):
            return _Resp({"error_code": fail[token], "error_message": "x"}, 400)
        if path == "/transactions/sync":
            return _Resp({"transactions_update_status": (status or {}).get(token, "NOT_READY")})
        acct = {
            "account_id": f"acct-{token}",
            "name": f"Checking {token}",
            "balances": {"current": 10, "iso_currency_code": "USD"},
            "type": "depository",
            "subtype": "checking",
        }
        item = {} if consent is None else {"consent_expiration_time": consent}
        return _Resp(
            {"accounts": [acct], "transactions": [], "total_transactions": 0, "item": item}
        )

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    return calls


# ── 2. consent end ───────────────────────────────────────────────────────────────────────


def _in_days(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()


def test_a_read_records_the_consent_end_and_the_accounts_an_item_holds(store, monkeypatch):
    store.add_item(_item("i1", "Chase"))
    end = _in_days(5)
    _bank(monkeypatch, consent=end)
    svc.fetch_window(store, RECENT, TODAY, None)
    item = store.items()[0]
    assert item["consent_expires_at"] == end and item["account_ids"] == ["acct-access-i1"]


def test_access_rows_warn_two_weeks_ahead_and_mark_an_ended_consent(store):
    store.add_item(_item("i1", "Chase", consent_expires_at=_in_days(5), account_ids=["a1"]))
    store.add_item(_item("i2", "Wells Fargo", consent_expires_at=_in_days(-1), account_ids=["a2"]))
    store.add_item(_item("i3", "Ally", account_ids=["a3"]))
    rows = {r["name"]: r for r in access.banks(PROVIDERS["plaid"])}
    assert rows["Chase"]["renew_soon"] and not rows["Chase"]["ended"]
    assert rows["Wells Fargo"]["ended"]
    assert rows["Ally"]["ends_at"] is None and not rows["Ally"]["renew_soon"]
    assert set(access.by_account(PROVIDERS["plaid"])) == {"a1", "a2", "a3"}


def test_a_finished_reconnect_replaces_the_consent_end(store, monkeypatch):
    store.add_item(_item("i1", "Chase", consent_expires_at=_in_days(2)))
    store.update({"link_token": "lt"})
    store.set_pending_link_bank(bank_key("i1"))
    renewed = _in_days(180)

    def fake_post(url, json=None, timeout=None):
        path = url.split(".plaid.com", 1)[1]
        if path == "/link/token/get":
            return _Resp({"link_sessions": [{"finished_at": "2026-11-15T00:00:00Z"}]})
        return _Resp({"item": {"consent_expiration_time": renewed}})

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    svc.complete_hosted_link()
    assert store.items()[0]["consent_expires_at"] == renewed


# ── 3. each account's error is its own bank's ────────────────────────────────────────────


def _two_failing(store, monkeypatch):
    store.add_item(_item("i1", "Chase", account_ids=["a1"]))
    store.add_item(_item("i2", "Wells Fargo", account_ids=["a2"]))
    store.add_item(_item("i3", "Ally", account_ids=["a3"]))
    _bank(
        monkeypatch,
        fail={"access-i1": "ITEM_LOGIN_REQUIRED", "access-i2": "ITEM_NOT_FOUND"},
    )


def test_an_account_raises_the_error_of_the_bank_that_holds_it(store, monkeypatch):
    _two_failing(store, monkeypatch)
    with pytest.raises(ValidationError) as exc:
        svc.fetch_window(store, RECENT, TODAY, "a2")
    assert exc.value.context["plaid_error_code"] == "ITEM_NOT_FOUND"
    with pytest.raises(ValidationError) as exc:
        svc.fetch_window(store, RECENT, TODAY, "a1")
    assert exc.value.context["plaid_error_code"] == "ITEM_LOGIN_REQUIRED"


def test_an_account_no_failed_bank_holds_is_gone_not_blamed_on_one(store, monkeypatch):
    _two_failing(store, monkeypatch)
    window = svc.fetch_window(store, RECENT, TODAY, "a-closed")
    assert "a-closed" not in {a["provider_account_id"] for a in window.accounts}


def test_every_bank_failing_still_gives_each_account_its_own_error(store, monkeypatch):
    store.add_item(_item("i1", "Chase", account_ids=["a1"]))
    store.add_item(_item("i2", "Wells Fargo", account_ids=["a2"]))
    _bank(monkeypatch, fail={"access-i1": "ITEM_LOGIN_REQUIRED", "access-i2": "ITEM_NOT_FOUND"})
    with pytest.raises(ValidationError) as exc:
        svc.fetch_window(store, RECENT, TODAY, "a2")
    assert exc.value.context["plaid_error_code"] == "ITEM_NOT_FOUND"
    # A read with no account named still fails as a whole.
    with pytest.raises(ValidationError):
        svc.fetch_window(store, RECENT, TODAY, None)


def test_a_failed_bank_that_never_recorded_its_accounts_stands_in_for_an_unknown_account(
    store, monkeypatch
):
    store.add_item(_item("i1", "Chase"))
    store.add_item(_item("i2", "Wells Fargo", account_ids=["a2"]))
    _bank(monkeypatch, fail={"access-i1": "ITEM_LOGIN_REQUIRED"})
    with pytest.raises(ValidationError) as exc:
        svc.fetch_window(store, RECENT, TODAY, "a-unknown")
    assert exc.value.context["plaid_error_code"] == "ITEM_LOGIN_REQUIRED"


def test_a_successful_listing_refreshes_the_accounts_an_item_holds(store, monkeypatch):
    store.add_item(_item("i1", "Chase", account_ids=["old"]))

    def fake_post(url, json=None, timeout=None):
        return _Resp({"accounts": [{"account_id": "new", "balances": {}}]})

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    svc.fetch_balances(store)
    assert store.items()[0]["account_ids"] == ["new"]
