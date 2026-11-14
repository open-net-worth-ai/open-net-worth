"""Bank data sync — expiry and re-link edge cases (review 2026-11-15, ADR 2026-11-13 Amendments
12+). Each section names the case it pins. Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.core import headless_turn
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import budget, pending, relink, runner, settings
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
    fake.account = linked_account("ACME Current", ACC_MAIN)
    settings.save("gocardless", mode="data")
    yield fake
    headless_turn.register_runner(None)


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


def _forget_reads() -> None:
    """A book linked before re-links were remembered, or whose bank failed every read since."""
    budget.mutate("gocardless", lambda e: e.pop("read_as", None))


# ── A posted row that names a pending one we never booked is not some other charge ─────────


def _fare(txn_id: str, on: str) -> dict:
    return {"txn_id": txn_id, "account_id": "acc_acme", "date": on, "amount": -2.90}


def _posted(on: str, supersedes: str | None) -> dict:
    return {
        "linked_account_id": "acc_acme",
        "date": on,
        "amount": -2.90,
        "supersedes_ref": supersedes,
    }


def test_a_named_pending_row_we_never_booked_matches_nothing():
    # Fare A is booked pending; fare B posts naming its own pending row, which we never saw.
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:B", _posted(days_ago(0), "acme:pB")) is None


def test_a_named_pending_row_we_booked_is_the_one_it_settles():
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:A", _posted(days_ago(0), "acme:pA")) == "acme:pA"


def test_a_row_that_names_nothing_still_settles_by_amount():
    # Enable Banking never names the pending row: the same amount a few days on is the one.
    entries = {"acme:pA": _fare("txn_a", days_ago(1))}
    assert pending._match(entries, "acme:A", _posted(days_ago(0), None)) == "acme:pA"


# ── A re-link on a book with nothing remembered still matches its history ───────────────────

_HISTORY = [(-12.50, 6, "ACME CAFE"), (-40.00, 4, "ACME FUEL")]


def test_a_relink_with_nothing_remembered_matches_instead_of_booking_again(bank):
    bank.booked[ACC_MAIN] = [
        txn(a, days_ago(d), w, tid=f"old-{i}") for i, (a, d, w) in enumerate(_HISTORY)
    ]
    _run()
    _forget_reads()

    accounts_service.update_account(
        bank.account, {"external_ids": [{"source": "gocardless", "ref": ACC_SAVER}]}
    )
    bank.booked[ACC_SAVER] = [
        txn(a, days_ago(d), w, tid=f"new-{i}") for i, (a, d, w) in enumerate(_HISTORY)
    ] + [txn(-3.00, days_ago(1), "ACME KIOSK", tid="new-k")]
    result = _run()

    assert result["counts"]["relinked"] == 2
    assert result["counts"]["booked"] == 1
    assert len(_live(bank.account)) == 3, "the re-linked history was booked a second time"


def test_an_ordinary_read_with_nothing_remembered_is_not_a_relink(bank):
    bank.booked[ACC_MAIN] = [txn(-12.50, days_ago(6), "ACME CAFE", tid="a1")]
    _run()
    _forget_reads()
    bank.booked[ACC_MAIN].append(txn(-3.00, days_ago(0), "ACME KIOSK", tid="a2"))
    result = _run()
    assert result["counts"]["relinked"] == 0
    assert result["counts"]["booked"] == 1
    assert not relink.looks_relinked("gocardless", bank.account, [], days_ago(30))


# ── A bank refusing one account is that bank's access, never the keys stopping every bank ─────


def test_gocardless_a_refusal_after_a_fresh_token_is_the_banks_access(monkeypatch):
    from backend.core.errors import ExternalServiceError

    monkeypatch.setattr(gocardless_svc, "_bearer", lambda store, fresh=False: "tok")

    def refuse(method, path, token=None, **kw):
        raise ExternalServiceError("GoCardless HTTP 401")

    monkeypatch.setattr(gocardless_svc, "_request", refuse)
    with pytest.raises(ExternalServiceError) as err:
        gocardless_svc._authed(None, "GET", "/accounts/acme-1/transactions/")
    assert err.value.context.get("bank_access") is True
    assert gocardless_svc.classify_error(err.value) == "reconnect_needed"

    # Anything that isn't an account read is still the keys.
    with pytest.raises(ExternalServiceError) as keys:
        gocardless_svc._authed(None, "GET", "/institutions/")
    assert gocardless_svc.classify_error(keys.value) == "auth_error"


def test_one_banks_refused_account_never_stops_the_other_bank(bank):
    from backend.core.errors import ExternalServiceError

    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_SAVER] = [txn(-4.00, days_ago(1), "ACME SHOP", tid="s1")]
    bank.fail_accounts[ACC_MAIN] = ExternalServiceError(
        "GoCardless HTTP 401 — the bank refused access", bank_access=True
    )
    result = _run()
    assert result["counts"]["booked"] == 1
    assert len(_live(saver)) == 1, "the other bank's account was skipped"
    assert budget.state("gocardless")["account_reads"][bank.account]["status"] == "reconnect_needed"


def _eb_get(monkeypatch, status: int, body: dict):
    import httpx

    from backend.services.aggregation import enable_banking as eb

    def get(url, **kw):
        request = httpx.Request("GET", url)
        response = httpx.Response(status, json=body, request=request)
        return response

    monkeypatch.setattr(eb.httpx, "get", get)
    return eb


@pytest.mark.parametrize("code", ["EXPIRED_SESSION", "CLOSED_SESSION", "REVOKED_SESSION"])
def test_enable_banking_an_ended_session_is_the_banks_consent(monkeypatch, code):
    eb = _eb_get(monkeypatch, 401, {"error": code, "message": "ACME"})
    with pytest.raises(eb.ConsentEndedError) as err:
        eb._safe_get("tok", "/accounts/acme-1/transactions", {})
    assert code in str(err.value)
    assert eb.classify_error(err.value) == "reconnect_needed"


def test_enable_banking_a_plain_401_is_still_the_app(monkeypatch):
    from backend.core.errors import ExternalServiceError

    eb = _eb_get(monkeypatch, 401, {"message": "ACME no code"})
    with pytest.raises(ExternalServiceError) as err:
        eb._safe_get("tok", "/sessions/acme", {})
    assert not isinstance(err.value, eb.ConsentEndedError)
    assert eb.classify_error(err.value) == "auth_error"


def test_enable_banking_one_refused_account_is_retried_not_a_stop():
    from backend.core.errors import ExternalServiceError
    from backend.services.aggregation import enable_banking as eb

    refused = ExternalServiceError("Enable Banking HTTP 403 — refused", account_refused=True)
    assert eb.classify_error(refused) == "connect_error"


# ── GoCardless: the bank's transaction id may change or repeat ───────────────────────────────


def test_a_bank_id_that_changes_never_books_the_row_twice(bank):
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(2), "ACME BAKERY", tid="bank-1", internalTransactionId="gc-1")
    ]
    _run()
    bank.booked[ACC_MAIN] = [
        txn(
            -5.00, days_ago(2), "ACME BAKERY", tid="bank-1-renumbered", internalTransactionId="gc-1"
        )
    ]
    result = _run()
    assert result["counts"]["booked"] == 0
    assert len(_live(bank.account)) == 1


def test_a_bank_id_that_repeats_never_hides_a_real_transaction(bank):
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(2), "ACME BAKERY", tid="bank-1", internalTransactionId="gc-1")
    ]
    _run()
    bank.booked[ACC_MAIN].append(
        txn(-7.00, days_ago(1), "ACME BUTCHER", tid="bank-1", internalTransactionId="gc-2")
    )
    result = _run()
    assert result["counts"]["booked"] == 1
    assert len(_live(bank.account)) == 2


def test_a_row_booked_under_the_bank_id_is_recognised_and_moves_to_the_stable_id(bank):
    # Booked before the internal id came first: the bank's id was the row's id then.
    bank.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME BAKERY", tid="bank-1")]
    _run()
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(2), "ACME BAKERY", tid="bank-1", internalTransactionId="gc-1")
    ]
    assert _run()["counts"]["booked"] == 0
    # Filed under the stable id now, so a later change of the bank's id is harmless too.
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(2), "ACME BAKERY", tid="bank-1b", internalTransactionId="gc-1")
    ]
    assert _run()["counts"]["booked"] == 0
    assert len(_live(bank.account)) == 1


def test_a_pending_row_booked_under_the_bank_id_still_settles_in_place(bank):
    bank.pending[ACC_MAIN] = [txn(-9.99, days_ago(1), "ACME STREAM", tid="bank-p")]
    _run()
    [booked] = _live(bank.account)
    bank.pending[ACC_MAIN] = []
    bank.booked[ACC_MAIN] = [
        txn(-9.99, days_ago(0), "ACME STREAM", tid="bank-p", internalTransactionId="gc-9")
    ]
    result = _run()
    assert result["counts"]["settled"] == 1
    assert [t["id"] for t in _live(bank.account)] == [booked["id"]]


# ── A second login at the same bank keeps the first ─────────────────────────────────────────


def _eb_with_old_login(monkeypatch, old_accounts: list[dict]):
    from backend.db.enable_banking_config_store import EnableBankingConfigStore
    from backend.services.aggregation import enable_banking as eb

    store = EnableBankingConfigStore()
    store.add_bank(
        "SESS-OLD",
        aspsp="ACME Bank",
        country="FI",
        valid_until=None,
        linked_at="2026-11-01T00:00:00+00:00",
        account_ids=["UID-OLD"],
    )
    monkeypatch.setattr(
        eb, "session_accounts_data", lambda token, sid: old_accounts if sid == "SESS-OLD" else []
    )
    return eb, store


def test_enable_banking_a_second_login_is_not_a_renewal(db_isolated, monkeypatch):
    eb, store = _eb_with_old_login(monkeypatch, [{"uid": "UID-OLD", "identification_hash": "H1"}])
    new = [{"uid": "UID-NEW", "identification_hash": "H2"}]
    assert eb._renewed_sessions("tok", store, "SESS-NEW", new, "ACME Bank", "FI") == set()


def test_enable_banking_the_same_accounts_again_is_a_renewal(db_isolated, monkeypatch):
    eb, store = _eb_with_old_login(monkeypatch, [{"uid": "UID-OLD", "identification_hash": "H1"}])
    new = [{"uid": "UID-NEW", "identification_hash": "H1"}]
    assert eb._renewed_sessions("tok", store, "SESS-NEW", new, "ACME Bank", "FI") == {"SESS-OLD"}


def test_enable_banking_logins_that_cannot_be_compared_renew_as_before(db_isolated, monkeypatch):
    eb, store = _eb_with_old_login(monkeypatch, [])
    new = [{"uid": "UID-NEW"}]
    assert eb._renewed_sessions("tok", store, "SESS-NEW", new, "ACME Bank", "FI") == {"SESS-OLD"}


def test_enable_banking_add_bank_keeps_a_login_it_does_not_renew(db_isolated):
    from backend.db.enable_banking_config_store import EnableBankingConfigStore

    store = EnableBankingConfigStore()
    kw = {"aspsp": "ACME Bank", "country": "FI", "valid_until": None, "linked_at": "2026-11-01"}
    store.add_bank("SESS-1", account_ids=["U1"], **kw)
    _, replaced = store.add_bank("SESS-2", account_ids=["U2"], renews=set(), **kw)
    assert replaced == []
    assert [b["session_id"] for b in store.banks()] == ["SESS-1", "SESS-2"]
