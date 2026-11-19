"""Lunch Money aggregation service — protocol + row normalization (NO real network).

RFC connectors-as-data-sources: this module is now a PURE protocol + normalize surface —
``fetch_window`` returns a ``FetchWindow`` a Lucius turn reads and books via the ordinary
``add_transactions`` tool; there is no more adapter class, no ``run_lunch_money_sync``, and
no local booking/dedup (that lives in ``backend.services.connector_fetch``, tested
elsewhere). Covers the v2 API shape (migrated 2026-08-17 — see the module docstring for
what changed from v1):
  * fetch_transactions — pagination via has_more, no more debit_as_negative param.
  * fetch_account_names — builds the "plaid:<id>"/"asset:<id>" -> display name lookup from
    GET /plaid_accounts + GET /manual_accounts (v2 dehydrated transactions no longer carry
    institution/account names inline), degrading gracefully on failure.
  * fetch_window — maps a raw feed into the common normalized row shape, carrying the
    ORIGINAL provider dict under "raw" (PRD-required evidence for the transaction drawer),
    filters by account, and skips group/split-parent rows (their real children carry the
    same money as separate rows) while logging a signal an operator can compare against.
  * the v2 sign convention (positive = debit/expense, negative = credit/income — FIXED, the
    opposite of v1's debit_as_negative=true shape this adapter used to request) is exercised
    through the real _txn_to_row mapping function with a known debit AND a known credit —
    getting this backwards would silently flip every imported transaction's direction
    (security-review-caliber risk).
  * a non-finite amount (NaN/Infinity/an overflowing literal) is skipped, never a row.
  * classify_error — plain function, exception -> status taxonomy.
  * an invalid/expired token surfaces a clear auth_error via classify_error, never leaked
    into logs.
  * a non-httpx exception raised while requesting (e.g. a header-encoding failure) still
    comes out of _safe_get sanitized, never the raw exception.

All HTTP is mocked with a fixture feed — there is no live-network call here (no real
Lunch Money access token exists in this environment).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.lunch_money_config_store import LunchMoneyConfigStore
from backend.services.aggregation import lunch_money as svc

# ── a deterministic Lunch Money /transactions feed fixture (real v2 shape) ─────────────

_FEED = {
    "transactions": [
        {
            "id": 101,
            "date": "2026-08-01",
            "payee": "Coffee Shop",
            "original_name": "COFFEE SHOP #123",
            "amount": "4.50",  # v2: positive = debit = expense
            "currency": "usd",
            "plaid_account_id": 9001,
            "manual_account_id": None,
            "category_id": 55,
            "status": "reviewed",
            "is_pending": False,
            "is_group_parent": False,
            "group_parent_id": None,
            "is_split_parent": False,
            "split_parent_id": None,
            "tag_ids": [],
        },
        {
            "id": 102,
            "date": "2026-08-02",
            "payee": "Employer Payroll",
            "original_name": "DIRECT DEPOSIT PAYROLL",
            "amount": "-2000.00",  # v2: negative = credit = income
            "currency": "usd",
            "plaid_account_id": 9001,
            "manual_account_id": None,
            "category_id": None,
            "status": "reviewed",
            "is_pending": False,
            "is_group_parent": False,
            "group_parent_id": None,
            "is_split_parent": False,
            "split_parent_id": None,
            "tag_ids": [],
        },
        {
            "id": 103,
            "date": "2026-08-03",
            "payee": "Interest",
            "original_name": "Interest",
            "amount": "-1.20",  # credit = income
            "currency": "usd",
            "plaid_account_id": None,
            "manual_account_id": 42,
            "category_id": None,
            "status": "unreviewed",
            "is_pending": False,
            "is_group_parent": False,
            "group_parent_id": None,
            "is_split_parent": False,
            "split_parent_id": None,
            "tag_ids": [],
        },
    ],
    "has_more": False,
}

_ACCOUNT_NAMES = {
    "plaid:9001": "Acme Bank · Checking",
    "asset:42": "Acme Bank · Savings (manual)",
}


@pytest.fixture(autouse=True)
def isolate_store(db_isolated, monkeypatch):
    """Fresh isolated DB per test (config store is DB-backed) and no stray token from a
    developer's real shell."""


def _configured_store() -> LunchMoneyConfigStore:
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok-abc", "enabled": True})
    return store


def _stub_feed(monkeypatch, feed=_FEED, account_names=_ACCOUNT_NAMES):
    """Stub fetch_transactions/fetch_account_names to return `feed`/`account_names` —
    mirrors what a real fetch_window round-trip sees."""
    monkeypatch.setattr(
        svc,
        "fetch_transactions",
        lambda store, *, start_date, end_date: list(feed["transactions"]),
    )
    monkeypatch.setattr(svc, "fetch_account_names", lambda store: dict(account_names))


# ── fetch_window ─────────────────────────────────────────────────────────────────────


def test_fetch_window_normalizes_rows_and_carries_raw(monkeypatch):
    """Every row carries external_refs/date/amount/currency/description plus the ORIGINAL
    provider dict under "raw" (PRD-required evidence for the transaction drawer) — never a
    re-serialization of the normalized fields. Sign convention: v2's positive=debit is
    flipped to this app's own "+=money in" convention."""
    store = _configured_store()
    _stub_feed(monkeypatch)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-03", None)

    assert window.complete is True
    by_ref = {row["external_refs"][0]["ref"]: row for row in window.rows}
    assert set(by_ref) == {"101", "102", "103"}

    coffee = by_ref["101"]
    assert coffee["amount"] == -4.5  # debit (money out) -> negative in our convention
    assert coffee["currency"] == "USD"
    assert coffee["provider_account_id"] == "plaid:9001"
    assert coffee["description"] == "Coffee Shop"
    assert coffee["external_refs"] == [{"source": "lunch_money", "ref": "101"}]
    assert coffee["raw"] is _FEED["transactions"][0]
    assert coffee["pending"] is False
    assert coffee["memo"] is None
    assert coffee["supersedes_ref"] is None

    payroll = by_ref["102"]
    assert payroll["amount"] == 2000.0  # credit (money in) -> positive

    interest = by_ref["103"]
    assert interest["amount"] == 1.2
    assert interest["provider_account_id"] == "asset:42"


def test_fetch_window_builds_accounts_block_with_names(monkeypatch):
    store = _configured_store()
    _stub_feed(monkeypatch)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-03", None)

    by_id = {a["provider_account_id"]: a for a in window.accounts}
    assert set(by_id) == {"plaid:9001", "asset:42"}
    assert by_id["plaid:9001"]["name"] == "Acme Bank · Checking"
    assert by_id["asset:42"]["name"] == "Acme Bank · Savings (manual)"
    assert by_id["plaid:9001"]["currency"] == "USD"
    assert by_id["plaid:9001"]["mask"] is None
    assert by_id["plaid:9001"]["balance"] is None  # never synced — balances are computed


def test_fetch_window_falls_back_to_generic_distinct_account_names(monkeypatch):
    """A failed/empty name lookup must not collide two accounts on the same literal
    string — each fallback name is qualified by its own account_key."""
    store = _configured_store()
    _stub_feed(monkeypatch, account_names={})

    window = svc.fetch_window(store, "2026-08-01", "2026-08-03", None)

    names = [a["name"] for a in window.accounts]
    assert len(names) == len(set(names))
    assert "Lunch Money account (plaid:9001)" in names
    assert "Lunch Money account (asset:42)" in names


def test_fetch_window_filters_rows_and_accounts_by_account(monkeypatch):
    store = _configured_store()
    _stub_feed(monkeypatch)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-03", "asset:42")

    assert [a["provider_account_id"] for a in window.accounts] == ["asset:42"]
    assert {r["provider_account_id"] for r in window.rows} == {"asset:42"}


def test_fetch_window_skips_unassigned_transaction(monkeypatch):
    """A transaction with neither plaid_account_id nor manual_account_id has nowhere
    sensible to book — skipped rather than dumped into a guessed bucket."""
    feed = {
        "transactions": [
            {
                "id": 301,
                "date": "2026-08-01",
                "payee": "Unassigned",
                "amount": "10.00",
                "currency": "usd",
                "is_group_parent": False,
                "is_split_parent": False,
            },
        ],
        "has_more": False,
    }
    store = _configured_store()
    _stub_feed(monkeypatch, feed=feed)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-01", None)
    assert window.rows == []
    assert window.accounts == []


def test_fetch_window_accounts_only_returns_no_rows_and_single_day_window(monkeypatch):
    """accounts_only=True must fetch a single-day window (before==before) and return no
    transaction rows — only the accounts block."""
    store = _configured_store()
    calls = []
    monkeypatch.setattr(
        svc,
        "fetch_transactions",
        lambda s, *, start_date, end_date: (
            calls.append((start_date, end_date)) or list(_FEED["transactions"])
        ),
    )
    monkeypatch.setattr(svc, "fetch_account_names", lambda s: dict(_ACCOUNT_NAMES))

    window = svc.fetch_window(store, "2026-01-01", "2026-08-03", None, accounts_only=True)

    assert window.rows == []
    assert len(window.accounts) == 2
    assert calls == [("2026-08-03", "2026-08-03")]


def test_fetch_window_skips_group_parent_children_still_import(monkeypatch, caplog):
    """A group parent's amount is a synthesized TOTAL of its own children (Lunch Money's
    v2 docs: "represent the totalled amount of transactions bearing this transaction's id
    as their group_parent_id") — its real children are separate rows in the same feed.
    Importing the parent alongside its children would double-count the underlying money
    movement — only leaf rows are booked, and the skip is logged so an operator (or
    Lucius) has a signal to compare against "children actually imported"."""
    feed = {
        "transactions": [
            {
                "id": 201,
                "date": "2026-08-01",
                "payee": "Costco (grouped)",
                "amount": "120.00",
                "currency": "usd",
                "plaid_account_id": 9001,
                "is_group_parent": True,  # synthesized total — must be skipped
                "group_parent_id": None,
                "is_split_parent": False,
                "split_parent_id": None,
            },
            {
                "id": 202,
                "date": "2026-08-01",
                "payee": "Costco - groceries",
                "amount": "80.00",
                "currency": "usd",
                "plaid_account_id": 9001,
                "group_parent_id": 201,
                "is_group_parent": False,
                "is_split_parent": False,
                "split_parent_id": None,
            },
            {
                "id": 203,
                "date": "2026-08-01",
                "payee": "Costco - household",
                "amount": "40.00",
                "currency": "usd",
                "plaid_account_id": 9001,
                "group_parent_id": 201,
                "is_group_parent": False,
                "is_split_parent": False,
                "split_parent_id": None,
            },
        ],
        "has_more": False,
    }
    store = _configured_store()
    _stub_feed(monkeypatch, feed=feed)

    with caplog.at_level(logging.INFO, logger=svc.logger.name):
        window = svc.fetch_window(store, "2026-08-01", "2026-08-01", None)

    ext_refs = {r["external_refs"][0]["ref"] for r in window.rows}
    assert ext_refs == {"202", "203"}

    assert any("skipped 1 group/split parent row" in r.message for r in caplog.records), (
        "expected an info log naming the group/split parent skip count"
    )
    # The healthy case (parent correctly recognized) must NOT trip the "detection may be
    # broken" warning below — no false positive when everything is working as designed.
    assert not any("may be broken" in r.message for r in caplog.records)
    assert window.warnings == []


def test_fetch_window_warns_when_group_children_present_but_no_parent_ever_recognized(
    monkeypatch, caplog
):
    """The whole skip-the-parent strategy rests on an assumption that's never been
    verified against a real account. If is_group_parent ever silently stops matching
    (e.g. a field got renamed upstream), the failure is SILENT: a full-amount parent row
    would get booked alongside its own already-imported children — a 2x double-count —
    with nothing skipped, indistinguishable from "this feed genuinely has no group
    parents". This simulates exactly that: a child row (group_parent_id set) is present,
    but nothing in the feed is ever recognized as the parent."""
    feed = {
        "transactions": [
            {
                "id": 701,
                "date": "2026-08-01",
                "payee": "Costco - groceries",
                "amount": "80.00",
                "currency": "usd",
                "plaid_account_id": 9001,
                "group_parent_id": 999,  # a child of SOME group parent...
                "is_group_parent": False,  # ...but nothing in this feed is ever recognized
                "is_split_parent": False,
                "split_parent_id": None,
            },
        ],
        "has_more": False,
    }
    store = _configured_store()
    _stub_feed(monkeypatch, feed=feed)

    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        window = svc.fetch_window(store, "2026-08-01", "2026-08-01", None)

    assert len(window.rows) == 1  # the unrecognized "child" still gets booked as-is
    assert any(
        "group_parent_id" in r.message and "may be broken" in r.message for r in caplog.records
    ), "expected a warning naming the unrecognized group-parent-detection scenario"
    # And it must reach the model/store, not just the server log (RFC connectors-as-
    # data-sources: FetchWindow.warnings is connector_fetch's own last_caution channel).
    assert any("may be broken" in w for w in window.warnings)


def test_fetch_window_warns_when_split_children_present_but_no_parent_ever_recognized(
    monkeypatch, caplog
):
    feed = {
        "transactions": [
            {
                "id": 801,
                "date": "2026-08-01",
                "payee": "Rent (split)",
                "amount": "500.00",
                "currency": "usd",
                "plaid_account_id": 9001,
                "split_parent_id": 999,  # a child of SOME split parent...
                "is_split_parent": False,  # ...but nothing in this feed is ever recognized
                "is_group_parent": False,
                "group_parent_id": None,
            },
        ],
        "has_more": False,
    }
    store = _configured_store()
    _stub_feed(monkeypatch, feed=feed)

    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        window = svc.fetch_window(store, "2026-08-01", "2026-08-01", None)

    assert any(
        "split_parent_id" in r.message and "may be broken" in r.message for r in caplog.records
    ), "expected a warning naming the unrecognized split-parent-detection scenario"
    assert any("may be broken" in w for w in window.warnings)


def test_fetch_window_skips_non_finite_amount_rows(monkeypatch):
    """A NaN-amount row (float() parses "NaN" without raising) must be skipped, never
    surfaced as a row Lucius could book."""
    feed = {
        "transactions": [
            {
                "id": 401,
                "date": "2026-08-01",
                "payee": "Poisoned",
                "amount": "NaN",
                "currency": "usd",
                "plaid_account_id": 9001,
                "is_group_parent": False,
                "is_split_parent": False,
            },
        ],
        "has_more": False,
    }
    store = _configured_store()
    _stub_feed(monkeypatch, feed=feed)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-01", None)
    assert window.rows == []
    # The account still shows up (the txn carries a valid account link) even though its
    # own row was dropped as unbookable.
    assert [a["provider_account_id"] for a in window.accounts] == ["plaid:9001"]


# ── _txn_to_row (the row-mapping helper fetch_window calls) ────────────────────────────


def test_txn_to_row_v2_sign_convention_debit_is_negative_credit_is_positive():
    """v2 FIXES the sign convention regardless of any per-user setting: positive amount =
    debit (money out), negative amount = credit (money in) — the OPPOSITE of what this
    adapter's old v1 debit_as_negative=true request used to produce. Getting this backwards
    would silently flip the direction of every imported transaction."""
    debit = {
        "id": 501,
        "date": "2026-08-01",
        "payee": "Grocery Store",
        "amount": "42.50",  # v2: positive = debit = money OUT
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    row = svc._txn_to_row(debit, "plaid:1", "USD")
    assert row is not None
    assert row["amount"] == -42.5

    credit = {
        "id": 502,
        "date": "2026-08-02",
        "payee": "Refund",
        "amount": "-15.00",  # v2: negative = credit = money IN
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    row = svc._txn_to_row(credit, "plaid:1", "USD")
    assert row is not None
    assert row["amount"] == 15.0


def test_a_group_parent_without_its_children_is_booked_not_dropped(monkeypatch):
    """Lunch Money returns a group as its parent ALONE unless the children are asked for
    (seen on its v2 mock, 2026-11-13). Skipping a parent whose children are not in the feed
    lost the whole group's money; it is booked itself instead."""
    parent = {
        "id": 301,
        "date": "2026-08-01",
        "payee": "ACME Fuel (grouped)",
        "amount": "25.69",
        "currency": "usd",
        "plaid_account_id": 9001,
        "is_group_parent": True,
        "group_parent_id": None,
        "is_split_parent": False,
        "split_parent_id": None,
    }
    _stub_feed(monkeypatch, feed={"transactions": [parent], "has_more": False})
    window = svc.fetch_window(_configured_store(), "2026-08-01", "2026-08-01", None)
    assert [(r["external_refs"][0]["ref"], r["amount"]) for r in window.rows] == [("301", -25.69)]


def test_fetch_transactions_asks_for_group_children(monkeypatch):
    """The children are the real movements, each in its own account — they must be asked
    for, or a group only ever arrives as its parent."""
    seen: list[dict] = []

    class _Resp:
        def json(self):
            return {"transactions": [], "has_more": False}

    def _get(store, path, params):
        seen.append(dict(params))
        return _Resp()

    monkeypatch.setattr(svc, "_safe_get", _get)
    svc.fetch_transactions(_configured_store(), start_date="2026-08-01", end_date="2026-08-02")
    assert seen and seen[0]["include_group_children"] == "true"


@pytest.mark.parametrize("bad_amount", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_txn_to_row_rejects_non_finite_amounts(bad_amount):
    """float() happily parses all four of these without raising (1e400 overflows to inf) —
    none of them is money. NaN in particular defeats the plain `amount == 0` guard (NaN
    never equals anything), so an unguarded row would become a poisoned row that corrupts
    every downstream sum it touches."""
    lm_txn = {
        "id": 999,
        "date": "2026-08-01",
        "payee": "Bad Amount",
        "amount": bad_amount,
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    assert svc._txn_to_row(lm_txn, "plaid:1", "USD") is None


def test_txn_to_row_zero_amount_skipped():
    lm_txn = {
        "id": 600,
        "date": "2026-08-01",
        "payee": "Nothing",
        "amount": "0",
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    assert svc._txn_to_row(lm_txn, "plaid:1", "USD") is None


def test_txn_to_row_description_falls_back_to_original_name_then_default():
    row = svc._txn_to_row(
        {
            "id": 1,
            "date": "2026-08-01",
            "original_name": "RAW BANK TEXT",
            "amount": "5.00",
            "is_group_parent": False,
            "is_split_parent": False,
        },
        "plaid:1",
        "USD",
    )
    assert row["description"] == "RAW BANK TEXT"

    row = svc._txn_to_row(
        {
            "id": 2,
            "date": "2026-08-01",
            "amount": "5.00",
            "is_group_parent": False,
            "is_split_parent": False,
        },
        "plaid:1",
        "USD",
    )
    assert row["description"] == ""


def test_txn_to_row_falls_back_to_derived_ref_when_no_id():
    """No stable provider id -> a content-hash ref, never dropped (RFC §A step 3)."""
    lm_txn = {
        "date": "2026-08-01",
        "payee": "No id",
        "amount": "5.00",
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    row = svc._txn_to_row(lm_txn, "plaid:1", "USD")
    assert row is not None
    ref = row["external_refs"][0]["ref"]
    assert ref.startswith("derived:")


def test_txn_to_row_carries_the_original_dict_as_raw():
    lm_txn = {
        "id": 42,
        "date": "2026-08-01",
        "payee": "Evidence",
        "amount": "5.00",
        "currency": "usd",
        "is_group_parent": False,
        "is_split_parent": False,
    }
    row = svc._txn_to_row(lm_txn, "plaid:1", "USD")
    assert row["raw"] is lm_txn


# ── classify_error (plain function) ────────────────────────────────────────────────────


def test_classify_error_auth_on_401_and_403_message():
    assert svc.classify_error(ExternalServiceError("Lunch Money HTTP 401")) == "auth_error"
    assert svc.classify_error(ExternalServiceError("Lunch Money HTTP 403")) == "auth_error"


def test_classify_error_rate_limited_on_429_message():
    assert svc.classify_error(ExternalServiceError("Lunch Money HTTP 429")) == "rate_limited"


def test_classify_error_connect_error_on_other_external_service_error():
    assert svc.classify_error(ExternalServiceError("Lunch Money HTTP 500")) == "connect_error"
    assert (
        svc.classify_error(ExternalServiceError("Lunch Money request failed (ConnectError)"))
        == "connect_error"
    )


def test_classify_error_on_validation_error_message():
    assert svc.classify_error(ValidationError("no access token")) == "connect_error"


def test_classify_error_auth_on_httpx_status_error():
    request = httpx.Request("GET", svc.API_BASE + "/transactions")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    assert svc.classify_error(exc) == "auth_error"


def test_classify_error_connect_on_generic_httpx_error():
    assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"


def test_classify_error_generic_on_unknown_exception():
    assert svc.classify_error(RuntimeError("unexpected")) == "error"


# ── credential handling / raw HTTP layer ────────────────────────────────────────────────


def test_safe_get_raises_validation_error_when_no_token(db_isolated):
    store = LunchMoneyConfigStore()
    with pytest.raises(ValidationError):
        svc.fetch_transactions(store, start_date="2026-01-01", end_date="2026-08-01")


def test_bearer_header_carries_the_token(monkeypatch):
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-secret-tok"})
    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {"transactions": [], "has_more": False}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    svc.fetch_transactions(store, start_date="2026-01-01", end_date="2026-08-01")
    assert captured["headers"]["Authorization"] == "Bearer lm-secret-tok"
    assert captured["url"] == f"{svc.API_BASE}/transactions"
    assert svc.API_BASE == "https://api.lunchmoney.dev/v2"


def test_safe_get_sanitizes_a_non_httpx_exception(db_isolated):
    """_safe_get's docstring claims it NEVER lets a raw exception escape, but the except
    clauses only catch httpx.HTTPStatusError/httpx.HTTPError by design. A non-ASCII access
    token demonstrates the gap for real: httpx raises UnicodeEncodeError while encoding the
    Authorization header, which is NOT an httpx.HTTPError subclass — so without the generic
    except clause it would sail straight past both and reach the caller (and, downstream,
    last_error/logs) as a raw, un-sanitized exception. This is a real network-free repro
    (header encoding fails before any socket is opened), not a mock standing in for the
    real failure (security-review finding L4)."""
    store = LunchMoneyConfigStore()
    store.update({"access_token": "tökén-non-ascii"})

    with pytest.raises(ExternalServiceError) as exc_info:
        svc._safe_get(store, "/transactions", {})

    msg = str(exc_info.value)
    assert "tökén" not in msg
    assert "UnicodeEncodeError" in msg


def test_safe_get_never_leaks_the_token_on_an_http_error(monkeypatch):
    """The token rides ONLY in the Authorization header — _safe_get reformats an
    httpx.HTTPStatusError down to a fixed "HTTP <code>" string, discarding the raw
    exception (and any header echo) entirely."""
    store = LunchMoneyConfigStore()
    store.update({"access_token": "SUPER-SECRET-LM-TOKEN"})

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request(
            "GET", url, headers={"Authorization": "Bearer SUPER-SECRET-LM-TOKEN"}
        )
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    with pytest.raises(ExternalServiceError) as exc_info:
        svc._safe_get(store, "/transactions", {})
    assert "SUPER-SECRET-LM-TOKEN" not in str(exc_info.value)
    assert "401" in str(exc_info.value)


# ── pagination + query shape ─────────────────────────────────────────────────────────


def test_fetch_transactions_paginates_on_has_more(monkeypatch):
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    pages = [
        {"transactions": [{"id": i} for i in range(svc._PAGE_LIMIT)], "has_more": True},
        {"transactions": [{"id": svc._PAGE_LIMIT}], "has_more": False},
    ]
    calls = []

    class _Resp:
        status_code = 200
        content = b"{}"

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    out = svc.fetch_transactions(store, start_date="2026-01-01", end_date="2026-08-01")
    assert len(out) == svc._PAGE_LIMIT + 1
    assert len(calls) == 2
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == svc._PAGE_LIMIT
    # v2 removed debit_as_negative entirely (and would 400 on an unrecognised param) — the
    # adapter must never send it.
    assert all("debit_as_negative" not in c for c in calls)


def test_fetch_transactions_bounds_response_size(monkeypatch):
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    class _HugeResp:
        status_code = 200
        content = b"x" * (svc._MAX_RESPONSE_BYTES + 1)

        def raise_for_status(self):
            return None

        def json(self):
            raise AssertionError("must never parse an oversized body")

    monkeypatch.setattr(
        svc.httpx, "get", lambda url, params=None, headers=None, timeout=None: _HugeResp()
    )
    with pytest.raises(ExternalServiceError, match="too large"):
        svc.fetch_transactions(store, start_date="2026-01-01", end_date="2026-08-01")


# ── fetch_account_names (v2's replacement for the removed inline hydration) ────────────


def test_fetch_account_names_builds_lookup_from_both_endpoints(monkeypatch):
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    responses = {
        "/plaid_accounts": {
            "plaid_accounts": [
                {
                    "id": 9001,
                    "name": "Checking",
                    "display_name": "Checking",
                    "institution_name": "Acme Bank",
                }
            ]
        },
        "/manual_accounts": {
            "manual_accounts": [
                {"id": 42, "name": "Savings", "display_name": None, "institution_name": "Acme Bank"}
            ]
        },
    }

    class _Resp:
        status_code = 200
        content = b"{}"

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_get(url, params=None, headers=None, timeout=None):
        for path, body in responses.items():
            if url.endswith(path):
                return _Resp(body)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    names = svc.fetch_account_names(store)
    assert names == {
        "plaid:9001": "Acme Bank · Checking",
        "asset:42": "Acme Bank · Savings",
    }


def test_fetch_account_names_degrades_to_empty_on_http_failure(monkeypatch):
    """A failure fetching /plaid_accounts or /manual_accounts must never raise past
    fetch_account_names — the caller (fetch_window) treats these as best-effort naming,
    not a fetch-blocking dependency."""
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    assert svc.fetch_account_names(store) == {}


# ── malformed responses must degrade, never abort the fetch (security-review finding M1) ─


def test_fetch_account_names_degrades_on_unparseable_json_body(monkeypatch):
    """resp.json() raising json.JSONDecodeError (a malformed/empty body — e.g. a proxy
    maintenance page instead of the real API response) must degrade to no names for that
    endpoint, not raise past fetch_account_names."""
    import json as json_mod

    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    class _MalformedResp:
        status_code = 200
        content = b"<html>503 Service Unavailable</html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise json_mod.JSONDecodeError("Expecting value", "<html>...", 0)

    monkeypatch.setattr(
        svc.httpx, "get", lambda url, params=None, headers=None, timeout=None: _MalformedResp()
    )
    assert svc.fetch_account_names(store) == {}


def test_fetch_account_names_degrades_when_body_is_a_json_array(monkeypatch):
    """A body that parses fine as JSON but is a bare array instead of the expected object
    (e.g. {"plaid_accounts": [...]}) used to raise AttributeError on the `.get(list_key)`
    call — a list has no .get. Must degrade instead of raising."""
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    class _ArrayResp:
        status_code = 200
        content = b"[]"

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": 9001, "name": "Checking"}]  # array, not {"plaid_accounts": [...]}

    monkeypatch.setattr(
        svc.httpx, "get", lambda url, params=None, headers=None, timeout=None: _ArrayResp()
    )
    assert svc.fetch_account_names(store) == {}


def test_fetch_account_names_skips_a_non_dict_account_entry(monkeypatch):
    """One malformed entry inside an otherwise-valid list (a bare string instead of an
    account object) must be skipped, not blow up the whole endpoint's worth of names —
    the well-formed sibling entries still resolve."""
    store = LunchMoneyConfigStore()
    store.update({"access_token": "lm-tok"})

    class _Resp:
        status_code = 200
        content = b"{}"

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    responses = {
        "/plaid_accounts": {
            "plaid_accounts": [
                "not-an-account-object",  # malformed entry — must be skipped, not raise
                {"id": 9001, "name": "Checking", "institution_name": "Acme Bank"},
            ]
        },
        "/manual_accounts": {"manual_accounts": []},
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        for path, body in responses.items():
            if url.endswith(path):
                return _Resp(body)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    names = svc.fetch_account_names(store)
    assert names == {"plaid:9001": "Acme Bank · Checking"}


def test_fetch_window_survives_malformed_account_names_response_end_to_end(monkeypatch):
    """The whole point: /transactions succeeding must not get dragged down by a broken
    /plaid_accounts or /manual_accounts response. An unguarded json.JSONDecodeError inside
    fetch_account_names must not propagate up through fetch_window — the transactions
    themselves already fetched successfully and must still come back as rows.

    This drives the REAL HTTP layer (httpx.get is the only thing mocked — fetch_account_names
    itself runs unmodified): /transactions returns a normal feed, /plaid_accounts +
    /manual_accounts both return a malformed (non-JSON) body, and fetch_window must still
    return all 3 rows."""
    import json as json_mod

    store = _configured_store()

    class _TxnResp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {"transactions": list(_FEED["transactions"]), "has_more": False}

    class _MalformedResp:
        status_code = 200
        content = b"<html>503 Service Unavailable</html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise json_mod.JSONDecodeError("Expecting value", "<html>...", 0)

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/transactions"):
            return _TxnResp()
        return _MalformedResp()  # /plaid_accounts, /manual_accounts

    monkeypatch.setattr(svc.httpx, "get", fake_get)

    window = svc.fetch_window(store, "2026-08-01", "2026-08-03", None)
    assert len(window.rows) == 3  # /transactions succeeded and must still be fully mapped
