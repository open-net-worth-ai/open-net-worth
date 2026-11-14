"""GoCardless and Enable Banking, the rest of the no-AI sync's PSD2 issues (ADR
2026-11-13, Amendments 14-15). Synthetic ACME data; no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services.aggregation import enable_banking as eb


@pytest.fixture(autouse=True)
def isolate_stores(db_isolated):
    """Every test runs against a fresh, isolated database."""


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


# ── Enable Banking asks for the bank's longest consent ───────────────────────────────


def _asked_days(monkeypatch, aspsps: list[dict], bank: str) -> float:
    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb.httpx, "get", lambda *a, **k: _Resp(200, {"aspsps": aspsps}))
    sent: dict = {}

    def _post(url, json, **kw):
        sent.update(json)
        return _Resp(200, {"url": "https://bank.example/a", "authorization_id": "A"})

    monkeypatch.setattr(eb.httpx, "post", _post)
    eb.start_authorization(bank, "FI", "")
    until = datetime.fromisoformat(sent["access"]["valid_until"])
    return (until - datetime.now(timezone.utc)).total_seconds() / 86400


def test_the_banks_own_maximum_consent_is_asked_for(monkeypatch):
    days = _asked_days(
        monkeypatch, [{"name": "ACME Bank", "maximum_consent_validity": 15552000}], "ACME Bank"
    )
    assert 179.9 < days <= 180


def test_a_maximum_beyond_the_ceiling_is_capped(monkeypatch):
    days = _asked_days(
        monkeypatch, [{"name": "ACME Bank", "maximum_consent_validity": 31536000}], "ACME Bank"
    )
    assert 179.9 < days <= 180


def test_a_bank_with_no_stated_maximum_gets_ninety_days(monkeypatch):
    assert 89.9 < _asked_days(monkeypatch, [{"name": "ACME Bank"}], "ACME Bank") <= 90
    assert 89.9 < _asked_days(monkeypatch, [{"name": "Other"}], "ACME Bank") <= 90


def test_a_failed_bank_lookup_still_starts_the_link_at_ninety_days(monkeypatch):
    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb.httpx, "get", lambda *a, **k: _Resp(500, {}))
    sent: dict = {}
    monkeypatch.setattr(
        eb.httpx,
        "post",
        lambda url, json, **kw: sent.update(json) or _Resp(200, {"url": "https://bank.example/a"}),
    )
    eb.start_authorization("ACME Bank", "FI", "")
    until = datetime.fromisoformat(sent["access"]["valid_until"])
    assert 89.9 < (until - datetime.now(timezone.utc)).total_seconds() / 86400 <= 90


# ── Enable Banking reads only what one account needs ─────────────────────────────────


def _two_banks(monkeypatch) -> tuple[EnableBankingConfigStore, list[str]]:
    """ACME Bank (U1, U2, U3) and ACME Savings (V1, V2): every path the code calls is logged."""
    store = EnableBankingConfigStore()
    store.update({"application_id": "APP", "private_key": "KEY"})
    store.add_bank(
        "SESS-A",
        aspsp="ACME Bank",
        country="FI",
        valid_until=None,
        linked_at="2026-11-01T00:00:00+00:00",
        account_ids=["U1", "U2", "U3"],
    )
    store.add_bank(
        "SESS-B",
        aspsp="ACME Savings",
        country="FI",
        valid_until=None,
        linked_at="2026-11-01T00:00:00+00:00",
        account_ids=["V1", "V2"],
    )
    sessions = {"SESS-A": ["U1", "U2", "U3"], "SESS-B": ["V1", "V2"]}
    calls: list[str] = []

    def _get(token, path, params):
        calls.append(path)
        if path.startswith("/sessions/"):
            uids = sessions[path.rsplit("/", 1)[1]]
            return _Resp(
                200,
                {
                    "status": "AUTHORIZED",
                    "accounts": uids,
                    "accounts_data": [{"uid": u, "identification_hash": f"H-{u}"} for u in uids],
                },
            )
        if path.endswith("/details"):
            return _Resp(200, {"name": "ACME", "currency": "EUR"})
        if path.endswith("/balances"):
            return _Resp(
                200,
                {"balances": [{"balance_type": "CLBD", "balance_amount": {"amount": "10.00"}}]},
            )
        return _Resp(200, {"transactions": []})

    monkeypatch.setattr(eb, "_bearer", lambda store: "JWT")
    monkeypatch.setattr(eb, "_safe_get", _get)
    return store, calls


def test_a_one_account_read_asks_for_one_session_and_one_details(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "V2")
    assert [a["provider_account_id"] for a in window.accounts] == ["V2"]
    assert calls.count("/sessions/SESS-A") == 0  # another bank is never touched
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/V2/details"]


def test_listing_every_account_still_reads_every_bank(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    window = eb.fetch_window(store, "2026-11-10", "2026-11-10", None, accounts_only=True)
    assert len(window.accounts) == 5
    assert len([c for c in calls if c.endswith("/details")]) == 5


def test_a_renewed_accounts_stable_id_is_still_found_with_a_filtered_read(monkeypatch):
    """The renewed session hands the account a new uid; the stable id is the first one."""
    store, calls = _two_banks(monkeypatch)
    bank_a = store.bank(store.banks()[0]["key"])
    assert (
        eb._stable_account_id(store, bank_a, {"uid": "U1", "identification_hash": "H-U1"}) == "U1"
    )
    store.add_bank(
        "SESS-A2",
        aspsp="ACME Bank",
        country="FI",
        valid_until=None,
        linked_at="2026-11-05T00:00:00+00:00",
        account_ids=["U1", "U2", "U3"],
    )
    original = eb._safe_get

    def _get(token, path, params):
        if path == "/sessions/SESS-A2":
            return _Resp(
                200,
                {
                    "status": "AUTHORIZED",
                    "accounts": ["N1"],
                    "accounts_data": [{"uid": "N1", "identification_hash": "H-U1"}],
                },
            )
        return original(token, path, params)

    monkeypatch.setattr(eb, "_safe_get", _get)
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "U1")
    assert [a["provider_account_id"] for a in window.accounts] == ["U1"]
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/N1/details"]


def test_the_other_banks_are_read_only_when_the_account_is_not_found_first(monkeypatch):
    store, calls = _two_banks(monkeypatch)
    store.set_account_ids(store.banks()[1]["key"], [])  # no bank is known to hold V1
    window = eb.fetch_window(store, "2026-11-01", "2026-11-10", "V1")
    assert [a["provider_account_id"] for a in window.accounts] == ["V1"]
    assert "/sessions/SESS-A" in calls  # tried first, no match, so the next bank is read
    assert [c for c in calls if c.endswith("/details")] == ["/accounts/V1/details"]


# ── GoCardless ───────────────────────────────────────────────────────────────────────

import httpx  # noqa: E402

from backend.db.gocardless_config_store import GoCardlessConfigStore  # noqa: E402
from backend.services.aggregation import gocardless as gc  # noqa: E402

OLD_REQ, NEW_REQ = "req-old", "req-new"
A1, A2 = "acct-old-1", "acct-old-2"
B1, B2 = "acct-new-1", "acct-new-2"
_CLOSING = {"balances": [{"balanceType": "closingBooked", "balanceAmount": {"amount": "5"}}]}


class _FakeGoCardless:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.requisitions: dict[str, dict] = {}
        self.agreements: dict[str, dict] = {}
        self.details: dict[str, dict] = {}
        self.balances: dict[str, httpx.Response] = {}

    def __call__(self, method, url, *, headers=None, json=None, params=None, timeout=None):
        path = url[len(gc.API_BASE) :]
        self.calls.append((method, path))
        if path == "/token/new/":
            return httpx.Response(200, json={"access": "TOK", "access_expires": 86400})
        if path.startswith("/requisitions/"):
            rid = path.split("/")[2]
            if method == "DELETE":
                self.requisitions.pop(rid, None)
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.requisitions[rid])
        if path.startswith("/agreements/enduser/"):
            return httpx.Response(200, json=self.agreements[path.split("/")[3]])
        acc = path.split("/")[2] if path.startswith("/accounts/") else ""
        if path.endswith("/details/"):
            return httpx.Response(200, json={"account": self.details[acc]})
        if path.endswith("/balances/"):
            return self.balances.get(acc) or httpx.Response(200, json=_CLOSING)
        if path.endswith("/transactions/"):
            txn = {
                "internalTransactionId": "T1",
                "bookingDate": "2026-11-01",
                "transactionAmount": {"amount": "-3.00", "currency": "EUR"},
                "remittanceInformationUnstructured": "ACME Coffee",
            }
            return httpx.Response(200, json={"transactions": {"booked": [txn], "pending": []}})
        return httpx.Response(404, json={})

    def count(self, suffix: str) -> int:
        return sum(1 for _, p in self.calls if p.endswith(suffix))


@pytest.fixture
def gocardless(monkeypatch):
    fake = _FakeGoCardless()
    monkeypatch.setattr(gc.httpx, "request", fake)
    return fake


def _gc_store() -> GoCardlessConfigStore:
    store = GoCardlessConfigStore()
    store.update({"secret_id": "SID", "secret_key": "SKEY", "enabled": True})
    return store


def _linked(store, fake, rid, accounts, *, replaces=None):
    fake.requisitions[rid] = {"status": "LN", "accounts": accounts}
    key = store.add_bank(
        rid,
        institution_id="ACME_BANK_GB",
        institution_name="ACME Bank",
        country="GB",
        consent_expires_at=None,
        replaces=replaces,
    )
    store.update_bank(key, status="LN", account_ids=accounts, consent_dated=True)
    return key


def _details(iban: str | None, name="ACME Current") -> dict:
    out = {"name": name, "currency": "EUR", "cashAccountType": "CACC"}
    if iban:
        out["iban"] = iban
    return out


IBAN1, IBAN2 = "GB00ACME00000000000001", "GB00ACME00000000000002"


def _renewal(gocardless, *, new_ibans=(IBAN2, IBAN1), cached_identities=True):
    """ACME Bank linked with A1 (IBAN1) and A2 (IBAN2), then renewed: B1 now holds IBAN2 and B2
    IBAN1 — the new ids come in a different order, so only the IBAN can match them."""
    store = _gc_store()
    gocardless.details.update({A1: _details(IBAN1), A2: _details(IBAN2)})
    gocardless.details.update({B1: _details(new_ibans[0]), B2: _details(new_ibans[1])})
    _linked(store, gocardless, OLD_REQ, [A1, A2])
    for acc in (A1, A2):
        if cached_identities:
            meta = gc._read_meta(store, acc, store.banks()[0], {"name": "x"})
        else:  # cached by an earlier version: no identity
            meta = {"name": "ACME Current", "currency": "EUR"}
        store.cache_account_meta(acc, meta)
    new = _linked(store, gocardless, NEW_REQ, [B1, B2], replaces=OLD_REQ)
    return store, new


def test_a_renewal_keeps_the_ids_the_links_use(gocardless):
    store, new = _renewal(gocardless)
    assert gc._refresh(store) == []
    banks = store.banks()
    assert [b["key"] for b in banks] == [new]  # the old consent is revoked and dropped
    assert banks[0]["account_ids"] == [A2, A1]
    assert banks[0]["api_account_ids"] == {A2: B1, A1: B2}
    assert OLD_REQ not in gocardless.requisitions


def test_a_renewed_account_is_read_by_its_new_id_and_booked_under_the_old_ref(gocardless):
    store, _ = _renewal(gocardless)
    gc._refresh(store)
    gocardless.calls.clear()
    window = gc.fetch_window(store, "2026-11-01", "2026-11-10", A1)
    assert ("GET", f"/accounts/{B2}/transactions/") in gocardless.calls
    assert not any(A1 in p for _, p in gocardless.calls)
    assert [a["provider_account_id"] for a in window.accounts] == [A1]
    assert window.rows[0]["external_refs"][0]["ref"] == f"{A1}:T1"
    assert window.rows[0]["provider_account_id"] == A1


def test_the_next_read_of_the_requisition_keeps_the_old_ids(gocardless):
    store, _ = _renewal(gocardless)
    gc._refresh(store)
    gc._refresh(store)
    assert store.banks()[0]["account_ids"] == [A2, A1]


def test_no_iban_is_ever_stored(gocardless):
    store, _ = _renewal(gocardless)
    gc._refresh(store)
    stored = str(store.get()) + str(store.accounts_meta())
    assert IBAN1 not in stored and IBAN2 not in stored


def test_an_old_account_cached_without_an_identity_is_read_from_the_old_consent(gocardless):
    store, _ = _renewal(gocardless, cached_identities=False)
    gc._refresh(store)
    assert store.banks()[0]["account_ids"] == [A2, A1]


def test_an_account_whose_iban_cannot_be_read_keeps_its_new_id_and_is_named(gocardless):
    store, _ = _renewal(gocardless, new_ibans=(IBAN2, None))
    warnings = gc._refresh(store)
    bank = store.banks()[0]
    assert bank["account_ids"] == [A2, B2]
    assert bank["api_account_ids"] == {A2: B1}
    assert len(warnings) == 1 and "1 account(s)" in warnings[0]


# ── GoCardless counts every read ─────────────────────────────────────────────────────


def _one_bank(gocardless):
    store = _gc_store()
    gocardless.details[A1] = _details(IBAN1)
    _linked(store, gocardless, OLD_REQ, [A1])
    return store


def _listing(store):
    return gc.fetch_window(store, "2026-11-10", "2026-11-10", None, accounts_only=True)


def test_listing_the_accounts_twice_reads_the_bank_once(gocardless):
    store = _one_bank(gocardless)
    first, second = _listing(store), _listing(store)
    assert (gocardless.count("/details/"), gocardless.count("/balances/")) == (1, 1)
    assert first.accounts == second.accounts and first.accounts[0]["balance"] == 5.0


def test_a_listing_reads_again_once_what_is_kept_is_old(gocardless, monkeypatch):
    store = _one_bank(gocardless)
    _listing(store)
    later = gc._now() + gc._LISTING_TTL + timedelta(minutes=1)
    monkeypatch.setattr(gc, "_now", lambda: later)
    _listing(store)
    assert (gocardless.count("/details/"), gocardless.count("/balances/")) == (2, 2)


def test_a_refused_listing_read_shows_the_last_figure(gocardless, monkeypatch):
    store = _one_bank(gocardless)
    _listing(store)
    gocardless.balances[A1] = httpx.Response(429, json={})
    later = gc._now() + gc._LISTING_TTL + timedelta(minutes=1)
    monkeypatch.setattr(gc, "_now", lambda: later)
    assert _listing(store).accounts[0]["balance"] == 5.0


def test_the_syncs_own_balance_read_refreshes_what_a_listing_shows(gocardless):
    store = _one_bank(gocardless)
    _listing(store)
    nine = {"balances": [{"balanceType": "closingBooked", "balanceAmount": {"amount": "9"}}]}
    gocardless.balances[A1] = httpx.Response(200, json=nine)
    gc.fetch_window(store, "2026-11-01", "2026-11-10", A1)
    gocardless.calls.clear()
    window = _listing(store)
    assert window.accounts[0]["balance"] == 9.0 and gocardless.count("/balances/") == 0


def test_a_sync_without_a_balance_says_so_and_stays_complete(gocardless):
    store = _one_bank(gocardless)
    gocardless.balances[A1] = httpx.Response(429, json={})
    window = gc.fetch_window(store, "2026-11-01", "2026-11-10", A1)
    assert window.complete is True and len(window.rows) == 1
    assert any("gave no balance" in w for w in window.warnings)


def test_a_sync_with_a_balance_has_no_warning(gocardless):
    store = _one_bank(gocardless)
    assert gc.fetch_window(store, "2026-11-01", "2026-11-10", A1).warnings == []


# ── GoCardless expiry is dated from the consent ──────────────────────────────────────


def _awaiting_a_date(gocardless, store, accepted):
    gocardless.agreements["AGR-1"] = {"accepted": accepted, "access_valid_for_days": 180}
    gocardless.requisitions[OLD_REQ] = {"status": "LN", "accounts": [A1], "agreement": "AGR-1"}
    return store.add_bank(
        OLD_REQ,
        institution_id="ACME_BANK_GB",
        institution_name="ACME Bank",
        country="GB",
        consent_expires_at="2026-12-01T00:00:00+00:00",
    )


def test_expiry_counts_from_the_users_acceptance(gocardless):
    store = _gc_store()
    key = _awaiting_a_date(gocardless, store, "2026-11-10T09:00:00Z")
    gc._refresh(store)
    bank = store.bank(key)
    assert bank["consent_expires_at"] == "2027-05-09T09:00:00+00:00"
    assert bank["consent_dated"] is True


def test_the_agreement_is_read_once_only(gocardless):
    store = _gc_store()
    _awaiting_a_date(gocardless, store, "2026-11-10T09:00:00Z")
    gc._refresh(store)
    gc._refresh(store)
    assert gocardless.count("/agreements/enduser/AGR-1/") == 1


def test_an_unreadable_agreement_keeps_the_estimate_and_is_tried_again(gocardless):
    store = _gc_store()
    key = _awaiting_a_date(gocardless, store, None)
    gc._refresh(store)
    assert store.bank(key)["consent_expires_at"] == "2026-12-01T00:00:00+00:00"
    assert store.bank(key)["consent_dated"] is False


# ── a multi-currency bank: pots share one IBAN ───────────────────────────────────────


def _pots(gocardless, *, new_order=("USD", "EUR"), same_currency=False):
    """Old pots A1 (EUR) and A2 (USD) on ONE IBAN; the renewal lists them as B1, B2 in
    ``new_order``."""
    store = _gc_store()
    currencies = {A1: "EUR", A2: "USD"}
    for acc, cur in currencies.items():
        gocardless.details[acc] = {**_details(IBAN1), "currency": cur}
    _linked(store, gocardless, OLD_REQ, [A1, A2])
    for acc in (A1, A2):
        store.cache_account_meta(acc, gc._read_meta(store, acc, store.banks()[0], {"name": "x"}))
    for acc, cur in zip((B1, B2), new_order):
        gocardless.details[acc] = {**_details(IBAN1), "currency": "EUR" if same_currency else cur}
    _linked(store, gocardless, NEW_REQ, [B1, B2], replaces=OLD_REQ)
    return store


def test_pots_sharing_an_iban_are_matched_by_currency(gocardless):
    store = _pots(gocardless)
    assert gc._refresh(store) == []
    bank = store.banks()[0]
    assert bank["account_ids"] == [A2, A1]  # B1 is the USD pot, B2 the EUR one
    assert bank["api_account_ids"] == {A2: B1, A1: B2}


def test_pots_that_cannot_be_told_apart_are_not_carried(gocardless):
    store = _pots(gocardless, same_currency=True)
    warnings = gc._refresh(store)
    bank = store.banks()[0]
    assert bank["account_ids"] == [B1, B2] and bank["api_account_ids"] == {}
    assert len(warnings) == 1 and "2 account(s)" in warnings[0]
