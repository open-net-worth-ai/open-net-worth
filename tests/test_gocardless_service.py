"""GoCardless Bank Account Data service — token, banks, fetch_window, errors (NO network).

``httpx.request`` is replaced by an in-memory fake of the GoCardless API, so every test runs
the real request path: the bearer cache, the 401 retry, the URL-free error text, the
rate-limit hint, each bank's requisition status, renewals, per-bank isolation and the row
mapping.
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
REQ2 = "22222222-2222-3333-4444-555555555555"
ACC = "aaaaaaaa-0000-0000-0000-00000000c001"
ACC2 = "aaaaaaaa-0000-0000-0000-00000000c002"
ACC3 = "aaaaaaaa-0000-0000-0000-00000000c003"

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
        # requisition id → {status, accounts}; POST /requisitions/ takes the next new id.
        self.requisitions: dict[str, dict] = {}
        self.new_ids = [REQ, REQ2, "NEW-REQ-3"]
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
            rid = self.new_ids.pop(0)
            self.requisitions[rid] = {"status": "CR", "accounts": []}
            return httpx.Response(201, json={"id": rid, "link": f"https://ob.gocardless.com/{rid}"})
        if path.startswith("/requisitions/"):
            rid = path.split("/")[2]
            if rid not in self.requisitions:
                return httpx.Response(404, json={})
            if method == "DELETE":
                del self.requisitions[rid]
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.requisitions[rid])
        for acc in (ACC, ACC2, ACC3):
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


def _bank(store, fake, rid, accounts, status="LN", name="ACME Bank", **kw):
    """A bank listed locally AND known to the fake with ``status`` and ``accounts``."""
    fake.requisitions[rid] = {"status": status, "accounts": accounts}
    fake.new_ids = [i for i in fake.new_ids if i != rid]
    key = store.add_bank(
        rid,
        institution_id="ACME_BANK_GB",
        institution_name=name,
        country="GB",
        consent_expires_at=None,
        **kw,
    )
    store.update_bank(key, status=status, account_ids=accounts if status == "LN" else [])
    return key


def _linked_store(fake, accounts=(ACC,)):
    store = _store()
    _bank(store, fake, REQ, list(accounts))
    return store


# ── token ────────────────────────────────────────────────────────────────────────────


def test_the_token_is_minted_once_and_reused(fake):
    store = _store()
    assert svc._bearer(store) == "TOK1"
    assert svc._bearer(store) == "TOK1"
    assert fake.minted == 1


def test_a_refused_cached_token_is_reminted_and_the_call_retried(fake):
    store = _linked_store(fake)
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


def test_start_link_asks_for_the_banks_maximum_and_adds_a_bank(fake):
    from backend.db.gocardless_config_store import bank_key

    store = _store()
    fake.overrides[("GET", "/institutions/ACME_BANK_GB/")] = httpx.Response(
        200,
        json={
            "name": "ACME Bank",
            "transaction_total_days": "540",
            "max_access_valid_for_days": "180",
        },
    )
    result = svc.start_link("ACME_BANK_GB", "gb", "http://localhost:8080")
    assert result == {"link": f"https://ob.gocardless.com/{REQ}", "key": bank_key(REQ)}
    agreement = next(c for c in fake.calls if c[1] == "/agreements/enduser/")
    assert agreement[2]["max_historical_days"] == 540
    assert agreement[2]["access_valid_for_days"] == 180
    requisition = next(c for c in fake.calls if c[1] == "/requisitions/")
    assert requisition[2]["agreement"] == "AGR-1"
    [bank] = store.banks()
    assert (bank["requisition_id"], bank["status"]) == (REQ, "CR")
    assert (bank["institution_id"], bank["institution_name"], bank["country"]) == (
        "ACME_BANK_GB",
        "ACME Bank",
        "GB",
    )
    assert bank["consent_expires_at"]
    assert store.has_link() is False  # not linked until GoCardless says LN


def test_linking_another_bank_leaves_the_first_untouched(fake):
    store = _linked_store(fake)
    svc.start_link("ACME_BANK_GB", "GB", "https://onw.example")
    assert [b["requisition_id"] for b in store.banks()] == [REQ, REQ2]
    assert not any(c[0] == "DELETE" for c in fake.calls)
    assert store.banks()[0]["status"] == "LN"


def test_a_refused_agreement_falls_back_to_the_default_one(fake):
    _store()
    fake.overrides[("POST", "/agreements/enduser/")] = httpx.Response(400, json={})
    svc.start_link("ACME_BANK_GB", "GB", "https://onw.example")
    requisition = next(c for c in fake.calls if c[1] == "/requisitions/")
    assert "agreement" not in requisition[2]


def test_a_failed_requisition_stores_nothing(fake):
    store = _store()
    fake.overrides[("POST", "/requisitions/")] = httpx.Response(201, json={"id": "X"})
    with pytest.raises(ExternalServiceError, match="bank link"):
        svc.start_link("ACME_BANK_GB", "GB", "https://onw.example")
    assert store.banks() == []


@pytest.mark.parametrize("redirect", ["", "javascript:alert(1)", "localhost:8080"])
def test_a_redirect_that_is_not_http_is_refused(fake, redirect):
    _store()
    with pytest.raises(ValidationError, match="redirect"):
        svc.start_link("ACME_BANK_GB", "GB", redirect)


def test_check_links_records_every_banks_status_and_never_resumes_a_paused_connector(fake):
    store = _store()
    _bank(store, fake, REQ, [], status="GA")
    _bank(store, fake, REQ2, [ACC2], name="Other Bank")
    store.update({"enabled": False})
    public = svc.check_links()
    assert [b["status"] for b in public["banks"]] == ["GA", "LN"]
    fake.requisitions[REQ] = {"status": "LN", "accounts": [ACC]}
    public = svc.check_links()
    assert [(b["status"], b["accounts"]) for b in public["banks"]] == [("LN", 1), ("LN", 1)]
    assert store.banks()[0]["linked_at"]
    assert store.get()["enabled"] is False


def test_check_links_with_no_bank_says_to_link_one(fake):
    _store()
    with pytest.raises(ValidationError, match="No bank linked"):
        svc.check_links()


@pytest.mark.parametrize("institution_id", ["", "../requisitions", "ACME BANK", "x" * 101])
def test_an_institution_id_that_is_not_a_bank_id_is_refused(fake, institution_id):
    _store()
    with pytest.raises(ValidationError, match="bank id"):
        svc.start_link(institution_id, "GB", "https://onw.example")
    assert fake.calls == []


# ── renew + remove one bank ──────────────────────────────────────────────────────────


def test_relinking_an_expired_bank_replaces_it_at_once(fake):
    store = _store()
    old = _bank(store, fake, REQ, [], status="EX")
    fake.new_ids = [REQ2]
    result = svc.relink_bank(old, "https://onw.example")
    assert ("DELETE", f"/requisitions/{REQ}/", {}) in fake.calls
    [bank] = store.banks()
    assert bank["requisition_id"] == REQ2 and bank["key"] == result["key"]
    assert bank["replaces"] is None


def test_renewing_a_working_bank_keeps_it_until_the_new_consent_is_linked(fake):
    store = _linked_store(fake)
    store.cache_account_meta(ACC, {"name": "ACME Current"})
    old = store.banks()[0]["key"]
    fake.new_ids = [REQ2]
    svc.relink_bank(old, "https://onw.example")
    assert not any(c[0] == "DELETE" for c in fake.calls)
    assert [b["requisition_id"] for b in store.banks()] == [REQ, REQ2]
    assert store.banks()[1]["replaces"] == REQ

    # Abandoned at the bank: the old one still fetches, and no warning is raised for the new.
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert {r["provider_account_id"] for r in window.rows} == {ACC}
    assert window.warnings == [] and window.complete is True

    # Finished at the bank: the renewal takes over and the old requisition is revoked.
    fake.requisitions[REQ2] = {"status": "LN", "accounts": [ACC3]}
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert ("DELETE", f"/requisitions/{REQ}/", {}) in fake.calls
    [bank] = store.banks()
    assert (bank["requisition_id"], bank["replaces"]) == (REQ2, None)
    assert {r["provider_account_id"] for r in window.rows} == {ACC3}
    assert ACC not in store.accounts_meta()


def test_relinking_an_unknown_bank_is_refused(fake):
    _store()
    with pytest.raises(ValidationError, match="not linked"):
        svc.relink_bank("nope", "https://onw.example")


def test_remove_bank_revokes_only_that_bank(fake):
    store = _linked_store(fake)
    other = _bank(store, fake, REQ2, [ACC2], name="Other Bank")
    assert svc.remove_bank(other) == {"removed": True, "banks_left": 1}
    assert ("DELETE", f"/requisitions/{REQ2}/", {}) in fake.calls
    assert [b["requisition_id"] for b in store.banks()] == [REQ]


def test_remove_bank_keeps_it_when_gocardless_cannot_confirm(fake):
    store = _linked_store(fake)
    key = store.banks()[0]["key"]
    fake.overrides[("DELETE", f"/requisitions/{REQ}/")] = httpx.Response(500)
    with pytest.raises(ValidationError, match="nothing changed"):
        svc.remove_bank(key)
    assert [b["key"] for b in store.banks()] == [key]


def test_remove_bank_drops_one_gocardless_already_deleted(fake):
    store = _linked_store(fake)
    key = store.banks()[0]["key"]
    del fake.requisitions[REQ]
    svc.remove_bank(key)
    assert store.banks() == []


# ── fetch_window ─────────────────────────────────────────────────────────────────────


def test_fetch_window_maps_booked_and_pending_rows(fake):
    store = _linked_store(fake)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.complete is True
    assert window.accounts == [
        {
            "provider_account_id": ACC,
            "name": "ACME Current",
            "mask": "1234",
            "currency": "EUR",
            "balance": None,
            "cleared_balance": None,
            "working_balance": None,
            "available_to_spend": None,
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
    store = _linked_store(fake)
    svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    assert sum(1 for c in fake.calls if c[1].endswith("/details/")) == 1
    assert not any(c[1].endswith("/transactions/") for c in fake.calls)


def test_an_account_filter_narrows_to_one_account(fake):
    window = svc.fetch_window(_linked_store(fake, [ACC, ACC2]), "2026-05-01", "2026-05-31", ACC2)
    assert [a["provider_account_id"] for a in window.accounts] == [ACC2]
    assert {r["provider_account_id"] for r in window.rows} == {ACC2}


def test_one_failing_account_is_isolated_and_marks_the_fetch_incomplete(fake):
    fake.overrides[("GET", f"/accounts/{ACC2}/transactions/")] = httpx.Response(500)
    window = svc.fetch_window(_linked_store(fake, [ACC, ACC2]), "2026-05-01", "2026-05-31", None)
    assert window.complete is False
    assert {r["provider_account_id"] for r in window.rows} == {ACC}
    assert window.warnings == ["ACME Current (ACME Bank): GoCardless HTTP 500"]


def test_a_rate_limit_on_every_account_is_raised_with_its_reset_time(fake):
    fake.overrides[("GET", f"/accounts/{ACC}/transactions/")] = httpx.Response(
        429, headers={"HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_RESET": "3600"}
    )
    with pytest.raises(ExternalServiceError) as exc:
        svc.fetch_window(_linked_store(fake), "2026-05-01", "2026-05-31", None)
    assert "60 min" in str(exc.value)
    assert svc.classify_error(exc.value) == "rate_limited"


def test_an_error_never_carries_an_account_or_requisition_id(fake):
    fake.overrides[("GET", f"/accounts/{ACC}/transactions/")] = httpx.Response(503)
    with pytest.raises(ExternalServiceError) as exc:
        svc.fetch_window(_linked_store(fake), "2026-05-01", "2026-05-31", None)
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
    store = _linked_store(fake)
    fake.requisitions[REQ]["status"] = status
    with pytest.raises(ValidationError, match=word):
        svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert store.banks()[0]["status"] == status
    assert store.has_link() is False


def test_an_unfinished_link_says_to_finish_it(fake):
    store = _linked_store(fake)
    fake.requisitions[REQ]["status"] = "UA"
    with pytest.raises(ValidationError, match="not finished"):
        svc.fetch_window(store, "2026-05-01", "2026-05-31", None)


def test_no_bank_at_all_says_to_link_one(fake):
    with pytest.raises(ValidationError, match="No bank linked"):
        svc.fetch_window(_store(), "2026-05-01", "2026-05-31", None)


def test_every_linked_bank_is_fetched(fake):
    store = _linked_store(fake)
    _bank(store, fake, REQ2, [ACC2], name="Other Bank")
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert [a["provider_account_id"] for a in window.accounts] == [ACC, ACC2]
    assert {r["provider_account_id"] for r in window.rows} == {ACC, ACC2}
    assert window.complete is True and window.warnings == []


def test_an_expired_bank_is_a_warning_while_the_others_still_fetch(fake):
    store = _linked_store(fake)
    _bank(store, fake, REQ2, [ACC2], name="Other Bank")
    fake.requisitions[REQ2]["status"] = "EX"
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert {r["provider_account_id"] for r in window.rows} == {ACC}
    assert window.complete is False  # the vanished-row check must not read ACC2's rows as gone
    [warning] = window.warnings
    assert warning.startswith("Other Bank: bank access expired")
    assert REQ2 not in warning


def test_a_bank_gocardless_cannot_be_asked_about_is_a_warning(fake):
    store = _linked_store(fake)
    _bank(store, fake, REQ2, [ACC2], name="Other Bank")
    fake.overrides[("GET", f"/requisitions/{REQ2}/")] = httpx.Response(503)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None)
    assert window.warnings == ["Other Bank: GoCardless HTTP 503"]
    # Its last known status is kept: it was linked, so it is still read.
    assert {r["provider_account_id"] for r in window.rows} == {ACC, ACC2}


def test_a_fallback_account_name_names_its_bank(fake):
    store = _linked_store(fake)
    fake.overrides[("GET", f"/accounts/{ACC}/details/")] = httpx.Response(500)
    window = svc.fetch_window(store, "2026-05-01", "2026-05-31", None, accounts_only=True)
    assert window.accounts[0]["name"] == "ACME Bank account c001"


def test_remove_deletes_every_requisition_then_the_row(fake):
    from backend.services.connector_catalog import CATALOG

    store = _linked_store(fake)
    _bank(store, fake, REQ2, [ACC2])
    CATALOG["gocardless"].remove("gocardless")
    assert ("DELETE", f"/requisitions/{REQ}/", {}) in fake.calls
    assert ("DELETE", f"/requisitions/{REQ2}/", {}) in fake.calls
    assert store.get()["secret_key"] == ""


def test_remove_still_clears_locally_when_gocardless_is_unreachable(fake):
    from backend.services.connector_catalog import CATALOG

    store = _linked_store(fake)
    fake.overrides[("DELETE", f"/requisitions/{REQ}/")] = httpx.Response(500)
    CATALOG["gocardless"].remove("gocardless")
    assert store.banks() == []


def test_the_fake_matches_the_wire_shape():
    # Guard: the fake returns what the OpenAPI spec names (transactions.booked/pending).
    body = json.loads(FakeGoCardless()._route("GET", f"/accounts/{ACC}/transactions/", {}).text)
    assert set(body["transactions"]) == {"booked", "pending"}


# ── what a row carries beyond its description (2026-11-13 audit) ──────────────────────


def _row_of(txn: dict) -> dict:
    row = svc._txn_to_row(txn, ACC, "GBP", pending=False)
    assert row is not None
    return row


def test_the_counterparty_is_the_payee_and_the_bank_wording_stays_the_description():
    row = _row_of(
        {
            "transactionId": "t1",
            "bookingDate": "2026-11-10",
            "transactionAmount": {"amount": "-12.40", "currency": "GBP"},
            "remittanceInformationUnstructured": "CARD 1234 ACME STORES LONDON",
            "creditorName": "Acme Grocer",
            "debtorName": "A. Person",
        }
    )
    assert row["description"] == "CARD 1234 ACME STORES LONDON"
    assert row["payee"] == "Acme Grocer"  # money out → who was paid
    assert row["memo"] is None


def test_money_in_names_who_paid():
    row = _row_of(
        {
            "transactionId": "t2",
            "bookingDate": "2026-11-10",
            "transactionAmount": {"amount": "2500.00", "currency": "GBP"},
            "debtorName": "ACME LTD",
            "creditorName": "A. Person",
        }
    )
    assert row["payee"] == "ACME LTD"


def test_no_counterparty_leaves_the_payee_to_the_description():
    row = _row_of(
        {
            "transactionId": "t2b",
            "bookingDate": "2026-11-10",
            "transactionAmount": {"amount": "-3.00", "currency": "GBP"},
            "remittanceInformationUnstructured": "ACME CAFE",
        }
    )
    assert row["payee"] is None


def test_a_purchase_in_another_currency_keeps_what_was_actually_paid():
    row = _row_of(
        {
            "transactionId": "t3",
            "bookingDate": "2026-11-09",
            "transactionAmount": {"amount": "-42.10", "currency": "GBP"},
            "remittanceInformationUnstructured": "CAFE ACME PARIS",
            "currencyExchange": [
                {
                    "sourceCurrency": "EUR",
                    "exchangeRate": "1.1401",
                    "instructedAmount": {"amount": "48.00", "currency": "EUR"},
                    "targetCurrency": "GBP",
                }
            ],
        }
    )
    assert (row["amount"], row["currency"]) == (-42.10, "GBP")
    # The ledger's own fields for the original charge (the duplicate check reads them), not
    # text in the memo, which is the user's.
    assert (row["original_amount"], row["original_currency"]) == (48.0, "EUR")
    assert row["memo"] is None


def test_a_purchase_in_the_accounts_own_currency_has_no_original_charge():
    row = _row_of(
        {
            "transactionId": "t3b",
            "bookingDate": "2026-11-09",
            "transactionAmount": {"amount": "-5.00", "currency": "GBP"},
            "currencyExchange": {"instructedAmount": {"amount": "5.00", "currency": "GBP"}},
        }
    )
    assert (row["original_amount"], row["original_currency"]) == (None, None)


@pytest.mark.parametrize(
    "code, hint",
    [
        ("5411", "Grocery stores and supermarkets"),
        ("6300", "Insurance"),
        ("8220", "Colleges and universities"),
        ("3015", "Airlines"),  # a block the networks give one industry
        ("0000", None),  # unknown: no hint rather than a guess
        (None, None),
    ],
)
def test_the_merchant_category_code_becomes_the_banks_category_hint(code, hint):
    txn = {
        "transactionId": "t5",
        "bookingDate": "2026-11-09",
        "transactionAmount": {"amount": "-9.00", "currency": "GBP"},
    }
    if code is not None:
        txn["merchantCategoryCode"] = code
    assert _row_of(txn)["category_hint"] == hint


def test_a_booking_datetime_alone_still_dates_the_row():
    row = _row_of(
        {
            "transactionId": "t4",
            "bookingDateTime": "2026-11-08T23:59:59Z",
            "transactionAmount": {"amount": "-1.00", "currency": "GBP"},
        }
    )
    assert row["date"] == "2026-11-08"


def test_rejected_keys_mark_the_connector_until_good_ones_sign_in(fake):
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    """Saving keys GoCardless refuses must not read Connected (2026-11-13 e2e)."""
    store = GoCardlessConfigStore()
    store.update({"secret_id": "SID", "secret_key": "WRONG"})
    with pytest.raises(ValidationError):
        svc.list_institutions("GB")
    cfg = store.get()
    assert (cfg["last_status"], cfg["last_synced_at"]) == ("auth_error", None)
    assert "rejected" in cfg["last_error"]

    store.update({"secret_key": "SKEY"})
    store.clear_token()
    svc.list_institutions("GB")
    cfg = store.get()
    assert (cfg["last_status"], cfg["last_error"]) == ("never_run", None)
