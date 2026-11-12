"""Enable Banking aggregation service — JWT + list/link/session + fetch_window mapping +
pagination + classify_error (NO network).

Covers:
  * _sign_jwt — produces a verifiable RS256 JWT with the right header/kid/claims.
  * list_institutions — GET /aspsps?country= (mocked); bad-country guard.
  * start_authorization — POST /auth → returns the consent url + persists aspsp/country.
  * create_session — POST /sessions → persists the session_id.
  * fetch_window — the plain read-only fetch entrypoint (RFC connectors-as-data-sources):
    maps EB accounts + transactions into the common FetchWindow shape, every row carries
    the ORIGINAL provider dict under "raw" (the transaction-drawer evidence), an account
    filter narrows to one account, accounts_only skips the transaction fetch, a per-account
    fetch failure is isolated (rest still returned, complete=False), and credit/debit sign
    handling regresses to a skip (never a corrupted negative amount) on an unrecognized
    indicator.
  * fetch_account_transactions — pages via continuation_key until exhausted, and reports
    complete=False if the page cap is hit before the continuation_key runs out.
  * classify_error — plain function; there is no adapter class any more.

All HTTP is mocked — no live-network call.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.services.aggregation import enable_banking as svc
from backend.services.aggregation.providers import FetchWindow, derived_ref

# ── deterministic Enable Banking fixtures ────────────────────────────────────────────

_ACCOUNTS = [
    {"uid": "EB-CHK", "name": "Checking", "currency": "EUR"},
    {"uid": "EB-SAV", "name": "Savings", "currency": "EUR"},
]

_TXNS = {
    "EB-CHK": [
        {
            "entry_reference": "T1",
            "booking_date": "2026-05-01",
            "transaction_amount": {"amount": "42.50", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "remittance_information": ["Coffee Shop"],
        },
        {
            "entry_reference": "T2",
            "booking_date": "2026-05-02",
            "transaction_amount": {"amount": "2000.00", "currency": "EUR"},
            "credit_debit_indicator": "CRDT",
            "creditor": {"name": "Payroll"},
        },
    ],
    "EB-SAV": [
        {
            "entry_reference": "T3",
            "booking_date": "2026-05-04",
            "transaction_amount": {"amount": "100.00", "currency": "EUR"},
            "credit_debit_indicator": "CRDT",
            "remittance_information": "Interest",
        }
    ],
}


def _rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(autouse=True)
def isolate_stores(db_isolated):
    """Every test runs against a fresh, isolated database."""


def _store():
    from backend.db.enable_banking_config_store import EnableBankingConfigStore

    return EnableBankingConfigStore()


def _configure(monkeypatch, txns_by_account=None):
    """Stub the wire calls fetch_window makes: a fresh bearer, the session's accounts, and
    each account's transaction page (single page, complete)."""
    feed = txns_by_account if txns_by_account is not None else _TXNS
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(svc, "fetch_session_accounts", lambda token, sid: list(_ACCOUNTS))
    monkeypatch.setattr(svc, "fetch_account_balance", lambda token, uid: None)
    monkeypatch.setattr(
        svc,
        "fetch_account_transactions",
        lambda token, uid, *, date_from, date_to: (list(feed.get(uid, [])), True),
    )
    return _store()


# ── JWT ────────────────────────────────────────────────────────────────────────────


def test_sign_jwt_verifiable():
    pem = _rsa_pem()
    tok = svc._sign_jwt("app-9", pem)
    parts = tok.split(".")
    assert len(parts) == 3
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    body = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    assert header == {"alg": "RS256", "kid": "app-9", "typ": "JWT"}
    assert body["iss"] == "enablebanking.com" and body["aud"] == "api.enablebanking.com"


def test_bearer_requires_creds():
    with pytest.raises(ValidationError):
        svc._bearer(_store())


def test_sign_jwt_bad_key_raises():
    with pytest.raises(ValidationError):
        svc._sign_jwt("app-9", "not-a-pem")


# ── list_institutions ─────────────────────────────────────────────────────────────────


def test_list_institutions(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "aspsps": [
                    {"name": "Bank A", "country": "DE", "logo": "a.png"},
                    {"name": "Bank B", "country": "DE"},
                    {"country": "DE"},  # no name — dropped
                ]
            }

    monkeypatch.setattr(svc.httpx, "get", lambda *a, **k: _Resp())
    rows = svc.list_institutions("DE")
    assert [r["id"] for r in rows] == ["Bank A", "Bank B"]
    # list_institutions returns picker rows (id/name/country/logo); the front-door adds the
    # provider tag, not this low-level call.
    assert "provider" not in rows[0]


def test_list_institutions_bad_country():
    with pytest.raises(ValidationError):
        svc.list_institutions("DEU")


# ── start_authorization + create_session ─────────────────────────────────────────────


def test_start_authorization_returns_url_and_persists(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"url": "https://bank.example/auth/abc", "authorization_id": "AUTH-1"}

    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _Resp())
    out = svc.start_authorization("Bank A", "DE", "https://app/connectors")
    assert out["url"] == "https://bank.example/auth/abc"
    cfg = _store().get()
    assert cfg["aspsp"] == "Bank A" and cfg["country"] == "DE"


class _JsonResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_start_authorization_sends_the_required_state(monkeypatch):
    """Issue #103: Enable Banking 422s an /auth body without `state` (verified on the
    sandbox). The body carries state, aspsp {name, country} and access.valid_until."""
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    sent = {}

    def _post(url, json, **kw):
        sent.update(json)
        return _JsonResp(200, {"url": "https://bank.example/a", "authorization_id": "A"})

    monkeypatch.setattr(svc.httpx, "post", _post)
    svc.start_authorization("Mock ASPSP", "fi", "")
    assert sent["state"]
    assert sent["aspsp"] == {"name": "Mock ASPSP", "country": "FI"}
    assert sent["access"]["valid_until"]
    assert sent["redirect_url"] == svc._DEFAULT_REDIRECT
    assert "valid_until" not in sent


def test_start_authorization_requires_a_country():
    with pytest.raises(ValidationError):
        svc.start_authorization("Mock ASPSP", "", "")


def test_start_authorization_error_carries_enable_bankings_reason(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    body = {
        "code": 422,
        "message": "Wrong request parameters provided",
        "error": "WRONG_REQUEST_PARAMETERS",
        "detail": [{"loc": ["body", "state"], "msg": "Field required", "input": {"x": 1}}],
    }
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _JsonResp(422, body))
    with pytest.raises(ValidationError) as exc:
        svc.start_authorization("Mock ASPSP", "FI", "")
    msg = str(exc.value)
    assert "Wrong request parameters provided" in msg and "WRONG_REQUEST_PARAMETERS" in msg
    assert "state" in msg and "'x'" not in msg


def test_start_authorization_unregistered_redirect_names_the_url(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    body = {"code": 400, "message": "Redirect URI not allowed", "error": "REDIRECT_URI_NOT_ALLOWED"}
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _JsonResp(400, body))
    with pytest.raises(ValidationError) as exc:
        svc.start_authorization("Mock ASPSP", "FI", "https://app.example/connectors")
    assert "https://app.example/connectors" in str(exc.value)


def test_api_error_survives_a_non_json_body():
    class _Bad:
        status_code = 502

        @staticmethod
        def json():
            raise ValueError("not json")

    assert str(svc._api_error(_Bad(), "listing banks")) == "listing banks failed: HTTP 502"


def test_fetch_session_accounts_reads_uid_strings_and_details(monkeypatch):
    """Issue #103 follow-up: GET /sessions/{id} lists `accounts` as bare uid strings (seen
    on the sandbox); each is enriched from /accounts/{uid}/details, and a details failure
    still keeps the uid so its transactions fetch."""
    from backend.core.errors import ExternalServiceError

    def _get(token, path, params):
        if path == "/sessions/S1":
            return _JsonResp(200, {"accounts": ["U1", "U2", "U1"], "accounts_data": []})
        if path == "/accounts/U1/details":
            return _JsonResp(200, {"uid": "U1", "name": "Main", "currency": "EUR"})
        raise ExternalServiceError("Enable Banking HTTP 500")

    monkeypatch.setattr(svc, "_safe_get", _get)
    out = svc.fetch_session_accounts("JWT", "S1")
    assert out == [{"uid": "U1", "name": "Main", "currency": "EUR"}, {"uid": "U2"}]


def test_fetch_account_balance_prefers_the_booked_balance(monkeypatch):
    body = {
        "balances": [
            {"balance_type": "ITAV", "balance_amount": {"amount": "11.12", "currency": "EUR"}},
            {"balance_type": "CLBD", "balance_amount": {"amount": "-7.98", "currency": "EUR"}},
        ]
    }
    monkeypatch.setattr(svc, "_safe_get", lambda *a: _JsonResp(200, body))
    assert svc.fetch_account_balance("JWT", "U1") == -7.98


def test_fetch_account_balance_failure_is_none(monkeypatch):
    from backend.core.errors import ExternalServiceError

    def _boom(*a):
        raise ExternalServiceError("Enable Banking HTTP 429")

    monkeypatch.setattr(svc, "_safe_get", _boom)
    assert svc.fetch_account_balance("JWT", "U1") is None


def test_account_to_common_suggests_type_and_mask():
    card = {"uid": "U1", "name": "Card", "currency": "EUR", "cash_account_type": "CARD"}
    current = {**card, "cash_account_type": "CACC", "account_id": {"iban": "FI0000001234"}}
    assert svc._account_to_common(card, -50.0)["account_type"] == "credit"
    assert svc._account_to_common(card, -50.0)["balance"] == -50.0
    assert svc._account_to_common(current)["account_type"] == "cash"
    assert svc._account_to_common(current)["mask"] == "1234"
    assert svc._account_to_common({"uid": "U2"})["account_type"] is None
    assert svc._account_to_common(card)["currency_inferred"] is False
    assert svc._account_to_common({"uid": "U2"})["currency_inferred"] is True


def test_transaction_status_decides_pending_or_dropped():
    """Seen on the sandbox: every status comes back by default. Cancelled, rejected and
    scheduled rows are not money that moved; pending and on-hold rows are not final."""
    base = {
        "entry_reference": "E1",
        "booking_date": "2026-11-11",
        "transaction_amount": {"amount": "9.50", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
    }

    def row(status):
        return svc._txn_to_row({**base, "status": status}, "U1", "EUR")

    for gone in ("CNCL", "RJCT", "SCHD", "cncl"):
        assert row(gone) is None
    assert row("PDNG")["pending"] is True
    assert row("HOLD")["pending"] is True
    for final in ("BOOK", "OTHR", None):
        assert row(final)["pending"] is False
        assert row(final)["amount"] == -9.5


def test_create_session_persists_session_id(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"session_id": "SESS-7", "accounts": _ACCOUNTS}

    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _Resp())
    out = svc.create_session("code-xyz")
    assert out["session_id"] == "SESS-7"
    assert _store().session_id() == "SESS-7"


def test_create_session_requires_code():
    with pytest.raises(ValidationError):
        svc.create_session("")


# ── fetch_window ─────────────────────────────────────────────────────────────────────


def test_fetch_window_normalizes_accounts_and_transactions(monkeypatch):
    store = _configure(monkeypatch)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)

    assert isinstance(window, FetchWindow)
    assert {a["provider_account_id"] for a in window.accounts} == {"EB-CHK", "EB-SAV"}
    assert window.complete is True
    assert len(window.rows) == 3

    # Every row carries the ORIGINAL provider record — the transaction-drawer evidence.
    all_raw_txns = _TXNS["EB-CHK"] + _TXNS["EB-SAV"]
    for row in window.rows:
        assert row["raw"] in all_raw_txns

    by_ref = {row["external_refs"][0]["ref"]: row for row in window.rows}
    assert set(by_ref) == {"EB-CHK:T1", "EB-CHK:T2", "EB-SAV:T3"}
    assert by_ref["EB-CHK:T1"]["amount"] == -42.5  # DBIT → negative
    assert by_ref["EB-CHK:T2"]["amount"] == 2000.0  # CRDT → positive
    assert by_ref["EB-SAV:T3"]["amount"] == 100.0
    assert all(row["external_refs"][0]["source"] == "enablebanking" for row in window.rows)


def test_fetch_window_filters_to_requested_account(monkeypatch):
    store = _configure(monkeypatch)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", "EB-CHK")
    assert [a["provider_account_id"] for a in window.accounts] == ["EB-CHK"]
    assert {row["provider_account_id"] for row in window.rows} == {"EB-CHK"}


def test_fetch_window_accounts_only_skips_transaction_fetch(monkeypatch):
    store = _configure(monkeypatch)
    calls = []

    def _unexpected(*a, **k):
        calls.append((a, k))
        return [], True

    monkeypatch.setattr(svc, "fetch_account_transactions", _unexpected)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    assert window.rows == []
    assert calls == []  # accounts_only never reaches the transaction fetch at all
    assert {a["provider_account_id"] for a in window.accounts} == {"EB-CHK", "EB-SAV"}


def test_fetch_window_incomplete_when_an_account_is_truncated(monkeypatch):
    store = _configure(monkeypatch)

    def _fetch(token, uid, *, date_from, date_to):
        if uid == "EB-CHK":
            return list(_TXNS["EB-CHK"]), False  # continuation_key remained — truncated
        return list(_TXNS.get(uid, [])), True

    monkeypatch.setattr(svc, "fetch_account_transactions", _fetch)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.complete is False
    # The truncated account's rows it DID get are still returned, not dropped.
    assert any(r["provider_account_id"] == "EB-CHK" for r in window.rows)


def test_fetch_window_per_account_error_isolated(monkeypatch):
    """One bad account's transaction fetch is skipped; the rest still come back, and the
    window is marked incomplete rather than silently missing rows."""
    store = _configure(monkeypatch)

    def _fetch(token, uid, *, date_from, date_to):
        if uid == "EB-CHK":
            raise httpx.ConnectError("boom")
        return list(_TXNS.get(uid, [])), True

    monkeypatch.setattr(svc, "fetch_account_transactions", _fetch)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.complete is False
    assert {row["provider_account_id"] for row in window.rows} == {"EB-SAV"}


def test_fetch_window_isolates_the_sanitized_error_safe_get_actually_raises(monkeypatch):
    """The real failure mode: fetch_account_transactions raises ExternalServiceError (via
    _safe_get), not a raw httpx exception -- fetch_window's per-account isolation must
    catch THAT type too, or a single account's HTTP error takes the whole fetch down
    instead of being isolated (regression guard for the S-F1 _safe_get wrap)."""
    store = _configure(monkeypatch)

    def _fetch(token, uid, *, date_from, date_to):
        if uid == "EB-CHK":
            raise ExternalServiceError("Enable Banking HTTP 500")
        return list(_TXNS.get(uid, [])), True

    monkeypatch.setattr(svc, "fetch_account_transactions", _fetch)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.complete is False
    assert {row["provider_account_id"] for row in window.rows} == {"EB-SAV"}


def test_fetch_window_skips_rows_with_unrecognized_indicator(monkeypatch):
    """Regression: a txn with an absent/unrecognized credit_debit_indicator must be
    SKIPPED from the fetch window — never silently returned as a negative-amount row,
    which would corrupt the account balance once the caller books it."""
    no_indicator_txn = {
        "entry_reference": "T-NOIND",
        "booking_date": "2026-05-10",
        "transaction_amount": {"amount": "500.00", "currency": "EUR"},
        # deliberately no credit_debit_indicator
    }
    store = _configure(monkeypatch, txns_by_account={"EB-CHK": [no_indicator_txn], "EB-SAV": []})
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.rows == []


def test_txn_to_row_falls_back_to_derived_ref_when_no_id():
    """A transaction with no entry_reference/transaction_id gets the shared content-hash
    fallback ref (RFC §A step 3) — never a dropped row."""
    txn = {
        "booking_date": "2026-05-01",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "remittance_information": "No ID Merchant",
    }
    row = svc._txn_to_row(txn, "EB-CHK", "EUR")
    expected_ref = derived_ref("EB-CHK", "2026-05-01", -10.0, "EUR", "No ID Merchant")
    assert row["external_refs"] == [{"source": "enablebanking", "ref": expected_ref}]
    assert row["raw"] is txn


# ── SECURITY (S-F1): the session id never leaks into an error message ────────────────


def test_safe_get_never_leaks_the_session_id_on_a_404(monkeypatch):
    """An unwrapped resp.raise_for_status() puts the FULL request URL --
    /sessions/{session_id} -- into httpx's own exception message. _safe_get must re-raise
    a sanitized ExternalServiceError carrying only the status code."""
    session_id = "SECRET-SESSION-ID-abc123"

    def boom(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", f"{svc.API_BASE}/sessions/{session_id}")
        response = httpx.Response(404, request=request, text="not found")
        raise httpx.HTTPStatusError("404", request=request, response=response)

    monkeypatch.setattr(svc.httpx, "get", boom)

    with pytest.raises(ExternalServiceError) as ei:
        svc.fetch_session_accounts("JWT", session_id)
    msg = str(ei.value)
    assert session_id not in msg
    assert "/sessions/" not in msg
    assert "404" in msg
    assert svc.classify_error(ei.value) == "connect_error"


def test_session_id_never_reaches_the_fetch_result_or_last_error(monkeypatch):
    """End to end through connector_fetch.fetch(): a 404 fetching session accounts must
    not leak the session id into the tool result or into public()['last_error'], which
    public() otherwise deliberately hides session_id from."""
    from backend.services import connector_fetch

    session_id = "SECRET-SESSION-ID-xyz789"
    store = _store()
    store.update(
        {
            "application_id": "app-1",
            "private_key": "not-a-real-key-but-truthy",
            "session_id": session_id,
            "enabled": True,
        }
    )
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")

    def boom(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request, text="not found")
        raise httpx.HTTPStatusError("404", request=request, response=response)

    # Mock the underlying transport, not fetch_session_accounts itself -- this must
    # exercise the REAL _safe_get wrap, not bypass it.
    monkeypatch.setattr(svc.httpx, "get", boom)

    result = connector_fetch.fetch("enable_banking")
    assert session_id not in str(result)
    assert session_id not in (store.public().get("last_error") or "")


# ── fetch_account_transactions pagination ────────────────────────────────────────────


def test_fetch_account_transactions_pages_via_continuation_key(monkeypatch):
    class _Resp:
        content = b"{}"  # _safe_get's size guard only reads len(), never parses this

        def __init__(self, txns, cont=None):
            self._txns = txns
            self._cont = cont

        def raise_for_status(self):
            pass

        def json(self):
            body = {"transactions": self._txns}
            if self._cont:
                body["continuation_key"] = self._cont
            return body

    responses = [
        _Resp([{"entry_reference": "A"}], cont="page-2"),
        _Resp([{"entry_reference": "B"}]),
    ]
    calls = []

    def _get(url, params=None, **kwargs):
        calls.append(params)
        return responses.pop(0)

    monkeypatch.setattr(svc.httpx, "get", _get)
    txns, complete = svc.fetch_account_transactions(
        "JWT", "EB-CHK", date_from="2026-05-01", date_to="2026-05-31"
    )
    assert [t["entry_reference"] for t in txns] == ["A", "B"]
    assert complete is True
    assert calls[0] == {"date_from": "2026-05-01", "date_to": "2026-05-31"}
    assert calls[1]["continuation_key"] == "page-2"


def test_fetch_account_transactions_page_cap_returns_incomplete(monkeypatch):
    """A never-terminating continuation_key must not spin forever — the safety cap
    (_MAX_FETCH_PAGES) trips and the caller is told the fetch is INCOMPLETE, never
    silently truncated-but-labeled-complete."""

    class _Resp:
        content = b"{}"  # _safe_get's size guard only reads len(), never parses this

        def raise_for_status(self):
            pass

        def json(self):
            return {"transactions": [{"entry_reference": "X"}], "continuation_key": "always-more"}

    monkeypatch.setattr(svc.httpx, "get", lambda *a, **k: _Resp())
    txns, complete = svc.fetch_account_transactions(
        "JWT", "EB-CHK", date_from="2026-05-01", date_to="2026-05-31"
    )
    assert complete is False
    assert len(txns) == svc._MAX_FETCH_PAGES


# ── credit_debit_indicator handling ──────────────────────────────────────────────────


def test_txn_amount_crdt_is_positive():
    """CRDT indicator → positive (income) amount."""
    txn = {
        "transaction_amount": {"amount": "100.00", "currency": "EUR"},
        "credit_debit_indicator": "CRDT",
    }
    amount, currency = svc._txn_amount(txn)
    assert amount == 100.0
    assert currency == "EUR"


def test_txn_amount_dbit_is_negative():
    """DBIT indicator → negative (expense) amount."""
    txn = {
        "transaction_amount": {"amount": "50.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
    }
    amount, currency = svc._txn_amount(txn)
    assert amount == -50.0
    assert currency == "EUR"


def test_txn_amount_absent_indicator_skipped():
    """Absent credit_debit_indicator → (None, '') so the txn is NOT booked."""
    txn = {
        "transaction_amount": {"amount": "75.00", "currency": "EUR"},
        # no credit_debit_indicator key at all
    }
    amount, currency = svc._txn_amount(txn)
    assert amount is None
    assert currency == ""


def test_txn_amount_empty_indicator_skipped():
    """Empty credit_debit_indicator → (None, '') so the txn is NOT booked."""
    txn = {
        "transaction_amount": {"amount": "75.00", "currency": "EUR"},
        "credit_debit_indicator": "",
    }
    amount, currency = svc._txn_amount(txn)
    assert amount is None


def test_txn_amount_camelcase_indicator_read():
    """creditDebitIndicator (camelCase) is treated as a fallback when snake_case absent."""
    txn = {
        "transaction_amount": {"amount": "200.00", "currency": "USD"},
        "creditDebitIndicator": "CRDT",
    }
    amount, currency = svc._txn_amount(txn)
    assert amount == 200.0
    assert currency == "USD"


def test_txn_amount_camelcase_dbit():
    """creditDebitIndicator=DBIT (camelCase) → negative amount."""
    txn = {
        "transaction_amount": {"amount": "30.00", "currency": "GBP"},
        "creditDebitIndicator": "DBIT",
    }
    amount, currency = svc._txn_amount(txn)
    assert amount == -30.0


# ── classify_error (plain function — the adapter class is retired) ──────────────────


def test_classify_error_validation_is_auth_error():
    assert svc.classify_error(ValidationError("no creds")) == "auth_error"


def test_classify_error_http_401_is_auth_error():
    resp = httpx.Response(401, request=httpx.Request("GET", "https://x"))
    exc = httpx.HTTPStatusError("unauthorized", request=resp.request, response=resp)
    assert svc.classify_error(exc) == "auth_error"


def test_classify_error_http_403_is_auth_error():
    resp = httpx.Response(403, request=httpx.Request("GET", "https://x"))
    exc = httpx.HTTPStatusError("forbidden", request=resp.request, response=resp)
    assert svc.classify_error(exc) == "auth_error"


def test_classify_error_http_500_is_connect_error():
    resp = httpx.Response(500, request=httpx.Request("GET", "https://x"))
    exc = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
    assert svc.classify_error(exc) == "connect_error"


def test_classify_error_generic_http_error_is_connect_error():
    assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"


def test_classify_error_unknown_exception_is_error():
    assert svc.classify_error(RuntimeError("unexpected")) == "error"
