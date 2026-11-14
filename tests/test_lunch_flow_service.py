"""Lunch Flow aggregation service — the PURE protocol + normalize half (RFC
``connectors-as-data-sources``: booking now happens through ``add_transactions`` in the
Lucius turn that calls ``lunch_flow_fetch_transactions``, never here). Covers:

  * fetch_accounts / fetch_account_transactions — plain GETs (mocked).
  * fetch_window — accounts + rows normalization, date-window/account filtering, the
    reactive-401-retry, per-account fetch isolation, and the polarity smell test surfaced
    via ``FetchWindow.warnings``. Every row carries a ``raw`` field with the ORIGINAL
    provider dict (evidence for the transaction drawer).
  * classify_error — exception → status taxonomy (plain function).
  * disconnect_connection — DELETE /connections/{id}, forgets the connection's local
    bookkeeping on success.

All HTTP is mocked — there is no live-network call here.
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.errors import ValidationError
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.services.aggregation import lunch_flow as svc
from backend.services.aggregation import lunch_flow_oauth
from backend.services.aggregation.providers import derived_ref

_ACCOUNTS = [
    {
        "id": 1,
        "connection_id": 100,
        "name": "Everyday Checking",
        "institution_name": "Example Bank",
        "institution_logo": "https://example.test/logo.png",
        "provider": "gocardless",
        "currency": "USD",
        "status": "ACTIVE",
    },
    {
        "id": 2,
        "connection_id": 100,
        "name": "Savings",
        "institution_name": "Example Bank",
        "provider": "gocardless",
        "currency": "USD",
        "status": "ACTIVE",
    },
]

# These fixtures ENCODE the assumed (GoCardless-style) polarity — POSITIVE = money in,
# NEGATIVE = money out — the same UNVERIFIED guess `_txn_to_row` makes (see its own inline
# comment). This suite passing does NOT confirm that guess is correct: no live Lunch Flow
# account exists in this environment, so every assertion below about which row lands as
# "income" vs "expense" is only checking that the CODE matches THIS assumption, not that the
# assumption matches Lunch Flow's real wire format. If a real fetch's classification turns out
# backwards, these fixtures (and every assertion built on them) would need re-signing too —
# see `_polarity_smell_test` in services/aggregation/lunch_flow.py for the runtime check that
# exists precisely because a green test suite here proves nothing about real-world polarity.
_TXNS_ACCOUNT_1 = [
    {
        "id": "T1",
        "accountId": 1,
        "amount": -42.50,  # money out → expense
        "currency": "USD",
        "date": "2026-05-01",
        "merchant": "Coffee Shop",
        "description": "COFFEE SHOP PURCHASE",
        "isPending": False,
    },
    {
        "id": "T2",
        "accountId": 1,
        "amount": 2000.00,  # money in → income
        "currency": "USD",
        "date": "2026-05-02",
        "description": "Payroll",
        "isPending": False,
    },
    {
        "id": "T3",
        "accountId": 1,
        "amount": 9.99,
        "currency": "USD",
        "date": "2026-05-03",
        "description": "Streaming",
        "isPending": True,  # PENDING — still normalized by fetch_window; connector_fetch
        # decides whether to book it (include_pending), not this module
    },
]
_TXNS_ACCOUNT_2 = [
    {
        "id": "T4",
        "accountId": 2,
        "amount": 100.00,  # interest in
        "currency": "USD",
        "date": "2026-05-04",
        "merchant": "Interest",
        "isPending": False,
    }
]


@pytest.fixture(autouse=True)
def _linked_store(db_isolated):
    """Give the connector a linked config (developer credentials + OAuth tokens) so
    ``ensure_fresh_token`` succeeds without needing a refresh in tests that don't care
    about that path."""
    LunchFlowConfigStore().update({"client_id": "cid", "client_secret": "csecret"})
    LunchFlowConfigStore().set_tokens(
        access_token="access-1", refresh_token="refresh-1", expires_at=None, user_id="u1"
    )


def _configure_transactions(monkeypatch, by_account: dict):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/accounts"):
            return _Resp(200, {"accounts": _ACCOUNTS, "total": len(_ACCOUNTS)})
        for account_id, txns in by_account.items():
            if url.endswith(f"/accounts/{account_id}/transactions"):
                return _Resp(200, {"transactions": txns, "total": len(txns)})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


# ── fetch_accounts / fetch_account_transactions ────────────────────────────────────────


def test_fetch_accounts_returns_the_account_list(monkeypatch):
    monkeypatch.setattr(
        svc.httpx, "get", lambda *a, **k: _Resp(200, {"accounts": _ACCOUNTS, "total": 2})
    )
    out = svc.fetch_accounts("access-1")
    assert out == _ACCOUNTS


def test_fetch_accounts_raises_on_error_status(monkeypatch):
    monkeypatch.setattr(svc.httpx, "get", lambda *a, **k: _Resp(401, {"error": "unauthorized"}))
    with pytest.raises(ValidationError):
        svc.fetch_accounts("bad-token")


# ── fetch_window: normalization + raw evidence ──────────────────────────────────────────


def test_fetch_window_normalizes_accounts_and_rows_with_raw_evidence(monkeypatch):
    _configure_transactions(monkeypatch, {1: _TXNS_ACCOUNT_1, 2: _TXNS_ACCOUNT_2})
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)

    assert {a["provider_account_id"] for a in window.accounts} == {"1", "2"}
    # Pending filtering is connector_fetch's job (via include_pending), not fetch_window's —
    # T3 (isPending) is still normalized and returned here.
    by_ref = {row["external_refs"][0]["ref"]: row for row in window.rows}
    assert set(by_ref) == {"1:T1", "1:T2", "1:T3", "2:T4"}

    t1 = by_ref["1:T1"]
    assert t1["amount"] == -42.5
    assert t1["provider_account_id"] == "1"
    assert t1["raw"] == _TXNS_ACCOUNT_1[0]  # the ORIGINAL provider dict, unmodified

    t2 = by_ref["1:T2"]
    assert t2["amount"] == 2000.0
    assert t2["raw"] == _TXNS_ACCOUNT_1[1]

    t3 = by_ref["1:T3"]
    assert t3["pending"] is True
    assert t3["raw"] == _TXNS_ACCOUNT_1[2]

    t4 = by_ref["2:T4"]
    assert t4["raw"] == _TXNS_ACCOUNT_2[0]

    assert window.complete is True
    assert window.warnings == []


def test_fetch_window_filters_to_since_before(monkeypatch):
    _configure_transactions(monkeypatch, {1: _TXNS_ACCOUNT_1, 2: _TXNS_ACCOUNT_2})
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-02", "2026-05-02", None)
    assert {row["external_refs"][0]["ref"] for row in window.rows} == {"1:T2"}


def test_fetch_window_filters_to_one_account(monkeypatch):
    _configure_transactions(monkeypatch, {1: _TXNS_ACCOUNT_1, 2: _TXNS_ACCOUNT_2})
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", "2")
    assert {a["provider_account_id"] for a in window.accounts} == {"2"}
    assert {row["provider_account_id"] for row in window.rows} == {"2"}


def test_fetch_window_accounts_only_skips_transaction_fetch(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/accounts"):
            return _Resp(200, {"accounts": _ACCOUNTS, "total": 2})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    assert len(window.accounts) == 2
    assert window.rows == []
    assert calls == [f"{svc.API_BASE}/accounts"]  # never fetched transactions


# ── fetch_window: 401 handling ───────────────────────────────────────────────────────────


def test_fetch_window_raises_after_a_persistent_401(monkeypatch):
    """A PERSISTENT 401 (every GET, even after the one reactive retry forces a refresh) must
    still propagate — the retry gives a transient 401 one chance to self-heal, it does not
    swallow a genuinely broken token. classify_error turns this into auth_error."""
    get_calls = []

    def fake_get(url, headers=None, timeout=None):
        get_calls.append(url)
        return _Resp(401, {"error": "unauthorized"})

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    # The reactive retry force-refreshes on the first 401 — mock its token POST too (real
    # network egress from a unit test would defeat "no live network here" and is flaky).
    monkeypatch.setattr(
        lunch_flow_oauth.httpx,
        "post",
        lambda *a, **k: _Resp(200, {"access_token": "access-2", "expires_in": 3600}),
    )
    store = LunchFlowConfigStore()

    with pytest.raises(ValidationError) as exc_info:
        svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert svc.classify_error(exc_info.value) == "auth_error"
    # fetch_accounts was tried, force-refreshed, then retried once — two GETs, not a loop.
    assert len(get_calls) == 2


def test_fetch_window_retries_once_on_a_live_401_then_succeeds(monkeypatch):
    """A token that looked fresh proactively but gets rejected live (expired a little early,
    revoked mid-run) gets ONE reactive retry — force-refresh, retry, succeed — rather than
    failing the whole fetch."""
    get_calls = []

    def fake_get(url, headers=None, timeout=None):
        get_calls.append((url, headers.get("Authorization")))
        if url.endswith("/accounts") and headers.get("Authorization") == "Bearer access-1":
            return _Resp(401, {"error": "unauthorized"})  # the stale token, rejected live
        if url.endswith("/accounts"):
            return _Resp(200, {"accounts": _ACCOUNTS, "total": len(_ACCOUNTS)})
        for account_id, txns in {1: _TXNS_ACCOUNT_1, 2: _TXNS_ACCOUNT_2}.items():
            if url.endswith(f"/accounts/{account_id}/transactions"):
                return _Resp(200, {"transactions": txns, "total": len(txns)})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    monkeypatch.setattr(
        lunch_flow_oauth.httpx,
        "post",
        lambda *a, **k: _Resp(
            200, {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}
        ),
    )
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert len(window.rows) == 4  # T1, T2, T3 (pending), T4
    # The retry actually happened (two Authorization headers seen for /accounts).
    accounts_auth = [auth for url, auth in get_calls if url.endswith("/accounts")]
    assert accounts_auth == ["Bearer access-1", "Bearer access-2"]
    assert LunchFlowConfigStore().access_token() == "access-2"


def test_fetch_window_isolates_a_single_bad_account_fetch(monkeypatch):
    """One account's transaction fetch failing must not abort the whole window — the other
    account's transactions still land."""

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/accounts"):
            return _Resp(200, {"accounts": _ACCOUNTS, "total": 2})
        if url.endswith("/accounts/1/transactions"):
            raise httpx.ConnectError("boom")
        if url.endswith("/accounts/2/transactions"):
            return _Resp(200, {"transactions": _TXNS_ACCOUNT_2, "total": 1})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert len(window.rows) == 1  # only account 2's transaction landed
    assert window.rows[0]["provider_account_id"] == "2"


# ── classify_error (plain function) ─────────────────────────────────────────────────────


def test_classify_error_validation_error_is_auth_error():
    assert svc.classify_error(ValidationError("boom")) == "auth_error"


def test_classify_error_http_401_is_auth_error():
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    assert svc.classify_error(exc) == "auth_error"


def test_classify_error_http_403_is_auth_error():
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(403, request=request)
    exc = httpx.HTTPStatusError("forbidden", request=request, response=response)
    assert svc.classify_error(exc) == "auth_error"


def test_classify_error_http_500_is_connect_error():
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)
    assert svc.classify_error(exc) == "connect_error"


def test_classify_error_generic_httpx_error_is_connect_error():
    assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"


def test_classify_error_unknown_exception_is_error():
    assert svc.classify_error(RuntimeError("unexpected")) == "error"


# ── polarity smell test (security review, 2026-08-17) ───────────────────────────────────


def test_polarity_smell_test_ignores_a_small_sample():
    # Even 100% one-sided is unremarkable below the sample floor.
    assert svc._polarity_smell_test(income_count=3, expense_count=0) is None


def test_polarity_smell_test_ignores_a_plausible_debit_heavy_split():
    # A normal bank feed: mostly expenses, a little income. Not a smell.
    assert svc._polarity_smell_test(income_count=1, expense_count=15) is None
    assert svc._polarity_smell_test(income_count=2, expense_count=8) is None  # exactly 20%


def test_polarity_smell_test_fires_on_an_implausibly_high_income_share():
    caution = svc._polarity_smell_test(income_count=9, expense_count=1)  # 90%
    assert caution is not None
    assert "90%" in caution


def test_polarity_smell_test_fires_when_every_row_is_on_one_side():
    caution = svc._polarity_smell_test(income_count=0, expense_count=12)
    assert caution is not None
    caution2 = svc._polarity_smell_test(income_count=12, expense_count=0)
    assert caution2 is not None


def _lopsided_income_txns(n: int) -> list[dict]:
    """n synthetic Lunch Flow transactions, every one POSITIVE (→ income under the current
    sign guess) — the exact signature a backwards polarity would produce for a normally
    debit-heavy real account."""
    return [
        {
            "id": f"L{i}",
            "accountId": 1,
            "amount": 10.0 + i,
            "currency": "USD",
            "date": "2026-05-01",
            "description": f"txn {i}",
            "isPending": False,
        }
        for i in range(n)
    ]


def test_fetch_window_surfaces_a_polarity_caution_in_warnings(monkeypatch, caplog):
    """The core Finding-1 fix: an implausible income/expense split must leave a runtime
    signal a caller can actually surface — ``FetchWindow.warnings`` — even though the fetch
    itself succeeded (this is a DATA caution, not a transport/auth failure)."""
    _configure_transactions(monkeypatch, {1: _lopsided_income_txns(12), 2: []})
    store = LunchFlowConfigStore()

    with caplog.at_level("WARNING"):
        window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)

    assert len(window.warnings) == 1
    assert "12" in window.warnings[0]
    assert any("polarity smell test fired" in r.message for r in caplog.records)


def test_fetch_window_has_no_warning_on_a_plausible_split(monkeypatch):
    _configure_transactions(monkeypatch, {1: _TXNS_ACCOUNT_1, 2: _TXNS_ACCOUNT_2})
    store = LunchFlowConfigStore()

    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.warnings == []


# ── disconnect_connection ───────────────────────────────────────────────────────────────


def test_disconnect_connection_forgets_local_bookkeeping(monkeypatch):
    store = LunchFlowConfigStore()
    store.record_account_meta(
        "1", connection_id=100, institution_name="Example Bank", name="Checking"
    )
    store.record_account_meta(
        "2", connection_id=100, institution_name="Example Bank", name="Savings"
    )
    assert len(store.connections()) == 1

    captured = {}

    def fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        return _Resp(204, {})

    monkeypatch.setattr(svc.httpx, "delete", fake_delete)
    result = svc.disconnect_connection(100)
    assert result["ok"] is True
    assert result["accounts_unlinked"] == 2
    assert captured["url"] == f"{svc.API_BASE}/connections/100"

    # bookkeeping forgotten — the connection's accounts are gone from accounts_meta
    assert LunchFlowConfigStore().accounts_meta() == {}
    assert LunchFlowConfigStore().connections() == []


def test_disconnect_connection_raises_on_failure(monkeypatch):
    monkeypatch.setattr(svc.httpx, "delete", lambda *a, **k: _Resp(403, {"error": "app-pays only"}))
    with pytest.raises(ValidationError):
        svc.disconnect_connection(100)


def test_merchant_is_the_payee_and_the_bank_wording_stays_the_description():
    """Lunch Flow sends a merchant AND the bank's own text. The merchant used to replace the
    text, losing what payee matching learns from; now each keeps its own field."""
    row = svc._txn_to_row(dict(_TXNS_ACCOUNT_1[0]), "1", "USD")
    assert (row["payee"], row["description"]) == ("Coffee Shop", "COFFEE SHOP PURCHASE")
    plain = svc._txn_to_row(dict(_TXNS_ACCOUNT_1[1]), "1", "USD")
    assert (plain["payee"], plain["description"]) == (None, "Payroll")


def test_an_id_less_row_keeps_the_key_it_had_before_the_description_change():
    txn = {k: v for k, v in _TXNS_ACCOUNT_1[0].items() if k != "id"}
    row = svc._txn_to_row(txn, "1", "USD")
    old_key = derived_ref("1", "2026-05-01", -42.5, "USD", "Coffee Shop")
    assert row["external_refs"][0]["ref"] == old_key
