"""Plaid aggregation service — link token + exchange + windowed fetch + normalize (NO net).

RFC connectors-as-data-sources retired the cursor-based ``/transactions/sync`` orchestrator
(``run_plaid_sync``/``fetch_sync``) along with the booking it used to do itself: Plaid is now
a plain read-only data source. Covers:

  * create_link_token — POST /link/token/create (hosted_link) → the Hosted Link URL (mocked).
  * exchange_public_token — POST /item/public_token/exchange → access_token + item_id
    persisted (mocked); bad-token guard.
  * fetch_window — the DATE-WINDOWED ``/transactions/get`` fetch (paged via count/offset),
    normalized rows (raw provider dict carried through, sign-flip, pending carried through
    rather than dropped, a posted row's supersedes_ref pointing at its former pending ref),
    account-side filtering, accounts_only mode, and the truncated-fetch → complete=False
    safety cap.
  * classify_error — the plain exception → status-taxonomy function (the adapter class is
    retired).

All HTTP is mocked — there is no live-network call here.
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.errors import ValidationError
from backend.services.aggregation import plaid as svc
from backend.services.aggregation.providers import FetchWindow

# ── isolation ────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_store(db_isolated, monkeypatch):
    """PlaidConfigStore is DB-backed (secret_config table) — db_isolated gives every
    test a fresh, isolated sqlite file; the env is cleared so a developer's real creds
    never bleed into a test."""


class _Resp:
    """A minimal stand-in for the httpx.Response shape ``_post`` reads."""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _configured_store(**extra):
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"client_id": "cid", "secret": "skey", "access_token": "access-1", **extra})
    return store


# ── create_link_token ──────────────────────────────────────────────────────────────────


def test_create_link_token_returns_hosted_url(monkeypatch):
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey"})

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp(
            {
                "link_token": "link-sandbox-1",
                "hosted_link_url": "https://hosted.plaid.com/link/1",
                "expiration": "2026-05-10T00:00:00Z",
            }
        )

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    out = svc.create_link_token("https://app/connectors")
    assert out["link"] == "https://hosted.plaid.com/link/1"
    assert out["link_token"] == "link-sandbox-1"
    assert "hosted_link" in captured["json"]
    assert captured["json"]["redirect_uri"] == "https://app/connectors"
    assert captured["json"]["client_id"] == "cid"


def test_create_link_token_requires_creds():
    with pytest.raises(ValidationError):
        svc.create_link_token("")


# ── complete_hosted_link (Hosted Link never shows the user a public token) ─────────────


def _route(responses: dict, calls: list):
    def fake_post(url, json=None, timeout=None):
        path = url.split(".plaid.com", 1)[1]
        calls.append((path, json))
        return _Resp(responses[path])

    return fake_post


def test_create_link_token_stores_the_link_token_server_side(monkeypatch):
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey"})
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda *a, **k: _Resp({"link_token": "link-1", "hosted_link_url": "https://h/1"}),
    )
    svc.create_link_token("")
    assert PlaidConfigStore().link_token() == "link-1"
    assert "link_token" not in PlaidConfigStore().public()


def test_complete_hosted_link_is_pending_until_the_user_finishes(monkeypatch):
    _configured_store(link_token="link-1")
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _route({"/link/token/get": {"link_sessions": [{"link_session_id": "s1"}]}}, calls),
    )
    assert svc.complete_hosted_link() == {"status": "pending"}
    assert calls == [
        ("/link/token/get", {"client_id": "cid", "secret": "skey", "link_token": "link-1"})
    ]


def test_complete_hosted_link_exchanges_the_session_public_token(monkeypatch):
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey", "link_token": "link-1"})
    calls: list = []
    session = {"results": {"item_add_results": [{"public_token": "public-prod-1"}]}}
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _route(
            {
                "/link/token/get": {"link_sessions": [session]},
                "/item/public_token/exchange": {"access_token": "access-7", "item_id": "item-7"},
            },
            calls,
        ),
    )
    out = svc.complete_hosted_link()
    assert out == {"status": "linked", "item_id": "item-7", "has_access_token": True}
    assert calls[1] == (
        "/item/public_token/exchange",
        {"client_id": "cid", "secret": "skey", "public_token": "public-prod-1"},
    )
    store = PlaidConfigStore()
    assert store.access_token() == "access-7"
    assert store.link_token() == ""


def test_complete_hosted_link_reads_the_legacy_on_success_shape(monkeypatch):
    _configured_store(link_token="link-1")
    calls: list = []
    monkeypatch.setattr(
        svc.httpx,
        "post",
        _route(
            {
                "/link/token/get": {"link_sessions": [{"on_success": {"public_token": "pub-2"}}]},
                "/item/public_token/exchange": {"access_token": "access-8", "item_id": "i"},
            },
            calls,
        ),
    )
    assert svc.complete_hosted_link()["status"] == "linked"
    assert calls[1][1]["public_token"] == "pub-2"


def test_complete_hosted_link_without_a_link_in_flight_refuses():
    _configured_store()
    with pytest.raises(ValidationError):
        svc.complete_hosted_link()


# ── exchange_public_token ──────────────────────────────────────────────────────────────


def test_exchange_public_token_persists_access(monkeypatch):
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey"})

    monkeypatch.setattr(
        svc.httpx, "post", lambda *a, **k: _Resp({"access_token": "access-9", "item_id": "item-9"})
    )
    out = svc.exchange_public_token("public-token-xyz")
    assert out == {"item_id": "item-9", "has_access_token": True}
    cfg = PlaidConfigStore().get()
    assert cfg["access_token"] == "access-9"
    assert cfg["item_id"] == "item-9"


def test_exchange_requires_token():
    with pytest.raises(ValidationError):
        svc.exchange_public_token("")


def test_exchange_bad_token(monkeypatch):
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey"})

    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda *a, **k: _Resp({"error_code": "INVALID_PUBLIC_TOKEN", "error_message": "used"}, 400),
    )
    with pytest.raises(ValidationError):
        svc.exchange_public_token("bad")


# ── _account_to_common / _txn_amount / _txn_description / _txn_date ────────────────────


class TestNormalizeHelpers:
    def test_account_to_common_prefers_official_name(self):
        from backend.services.aggregation.plaid import _account_to_common

        account = {
            "account_id": "A1",
            "name": "checking",
            "official_name": "Plaid Gold Checking",
            "mask": "0000",
            "balances": {"iso_currency_code": "usd", "current": 150.25},
        }
        assert _account_to_common(account) == {
            "provider_account_id": "A1",
            "name": "Plaid Gold Checking",
            "mask": "0000",
            "currency": "USD",
            "balance": 150.25,
        }

    def test_account_to_common_falls_back_to_name_and_unofficial_currency(self):
        from backend.services.aggregation.plaid import _account_to_common

        account = {
            "account_id": "A2",
            "name": "Savings",
            "balances": {"unofficial_currency_code": "btc", "current": 0.5},
        }
        common = _account_to_common(account)
        assert common["name"] == "Savings"
        assert common["currency"] == "BTC"

    def test_amount_flips_plaid_sign_for_an_expense(self):
        from backend.services.aggregation.plaid import _txn_amount

        # Plaid: positive = money OUT (an expense) → this app: negative.
        assert _txn_amount({"amount": 42.50}) == -42.50

    def test_amount_flips_plaid_sign_for_income(self):
        from backend.services.aggregation.plaid import _txn_amount

        # Plaid: negative = money IN → this app: positive.
        assert _txn_amount({"amount": -2000.0}) == 2000.0

    def test_amount_zero_returns_none(self):
        from backend.services.aggregation.plaid import _txn_amount

        assert _txn_amount({"amount": 0.0}) is None

    def test_amount_unparseable_returns_none(self):
        from backend.services.aggregation.plaid import _txn_amount

        assert _txn_amount({"amount": "not-a-number"}) is None
        assert _txn_amount({}) is None

    def test_description_prefers_merchant_name(self):
        from backend.services.aggregation.plaid import _txn_description

        assert _txn_description({"merchant_name": "Starbucks", "name": "SQ *STARBUCKS"}) == (
            "Starbucks"
        )

    def test_description_falls_back_to_name(self):
        from backend.services.aggregation.plaid import _txn_description

        assert _txn_description({"name": "ACH Deposit"}) == "ACH Deposit"

    def test_description_default(self):
        from backend.services.aggregation.plaid import _txn_description

        assert _txn_description({}) == "Plaid"

    def test_date_parsed_from_string(self):
        from backend.services.aggregation.plaid import _txn_date

        assert _txn_date({"date": "2026-03-22"}) == "2026-03-22"

    def test_date_falls_back_to_authorized_date(self):
        from backend.services.aggregation.plaid import _txn_date

        assert _txn_date({"authorized_date": "2026-04-01"}) == "2026-04-01"

    def test_date_falls_back_to_today(self):
        from datetime import datetime, timezone

        from backend.services.aggregation.plaid import _txn_date

        parsed = datetime.fromisoformat(_txn_date({}))
        assert abs((datetime.now(timezone.utc).date() - parsed.date()).days) <= 1


# ── _txn_to_row ──────────────────────────────────────────────────────────────────────


class TestTxnToRow:
    def _make_txn(self, **overrides) -> dict:
        base = {
            "transaction_id": "T1",
            "account_id": "PL-CHK",
            "date": "2026-05-01",
            "amount": 42.50,
            "merchant_name": "Coffee Shop",
            "iso_currency_code": "USD",
            "pending": False,
        }
        base.update(overrides)
        return base

    def test_row_carries_the_original_raw_dict(self):
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn()
        row = _txn_to_row(txn, {}, "PL-CHK")
        assert row is not None
        assert row["raw"] is txn

    def test_row_shape_and_sign_flip(self):
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn()
        row = _txn_to_row(txn, {}, "PL-CHK")
        assert row["amount"] == -42.50  # money out → expense
        assert row["currency"] == "USD"
        assert row["provider_account_id"] == "PL-CHK"
        assert row["description"] == "Coffee Shop"
        assert row["memo"] is None
        assert row["pending"] is False
        assert row["external_refs"] == [{"source": "plaid", "ref": "PL-CHK:T1"}]

    def test_pending_transaction_is_carried_through_not_dropped(self):
        """RFC connectors-as-data-sources: pending filtering is connector_fetch's job
        (include_pending), never the provider's — unlike the old contract, a pending row
        must NOT come back as None."""
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn(pending=True)
        row = _txn_to_row(txn, {}, "PL-CHK")
        assert row is not None
        assert row["pending"] is True

    def test_posted_row_carries_supersedes_ref_from_its_former_pending_ref(self):
        """A posted row whose pending_transaction_id names the pending row it replaces
        carries that as supersedes_ref, in the SAME <account>:<id> ref shape the pending
        row was originally emitted under."""
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn(
            transaction_id="T2-POSTED", pending=False, pending_transaction_id="T2-PENDING"
        )
        row = _txn_to_row(txn, {}, "PL-CHK")
        assert row["supersedes_ref"] == "PL-CHK:T2-PENDING"
        assert row["external_refs"] == [{"source": "plaid", "ref": "PL-CHK:T2-POSTED"}]

    def test_no_pending_id_means_no_supersedes_ref(self):
        from backend.services.aggregation.plaid import _txn_to_row

        row = _txn_to_row(self._make_txn(), {}, "PL-CHK")
        assert row["supersedes_ref"] is None

    def test_zero_amount_returns_none(self):
        from backend.services.aggregation.plaid import _txn_to_row

        row = _txn_to_row(self._make_txn(amount=0.0), {}, "PL-CHK")
        assert row is None

    def test_missing_transaction_id_uses_derived_ref(self):
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn()
        txn.pop("transaction_id")
        row = _txn_to_row(txn, {}, "PL-CHK")
        assert row is not None
        assert row["external_refs"][0]["ref"].startswith("derived:")

    def test_currency_falls_back_to_account_currency(self):
        from backend.services.aggregation.plaid import _txn_to_row

        txn = self._make_txn()
        txn.pop("iso_currency_code")
        row = _txn_to_row(txn, {"currency": "EUR"}, "PL-CHK")
        assert row["currency"] == "EUR"


# ── fetch_window ─────────────────────────────────────────────────────────────────────


class TestFetchWindow:
    def _txn(self, **overrides) -> dict:
        base = {
            "transaction_id": "T1",
            "account_id": "PL-CHK",
            "date": "2026-05-01",
            "amount": 10.0,
            "iso_currency_code": "USD",
            "pending": False,
        }
        base.update(overrides)
        return base

    def _account(self, account_id="PL-CHK", **overrides) -> dict:
        base = {
            "account_id": account_id,
            "name": "Checking",
            "balances": {"iso_currency_code": "USD", "current": 100.0},
        }
        base.update(overrides)
        return base

    def test_returns_normalized_rows_and_accounts(self, monkeypatch):
        store = _configured_store()
        txn = self._txn()
        page = {
            "transactions": [txn],
            "accounts": [self._account()],
            "total_transactions": 1,
        }
        monkeypatch.setattr(svc.httpx, "post", lambda url, **k: _Resp(page))

        window = svc.fetch_window(store, "2026-05-01", "2026-05-01", None)

        assert isinstance(window, FetchWindow)
        assert window.complete is True
        assert window.accounts == [
            {
                "provider_account_id": "PL-CHK",
                "name": "Checking",
                "mask": None,
                "currency": "USD",
                "balance": 100.0,
            }
        ]
        assert len(window.rows) == 1
        row = window.rows[0]
        assert row["raw"] == txn
        assert row["amount"] == -10.0
        assert row["external_refs"] == [{"source": "plaid", "ref": "PL-CHK:T1"}]

    def test_posted_row_in_a_full_window_fetch_carries_supersedes_ref(self, monkeypatch):
        store = _configured_store()
        txn = self._txn(
            transaction_id="T2-POSTED", pending_transaction_id="T2-PENDING", amount=-5.0
        )
        page = {"transactions": [txn], "accounts": [], "total_transactions": 1}
        monkeypatch.setattr(svc.httpx, "post", lambda url, **k: _Resp(page))

        window = svc.fetch_window(store, "2026-05-01", "2026-05-02", None)
        assert window.rows[0]["supersedes_ref"] == "PL-CHK:T2-PENDING"

    def test_filters_rows_by_account(self, monkeypatch):
        store = _configured_store()
        txns = [
            self._txn(transaction_id="T1", account_id="PL-CHK"),
            self._txn(transaction_id="T2", account_id="PL-SAV"),
        ]
        page = {"transactions": txns, "accounts": [], "total_transactions": 2}
        monkeypatch.setattr(svc.httpx, "post", lambda url, **k: _Resp(page))

        window = svc.fetch_window(store, "2026-05-01", "2026-05-01", "PL-SAV")
        assert [r["provider_account_id"] for r in window.rows] == ["PL-SAV"]

    def test_accounts_only_fetches_a_single_day_window_and_no_rows(self, monkeypatch):
        store = _configured_store()
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["start_date"] = json["start_date"]
            captured["end_date"] = json["end_date"]
            return _Resp(
                {
                    "transactions": [self._txn()],
                    "accounts": [self._account()],
                    "total_transactions": 1,
                }
            )

        monkeypatch.setattr(svc.httpx, "post", fake_post)
        window = svc.fetch_window(store, "2026-05-01", "2026-05-10", None, accounts_only=True)

        # A single-day window pinned to `before` — never `since`.
        assert captured["start_date"] == captured["end_date"] == "2026-05-10"
        assert window.rows == []
        assert len(window.accounts) == 1
        assert window.complete is True

    def test_truncated_fetch_reports_incomplete(self, monkeypatch):
        """The safety-cap page loop must report complete=False rather than silently
        looking like a clean window — a truncated fetch must never feed the
        vanished-row check a false "nothing missing" signal."""
        store = _configured_store()
        monkeypatch.setattr(svc, "_PAGE_SIZE", 1)
        monkeypatch.setattr(svc, "_MAX_FETCH_PAGES", 2)
        pages = [
            {
                "transactions": [self._txn(transaction_id="T1")],
                "accounts": [],
                "total_transactions": 5,  # more than the safety cap will ever fetch
            },
            {
                "transactions": [self._txn(transaction_id="T2")],
                "accounts": [],
                "total_transactions": 5,
            },
        ]
        state = {"i": 0}

        def fake_post(url, **k):
            page = pages[min(state["i"], len(pages) - 1)]
            state["i"] += 1
            return _Resp(page)

        monkeypatch.setattr(svc.httpx, "post", fake_post)
        window = svc.fetch_window(store, "2026-05-01", "2026-05-10", None)
        assert window.complete is False
        assert len(window.rows) == 2

    def test_requires_a_linked_access_token(self, monkeypatch):
        from backend.db.plaid_config_store import PlaidConfigStore

        store = PlaidConfigStore()
        store.update({"client_id": "cid", "secret": "skey"})  # no access_token
        with pytest.raises(ValidationError, match="not linked"):
            svc.fetch_window(store, "2026-05-01", "2026-05-01", None)


# ── classify_error (plain function — the adapter class is retired) ────────────────────


class TestClassifyError:
    def test_validation_error_is_auth_error(self):
        assert svc.classify_error(
            ValidationError("Plaid client_id / secret are not configured")
        ) == ("auth_error")

    def test_validation_error_wrapping_a_401_is_auth_error(self):
        assert svc.classify_error(ValidationError("Plaid /transactions/get failed: HTTP 401")) == (
            "auth_error"
        )

    def test_validation_error_wrapping_a_transport_failure_is_connect_error(self):
        """`_post` wraps a raw connectivity failure (DNS/refused/timeout) into the SAME
        ValidationError type a bad-credentials response uses — a self-hoster's home
        internet dropping mid-fetch must not be reported as 'your credentials are wrong'."""
        assert (
            svc.classify_error(ValidationError("could not reach Plaid: Connection refused"))
            == "connect_error"
        )

    def test_validation_error_wrapping_a_429_is_rate_limited(self):
        assert svc.classify_error(ValidationError("Plaid /transactions/get failed: HTTP 429")) == (
            "rate_limited"
        )

    def test_http_status_401_is_auth_error(self):
        request = httpx.Request("POST", "https://sandbox.plaid.com/transactions/get")
        response = httpx.Response(401, request=request)
        exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
        assert svc.classify_error(exc) == "auth_error"

    def test_http_status_500_is_connect_error(self):
        request = httpx.Request("POST", "https://sandbox.plaid.com/transactions/get")
        response = httpx.Response(500, request=request)
        exc = httpx.HTTPStatusError("server error", request=request, response=response)
        assert svc.classify_error(exc) == "connect_error"

    def test_connect_error_is_connect_error(self):
        assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"

    def test_generic_exception_is_error(self):
        assert svc.classify_error(RuntimeError("unexpected")) == "error"
