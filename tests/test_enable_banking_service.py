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
    monkeypatch.setattr(svc, "fetch_account_balances", lambda token, uid: (None, {}))
    _store().update({"session_id": "SESS-1", "aspsp": "ACME Bank", "country": "FI"})
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


def test_start_authorization_unregistered_default_redirect_names_the_url(monkeypatch):
    """Linking with a code (no app address sent) and the default URL isn't registered
    either: Enable Banking's own reason, naming the URL to add."""
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    body = {"code": 400, "message": "Redirect URI not allowed", "error": "REDIRECT_URI_NOT_ALLOWED"}
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _JsonResp(400, body))
    with pytest.raises(ValidationError) as exc:
        svc.start_authorization("Mock ASPSP", "FI", "")
    assert svc._DEFAULT_REDIRECT in str(exc.value)


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
    assert svc.fetch_account_balances("JWT", "U1") == (
        -7.98,
        {"cleared_balance": -7.98, "working_balance": None, "available_to_spend": 11.12},
    )


def test_fetch_account_balance_failure_is_none(monkeypatch):
    from backend.core.errors import ExternalServiceError

    def _boom(*a):
        raise ExternalServiceError("Enable Banking HTTP 429")

    monkeypatch.setattr(svc, "_safe_get", _boom)
    assert svc.fetch_account_balances("JWT", "U1")[0] is None


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


def _pending(state, aspsp="ACME Bank", country="FI", age_minutes=0):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    _store().add_pending_link(state, aspsp=aspsp, country=country, now=now)


def _refused():
    return _JsonResp(
        400,
        {"code": 400, "message": "Redirect URI not allowed", "error": "REDIRECT_URI_NOT_ALLOWED"},
    )


def test_start_authorization_returns_to_the_app_when_its_url_is_registered(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    sent = []

    def _post(url, json, **kw):
        sent.append(dict(json))
        return _JsonResp(200, {"url": "https://bank.example/a", "authorization_id": "A"})

    monkeypatch.setattr(svc.httpx, "post", _post)
    out = svc.start_authorization("Mock ASPSP", "FI", "http://localhost:8090/connectors")
    assert [p["redirect_url"] for p in sent] == ["http://localhost:8090/connectors"]
    assert out["returns_to_app"] is True
    assert [p["state"] for p in _store().get()["pending_links"]] == [sent[0]["state"]]


def test_start_authorization_stops_when_the_app_url_is_not_registered(monkeypatch):
    """Enable Banking refuses an unregistered address before any bank page exists: the link
    stops there, naming the address to add — never a silent switch to a URL whose return
    lands on a page that cannot load. Nothing is remembered as a pending link."""
    from backend.core.errors import RedirectNotRegisteredError

    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    sent = []

    def _post(url, json, **kw):
        sent.append(dict(json))
        return _refused()

    monkeypatch.setattr(svc.httpx, "post", _post)
    app_url = "http://localhost:8090/connectors/callback/enable_banking"
    with pytest.raises(RedirectNotRegisteredError) as exc:
        svc.start_authorization("Mock ASPSP", "FI", app_url)
    assert [p["redirect_url"] for p in sent] == [app_url]
    assert exc.value.context["redirect_url"] == app_url and app_url in exc.value.message
    assert not _store().get()["pending_links"]


def test_start_authorization_with_a_code_uses_the_default_redirect(monkeypatch):
    """The user chose "Link with a code instead": no app address, the default one, and the
    form is told the bank won't bring them back."""
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    sent = []

    def _post(url, json, **kw):
        sent.append(dict(json))
        return _JsonResp(200, {"url": "https://bank.example/a", "authorization_id": "A"})

    monkeypatch.setattr(svc.httpx, "post", _post)
    out = svc.start_authorization("Mock ASPSP", "FI", "")
    assert [p["redirect_url"] for p in sent] == [svc._DEFAULT_REDIRECT]
    assert out["returns_to_app"] is False


def test_start_authorization_refuses_a_non_http_redirect():
    with pytest.raises(ValidationError):
        svc.start_authorization("Mock ASPSP", "FI", "javascript:alert(1)")


def test_create_session_refuses_a_state_it_did_not_send(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    _pending("S-1")
    called = []
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: called.append(1))
    with pytest.raises(ValidationError):
        svc.create_session("code-xyz", "S-OTHER")
    assert called == []


def test_create_session_with_its_state_completes_and_clears_it(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    _pending("S-1")
    monkeypatch.setattr(
        svc.httpx, "post", lambda *a, **k: _JsonResp(200, {"session_id": "SESS-9", "accounts": []})
    )
    svc.create_session("code-xyz", "S-1")
    assert [b["session_id"] for b in _store().banks()] == ["SESS-9"]
    assert _store().get()["pending_links"] == []
    with pytest.raises(ValidationError):
        svc.create_session("code-xyz", "S-1")  # a replayed return is refused


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
    assert [b["session_id"] for b in _store().banks()] == ["SESS-7"]


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
    assert all(row["external_refs"][0]["source"] == "enable_banking" for row in window.rows)


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
    """A transaction with no entry_reference gets the shared content-hash
    fallback ref (RFC §A step 3) — never a dropped row."""
    txn = {
        "booking_date": "2026-05-01",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "remittance_information": "No ID Merchant",
    }
    row = svc._txn_to_row(txn, "EB-CHK", "EUR")
    expected_ref = derived_ref("EB-CHK", "2026-05-01", -10.0, "EUR", "No ID Merchant")
    assert row["external_refs"] == [{"source": "enable_banking", "ref": expected_ref}]
    assert row["raw"] is txn


def test_a_relisted_row_keeps_its_ref_when_only_transaction_id_changes():
    """Enable Banking's transaction_id "may change if the list of transactions is requested
    again" (its docs). The same money listed twice with two transaction_ids must carry ONE
    ref, or the second listing books it again; entry_reference, when sent, is the key."""
    base = {
        "booking_date": "2026-05-01",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "remittance_information": "ACME Cafe",
    }
    first = svc._txn_to_row({**base, "transaction_id": "T-1"}, "EB-CHK", "EUR")
    again = svc._txn_to_row({**base, "transaction_id": "T-99"}, "EB-CHK", "EUR")
    assert first["external_refs"] == again["external_refs"]
    keyed = svc._txn_to_row(
        {**base, "transaction_id": "T-1", "entry_reference": "E-7"}, "EB-CHK", "EUR"
    )
    assert keyed["external_refs"] == [{"source": "enable_banking", "ref": "EB-CHK:E-7"}]


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


# ── several banks ───────────────────────────────────────────────────────────────────


def _link(monkeypatch, session_id, aspsp, country="FI", accounts=None, deleted=None, sessions=None):
    """Complete one bank link the way the app does: start (pending bank) → return → session.
    ``sessions`` ({session_id: [accounts]}) is what Enable Banking lists for each session; a
    renewal reads the old one before revoking it. Never the network."""
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    listed = sessions or {}
    monkeypatch.setattr(svc, "fetch_session_accounts", lambda token, sid: list(listed.get(sid, [])))
    monkeypatch.setattr(svc, "session_accounts_data", lambda token, sid: list(listed.get(sid, [])))
    _pending("S", aspsp, country)
    body = {
        "session_id": session_id,
        "accounts": accounts or [{"uid": f"{session_id}-A"}],
        "access": {"valid_until": "2027-02-10T00:00:00+00:00"},
    }
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _JsonResp(200, body))
    if deleted is not None:
        monkeypatch.setattr(
            svc.httpx, "delete", lambda url, **k: deleted.append(url) or _JsonResp(200, {})
        )
    return svc.create_session("code", "S")


def test_a_second_bank_is_added_beside_the_first(monkeypatch):
    _link(monkeypatch, "SESS-A", "ACME Bank")
    _link(monkeypatch, "SESS-B", "Other Bank", "SE")
    banks = _store().banks()
    assert [(b["aspsp"], b["country"]) for b in banks] == [
        ("ACME Bank", "FI"),
        ("Other Bank", "SE"),
    ]
    assert banks[0]["valid_until"] == "2027-02-10T00:00:00+00:00"
    assert banks[1]["account_ids"] == ["SESS-B-A"]


def test_linking_the_same_bank_again_renews_it_and_revokes_the_old_consent(monkeypatch):
    deleted: list[str] = []
    _link(monkeypatch, "SESS-OLD", "ACME Bank", deleted=deleted)
    _link(monkeypatch, "SESS-B", "Other Bank", "SE", deleted=deleted)
    _link(monkeypatch, "SESS-NEW", "ACME Bank", deleted=deleted)
    assert [b["session_id"] for b in _store().banks()] == ["SESS-B", "SESS-NEW"]
    assert deleted == [f"{svc.API_BASE}/sessions/SESS-OLD"]


def test_an_installs_single_session_becomes_its_first_bank(monkeypatch):
    """Before several banks, one session_id + aspsp/country held the one bank. It must read
    as a bank, survive the next link, and never need relinking."""
    _store().update({"session_id": "SESS-LEGACY", "aspsp": "ACME Bank", "country": "FI"})
    assert [(b["session_id"], b["aspsp"]) for b in _store().banks()] == [
        ("SESS-LEGACY", "ACME Bank")
    ]
    _link(monkeypatch, "SESS-B", "Other Bank", "SE")
    assert [b["session_id"] for b in _store().banks()] == ["SESS-LEGACY", "SESS-B"]
    assert _store().get()["session_id"] == ""  # folded into the list


def test_remove_bank_revokes_it_and_keeps_the_others(monkeypatch):
    deleted: list[str] = []
    _link(monkeypatch, "SESS-A", "ACME Bank", deleted=deleted)
    _link(monkeypatch, "SESS-B", "Other Bank", "SE", deleted=deleted)
    key = _store().banks()[0]["key"]
    out = svc.remove_bank(key)
    assert out == {"removed": True, "revoked": True, "banks_left": 1}
    assert deleted == [f"{svc.API_BASE}/sessions/SESS-A"]
    assert [b["aspsp"] for b in _store().banks()] == ["Other Bank"]
    with pytest.raises(ValidationError):
        svc.remove_bank(key)


def test_public_names_each_bank_but_never_its_session(monkeypatch):
    _link(monkeypatch, "SESS-SECRET-A", "ACME Bank")
    pub = _store().public()
    assert [(b["aspsp"], b["accounts"]) for b in pub["banks"]] == [("ACME Bank", 1)]
    assert "SESS-SECRET-A" not in str(pub)


def test_fetch_window_reads_every_bank_and_one_failure_hides_none(monkeypatch):
    from backend.core.errors import ExternalServiceError

    _link(monkeypatch, "SESS-A", "ACME Bank")
    _link(monkeypatch, "SESS-B", "Other Bank", "SE")
    _link(monkeypatch, "SESS-C", "Broken Bank", "DE")

    def _accounts(token, sid):
        if sid == "SESS-C":
            raise ExternalServiceError("Enable Banking HTTP 401")
        return [{"uid": f"{sid}-A", "name": f"{sid} main", "currency": "EUR"}]

    monkeypatch.setattr(svc, "fetch_session_accounts", _accounts)
    monkeypatch.setattr(svc, "fetch_account_balances", lambda token, uid: (10.0, {}))
    monkeypatch.setattr(
        svc, "fetch_account_transactions", lambda token, uid, *, date_from, date_to: ([], True)
    )
    w = svc.fetch_window(_store(), "2026-10-01", "2026-11-13", None)
    assert [(a["provider_account_id"], a["institution"]) for a in w.accounts] == [
        ("SESS-A-A", "ACME Bank"),
        ("SESS-B-A", "Other Bank"),
    ]
    assert w.complete is False
    assert w.warnings == ["Broken Bank: couldn't be read (Enable Banking HTTP 401)"]
    assert [b["access_ended"] for b in _store().banks()] == [None, None, None]


# ── a consent that no longer answers ────────────────────────────────────────────────


def test_a_closed_consent_is_an_ended_one_not_an_empty_bank(monkeypatch):
    """Verified on the sandbox (2026-11-13): a revoked consent still answers GET /sessions
    with 200 and its accounts, status CLOSED; only its transactions refuse. Reading it as a
    bank with accounts made every fetch an "ok" with nothing in it."""
    body = {"status": "CLOSED", "accounts": ["UID-1"]}
    monkeypatch.setattr(
        svc.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request("GET", "https://x")),
    )
    with pytest.raises(svc.ConsentEndedError, match="closed"):
        svc.fetch_session_accounts("JWT", "SESS-1")


def test_another_apps_consent_is_an_ended_one(monkeypatch):
    def _404(url, **k):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )

    monkeypatch.setattr(svc.httpx, "get", _404)
    with pytest.raises(svc.ConsentEndedError):
        svc.fetch_session_accounts("JWT", "SESS-1")


def _two_banks(monkeypatch, ended_sid):
    _link(monkeypatch, "SESS-A", "ACME Bank")
    _link(monkeypatch, "SESS-B", "Other Bank", "SE")

    def _accounts(token, sid):
        if sid == ended_sid:
            raise svc.ConsentEndedError("Enable Banking says this consent is closed")
        return [{"uid": f"{sid}-A", "currency": "EUR"}]

    monkeypatch.setattr(svc, "fetch_session_accounts", _accounts)
    monkeypatch.setattr(svc, "fetch_account_balances", lambda token, uid: (None, {}))
    monkeypatch.setattr(
        svc, "fetch_account_transactions", lambda token, uid, *, date_from, date_to: ([], True)
    )


def test_a_bank_whose_consent_ended_is_named_and_flagged_to_renew(monkeypatch):
    _two_banks(monkeypatch, "SESS-B")
    w = svc.fetch_window(_store(), "2026-11-01", "2026-11-13", None)
    assert [a["provider_account_id"] for a in w.accounts] == ["SESS-A-A"]
    assert w.complete is False
    assert w.warnings == ["Other Bank: access ended — renew it in Connectors to keep it syncing"]
    assert [b["access_ended"] for b in _store().banks()] == [None, "ended"]
    assert [b["access_ended"] for b in _store().public()["banks"]] == [None, "ended"]


def test_no_bank_answering_is_an_error_never_an_empty_ok(monkeypatch):
    _link(monkeypatch, "SESS-A", "ACME Bank")

    def _ended(token, sid):
        raise svc.ConsentEndedError("Enable Banking no longer knows this consent (HTTP 404)")

    monkeypatch.setattr(svc, "fetch_session_accounts", _ended)
    with pytest.raises(ValidationError, match="No bank could be read. ACME Bank: access ended"):
        svc.fetch_window(_store(), "2026-11-01", "2026-11-13", None)
    assert svc.classify_error(ValidationError("x")) == "auth_error"


def test_a_bank_from_the_previous_app_is_named_without_a_call(monkeypatch):
    _two_banks(monkeypatch, None)
    store = _store()
    store.mark_access_ended([store.banks()[0]["key"]], "app_changed")
    read: list[str] = []
    monkeypatch.setattr(
        svc, "fetch_session_accounts", lambda token, sid: read.append(sid) or [{"uid": f"{sid}-A"}]
    )
    w = svc.fetch_window(_store(), "2026-11-01", "2026-11-13", None)
    assert read == ["SESS-B"]
    assert w.warnings == [
        "ACME Bank: linked under your previous Enable Banking app — renew it in Connectors to"
        " keep it syncing"
    ]


def test_renewing_a_bank_from_the_previous_app_leaves_its_old_consent_alone(monkeypatch):
    """The previous app's key remembered and revoked it at the switch; this app can't read
    it: no read, no revoke with the wrong key."""
    deleted: list[str] = []
    _link(monkeypatch, "SESS-OLD", "ACME Bank", deleted=deleted)
    store = _store()
    store.mark_access_ended([store.banks()[0]["key"]], "app_changed")
    _link(monkeypatch, "SESS-NEW", "ACME Bank", deleted=deleted)
    assert [(b["session_id"], b["access_ended"]) for b in _store().banks()] == [("SESS-NEW", None)]
    assert deleted == []


def _window_with(monkeypatch, sessions, seen_uids):
    """fetch_window over ``sessions`` ({session_id: [accounts]}), recording which uids the
    Enable Banking endpoints were called with."""
    monkeypatch.setattr(svc, "fetch_session_accounts", lambda token, sid: list(sessions[sid]))

    def _balance(token, uid):
        seen_uids.append(("balance", uid))
        return 10.0, {}

    def _txns(token, uid, *, date_from, date_to):
        seen_uids.append(("transactions", uid))
        return [
            {
                "transaction_id": "T1",
                "booking_date": "2026-11-12",
                "status": "BOOK",
                "transaction_amount": {"amount": "45.20", "currency": "EUR"},
                "credit_debit_indicator": "DBIT",
                "remittance_information": ["GROCERY"],
            }
        ], True

    monkeypatch.setattr(svc, "fetch_account_balances", _balance)
    monkeypatch.setattr(svc, "fetch_account_transactions", _txns)
    return svc.fetch_window(_store(), "2026-11-01", "2026-11-13", None)


def test_renewing_a_bank_keeps_its_accounts_id_links_and_refs(monkeypatch):
    """Enable Banking gives an account a NEW uid in every session (seen on the sandbox,
    2026-11-13). Renewing must not change the account's id here: its app-account link and
    every transaction ref are built from it, so a new id orphaned the link and re-imported
    already-booked rows. The new uid is still the one the API is called with."""
    old = [{"uid": "UID-OLD", "identification_hash": "HASH-1", "currency": "EUR"}]
    new = [{"uid": "UID-NEW", "identification_hash": "HASH-1", "currency": "EUR"}]
    sessions = {"SESS-OLD": old, "SESS-NEW": new}
    deleted: list[str] = []
    _link(monkeypatch, "SESS-OLD", "ACME Bank", accounts=old, deleted=deleted, sessions=sessions)
    calls: list = []
    before = _window_with(monkeypatch, {"SESS-OLD": old}, calls)

    _link(monkeypatch, "SESS-NEW", "ACME Bank", accounts=new, deleted=deleted, sessions=sessions)
    calls.clear()
    after = _window_with(monkeypatch, {"SESS-NEW": new}, calls)

    assert [a["provider_account_id"] for a in after.accounts] == ["UID-OLD"]
    assert [r["external_refs"] for r in after.rows] == [r["external_refs"] for r in before.rows]
    assert [r["provider_account_id"] for r in after.rows] == ["UID-OLD"]
    assert calls == [("balance", "UID-NEW"), ("transactions", "UID-NEW")]


def test_a_renewal_keeps_ids_even_when_the_accounts_were_never_listed(monkeypatch):
    """A book linked before this fix (0.1.48) has recorded no identities. Renewing reads the
    old consent's accounts before revoking it, so the link it already holds survives."""
    old = [{"uid": "UID-OLD", "identification_hash": "HASH-1"}]
    new = [{"uid": "UID-NEW", "identification_hash": "HASH-1"}]
    sessions = {"SESS-OLD": old, "SESS-NEW": new}
    deleted: list[str] = []
    _link(monkeypatch, "SESS-OLD", "ACME Bank", accounts=old, deleted=deleted, sessions=sessions)
    assert _store().get()["account_identities"] == {}  # nothing listed yet
    _link(monkeypatch, "SESS-NEW", "ACME Bank", accounts=new, deleted=deleted, sessions=sessions)
    after = _window_with(monkeypatch, {"SESS-NEW": new}, [])
    assert [a["provider_account_id"] for a in after.accounts] == ["UID-OLD"]
    assert deleted == [f"{svc.API_BASE}/sessions/SESS-OLD"]


def test_the_same_account_hash_at_two_banks_stays_two_accounts(monkeypatch):
    """The sandbox's mock banks in FI and NO expose one IBAN, so one identification_hash.
    Two linked banks are two accounts here, whatever the hash says."""
    fi = [{"uid": "UID-FI", "identification_hash": "HASH-1"}]
    no = [{"uid": "UID-NO", "identification_hash": "HASH-1"}]
    sessions = {"SESS-FI": fi, "SESS-NO": no}
    _link(monkeypatch, "SESS-FI", "Mock ASPSP", "FI", accounts=fi, sessions=sessions)
    _link(monkeypatch, "SESS-NO", "Mock ASPSP", "NO", accounts=no, sessions=sessions)
    w = _window_with(monkeypatch, sessions, [])
    assert sorted(a["provider_account_id"] for a in w.accounts) == ["UID-FI", "UID-NO"]


def test_an_account_without_a_hash_keeps_its_session_uid(monkeypatch):
    accounts = [{"uid": "UID-1"}]
    _link(monkeypatch, "SESS-A", "ACME Bank", accounts=accounts, sessions={"SESS-A": accounts})
    w = _window_with(monkeypatch, {"SESS-A": accounts}, [])
    assert [a["provider_account_id"] for a in w.accounts] == ["UID-1"]
    assert _store().get()["account_identities"] == {}


def test_a_second_link_never_strands_the_first(monkeypatch):
    """Two links started (a retry, or a second bank while the first bank's page is still
    open): the FIRST bank's return still completes, and links the first bank."""
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    _pending("S-FI", "Mock Bank", "FI")
    _pending("S-NO", "Mock Bank", "NO")
    body = {"session_id": "SESS-FI", "accounts": []}
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: _JsonResp(200, body))
    svc.create_session("code", "S-FI")
    assert [(b["aspsp"], b["country"]) for b in _store().banks()] == [("Mock Bank", "FI")]
    assert [p["state"] for p in _store().get()["pending_links"]] == ["S-NO"]


def test_a_stale_pending_link_is_refused(monkeypatch):
    monkeypatch.setattr(svc, "_bearer", lambda store: "JWT")
    _pending("S-OLD", age_minutes=3 * 60)
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: pytest.fail("no call for a stale link"))
    with pytest.raises(ValidationError):
        svc.create_session("code", "S-OLD")


def test_only_the_newest_few_pending_links_are_kept():
    for i in range(8):
        _pending(f"S-{i}")
    assert [p["state"] for p in _store().get()["pending_links"]] == [f"S-{i}" for i in range(3, 8)]


def test_accounts_name_the_country_only_when_two_banks_share_a_name(monkeypatch):
    _link(monkeypatch, "SESS-FI", "Nordea", "FI")
    _link(monkeypatch, "SESS-SE", "Nordea", "SE")
    _link(monkeypatch, "SESS-OP", "OP", "FI")
    monkeypatch.setattr(
        svc, "fetch_session_accounts", lambda token, sid: [{"uid": f"{sid}-A", "currency": "EUR"}]
    )
    monkeypatch.setattr(svc, "fetch_account_balances", lambda token, uid: (None, {}))
    w = svc.fetch_window(_store(), "2026-10-01", "2026-11-13", None, accounts_only=True)
    assert [a["institution"] for a in w.accounts] == ["Nordea (FI)", "Nordea (SE)", "OP"]


def test_rows_carry_the_counterparty_as_payee():
    """Money out names who was paid (creditor); money in names who paid (debtor). The
    description keeps the bank's own wording."""
    out = {
        "entry_reference": "E1",
        "booking_date": "2026-11-11",
        "transaction_amount": {"amount": "9.50", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "creditor": {"name": "ACME  Cafe"},
        "remittance_information": ["ACME Cafe-DBIT-9.50-ref"],
    }
    row = svc._txn_to_row(out, "U1", "EUR")
    assert row["payee"] == "ACME Cafe"
    assert row["description"] == "ACME Cafe-DBIT-9.50-ref"
    salary = {
        **out,
        "credit_debit_indicator": "CRDT",
        "creditor": None,
        "debtor": {"name": "ACME Corp"},
    }
    assert svc._txn_to_row(salary, "U1", "EUR")["payee"] == "ACME Corp"
    assert svc._txn_to_row({**out, "creditor": None}, "U1", "EUR")["payee"] is None


def test_every_provider_tags_its_rows_with_its_own_key():
    """Every "already booked?" lookup (the fetch filter, its default window, the evidence
    snapshots) and the dedup authority list (core.txn_sources) key a connector's rows by its
    provider key. Enable Banking once tagged rows "enablebanking": nothing it booked was ever
    seen as booked, and its bank ids lost their authority in dedup."""
    import importlib

    from backend.core.txn_sources import AUTHORITATIVE_ID_NAMESPACES
    from backend.services.aggregation.providers import PROVIDERS

    for provider in PROVIDERS.values():
        module = importlib.import_module(provider._fetch[0])
        assert getattr(module, "EXTERNAL_SOURCE", provider.key) == provider.key, provider.key
    assert svc.EXTERNAL_SOURCE in AUTHORITATIVE_ID_NAMESPACES


def test_fetch_counts_an_enable_banking_row_already_in_the_ledger_as_booked(monkeypatch):
    from backend.services import connector_fetch
    from backend.services import ledger as ledger_service
    from backend.services.aggregation.providers import FetchWindow

    _store().update(
        {"application_id": "app", "private_key": "k", "enabled": True, "session_id": "S"}
    )
    ref = "ACC-1:E1"
    row = {
        "external_refs": [{"source": svc.EXTERNAL_SOURCE, "ref": ref}],
        "raw": {},
        "provider_account_id": "ACC-1",
        "date": "2026-11-12",
        "amount": -45.2,
        "currency": "EUR",
        "description": "ACME Grocery",
        "payee": "ACME Grocery",
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }
    monkeypatch.setattr(
        svc, "fetch_window", lambda *a, **k: FetchWindow(accounts=[], rows=[row], complete=True)
    )
    booked = {(svc.EXTERNAL_SOURCE, ref): "txn_1"}
    monkeypatch.setattr(
        ledger_service,
        "known_external_refs",
        lambda source: booked if source == "enable_banking" else {},
    )
    out = connector_fetch.fetch("enable_banking", since="2026-11-01", before="2026-11-13")
    assert out["counts"]["already_booked"] == 1
    assert out["transactions"] == []
    with_booked = connector_fetch.fetch(
        "enable_banking", since="2026-11-01", before="2026-11-13", include_booked=True
    )
    assert with_booked["transactions"][0]["booked_txn_id"] == "txn_1"


def test_a_flagged_bank_that_answers_again_is_unflagged(monkeypatch):
    """Switching back to the app a bank was linked under: its consent answers again."""
    _two_banks(monkeypatch, None)
    store = _store()
    store.mark_access_ended([store.banks()[1]["key"]], "ended")
    w = svc.fetch_window(_store(), "2026-11-01", "2026-11-13", None)
    assert w.warnings == []
    assert [b["access_ended"] for b in _store().banks()] == [None, None]


def test_renewing_a_closed_consent_still_keeps_its_accounts_ids(monkeypatch):
    """A 0.1.48 book recorded no identities, and its consent ran out: the account endpoints
    refuse, but the session's accounts_data still carries each uid and hash."""
    old = [{"uid": "UID-OLD", "identification_hash": "HASH-1"}]
    new = [{"uid": "UID-NEW", "identification_hash": "HASH-1"}]
    _link(monkeypatch, "SESS-OLD", "ACME Bank", accounts=old)
    store = _store()
    store.mark_access_ended([store.banks()[0]["key"]], "ended")
    assert store.get()["account_identities"] == {}
    _link(monkeypatch, "SESS-NEW", "ACME Bank", accounts=new, sessions={"SESS-OLD": old})
    after = _window_with(monkeypatch, {"SESS-NEW": new}, [])
    assert [a["provider_account_id"] for a in after.accounts] == ["UID-OLD"]


def test_session_accounts_data_reads_a_closed_session(monkeypatch):
    body = {
        "status": "CLOSED",
        "accounts": ["UID-1"],
        "accounts_data": [{"uid": "UID-1", "identification_hash": "HASH-1"}],
    }
    monkeypatch.setattr(
        svc.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request("GET", "https://x")),
    )
    assert svc.session_accounts_data("JWT", "SESS-1") == [
        {"uid": "UID-1", "identification_hash": "HASH-1"}
    ]


# ── description when the bank sends little ─────────────────────────────────────────


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"remittance_information": ["CARD 1234", " ACME Grocery "]}, "CARD 1234 ACME Grocery"),
        ({"remittance_information": [], "creditor": {"name": "ACME Taxi"}}, "ACME Taxi"),
        ({"remittance_information": [""], "debtor": {"name": "ACME Corp"}}, "ACME Corp"),
        (
            {
                "remittance_information": None,
                "bank_transaction_code": {"description": "Card payment"},
            },
            "Card payment",
        ),
        (
            {"bank_transaction_code": {"description": " "}, "reference_number": "RF18 5390"},
            "RF18 5390",
        ),
        ({"creditor": {"name": None}, "bank_transaction_code": None}, "Enable Banking"),
        ({"note": "my own note"}, "Enable Banking"),
    ],
)
def test_the_description_comes_from_the_first_field_the_bank_filled(fields, expected):
    """Reported 2026-11-13: a bank whose rows carry no remittance text and no counterparty
    came out as a bare "Enable Banking" on every row, even when it named the kind of
    transaction. ``note`` is the account holder's own, never the bank's description."""
    assert svc._txn_description(fields) == expected


def test_a_row_the_bank_names_only_by_its_code_is_booked_with_that_wording():
    row = svc._txn_to_row(
        {
            "entry_reference": "T9",
            "booking_date": "2026-11-12",
            "status": "BOOK",
            "transaction_amount": {"amount": "12.00", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "bank_transaction_code": {"description": "Card payment", "code": "PMNT"},
        },
        "EB-CHK",
        "EUR",
    )
    assert row["description"] == "Card payment"
    assert row.get("payee") is None
