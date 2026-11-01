"""GoCardless Bank Account Data service — token, bank link, fetch_window, errors (NO network).

``httpx.request`` is replaced by an in-memory fake of the GoCardless API, so every test runs
the real request path: the bearer cache, the 401 retry, the URL-free error text, the
rate-limit hint, the requisition status check and the row mapping.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.services.aggregation import gocardless as svc
from backend.services.aggregation.providers import derived_ref

API = svc.API_BASE
REQ = "11111111-2222-3333-4444-555555555555"
ACC = "aaaaaaaa-0000-0000-0000-00000000c001"
ACC2 = "aaaaaaaa-0000-0000-0000-00000000c002"

_BOOKED = [
    {
        "transactionId": "T1",
        "bookingDate": "2026-05-01",
        "transactionAmount": {"amount": "-42.50", "currency": "EUR"},
        "remittanceInformationUnstructured": "ACME Coffee",
    },
    {
        "internalTransactionId": "INT-2",
        "bookingDate": "2026-05-02",
        "transactionAmount": {"amount": "2000.00", "currency": "EUR"},
        "debtorName": "ACME Payroll",
    },
    {"transactionId": "T0", "bookingDate": "2026-05-03", "transactionAmount": {"amount": "0"}},
]
_PENDING = [
    {
        "valueDate": "2026-05-04",
        "transactionAmount": {"amount": "-9.99", "currency": "EUR"},
        "remittanceInformationUnstructuredArray": ["ACME", "Streaming"],
    }
]


class FakeGoCardless:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.status = "LN"
        self.accounts = [ACC]
        self.reject_tokens: set[str] = set()
        self.minted = 0
        self.overrides: dict[tuple[str, str], httpx.Response] = {}

    def __call__(self, method, url, *, headers=None, json=None, params=None, timeout=None):
        path = url[len(API) :]
        self.calls.append((method, path, params or json or {}))
        if (method, path) in self.overrides:
            return self.overrides[(method, path)]
        if path == "/token/new/":
            if json != {"secret_id": "SID", "secret_key": "SKEY"}:
                return httpx.Response(401, json={"detail": "bad"})
            self.minted += 1
            return httpx.Response(
                200, json={"access": f"TOK{self.minted}", "access_expires": 86400}
            )
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        if not token or token in self.reject_tokens:
            return httpx.Response(401, json={"detail": "token"})
        return self._route(method, path, params or {})

    def _route(self, method, path, params):
        if path == "/institutions/":
            return httpx.Response(
                200,
                json=[
                    {"id": "ACME_BANK_GB", "name": "ACME Bank", "transaction_total_days": "540"},
                    {"name": "no id"},
                ],
            )
        if path == "/institutions/ACME_BANK_GB/":
            return httpx.Response(
                200, json={"transaction_total_days": "540", "max_access_valid_for_days": "180"}
            )
        if path == "/agreements/enduser/":
            return httpx.Response(201, json={"id": "AGR-1"})
        if path == "/requisitions/" and method == "POST":
            return httpx.Response(201, json={"id": REQ, "link": "https://ob.gocardless.com/x"})
        if path == f"/requisitions/{REQ}/":
            if method == "DELETE":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"status": self.status, "accounts": self.accounts})
        for acc in (ACC, ACC2):
            if path == f"/accounts/{acc}/details/":
                return httpx.Response(
                    200,
                    json={
                        "account": {
                            "name": "ACME Current",
                            "currency": "eur",
                            "iban": "GB00ACME00000000001234",
                            "cashAccountType": "CACC",
                        }
                    },
                )
            if path == f"/accounts/{acc}/transactions/":
                return httpx.Response(
                    200, json={"transactions": {"booked": _BOOKED, "pending": _PENDING}}
                )
        return httpx.Response(404, json={})


@pytest.fixture
def fake(db_isolated, monkeypatch):
    server = FakeGoCardless()
    monkeypatch.setattr(svc.httpx, "request", server)
    return server


def _store():
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    store = GoCardlessConfigStore()
    store.update({"secret_id": "SID", "secret_key": "SKEY", "enabled": True})
    return store


def _linked_store():
    store = _store()
    store.start_link("ACME_BANK_GB", "GB", REQ, None)
    store.record_requisition_status("LN")
    return store


# ── token ────────────────────────────────────────────────────────────────────────────


def test_the_token_is_minted_once_and_reused(fake):
    store = _store()
    assert svc._bearer(store) == "TOK1"
    assert svc._bearer(store) == "TOK1"
    assert fake.minted == 1


def test_a_refused_cached_token_is_reminted_and_the_call_retried(fake):
    store = _linked_store()
    svc._bearer(store)
    fake.reject_tokens.add("TOK1")
    svc.list_institutions("gb")
    assert fake.minted == 2


def test_wrong_credentials_read_as_a_validation_error(fake):
    store = _store()
    store.update({"secret_key": "WRONG"})
    with pytest.raises(ValidationError, match="rejected"):
        svc._bearer(store)
    assert svc.classify_error(ValidationError("x")) == "auth_error"


def test_missing_credentials_never_call_out(fake):
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    with pytest.raises(ValidationError, match="not configured"):
        svc._bearer(GoCardlessConfigStore())
    assert fake.calls == []


# ── bank picker + link ───────────────────────────────────────────────────────────────


def test_list_institutions_trims_and_drops_idless_rows(fake):
    _store()
    banks = svc.list_institutions("gb")
    assert banks == [
        {"id": "ACME_BANK_GB", "name": "ACME Bank", "bic": None, "logo": None, "history_days": 540}
    ]
    assert ("GET", "/institutions/", {"country": "GB"}) in fake.calls


@pytest.mark.parametrize("country", ["", "GBR", "1A"])
def test_a_bad_country_is_refused_before_any_call(fake, country):
    _store()
    with pytest.raises(ValidationError):
        svc.list_institutions(country)
    assert fake.calls == []


def test_start_link_asks_for_the_banks_maximum_and_stores_the_requisition(fake):
    store = _store()
    assert svc.start_link("ACME_BANK_GB", "gb", "http://localhost:8080") == {
        "link": "https://ob.gocardless.com/x"
    }
    agreement = next(c for c in fake.calls if c[1] == "/agreements/enduser/")
    assert agreement[2]["max_historical_days"] == 540
    assert agreement[2]["access_valid_for_days"] == 180
    requisition = next(c for c in fake.calls if c[1] == "/requisitions/")
    assert requisition[2]["agreement"] == "AGR-1"
    cfg = store.get()
    assert cfg["requisition_id"] == REQ
    assert cfg["requisition_status"] == "CR"
    assert (cfg["institution_id"], cfg["country"]) == ("ACME_BANK_GB", "GB")
    assert store.has_link() is False  # not linked until GoCardless says LN


def test_a_refused_agreement_falls_back_to_the_default_one(fake):
    _store()
    fake.overrides[("POST", "/agreements/enduser/")] = httpx.Response(400, json={})
    svc.start_link("ACME_BANK_GB", "GB", "https://onw.example")
    requisition = next(c for c in fake.calls if c[1] == "/requisitions/")
    assert "agreement" not in requisition[2]


def test_relinking_deletes_the_old_requisition_at_gocardless(fake):
    store = _linked_store()
    store.cache_account_meta(ACC, {"name": "old"})
    fake.overrides[("POST", "/requisitions/")] = httpx.Response(
        201, json={"id": "NEW-REQ", "link": "https://ob.gocardless.com/y"}
    )
    svc.start_link("ACME_BANK_GB", "GB", "https://onw.example")
    assert ("DELETE", f"/requisitions/{REQ}/", {}) in fake.calls
    assert store.requisition_id() == "NEW-REQ"
    assert store.accounts_meta() == {}


@pytest.mark.parametrize("redirect", ["", "javascript:alert(1)", "localhost:8080"])
def test_a_redirect_that_is_not_http_is_refused(fake, redirect):
    _store()
    with pytest.raises(ValidationError, match="redirect"):
        svc.start_link("ACME_BANK_GB", "GB", redirect)


def test_check_link_records_the_status_and_never_resumes_a_paused_connector(fake):
    store = _store()
    store.start_link("ACME_BANK_GB", "GB", REQ, None)
    store.update({"enabled": False})
    fake.status = "GA"
    assert svc.check_link()["has_link"] is False
    fake.status = "LN"
    assert svc.check_link()["has_link"] is True
    assert store.get()["enabled"] is False


@pytest.mark.parametrize("institution_id", ["", "../requisitions", "ACME BANK", "x" * 101])
def test_an_institution_id_that_is_not_a_bank_id_is_refused(fake, institution_id):
    _store()
    with pytest.raises(ValidationError, match="bank id"):
        svc.start_link(institution_id, "GB", "https://onw.example")
    assert fake.calls == []


# ── fetch_window ─────────────────────────────────────────────────────────────────────


def test_fetch_window_maps_booked_and_pending_rows(fake):
    store = _linked_store()
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.complete is True
    assert window.accounts == [
        {
            "provider_account_id": ACC,
            "name": "ACME Current",
            "mask": "1234",
            "currency": "EUR",
            "balance": None,
            "account_type": "cash",
        }
    ]
    by_ref = {r["external_refs"][0]["ref"]: r for r in window.rows}
    coffee = by_ref[f"{ACC}:T1"]
    assert (coffee["amount"], coffee["currency"], coffee["pending"]) == (-42.5, "EUR", False)
    assert coffee["description"] == "ACME Coffee"
    assert coffee["raw"] == _BOOKED[0]
    assert by_ref[f"{ACC}:INT-2"]["description"] == "ACME Payroll"
    pending = [r for r in window.rows if r["pending"]]
    assert len(pending) == 1
    assert pending[0]["description"] == "ACME Streaming"
    assert pending[0]["external_refs"][0]["ref"] == derived_ref(
        ACC, "2026-05-04", -9.99, "EUR", "ACME Streaming"
    )
    assert len(window.rows) == 3  # the zero-amount row is dropped
    assert all(r["external_refs"][0]["source"] == "gocardless" for r in window.rows)
    txn_call = next(c for c in fake.calls if c[1].endswith("/transactions/"))
    assert txn_call[2] == {"date_from": "2026-05-01", "date_to": "2026-05-31"}


def test_account_details_are_fetched_once_then_read_from_the_cache(fake):
    store = _linked_store()
    svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    assert sum(1 for c in fake.calls if c[1].endswith("/details/")) == 1
    assert not any(c[1].endswith("/transactions/") for c in fake.calls)


def test_an_account_filter_narrows_to_one_account(fake):
    fake.accounts = [ACC, ACC2]
    window = svc.fetch_window(_linked_store(), "2026-05-01", "2026-05-31", ACC2)
    assert [a["provider_account_id"] for a in window.accounts] == [ACC2]
    assert {r["provider_account_id"] for r in window.rows} == {ACC2}


def test_one_failing_account_is_isolated_and_marks_the_fetch_incomplete(fake):
    fake.accounts = [ACC, ACC2]
    fake.overrides[("GET", f"/accounts/{ACC2}/transactions/")] = httpx.Response(500)
    window = svc.fetch_window(_linked_store(), "2026-05-01", "2026-05-31", None)
    assert window.complete is False
    assert {r["provider_account_id"] for r in window.rows} == {ACC}


def test_a_rate_limit_on_every_account_is_raised_with_its_reset_time(fake):
    fake.overrides[("GET", f"/accounts/{ACC}/transactions/")] = httpx.Response(
        429, headers={"HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_RESET": "3600"}
    )
    with pytest.raises(ExternalServiceError) as exc:
        svc.fetch_window(_linked_store(), "2026-05-01", "2026-05-31", None)
    assert "60 min" in str(exc.value)
    assert svc.classify_error(exc.value) == "rate_limited"


def test_an_error_never_carries_an_account_or_requisition_id(fake):
    fake.overrides[("GET", f"/accounts/{ACC}/transactions/")] = httpx.Response(503)
    with pytest.raises(ExternalServiceError) as exc:
        svc.fetch_window(_linked_store(), "2026-05-01", "2026-05-31", None)
    assert ACC not in str(exc.value) and REQ not in str(exc.value)
    assert svc.classify_error(exc.value) == "connect_error"


def test_a_transport_failure_is_wrapped_without_the_url(db_isolated, monkeypatch):
    def boom(method, url, **kwargs):
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(svc.httpx, "request", boom)
    _store().save_token("CACHED", "2999-01-01T00:00:00+00:00")
    with pytest.raises(ExternalServiceError) as exc:
        svc.list_institutions("GB")
    assert "ConnectError" in str(exc.value) and "gocardless.com" not in str(exc.value)


@pytest.mark.parametrize("status, word", [("EX", "expired"), ("RJ", "refused")])
def test_an_ended_consent_says_to_link_again(fake, status, word):
    fake.status = status
    store = _linked_store()
    with pytest.raises(ValidationError, match=word):
        svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert store.get()["requisition_status"] == status
    assert store.has_link() is False


def test_an_unfinished_link_says_to_finish_it(fake):
    fake.status = "UA"
    with pytest.raises(ValidationError, match="not finished"):
        svc.fetch_window(_linked_store(), "2026-05-01", "2026-05-31", None)


def test_remove_deletes_the_requisition_then_the_row(fake):
    from backend.services.connector_catalog import CATALOG

    store = _linked_store()
    CATALOG["gocardless"].remove("gocardless")
    assert ("DELETE", f"/requisitions/{REQ}/", {}) in fake.calls
    assert store.get()["secret_key"] == ""


def test_remove_still_clears_locally_when_gocardless_is_unreachable(fake):
    from backend.services.connector_catalog import CATALOG

    store = _linked_store()
    fake.overrides[("DELETE", f"/requisitions/{REQ}/")] = httpx.Response(500)
    CATALOG["gocardless"].remove("gocardless")
    assert store.get()["requisition_id"] == ""


def test_the_fake_matches_the_wire_shape():
    # Guard: the fake returns what the OpenAPI spec names (transactions.booked/pending).
    body = json.loads(FakeGoCardless()._route("GET", f"/accounts/{ACC}/transactions/", {}).text)
    assert set(body["transactions"]) == {"booked", "pending"}
