"""One Plaid connector, several banks: each linked bank is its own Plaid Item (own access
token). Linking another bank never drops the first; re-linking the same bank replaces its
entry; a fetch covers every bank and one bank's failure never sinks the others; one bank can
be removed on its own; the client only ever sees a bank's name and an opaque key."""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ValidationError
from backend.db.plaid_config_store import PlaidConfigStore, bank_key
from backend.services.aggregation import plaid as svc


@pytest.fixture(autouse=True)
def isolate(db_isolated):
    """Fresh, isolated DB per test."""


@pytest.fixture
def store():
    s = PlaidConfigStore()
    s.update({"client_id": "cid", "secret": "skey", "enabled": True})
    return s


def _item(item_id: str, inst_id: str, name: str) -> dict:
    return {
        "item_id": item_id,
        "access_token": f"access-{item_id}",
        "institution_id": inst_id,
        "institution_name": name,
    }


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


# ── storage ──────────────────────────────────────────────────────────────────────────────


def test_linking_a_second_bank_keeps_the_first(store):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    assert [i["institution_name"] for i in store.items()] == ["Wells Fargo", "Chase"]
    assert store.is_configured()


def _exchange_as(monkeypatch, *, new_item: str, remove_fails: bool = False):
    calls: list[str] = []

    def fake_post(url, json=None, timeout=None):
        path = url.split(".plaid.com", 1)[1]
        calls.append(path)
        if path == "/item/public_token/exchange":
            return _Resp({"access_token": f"access-{new_item}", "item_id": new_item})
        if path == "/item/remove" and remove_fails:
            return _Resp({"error_code": "INTERNAL_SERVER_ERROR"}, 500)
        return _Resp({})

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    return calls


def test_relinking_the_same_bank_revokes_the_old_link_then_replaces_it(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    calls = _exchange_as(monkeypatch, new_item="i9")
    svc.exchange_public_token("pub", institution={"institution_id": "ins_1", "name": "Wells Fargo"})
    assert [i["item_id"] for i in store.items()] == ["i2", "i9"]  # Chase untouched
    assert "/item/remove" in calls  # the old Wells Fargo link was revoked at Plaid first


def test_a_relink_that_cannot_revoke_the_old_link_keeps_it_rather_than_lose_its_token(
    store, monkeypatch
):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    _exchange_as(monkeypatch, new_item="i9", remove_fails=True)
    svc.exchange_public_token("pub", institution={"institution_id": "ins_1", "name": "Wells Fargo"})
    assert [i["item_id"] for i in store.items()] == ["i1", "i9"]


def test_a_store_from_before_multi_bank_reads_as_one_bank_and_folds_in(store):
    store.update({"access_token": "legacy-tok", "item_id": "legacy-item"})
    [legacy] = store.items()
    assert legacy["access_token"] == "legacy-tok" and legacy["item_id"] == "legacy-item"
    store.add_item(_item("i2", "ins_2", "Chase"))
    assert [i["item_id"] for i in store.items()] == ["legacy-item", "i2"]
    assert store.get().get("access_token") in ("", None)  # folded into the list, not kept twice


def test_the_status_names_each_bank_but_never_a_token_or_item_id(store):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    public = store.public()
    assert [b["institution_name"] for b in public["banks"]] == ["Wells Fargo", "Chase"]
    assert public["banks"][0]["key"] == bank_key("i1")
    dumped = json.dumps(public)
    assert "access-i1" not in dumped and '"i1"' not in dumped and "items" not in public


def test_items_cannot_be_written_through_the_generic_update(store):
    store.update({"items": [_item("x", "ins_x", "Sneaky")]})
    assert store.items() == []


# ── linking ──────────────────────────────────────────────────────────────────────────────


def test_completing_a_link_adds_the_bank_with_its_name(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.update({"link_token": "lt"})
    session = {
        "results": {
            "item_add_results": [
                {"public_token": "pub", "institution": {"institution_id": "ins_2", "name": "Chase"}}
            ]
        }
    }
    replies = {
        "/link/token/get": {"link_sessions": [session]},
        "/item/public_token/exchange": {"access_token": "access-i2", "item_id": "i2"},
    }
    monkeypatch.setattr(
        svc.httpx, "post", lambda url, **k: _Resp(replies[url.split(".plaid.com", 1)[1]])
    )
    out = svc.complete_hosted_link()
    assert out["status"] == "linked" and out["institution_name"] == "Chase"
    assert [i["institution_name"] for i in store.items()] == ["Wells Fargo", "Chase"]


# ── fetching ─────────────────────────────────────────────────────────────────────────────


def _per_bank(fail: set[str] = frozenset()):
    def fake_post(url, json=None, timeout=None):
        token = json.get("access_token")
        if token in fail:
            return _Resp({"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login"}, 400)
        acct = {
            "account_id": f"acct-{token}",
            "name": f"Checking {token}",
            "balances": {"current": 10, "iso_currency_code": "USD"},
            "type": "depository",
            "subtype": "checking",
        }
        return _Resp({"accounts": [acct], "transactions": [], "total_transactions": 0})

    return fake_post


def test_a_fetch_covers_every_bank_and_says_which_bank_each_account_is_at(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    monkeypatch.setattr(svc.httpx, "post", _per_bank())
    window = svc.fetch_window(store, "2026-10-01", "2026-10-29", None)
    assert {a["institution"] for a in window.accounts} == {"Wells Fargo", "Chase"}
    assert window.complete and window.warnings == []


def test_one_failing_bank_warns_and_marks_the_window_incomplete_but_keeps_the_rest(
    store, monkeypatch
):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    monkeypatch.setattr(svc.httpx, "post", _per_bank(fail={"access-i1"}))
    window = svc.fetch_window(store, "2026-10-01", "2026-10-29", None)
    assert [a["institution"] for a in window.accounts] == ["Chase"]
    assert window.complete is False
    assert len(window.warnings) == 1 and window.warnings[0].startswith("Wells Fargo:")


def test_every_bank_failing_is_a_failed_fetch(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    monkeypatch.setattr(svc.httpx, "post", _per_bank(fail={"access-i1"}))
    with pytest.raises(ValidationError):
        svc.fetch_window(store, "2026-10-01", "2026-10-29", None)


# ── removing one bank ────────────────────────────────────────────────────────────────────


def test_removing_one_bank_tells_plaid_and_leaves_the_others(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    calls = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda url, json=None, timeout=None: calls.append((url, json)) or _Resp({}),
    )
    out = svc.remove_bank(bank_key("i1"))
    assert out == {"removed": True, "banks_left": 1}
    assert [i["institution_name"] for i in store.items()] == ["Chase"]
    assert calls[0][0].endswith("/item/remove") and calls[0][1]["access_token"] == "access-i1"


def test_a_bank_plaid_already_forgot_still_leaves_the_list(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    monkeypatch.setattr(
        svc.httpx, "post", lambda *a, **k: _Resp({"error_code": "ITEM_NOT_FOUND"}, 400)
    )
    svc.remove_bank(bank_key("i1"))
    assert store.items() == []


def test_removing_a_bank_while_plaid_is_unreachable_changes_nothing(store, monkeypatch):
    import httpx

    store.add_item(_item("i1", "ins_1", "Wells Fargo"))

    def offline(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(svc.httpx, "post", offline)
    with pytest.raises(ValidationError, match="nothing changed"):
        svc.remove_bank(bank_key("i1"))
    assert [i["item_id"] for i in store.items()] == ["i1"]  # the token was not thrown away


def test_one_failing_bank_is_recorded_and_reaches_the_fetch_result(store, monkeypatch):
    from backend.services import connector_fetch

    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    monkeypatch.setattr(svc.httpx, "post", _per_bank(fail={"access-i1"}))
    result = connector_fetch.fetch("plaid")
    assert result["status"] == "ok"
    assert len(result["warnings"]) == 1 and result["warnings"][0].startswith("Wells Fargo:")
    assert store.public()["last_caution"].startswith("Wells Fargo:")


def test_removing_the_plaid_connector_revokes_every_bank_first(store, monkeypatch):
    from backend.services import connectors as connectors_service

    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    revoked = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda url, json=None, timeout=None: revoked.append(json.get("access_token")) or _Resp({}),
    )
    connectors_service.remove_connector("plaid")
    assert sorted(revoked) == ["access-i1", "access-i2"]
    assert PlaidConfigStore().items() == []


def test_removing_an_unknown_bank_is_refused(store):
    with pytest.raises(ValidationError):
        svc.remove_bank("nope")


# ── update mode: Reconnect / Add accounts keep the SAME Item (and its account ids) ─────────


def _recorder(replies: dict, calls: list):
    def fake_post(url, json=None, timeout=None):
        path = url.split(".plaid.com", 1)[1]
        calls.append((path, json))
        reply = replies.get(path, {})
        return reply if isinstance(reply, _Resp) else _Resp(reply)

    return fake_post


def test_reconnect_opens_update_mode_on_that_banks_own_token(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _recorder(
            {"/link/token/create": {"link_token": "lt", "hosted_link_url": "https://h"}}, calls
        ),
    )
    svc.create_link_token("", bank=bank_key("i1"))
    _, body = calls[0]
    assert body["access_token"] == "access-i1"
    assert "products" not in body  # Plaid forbids products in update mode
    assert "update" not in body
    assert store.pending_link_bank() == bank_key("i1")


def test_add_accounts_opens_the_account_picker(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _recorder(
            {"/link/token/create": {"link_token": "lt", "hosted_link_url": "https://h"}}, calls
        ),
    )
    svc.create_link_token("", bank=bank_key("i1"), add_accounts=True)
    assert calls[0][1]["update"] == {"account_selection_enabled": True}


def test_a_new_bank_link_still_asks_for_transactions_and_is_not_update_mode(store, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _recorder(
            {"/link/token/create": {"link_token": "lt", "hosted_link_url": "https://h"}}, calls
        ),
    )
    svc.create_link_token("")
    assert calls[0][1]["products"] == ["transactions"] and "access_token" not in calls[0][1]
    assert store.pending_link_bank() is None


def test_reconnect_unknown_bank_is_refused(store):
    with pytest.raises(ValidationError):
        svc.create_link_token("", bank="nope")


def test_finishing_update_mode_exchanges_nothing_and_keeps_the_same_item(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.update({"link_token": "lt"})
    store.set_pending_link_bank(bank_key("i1"))
    store.mark_reconnect(needed={bank_key("i1")}, ok=set())
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _recorder(
            {"/link/token/get": {"link_sessions": [{"finished_at": "2026-10-29T00:00:00Z"}]}}, calls
        ),
    )
    out = svc.complete_hosted_link()
    assert out["status"] == "linked" and out["mode"] == "update"
    assert [p for p, _ in calls] == ["/link/token/get"]  # no public-token exchange
    assert [i["item_id"] for i in store.items()] == ["i1"]  # same Item, same token
    assert store.public()["banks"][0]["needs_reconnect"] is False
    assert store.pending_link_bank() is None and store.link_token() == ""


def test_update_mode_still_in_progress_is_pending(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.update({"link_token": "lt"})
    store.set_pending_link_bank(bank_key("i1"))
    monkeypatch.setattr(
        svc.httpx, "post", _recorder({"/link/token/get": {"link_sessions": [{}]}}, [])
    )
    assert svc.complete_hosted_link() == {"status": "pending"}


def test_an_expired_sign_in_marks_only_that_bank_for_reconnect_and_says_so(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))
    monkeypatch.setattr(svc.httpx, "post", _per_bank(fail={"access-i1"}))
    window = svc.fetch_window(store, "2026-10-01", "2026-10-29", None)
    banks = {b["institution_name"]: b["needs_reconnect"] for b in store.public()["banks"]}
    assert banks == {"Wells Fargo": True, "Chase": False}
    assert window.warnings == ["Wells Fargo: its sign-in expired — reconnect it on the Plaid page"]

    monkeypatch.setattr(svc.httpx, "post", _per_bank())  # the bank answers again
    svc.fetch_window(store, "2026-10-01", "2026-10-29", None)
    assert not any(b["needs_reconnect"] for b in store.public()["banks"])


def test_a_non_login_failure_does_not_ask_for_a_reconnect(store, monkeypatch):
    store.add_item(_item("i1", "ins_1", "Wells Fargo"))
    store.add_item(_item("i2", "ins_2", "Chase"))

    def flaky(url, json=None, timeout=None):
        if json.get("access_token") == "access-i1":
            return _Resp({"error_code": "INTERNAL_SERVER_ERROR", "error_message": "oops"}, 500)
        return _per_bank()(url, json=json, timeout=timeout)

    monkeypatch.setattr(svc.httpx, "post", flaky)
    svc.fetch_window(store, "2026-10-01", "2026-10-29", None)
    assert not any(b["needs_reconnect"] for b in store.public()["banks"])


def test_an_expired_sign_in_is_classified_as_reconnect_needed_not_a_network_error():
    exc = ValidationError(
        "Plaid /transactions/get failed: HTTP 400", plaid_error_code="ITEM_LOGIN_REQUIRED"
    )
    assert svc.classify_error(exc) == "reconnect_needed"
    other = ValidationError(
        "Plaid /transactions/get failed: HTTP 500", plaid_error_code="INTERNAL_SERVER_ERROR"
    )
    assert svc.classify_error(other) == "connect_error"
