"""SimpleFIN aggregation service — protocol + normalize + error classification (NO real
network).

Covers:
  * claim_setup_token — base64-decode -> POST -> access URL (mocked httpx); malformed
    token, already-claimed (403), and unsafe-claim-URL error paths.
  * fetch_accounts — SSRF guard, response-size bound, and credential-leak sanitization.
  * fetch_window — SimpleFIN accounts/transactions -> the common normalized shape (RFC
    connectors-as-data-sources §A); every row carries ``raw`` (the original provider
    dict, for the transaction drawer). Booking/dedup is no longer this module's job — see
    ``services/connector_fetch.py`` and its own tests.
  * classify_error — the plain exception -> status-taxonomy function (the adapter class
    is retired).

All HTTP is mocked with a fixture feed — there is no live-network call here.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.services.aggregation import simplefin as svc

# ── a deterministic SimpleFIN /accounts feed fixture ─────────────────────────────────

_FEED = {
    "errors": [],
    "accounts": [
        {
            "org": {"name": "Acme Bank", "domain": "acme.example"},
            "id": "ACT-CHK",
            "name": "Checking",
            "currency": "USD",
            "balance": "1234.56",
            "balance-date": 1717000000,
            "transactions": [
                {
                    "id": "T1",
                    "posted": 1716900000,
                    "amount": "-42.50",
                    "description": "Coffee Shop",
                },
                {
                    "id": "T2",
                    "posted": 1716950000,
                    "amount": "2000.00",
                    "description": "Payroll",
                },
            ],
        },
        {
            "org": {"name": "Acme Bank"},
            "id": "ACT-SAV",
            "name": "Savings",
            "currency": "USD",
            "balance": "5000.00",
            "transactions": [
                {"id": "T3", "posted": 1716960000, "amount": "100.00", "description": "Interest"},
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def isolate_store(db_isolated):
    """SimpleFinConfigStore is the ``simplefin`` connector row — db_isolated gives every
    test a fresh, isolated sqlite file."""


def _configured_store(access_url: str = "https://u:p@bridge.example/sf") -> SimpleFinConfigStore:
    store = SimpleFinConfigStore()
    store.update({"access_url": access_url, "enabled": True})
    return store


# ── claim_setup_token ────────────────────────────────────────────────────────────────


def test_claim_setup_token_happy(monkeypatch):
    # 1.1.1.1 is a public IP literal (see test_net_guard.py convention) — exercises the
    # REAL assert_public_url check with no DNS resolution needed.
    claim_url = "https://1.1.1.1/claim/abc"
    token = base64.b64encode(claim_url.encode()).decode()
    access_url = "https://user:pass@1.1.1.1/sf"

    class _Resp:
        status_code = 200
        text = access_url
        content = access_url.encode()

    captured = {}

    def fake_post(url, timeout=None):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    result = svc.claim_setup_token(token)
    assert result == access_url
    assert captured["url"] == claim_url


def test_claim_setup_token_rejects_bad_base64():
    with pytest.raises(ValidationError):
        svc.claim_setup_token("!!! not base64 !!!")


def test_claim_setup_token_rejects_already_claimed(monkeypatch):
    token = base64.b64encode(b"https://1.1.1.1/claim/used").decode()

    class _Resp:
        status_code = 403
        text = "forbidden"
        content = b"forbidden"

    monkeypatch.setattr(svc.httpx, "post", lambda url, timeout=None: _Resp())
    with pytest.raises(ValidationError):
        svc.claim_setup_token(token)


# ── SSRF guard (Fix 2) ──────────────────────────────────────────────────────────────


def test_claim_setup_token_rejects_private_claim_url(monkeypatch):
    """A setup token that decodes to a private/internal claim URL is refused BEFORE any
    network call — SimpleFIN's claim URL is attacker-influenceable (a pasted-in setup
    token), unlike the fixed vendor domains the sibling providers call."""
    token = base64.b64encode(b"http://169.254.169.254/claim/steal").decode()

    def _boom(*_a, **_k):
        raise AssertionError("httpx.post must never run for an unsafe claim URL")

    monkeypatch.setattr(svc.httpx, "post", _boom)
    with pytest.raises(ValidationError):
        svc.claim_setup_token(token)


def test_fetch_accounts_rejects_private_access_url(monkeypatch):
    """A stored access URL pointing at a private/internal host is refused BEFORE any
    network call. Unlike the sibling providers' fixed vendor domains, SimpleFIN's
    access_url is whatever the claim response said — fully attacker-influenceable — and
    is re-fetched on every Lucius turn that calls ``simplefin_fetch_transactions``."""

    def _boom(*_a, **_k):
        raise AssertionError("httpx.get must never run for an unsafe access URL")

    monkeypatch.setattr(svc.httpx, "get", _boom)
    with pytest.raises(ValidationError):
        svc.fetch_accounts("https://user:pass@127.0.0.1/sf")


# ── credential-leak sanitization (Fix 1, CRITICAL) ──────────────────────────────────
#
# access_url embeds HTTP-basic creds (https://user:pass@host/...). httpx puts the FULL
# request URL into HTTPStatusError's str() — credentials included; httpx only masks a
# credential in repr(httpx.URL), never in the exception string. This string reaches the
# UNGATED get_simplefin_status / bulk get_connector_status AI tools via
# ``SimpleFinConfigStore.record_status``'s ``last_error`` — no mutates= tag, so never
# confirmed. A routine status check, or a Lucius turn calling
# ``simplefin_fetch_transactions`` and hitting a bad credential, was enough to hand the
# model the bank credential.


def test_fetch_accounts_error_never_leaks_credentials(monkeypatch):
    """The exception fetch_accounts itself raises must never contain the credential, the
    host, or the request URL — only a status code."""
    import httpx

    access_url = "https://sfin_user_42:s3cr3t_pw@bridge.simplefin.org/simplefin"

    def fake_get(url, params=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request, text="forbidden")
        # This mirrors REAL httpx behavior: str(exc) embeds the full request URL,
        # credentials included — the exact leak the reviewer reproduced.
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'", request=request, response=response
        )

    monkeypatch.setattr(svc.httpx, "get", fake_get)
    with pytest.raises(ExternalServiceError) as excinfo:
        svc.fetch_accounts(access_url)

    message = str(excinfo.value)
    assert "sfin_user_42" not in message
    assert "s3cr3t_pw" not in message
    assert "bridge.simplefin.org" not in message
    assert message == "SimpleFIN HTTP 403"


def test_fetch_window_scrubs_credential_from_an_unexpected_exception(monkeypatch):
    """Belt-and-braces: even if some OTHER exception (not the sanitized
    ``ExternalServiceError`` ``_safe_get`` normally raises) escaped ``fetch_accounts``
    with the access URL embedded in its message, ``fetch_window``'s own scrub (mirrors
    ``ibkr``'s) must still strip it before the exception reaches a caller — a Lucius tool,
    or ``connector_fetch.fetch()``'s ``record_status(error=...)`` write."""
    access_url = "https://sfin_user_42:s3cr3t_pw@bridge.simplefin.org/simplefin"
    store = _configured_store(access_url)

    def _leaky_fetch_accounts(url, start_date=None, end_date=None, account=None):
        raise RuntimeError(f"unexpected failure while fetching {url}/accounts")

    monkeypatch.setattr(svc, "fetch_accounts", _leaky_fetch_accounts)

    with pytest.raises(RuntimeError) as excinfo:
        svc.fetch_window(store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None)

    message = str(excinfo.value)
    assert "sfin_user_42" not in message
    assert "s3cr3t_pw" not in message
    assert "bridge.simplefin.org" not in message


def test_fetch_window_error_never_leaks_credential_into_record_status(monkeypatch):
    """End-to-end through the REAL fetch path (fetch_window -> fetch_accounts, not
    monkeypatched away) — the same leak, verified at the surface an ungated AI tool can
    read: ``SimpleFinConfigStore.record_status`` -> ``public()['last_error']`` (what
    ``get_simplefin_status`` returns)."""
    import httpx

    access_url = "https://sfin_user_42:s3cr3t_pw@bridge.simplefin.org/simplefin"
    store = _configured_store(access_url)

    def fake_get(url, params=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request, text="forbidden")
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url '{url}'", request=request, response=response
        )

    monkeypatch.setattr(svc.httpx, "get", fake_get)

    with pytest.raises(ExternalServiceError) as excinfo:
        svc.fetch_window(store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None)

    status = svc.classify_error(excinfo.value)
    assert status == "auth_error"
    store.record_status(status, error=str(excinfo.value))

    last_error = store.public()["last_error"]
    assert last_error is not None
    assert "sfin_user_42" not in last_error
    assert "s3cr3t_pw" not in last_error
    assert "bridge.simplefin.org" not in last_error


def test_scrub_strips_credential_and_full_url():
    """Direct unit test of the module-level defense-in-depth scrub."""
    access_url = "https://sfin_user_42:s3cr3t_pw@bridge.simplefin.org/simplefin"
    text = f"unexpected failure while fetching {access_url}/accounts: connection reset"
    scrubbed = svc._scrub(text, access_url)
    assert "sfin_user_42" not in scrubbed
    assert "s3cr3t_pw" not in scrubbed
    assert access_url not in scrubbed


# ── response-size bound (Fix 3) ──────────────────────────────────────────────────────


def test_fetch_accounts_bounds_response_size(monkeypatch):
    class _HugeResp:
        status_code = 200
        content = b"x" * (svc._MAX_RESPONSE_BYTES + 1)

        def raise_for_status(self):
            return None

        def json(self):
            raise AssertionError("must never parse an oversized body")

    monkeypatch.setattr(svc.httpx, "get", lambda url, params=None, timeout=None: _HugeResp())
    with pytest.raises(ExternalServiceError, match="too large"):
        svc.fetch_accounts("https://user:pass@1.1.1.1/sf")


def test_claim_setup_token_bounds_response_size(monkeypatch):
    token = base64.b64encode(b"https://1.1.1.1/claim/abc").decode()

    class _HugeResp:
        status_code = 200
        text = "x" * (svc._MAX_RESPONSE_BYTES + 1)
        content = b"x" * (svc._MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr(svc.httpx, "post", lambda url, timeout=None: _HugeResp())
    with pytest.raises(ExternalServiceError, match="too large"):
        svc.claim_setup_token(token)


# ── fetch_window — normalization (RFC §A) ────────────────────────────────────────────


class TestFetchWindow:
    def _fake_fetch_accounts(self, feed=_FEED, captured=None):
        def _fake(access_url, start_date=None, end_date=None, account=None):
            if captured is not None:
                captured["access_url"] = access_url
                captured["start_date"] = start_date
                captured["end_date"] = end_date
                captured["account"] = account
            return feed

        return _fake

    def test_accounts_normalized_to_common_shape(self, monkeypatch):
        store = _configured_store()
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts())
        window = svc.fetch_window(
            store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None
        )

        assert window.complete is True
        assert len(window.accounts) == 2
        checking = next(a for a in window.accounts if a["provider_account_id"] == "ACT-CHK")
        assert checking["name"] == "Acme Bank · Checking"
        assert checking["currency"] == "USD"
        assert checking["balance"] == 1234.56
        assert checking["mask"] is None

    def test_transactions_normalized_and_carry_raw(self, monkeypatch):
        store = _configured_store()
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts())
        window = svc.fetch_window(
            store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None
        )

        assert len(window.rows) == 3
        by_ref = {r["external_refs"][0]["ref"]: r for r in window.rows}
        assert set(by_ref) == {"ACT-CHK:T1", "ACT-CHK:T2", "ACT-SAV:T3"}

        coffee = by_ref["ACT-CHK:T1"]
        assert coffee["amount"] == -42.5
        assert coffee["currency"] == "USD"
        assert coffee["provider_account_id"] == "ACT-CHK"
        assert coffee["description"] == "Coffee Shop"
        assert coffee["pending"] is False
        assert coffee["supersedes_ref"] is None
        # PRD requirement: raw carries the ORIGINAL provider dict, for the txn drawer.
        assert coffee["raw"] == _FEED["accounts"][0]["transactions"][0]

        payroll = by_ref["ACT-CHK:T2"]
        assert payroll["amount"] == 2000.0
        assert payroll["raw"] == _FEED["accounts"][0]["transactions"][1]

        interest = by_ref["ACT-SAV:T3"]
        assert interest["raw"] == _FEED["accounts"][1]["transactions"][0]

    def test_accounts_only_skips_transactions_and_windows_to_zero_width(self, monkeypatch):
        store = _configured_store()
        captured: dict = {}
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts(captured=captured))
        window = svc.fetch_window(
            store,
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            None,
            accounts_only=True,
        )
        assert len(window.accounts) == 2
        assert window.rows == []
        assert captured["start_date"] == captured["end_date"]

    def test_account_filter_passed_through_to_provider(self, monkeypatch):
        store = _configured_store()
        captured: dict = {}
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts(captured=captured))
        svc.fetch_window(store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "ACT-CHK")
        assert captured["account"] == "ACT-CHK"

    def test_zero_amount_transaction_skipped(self, monkeypatch):
        store = _configured_store()
        feed = {
            "accounts": [
                {
                    "id": "ACT-X",
                    "name": "X",
                    "currency": "USD",
                    "balance": "0",
                    "transactions": [
                        {"id": "T0", "posted": 1716900000, "amount": "0", "description": "noop"}
                    ],
                }
            ]
        }
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts(feed=feed))
        window = svc.fetch_window(
            store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None
        )
        assert window.rows == []

    def test_missing_transaction_id_falls_back_to_derived_ref(self, monkeypatch):
        store = _configured_store()
        feed = {
            "accounts": [
                {
                    "id": "ACT-X",
                    "name": "X",
                    "currency": "USD",
                    "balance": "10",
                    "transactions": [
                        {"posted": 1716900000, "amount": "-5.00", "description": "No id txn"}
                    ],
                }
            ]
        }
        monkeypatch.setattr(svc, "fetch_accounts", self._fake_fetch_accounts(feed=feed))
        window = svc.fetch_window(
            store, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", None
        )
        assert len(window.rows) == 1
        ref = window.rows[0]["external_refs"][0]["ref"]
        assert ref.startswith("derived:")


# ── classify_error (plain function — the adapter class is retired) ────────────────


class TestClassifyError:
    def test_external_service_error_401_is_auth_error(self):
        assert svc.classify_error(ExternalServiceError("SimpleFIN HTTP 401")) == "auth_error"

    def test_external_service_error_403_is_auth_error(self):
        assert svc.classify_error(ExternalServiceError("SimpleFIN HTTP 403")) == "auth_error"

    def test_external_service_error_429_is_rate_limited(self):
        assert svc.classify_error(ExternalServiceError("SimpleFIN HTTP 429")) == "rate_limited"

    def test_external_service_error_other_is_connect_error(self):
        assert svc.classify_error(ExternalServiceError("SimpleFIN HTTP 500")) == "connect_error"

    def test_httpx_status_error_401_is_auth_error(self):
        import httpx

        request = httpx.Request("GET", "https://example.test/x")
        response = httpx.Response(401, request=request)
        exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
        assert svc.classify_error(exc) == "auth_error"

    def test_httpx_status_error_other_is_connect_error(self):
        import httpx

        request = httpx.Request("GET", "https://example.test/x")
        response = httpx.Response(500, request=request)
        exc = httpx.HTTPStatusError("server error", request=request, response=response)
        assert svc.classify_error(exc) == "connect_error"

    def test_httpx_connect_error_is_connect_error(self):
        import httpx

        assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"

    def test_generic_exception_is_error(self):
        assert svc.classify_error(RuntimeError("boom")) == "error"


# ── 2026-11-13 live findings: errors in a 200, 45-day ranges, local dates ─────────────


def _capture_slices(monkeypatch, feed):
    calls: list[dict] = []

    def _fake(access_url, start_date=None, end_date=None, account=None):
        calls.append({"start": start_date, "end": end_date})
        return feed

    monkeypatch.setattr(svc, "fetch_accounts", _fake)
    return calls


def test_an_error_inside_a_200_feed_is_a_warning_and_the_window_is_incomplete(monkeypatch):
    """SimpleFIN puts a failed bank connection in ``errors``/``errlist`` of a normal 200
    answer. Ignored, a feed missing a whole bank looked complete — and a complete window is
    what lets the vanished-row check call booked rows gone."""
    feed = {
        "errors": ["Connection to ACME Bank may need attention"],
        "errlist": [{"code": "con.auth", "msg": "Connection to ACME Bank may need attention"}],
        "accounts": [],
    }
    _capture_slices(monkeypatch, feed)
    window = svc.fetch_window(_configured_store(), "2026-01-01", "2026-01-10", None)
    assert window.complete is False
    assert window.warnings == [
        "SimpleFIN: Connection to ACME Bank may need attention",
        "SimpleFIN: con.auth Connection to ACME Bank may need attention",
    ]


def test_a_long_window_is_fetched_in_45_day_slices_that_include_the_last_day(monkeypatch):
    """The bridge flags any range over 45 days (and says it may cap it), and its end-date
    is exclusive — so slices cover every day through ``before`` and no slice is longer."""
    monkeypatch.setattr(svc, "user_zone", lambda: timezone.utc)
    calls = _capture_slices(monkeypatch, {"errors": [], "accounts": []})
    svc.fetch_window(_configured_store(), "2026-01-01", "2026-03-31", None)
    day = 86400
    assert [(c["end"] - c["start"]) // day for c in calls] == [45, 45]
    assert calls[0]["start"] == int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    assert calls[-1]["end"] == int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    assert all(a["end"] == b["start"] for a, b in zip(calls, calls[1:]))


def test_a_row_seen_in_two_slices_is_returned_once(monkeypatch):
    txn = {"id": "T1", "posted": 1767225600, "amount": "-5.00", "description": "ACME"}
    feed = {"errors": [], "accounts": [{"id": "A1", "currency": "USD", "transactions": [txn]}]}
    _capture_slices(monkeypatch, feed)
    window = svc.fetch_window(_configured_store(), "2026-01-01", "2026-03-31", None)
    assert [r["external_refs"][0]["ref"] for r in window.rows] == ["A1:T1"]


def test_a_row_is_dated_in_the_users_zone_and_keeps_payee_memo_and_mcc(monkeypatch):
    """23:30 on Jan 1 in Los Angeles is Jan 2 in UTC — the UTC date put an evening
    purchase on the wrong day. The bridge's payee, memo and MCC each keep their own field."""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(svc, "user_zone", lambda: ZoneInfo("America/Los_Angeles"))
    ts = int(datetime(2026, 1, 2, 7, 30, tzinfo=timezone.utc).timestamp())
    txn = {
        "id": "T9",
        "posted": ts,
        "amount": "-12.00",
        "description": "ACME DINER 123",
        "payee": "ACME Diner",
        "memo": "CARD 0000",
        "mcc": "5812",
    }
    row = svc._txn_to_row(txn, "A1", "USD")
    assert row["date"] == "2026-01-01"
    assert (row["payee"], row["memo"], row["description"]) == (
        "ACME Diner",
        "CARD 0000",
        "ACME DINER 123",
    )
    assert row["category_hint"] == svc.merchant_category("5812")


def test_a_pending_row_is_dated_by_transacted_at_and_an_undated_row_is_dropped(monkeypatch):
    monkeypatch.setattr(svc, "user_zone", lambda: timezone.utc)
    ts = int(datetime(2026, 1, 5, 12, tzinfo=timezone.utc).timestamp())
    pending = {"id": "P1", "posted": 0, "transacted_at": ts, "amount": "-3.00", "pending": True}
    row = svc._txn_to_row(pending, "A1", "USD")
    assert (row["date"], row["pending"]) == ("2026-01-05", True)
    assert svc._txn_to_row({"id": "X", "posted": 0, "amount": "-3.00"}, "A1", "USD") is None


def test_the_feed_asks_for_pending_rows(monkeypatch):
    seen: dict = {}

    class _Resp:
        def json(self):
            return {"errors": [], "accounts": []}

    def _get(url, params):
        seen.update(params)
        return _Resp()

    monkeypatch.setattr(svc, "_safe_get", _get)
    monkeypatch.setattr(svc, "assert_public_url", lambda u: u)
    svc.fetch_accounts("https://user:pass@bridge.example/simplefin", start_date=1, end_date=2)
    assert seen["pending"] == 1
