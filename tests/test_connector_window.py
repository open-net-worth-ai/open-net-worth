"""A bank fetch must not miss rows: the default window catches up after a pause, and two
identical rows with no provider id are two rows (``services/connector_window.py``).

Both were silent data loss found by the GoCardless end-to-end run (2026-11-13): a fixed
14-day window from today dropped everything older after any longer pause, and two £3.00
coffees on one day shared one content key, so the second was refused as already booked.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from backend.core.user_time import user_today
from backend.services import connector_fetch, connector_window
from backend.services import ledger as ledger_service
from backend.services.aggregation import simplefin as simplefin_svc
from backend.services.aggregation.providers import PROVIDERS, FetchWindow, derived_ref


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")


@pytest.fixture
def configured_store():
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    store = SimpleFinConfigStore()
    store.update({"access_url": "https://user:pass@bridge.example.org/simplefin", "enabled": True})
    return store


def _account_id() -> str:
    from backend.services import accounts as accounts_service

    return accounts_service.create_account(
        {
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0,
            "external_ids": [{"source": "simplefin", "ref": "acc-1"}],
        }
    )["id"]


def _book(on: str, ref: str, account_id: str, *, deleted: bool = False) -> None:
    txn = ledger_service.create_transaction(
        {
            "date": on,
            "kind": "expense",
            "description": "ACME CAFE",
            "from_account_id": account_id,
            "from_amount": 3.0,
            "from_currency": "USD",
            "source": "ai",
            "external_refs": [{"source": "simplefin", "ref": ref}],
        }
    )
    if deleted:
        ledger_service.bulk_delete_transactions([txn["id"]])


def _days_ago(n: int) -> str:
    return (user_today() - timedelta(days=n)).isoformat()


# ── the default window ────────────────────────────────────────────────────────────────


def test_nothing_booked_yet_looks_back_the_providers_own_window():
    since, before, warning = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(PROVIDERS["simplefin"].lookback_days)
    assert before == user_today().isoformat()
    assert warning is None


def test_a_pause_is_caught_up_from_the_newest_booked_row():
    _book(_days_ago(30), "tx-old", _account_id())
    since, _, warning = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(30 + PROVIDERS["simplefin"].lookback_days)
    assert warning is None


def test_recent_activity_keeps_the_usual_window():
    _book(user_today().isoformat(), "tx-today", _account_id())
    since, _, _ = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(PROVIDERS["simplefin"].lookback_days)


def test_a_future_dated_row_never_narrows_the_window():
    _book((user_today() + timedelta(days=5)).isoformat(), "tx-future", _account_id())
    since, _, _ = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(PROVIDERS["simplefin"].lookback_days)


def test_a_deleted_row_is_not_where_the_ledger_stops():
    account_id = _account_id()
    _book(_days_ago(3), "tx-deleted", account_id, deleted=True)
    _book(_days_ago(40), "tx-live", account_id)
    since, _, _ = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(40 + PROVIDERS["simplefin"].lookback_days)


def test_a_gap_past_what_banks_serve_stops_there_and_says_so():
    _book(_days_ago(200), "tx-ancient", _account_id())
    since, _, warning = connector_window.default_window(PROVIDERS["simplefin"], None, None)
    assert since == _days_ago(connector_window.MAX_CATCH_UP_DAYS)
    assert warning and "statement" in warning


def test_an_explicit_since_is_used_as_given():
    _book(_days_ago(30), "tx-old", _account_id())
    since, _, warning = connector_window.default_window(
        PROVIDERS["simplefin"], "2020-01-01", "2020-02-01"
    )
    assert (since, warning) == ("2020-01-01", None)


def test_fetch_reports_the_catch_up_warning(configured_store, monkeypatch):
    _book(_days_ago(200), "tx-ancient", _account_id())
    monkeypatch.setattr(simplefin_svc, "fetch_window", lambda *a, **k: FetchWindow())
    result = connector_fetch.fetch("simplefin")
    assert result["window"]["since"] == _days_ago(connector_window.MAX_CATCH_UP_DAYS)
    assert any("statement" in w for w in result["warnings"])


# ── identical rows with no provider id ────────────────────────────────────────────────


def _derived_row(pending: bool = False) -> dict:
    ref = derived_ref("acc-1", _days_ago(1), -3.0, "USD", "ACME CAFE")
    return {
        "external_refs": [{"source": "simplefin", "ref": ref}],
        "provider_account_id": "acc-1",
        "date": _days_ago(1),
        "amount": -3.0,
        "currency": "USD",
        "description": "ACME CAFE",
        "memo": None,
        "pending": pending,
        "raw": {},
    }


def _refs(rows: list[dict]) -> list[str]:
    return [r["external_refs"][0]["ref"] for r in rows]


def test_two_identical_rows_get_two_keys_and_the_first_keeps_its_own():
    rows = [_derived_row(), _derived_row(), _derived_row()]
    original = _refs(rows)[0]
    connector_window.number_repeated_refs(rows)
    assert _refs(rows) == [original, f"{original}#2", f"{original}#3"]


def test_a_pending_twin_does_not_renumber_the_booked_row():
    rows = [_derived_row(pending=True), _derived_row()]
    original = _refs(rows)[0]
    connector_window.number_repeated_refs(rows)
    assert _refs(rows) == [original, original]


def test_a_provider_id_is_never_renumbered():
    rows = [_derived_row(), _derived_row()]
    for r in rows:
        r["external_refs"] = [{"source": "simplefin", "ref": "acc-1:tx-9"}]
    connector_window.number_repeated_refs(rows)
    assert _refs(rows) == ["acc-1:tx-9", "acc-1:tx-9"]


def test_both_identical_rows_are_offered_and_the_second_after_the_first_is_booked(
    configured_store, monkeypatch
):
    account_id = _account_id()
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda *a, **k: FetchWindow(rows=[_derived_row(), _derived_row()]),
    )
    first = connector_fetch.fetch("simplefin")
    assert first["counts"]["returned"] == 2
    _book(_days_ago(1), _refs(first["transactions"])[0], account_id)

    second = connector_fetch.fetch("simplefin")
    assert second["counts"]["already_booked"] == 1
    assert _refs(second["transactions"]) == [_refs(first["transactions"])[1]]


# ── a first import takes the bank's whole history ─────────────────────────────────────


def _link_gocardless(provider_account: str) -> str:
    from backend.services import accounts as accounts_service

    return accounts_service.create_account(
        {
            "name": f"Monzo {provider_account}",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0,
            "external_ids": [{"source": "gocardless", "ref": provider_account}],
        }
    )["id"]


def _book_gocardless(on: str, account_id: str, ref: str) -> None:
    ledger_service.create_transaction(
        {
            "date": on,
            "kind": "expense",
            "description": "ACME CAFE",
            "from_account_id": account_id,
            "from_amount": 3.0,
            "from_currency": "USD",
            "source": "ai",
            "external_refs": [{"source": "gocardless", "ref": ref}],
        }
    )


def test_a_newly_linked_account_imports_the_banks_whole_history():
    gc = PROVIDERS["gocardless"]
    _link_gocardless("acc-new")
    since, _, warning = connector_window.default_window(gc, None, None)
    assert since == _days_ago(gc.history_days) == _days_ago(90)
    assert warning is None


def test_once_every_linked_account_has_imported_the_usual_catch_up_applies():
    gc = PROVIDERS["gocardless"]
    account_id = _link_gocardless("acc-1")
    _book_gocardless(_days_ago(1), account_id, "acc-1:tx-1")
    since, _, _ = connector_window.default_window(gc, None, None)
    assert since == _days_ago(1 + gc.lookback_days)


def test_linking_a_second_bank_later_brings_in_its_history_too():
    gc = PROVIDERS["gocardless"]
    first = _link_gocardless("acc-1")
    _book_gocardless(_days_ago(1), first, "acc-1:tx-1")
    _link_gocardless("acc-2")  # linked after the first was already importing
    since, _, _ = connector_window.default_window(gc, None, None)
    assert since == _days_ago(90)


def test_a_provider_without_history_days_keeps_its_lookback_for_a_first_import():
    sf = dataclasses.replace(PROVIDERS["simplefin"], history_days=0)
    _account_id()  # linked to simplefin, nothing booked
    since, _, _ = connector_window.default_window(sf, None, None)
    assert since == _days_ago(sf.lookback_days)
