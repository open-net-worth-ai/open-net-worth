"""Bank data sync — gaps found by comparing GoCardless and Enable Banking handling with other
open-source importers (Actual Budget, Sure, Firefly III, ynabber); ADR 2026-11-13, Amendment 20.

A bank that repeats a page key, an id that names no single row, a bank that won't serve the
range asked for, a card in credit, a card's limit from its bank, a row dated only by its
purchase date, and a multi-currency account. Real runner, connector_fetch and ledger chokes where
a sync is involved; only the bank is fake. Synthetic ACME data only."""

from __future__ import annotations

import httpx
import pytest

from backend.services import accounts as accounts_service
from backend.services import connector_fetch
from backend.services import ledger as ledger_service
from backend.services.aggregation import enable_banking as eb_svc
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation import row_ids
from backend.services.aggregation.providers import PROVIDERS, FetchWindow
from backend.services.bank_data_sync import balances, runner, settings
from tests.bank_sync_fakes import (
    ACC_MAIN,
    ACC_SAVER,
    FakeBank,
    configure_gocardless,
    days_ago,
    linked_account,
    txn,
)

GC = PROVIDERS["gocardless"]

pytestmark = pytest.mark.flags(bank_data_sync=True)


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    settings.save("gocardless", mode="data")
    return fake


def _run() -> dict:
    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(account_id: str) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def _amounts(account_id: str) -> list[float]:
    return sorted(float(t.get("from_amount") or t.get("to_amount") or 0) for t in _live(account_id))


# ── 1. Enable Banking: a repeated page key ends the read ────────────────────────────────────


class _Page:
    content = b"{}"

    def __init__(self, body: dict, status: int = 200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.example.test")
            response = httpx.Response(self.status_code, json=self._body, request=request)
            raise httpx.HTTPStatusError("refused", request=request, response=response)

    def json(self):
        return self._body


def test_a_repeated_page_key_ends_the_read_whole_with_every_row_once(monkeypatch):
    pages = [
        _Page({"transactions": [{"entry_reference": "A"}], "continuation_key": "k1"}),
        _Page({"transactions": [{"entry_reference": "B"}], "continuation_key": "k1"}),
    ]
    calls: list[dict] = []

    def _get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return pages.pop(0)

    monkeypatch.setattr(eb_svc.httpx, "get", _get)

    txns, complete = eb_svc.fetch_account_transactions(
        "JWT", "EB-1", date_from="2026-05-01", date_to="2026-05-31"
    )

    assert [t["entry_reference"] for t in txns] == ["A", "B"]
    assert complete is True
    assert len(calls) == 2  # never asked for the same page again


def test_a_bank_that_sends_page_one_again_books_no_id_less_row_twice(monkeypatch):
    """Actual #8846: the repeated key came with the first page again. An id-less row read twice
    would be numbered ``#2`` and booked as a second purchase."""
    no_id = {
        "booking_date": "2026-05-04",
        "transaction_amount": {"amount": "3.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "remittance_information": ["ACME COFFEE"],
    }
    first = {"transactions": [no_id, {"entry_reference": "A"}], "continuation_key": "k1"}
    pages = [_Page(first), _Page(dict(first))]
    monkeypatch.setattr(eb_svc.httpx, "get", lambda url, params=None, **k: pages.pop(0))

    txns, complete = eb_svc.fetch_account_transactions(
        "JWT", "EB-1", date_from="2026-05-01", date_to="2026-05-31"
    )

    assert txns == [no_id, {"entry_reference": "A"}] and complete is True


# ── 2. an id that names no single row ───────────────────────────────────────────────────────


def test_a_placeholder_id_is_no_id():
    assert row_ids.real_id("NOTPROVIDED") == ""
    assert row_ids.real_id(" n/a ") == ""
    assert row_ids.real_id("000000") == ""
    assert row_ids.real_id("ACME-123") == "ACME-123"


def _without_hash(row: dict) -> dict:
    row.pop("_content_hash", None)
    return row


def test_two_payments_with_a_placeholder_id_are_both_booked(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-12.00, days_ago(3), "ACME BAKERY", tid="NOTPROVIDED"),
        txn(-30.00, days_ago(2), "ACME FUEL", tid="NOTPROVIDED"),
    ]

    _run()

    assert _amounts(main) == [12.00, 30.00]


def test_a_row_booked_under_a_placeholder_before_is_not_booked_again(bank, monkeypatch):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-12.00, days_ago(3), "ACME BAKERY", tid="NOTPROVIDED"),
        txn(-30.00, days_ago(2), "ACME FUEL", tid="NOTPROVIDED"),
    ]
    with monkeypatch.context() as old:
        # What the last release did: the placeholder was the id, so the second row was lost.
        old.setattr(row_ids, "real_id", lambda v: str(v or "").strip())
        old.setattr(row_ids, "settle", lambda rows: [_without_hash(r) for r in rows])
        _run()
    assert _amounts(main) == [12.00]

    _run()

    assert _amounts(main) == [12.00, 30.00]  # the lost one booked; the first never twice


def test_one_bank_id_on_two_different_rows_books_both(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(3), "ACME COFFEE", tid="tx-7"),
        txn(-41.10, days_ago(2), "ACME GROCER", tid="tx-7"),
    ]

    _run()
    _run()

    assert _amounts(main) == [5.00, 41.10]


def test_a_row_listed_twice_under_its_id_is_booked_once(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    row = txn(-5.00, days_ago(3), "ACME COFFEE", tid="tx-7")
    bank.booked[ACC_MAIN] = [row, dict(row)]

    _run()

    assert _amounts(main) == [5.00]


def test_a_shared_id_row_listed_alone_later_is_still_known(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    first = txn(-5.00, days_ago(3), "ACME COFFEE", tid="tx-7")
    second = txn(-41.10, days_ago(2), "ACME GROCER", tid="tx-7")
    bank.booked[ACC_MAIN] = [first, second]
    _run()

    bank.booked[ACC_MAIN] = [second]  # the other one has left the bank's window
    _run()

    assert _amounts(main) == [5.00, 41.10]


# ── 3. Enable Banking: a bank that won't serve the range asked for ─────────────────────────


def _refusal(detail=None) -> _Page:
    body = {
        "code": 422,
        "message": "Wrong transactions period",
        "error": "WRONG_TRANSACTIONS_PERIOD",
    }
    if detail is not None:
        body["detail"] = detail
    return _Page(body, status=422)


def _served(monkeypatch, *responses) -> list[dict]:
    queue, calls = list(responses), []

    def _get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return queue.pop(0)

    monkeypatch.setattr(eb_svc.httpx, "get", _get)
    return calls


def test_a_refused_range_is_asked_again_from_the_day_the_bank_names(monkeypatch):
    calls = _served(
        monkeypatch,
        _refusal({"date_from": "2026-04-15"}),
        _Page({"transactions": [{"entry_reference": "A"}]}),
    )

    txns, complete, read_from = eb_svc.fetch_served_transactions(
        "JWT", "EB-1", date_from="2026-03-01", date_to="2026-05-31"
    )

    assert (len(txns), complete, read_from) == (1, True, "2026-04-15")
    assert calls[1]["date_from"] == "2026-04-15"


def test_n26s_period_invalid_wording_is_read_too(monkeypatch):
    refusal = _Page(
        {"code": "PERIOD_INVALID", "detail": "dateFrom=2026-04-20,dateTo=2026-05-31"}, status=400
    )
    _served(monkeypatch, refusal, _Page({"transactions": []}))

    assert (
        eb_svc.fetch_served_transactions(
            "JWT", "EB-1", date_from="2026-03-01", date_to="2026-05-31"
        )[2]
        == "2026-04-20"
    )


def test_without_a_named_day_it_steps_down_89_60_30_days(monkeypatch):
    calls = _served(monkeypatch, _refusal(), _refusal(), _Page({"transactions": []}))

    _txns, _complete, read_from = eb_svc.fetch_served_transactions(
        "JWT", "EB-1", date_from="2026-03-01", date_to="2026-05-31"
    )

    assert [c["date_from"] for c in calls] == ["2026-03-01", "2026-03-03", "2026-04-01"]
    assert read_from == "2026-04-01"


def test_a_bank_that_refuses_every_range_is_a_failed_read(monkeypatch):
    from backend.core.errors import ExternalServiceError

    _served(monkeypatch, *[_refusal() for _ in range(4)])

    with pytest.raises(ExternalServiceError):
        eb_svc.fetch_served_transactions(
            "JWT", "EB-1", date_from="2026-03-01", date_to="2026-05-31"
        )


def test_a_shorter_served_range_is_the_window_read(db_isolated, monkeypatch):
    """The real Enable Banking fetch_window, one account: the bank refuses 90 days, serves from
    the day it names; the fetch says so and its window starts there — so nothing earlier is
    ever read as gone."""
    from backend.db import EnableBankingConfigStore

    monkeypatch.setattr(EnableBankingConfigStore, "is_configured", lambda self: True)
    EnableBankingConfigStore().update({"enabled": True})
    monkeypatch.setattr(eb_svc, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(
        eb_svc,
        "_accounts_of_every_bank",
        lambda token, store, **kw: ([{"uid": "EB-1", "currency": "EUR", "name": "ACME"}], True, []),
    )
    monkeypatch.setattr(eb_svc, "fetch_account_balances", lambda token, uid: (None, {}))
    served = days_ago(30)
    entry = {
        "entry_reference": "A",
        "booking_date": days_ago(2),
        "transaction_amount": {"amount": "9.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
    }
    _served(monkeypatch, _refusal({"date_from": served}), _Page({"transactions": [entry]}))

    result = connector_fetch.fetch_all(
        "enable_banking", since=days_ago(90), before=days_ago(0), account="EB-1"
    )

    assert result["status"] == "ok", result
    assert result["window"]["since"] == served
    assert result["window"]["complete"] is True
    assert any(w.startswith("the bank serves transactions only from") for w in result["warnings"])


def test_a_first_import_the_bank_serves_less_of_still_books(bank, monkeypatch):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-9.00, days_ago(5), "ACME SHOP", tid="t1")]
    real = bank.fetch_window

    def shorter(store, since, before, account, **kw):
        window = real(store, since, before, account, **kw)
        window.read_from = days_ago(30)
        return window

    monkeypatch.setattr(gocardless_svc, "fetch_window", shorter)

    result = _run()

    assert _amounts(main) == [9.00]
    assert result["status"] != "error"


# ── 4. a card in credit reads as money held ────────────────────────────────────────────────


def _card(limit: float | None = None) -> dict:
    account_id = accounts_service.create_account(
        {
            "name": "ACME Card",
            "kind": "liability",
            "type": "credit",
            "currency": "GBP",
            "initial_balance": -500.00,
            "credit_limit": limit,
            "external_ids": [{"source": "gocardless", "ref": ACC_SAVER}],
        }
    )["id"]
    return ledger_service.accounts.find_by_id(account_id)


def test_a_card_in_credit_by_its_booked_balance_is_held_not_owed(bank):
    card = _card(limit=1000.0)
    bank_says = {"amount": 50.0, "cleared": 50.0, "available": 1050.0, "working": None}

    assert balances.bank_figure(card, bank_says)[0] == 50.0


def test_a_card_that_owes_its_booked_balance_still_owes_it(bank):
    card = _card(limit=1000.0)
    bank_says = {"amount": 50.0, "cleared": 50.0, "available": 950.0, "working": None}

    assert balances.bank_figure(card, bank_says)[0] == -50.0


def test_without_a_limit_a_card_balance_is_owed_as_before(bank):
    card = _card()
    bank_says = {"amount": 50.0, "cleared": 50.0, "available": 1050.0, "working": None}

    assert balances.bank_figure(card, bank_says)[0] == -50.0


# ── 5. a card takes its bank's credit limit ────────────────────────────────────────────────


def test_enable_banking_details_carry_the_cards_limit():
    account = {
        "uid": "EB-1",
        "currency": "EUR",
        "cash_account_type": "CARD",
        "credit_limit": {"amount": "2500.00", "currency": "EUR"},
    }
    assert eb_svc._account_to_common(account)["credit_limit"] == 2500.0
    other = {**account, "credit_limit": {"amount": "2500.00", "currency": "USD"}}
    assert eb_svc._account_to_common(other)["credit_limit"] is None


def test_a_card_with_no_limit_takes_its_banks_and_its_balance_is_worked_out(bank):
    card = _card()
    bank.balances[ACC_SAVER] = {
        "balance": 1200.00,
        "available_to_spend": 1200.00,
        "credit_limit": 5000.00,
    }

    _run()

    assert ledger_service.accounts.find_by_id(card["id"])["credit_limit"] == 5000.0
    row = next(a for a in runner_status() if a["name"] == "ACME Card")
    assert row["balance_state"] == "match"


def test_a_limit_already_set_is_never_replaced(bank):
    card = _card(limit=3000.0)
    bank.balances[ACC_SAVER] = {
        "balance": 1200.0,
        "available_to_spend": 1200.0,
        "credit_limit": 5000.0,
    }

    _run()

    assert ledger_service.accounts.find_by_id(card["id"])["credit_limit"] == 3000.0


def test_a_current_accounts_overdraft_is_not_a_credit_limit(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    assert not balances.fill_limit(main, {"limit": 500.0, "currency": "GBP"})


def test_a_limit_that_cant_be_set_never_costs_the_runs_balances(bank, monkeypatch):
    from backend.core.errors import ValidationError

    card = _card()

    def refuse(*a, **k):
        raise ValidationError("refused")

    monkeypatch.setattr(accounts_service, "update_account", refuse)
    bank.balances[ACC_SAVER] = {
        "balance": 1200.0,
        "available_to_spend": 1200.0,
        "credit_limit": 5000.0,
    }

    _run()

    assert card["id"] in balances.stored("gocardless")


def runner_status() -> list[dict]:
    from backend.services.bank_data_sync import status

    return status("gocardless")["accounts"]


# ── 6. Enable Banking dates ────────────────────────────────────────────────────────────────


def test_a_row_dated_only_by_its_purchase_date_keeps_that_date():
    row = {"transaction_date": "2026-05-04"}
    assert eb_svc._txn_date(row) == "2026-05-04"
    assert eb_svc._txn_date({"value_date": "2026-05-05", **row}) == "2026-05-05"


def test_a_row_with_no_date_is_the_users_today(monkeypatch):
    from datetime import date

    monkeypatch.setattr(eb_svc, "user_today", lambda: date(2026, 5, 9))
    assert eb_svc._txn_date({}) == "2026-05-09"


# ── 7. a multi-currency account is read in its own currency ────────────────────────────────


def test_enable_banking_reads_the_balance_in_the_accounts_currency(monkeypatch):
    body = {
        "balances": [
            {"balance_type": "CLBD", "balance_amount": {"amount": "100.00", "currency": "EUR"}},
            {"balance_type": "CLBD", "balance_amount": {"amount": "250.00", "currency": "USD"}},
        ]
    }
    monkeypatch.setattr(eb_svc, "_safe_get", lambda *a: _Page(body))
    best, kinds = eb_svc.fetch_account_balances("JWT", "EB-1")

    usd = eb_svc._account_to_common({"uid": "EB-1", "currency": "USD"}, best, kinds)
    eur = eb_svc._account_to_common({"uid": "EB-1", "currency": "EUR"}, best, kinds)

    assert (usd["balance"], usd["cleared_balance"]) == (250.0, 250.0)
    assert (eur["balance"], eur["cleared_balance"]) == (100.0, 100.0)
    assert "by_currency" not in usd


def test_gocardless_reads_the_balance_in_the_accounts_currency(db_isolated, monkeypatch):
    body = {
        "balances": [
            {
                "balanceType": "interimBooked",
                "balanceAmount": {"amount": "100.00", "currency": "EUR"},
            },
            {
                "balanceType": "interimBooked",
                "balanceAmount": {"amount": "250.00", "currency": "USD"},
            },
        ]
    }
    monkeypatch.setattr(gocardless_svc, "_authed", lambda *a, **k: _Page(body))
    monkeypatch.setattr(gocardless_svc, "_json", lambda resp: resp.json())
    store = type("S", (), {"cache_account_balance": lambda self, *a: None})()

    figure = gocardless_svc._account_balance(store, {"key": "b"}, "acc", {}, currency="USD")

    assert figure["cleared_balance"] == 250.0


def test_a_window_with_no_read_from_is_unchanged():
    assert FetchWindow().read_from is None


# ── The account list says a card in credit holds its balance, never "owed" ──────────────────


def _listed(account_id: str) -> dict:
    rows = connector_fetch.provider_accounts("gocardless")["accounts"]
    return next(a for a in rows if a["provider_account_id"] == account_id)


def test_the_list_marks_a_card_in_credit_by_its_banks_limit(bank):
    bank.balances[ACC_SAVER] = {
        "account_type": "credit",
        "cleared_balance": 50.0,
        "available_to_spend": 2050.0,
        "credit_limit": 2000.0,
    }

    row = _listed(ACC_SAVER)

    assert row["in_credit"] is True
    assert row["credit_limit"] == 2000.0


def test_the_list_marks_a_card_that_owes_as_owing(bank):
    bank.balances[ACC_SAVER] = {
        "account_type": "credit",
        "cleared_balance": 50.0,
        "available_to_spend": 1950.0,
        "credit_limit": 2000.0,
    }

    assert _listed(ACC_SAVER)["in_credit"] is False


def test_the_list_reads_a_linked_cards_own_limit_when_the_bank_states_none(bank):
    _card(limit=2000.0)
    bank.balances[ACC_SAVER] = {
        "account_type": "credit",
        "cleared_balance": 50.0,
        "available_to_spend": 2050.0,
    }

    assert _listed(ACC_SAVER)["in_credit"] is True


def test_with_no_limit_anywhere_the_list_cannot_say_a_card_is_in_credit(bank):
    bank.balances[ACC_SAVER] = {
        "account_type": "credit",
        "cleared_balance": 50.0,
        "available_to_spend": 2050.0,
    }

    row = _listed(ACC_SAVER)

    assert row["in_credit"] is False
    assert row["credit_limit"] is None


def test_a_current_account_is_never_in_credit_whatever_its_overdraft(bank):
    bank.balances[ACC_MAIN] = {
        "account_type": "cash",
        "cleared_balance": 50.0,
        "available_to_spend": 2050.0,
        "credit_limit": 2000.0,
    }

    row = _listed(ACC_MAIN)

    assert row["in_credit"] is False
    assert row["credit_limit"] is None
