"""Saving another Enable Banking application while banks are linked (2026-11-13).

A bank's consent belongs to the application that created it — another application gets 404
for it (verified on the sandbox). Swapping apps used to leave every bank silently "ok" with
nothing in it, and its old consent never revoked. Now: the new credentials are checked first
(a refusal changes nothing), each old consent is revoked with the OLD key, its accounts are
remembered so the renewal keeps their links, and every bank is flagged to renew. No network.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services.aggregation import enable_banking as svc
from backend.services.aggregation import enable_banking_app as app


@pytest.fixture(autouse=True)
def _db(db_isolated):
    pass


class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _book(*banks):
    """A book on application OLD-APP with ``banks`` ([(session_id, aspsp)]) linked."""
    store = EnableBankingConfigStore()
    store.update({"application_id": "OLD-APP", "private_key": "OLD-KEY", "enabled": True})
    for sid, aspsp in banks:
        store.add_bank(
            sid, aspsp=aspsp, country="FI", valid_until=None, linked_at="2026-11-01", account_ids=[]
        )
    return store


@pytest.fixture
def wire(monkeypatch):
    """Records every call by the application id that signed it (``_sign_jwt`` → its kid)."""
    calls: list[tuple] = []
    refuse = {"check": False}
    monkeypatch.setattr(app, "_sign_jwt", lambda kid, key: f"{kid}|{key}")
    monkeypatch.setattr(svc, "_sign_jwt", lambda kid, key: f"{kid}|{key}")

    def _get(url, headers=None, **k):
        calls.append(("GET", url.removeprefix(svc.API_BASE), headers["Authorization"]))
        if refuse["check"]:
            return _Resp(403, {"message": "Application does not exist"})
        return _Resp(200, {"name": "QA"})

    def _delete(url, headers=None, **k):
        calls.append(("DELETE", url.removeprefix(svc.API_BASE), headers["Authorization"]))
        return _Resp(200)

    def _accounts(token, sid):
        calls.append(("ACCOUNTS", sid, token))
        return [{"uid": f"{sid}-UID", "identification_hash": f"{sid}-HASH"}]

    monkeypatch.setattr(app.httpx, "get", _get)
    monkeypatch.setattr(svc.httpx, "delete", _delete)
    monkeypatch.setattr(svc, "session_accounts_data", _accounts)
    return calls, refuse


def test_switching_apps_revokes_with_the_old_key_and_flags_every_bank(wire):
    calls, _ = wire
    _book(("SESS-A", "ACME Bank"), ("SESS-B", "Other Bank"))

    out = app.save_credentials({"application_id": "NEW-APP", "private_key": "NEW-KEY"})

    assert out == {"switched": True, "revoked": 2}
    assert calls == [
        ("GET", "/application", "Bearer NEW-APP|NEW-KEY"),
        ("ACCOUNTS", "SESS-A", "OLD-APP|OLD-KEY"),
        ("DELETE", "/sessions/SESS-A", "Bearer OLD-APP|OLD-KEY"),
        ("ACCOUNTS", "SESS-B", "OLD-APP|OLD-KEY"),
        ("DELETE", "/sessions/SESS-B", "Bearer OLD-APP|OLD-KEY"),
    ]
    store = EnableBankingConfigStore()
    assert (store.application_id(), store.private_key()) == ("NEW-APP", "NEW-KEY")
    assert [b["access_ended"] for b in store.banks()] == ["app_changed", "app_changed"]
    # The accounts were remembered, so the renewal under the new app keeps their ids.
    assert len(store.get()["account_identities"]) == 2


def test_credentials_enable_banking_refuses_change_nothing(wire):
    calls, refuse = wire
    refuse["check"] = True
    _book(("SESS-A", "ACME Bank"))

    with pytest.raises(
        ValidationError,
        match="refused the new app: Application does not exist \\(HTTP 403\\). Check the application ID. Nothing was changed",
    ):
        app.save_credentials({"application_id": "TYPO-APP", "private_key": "NEW-KEY"})

    assert [c[0] for c in calls] == ["GET"]
    store = EnableBankingConfigStore()
    assert (store.application_id(), store.private_key()) == ("OLD-APP", "OLD-KEY")
    assert [b["access_ended"] for b in store.banks()] == [None]


def test_another_app_needs_its_own_key(wire):
    calls, _ = wire
    _book(("SESS-A", "ACME Bank"))
    # The saved key is the old app's; checking it against the new id is what catches this.
    app.save_credentials({"application_id": "NEW-APP"})
    assert calls[0] == ("GET", "/application", "Bearer NEW-APP|OLD-KEY")


def test_a_new_key_for_the_same_app_is_a_plain_save(wire):
    calls, _ = wire
    _book(("SESS-A", "ACME Bank"))
    out = app.save_credentials({"application_id": "OLD-APP", "private_key": "ROTATED"})
    assert out == {"switched": False, "revoked": 0}
    assert calls == []
    assert [b["access_ended"] for b in EnableBankingConfigStore().banks()] == [None]


def test_first_setup_is_a_plain_save(wire):
    calls, _ = wire
    app.save_credentials({"application_id": "NEW-APP", "private_key": "NEW-KEY"})
    assert calls == []
    assert EnableBankingConfigStore().application_id() == "NEW-APP"


def test_switching_again_leaves_banks_already_waiting_for_renewal(wire):
    """Their consents went with the first switch; nothing is left to revoke."""
    calls, _ = wire
    _book(("SESS-A", "ACME Bank"))
    app.save_credentials({"application_id": "NEW-APP", "private_key": "NEW-KEY"})
    calls.clear()
    out = app.save_credentials({"application_id": "THIRD-APP", "private_key": "THIRD-KEY"})
    assert out == {"switched": False, "revoked": 0}
    assert calls == []
    assert [b["access_ended"] for b in EnableBankingConfigStore().banks()] == ["app_changed"]
