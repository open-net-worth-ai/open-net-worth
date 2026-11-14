"""A row a bank sends with no id is keyed by a hash of its content, text included — and that
key must outlive every release, or each upgrade books the same rows again.

0.1.50 improved Enable Banking's description, and the hash read the description: every row
booked under 0.1.49 without an ``entry_reference`` came back as new and was booked twice.
The ref text is now frozen apart from the description (``_ref_text``), and a row still carries
the ref 0.1.49 gave it (``ref_aliases``), which the fetch checks like its own ref."""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import connector_fetch, connector_snapshots, connector_window
from backend.services import ledger as ledger_service
from backend.services.aggregation import enable_banking as eb_svc
from backend.services.aggregation import gocardless as gc_svc
from backend.services.aggregation import lunch_money as lm_svc
from backend.services.aggregation import plaid as plaid_svc
from backend.services.aggregation import simplefin as sf_svc
from backend.services.aggregation.providers import PROVIDERS, FetchWindow, derived_ref
from backend.services.bank_data_sync import runner, settings
from tests.bank_sync_fakes import days_ago

UID = "uid-acme-1"
ON = days_ago(3)


@pytest.fixture
def bank(db_isolated, monkeypatch):
    from backend.db import EnableBankingConfigStore

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "EUR")
    monkeypatch.setattr(EnableBankingConfigStore, "is_configured", lambda self: True)
    EnableBankingConfigStore().update({"enabled": True})
    feed: dict = {"txns": []}

    def fetch_window(store, since, before, account, *, accounts_only=False):
        accounts = [{"provider_account_id": UID, "name": "ACME Giro", "currency": "EUR"}]
        if accounts_only:
            return FetchWindow(accounts=accounts)
        rows = [eb_svc._txn_to_row(t, UID, "EUR") for t in feed["txns"]]
        return FetchWindow(accounts=accounts, rows=[r for r in rows if r])

    monkeypatch.setattr(eb_svc, "fetch_window", fetch_window)
    feed["account"] = accounts_service.create_account(
        {
            "name": "ACME Giro",
            "kind": "asset",
            "type": "cash",
            "currency": "EUR",
            "initial_balance": 500,
            "external_ids": [{"source": "enable_banking", "ref": UID}],
        }
    )["id"]
    return feed


def _txn(amount: str = "12.50", indicator: str = "DBIT", **fields) -> dict:
    return {
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "credit_debit_indicator": indicator,
        "booking_date": ON,
        "status": "BOOK",
        **fields,
    }


def _book(account_id: str, ref: str, *, on: str = ON, amount: float = 12.5) -> str:
    return ledger_service.create_transaction(
        {
            "date": on,
            "kind": "expense",
            "description": "ACME booked earlier",
            "from_account_id": account_id,
            "from_amount": amount,
            "from_currency": "EUR",
            "source": "ai",
            "external_refs": [{"source": "enable_banking", "ref": ref}],
        }
    )["id"]


def _hash(text: str, amount: float = -12.5) -> str:
    return derived_ref(UID, ON, amount, "EUR", text)


# Each case: what the bank sends, the ref 0.1.49 booked it under, the ref 0.1.50 did. The
# texts are written out here, never computed by the code under test.
CASES = {
    "spaced_remittance": (
        {"remittance_information": ["ACME  GROCERY\nSTORE ", "  ", "REF 42"]},
        _hash("ACME  GROCERY\nSTORE     REF 42"),
        _hash("ACME GROCERY STORE REF 42"),
    ),
    "transaction_code_only": (
        {"bank_transaction_code": {"description": "Card payment"}},
        _hash("Enable Banking"),
        _hash("Card payment"),
    ),
    "reference_number_only": (
        {"reference_number": " RF18 ACME 0001 "},
        _hash("Enable Banking"),
        _hash("RF18 ACME 0001"),
    ),
    "nothing_at_all": ({}, _hash("Enable Banking"), _hash("Enable Banking")),
    "transaction_id_handle": (
        {"transaction_id": "tx-handle-7", "remittance_information": ["ACME  CAFE"]},
        f"{UID}:tx-handle-7",
        _hash("ACME CAFE"),
    ),
}


@pytest.mark.parametrize("case", CASES)
def test_the_ref_since_0150_is_frozen(case):
    fields, _, ref_0150 = CASES[case]
    row = eb_svc._txn_to_row(_txn(**fields), UID, "EUR")
    assert row["external_refs"][0]["ref"] == ref_0150


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("version", ["0.1.49", "0.1.50"])
@pytest.mark.parametrize("read", [connector_fetch.fetch, connector_fetch.fetch_all])
def test_a_row_booked_under_either_versions_ref_is_already_booked(bank, case, version, read):
    fields, ref_0149, ref_0150 = CASES[case]
    _book(bank["account"], ref_0149 if version == "0.1.49" else ref_0150)
    bank["txns"] = [_txn(**fields)]

    result = read("enable_banking")

    assert result["status"] == "ok"
    assert result["counts"]["already_booked"] == 1
    assert result["transactions"] == []


@pytest.mark.parametrize("read", [connector_fetch.fetch, connector_fetch.fetch_all])
def test_include_booked_names_the_transaction_found_by_the_old_ref(bank, read):
    fields, ref_0149, _ = CASES["spaced_remittance"]
    txn_id = _book(bank["account"], ref_0149)
    bank["txns"] = [_txn(**fields)]

    (row,) = read("enable_banking", include_booked=True)["transactions"]

    assert row["booked_txn_id"] == txn_id
    assert "ref_aliases" not in row


def test_the_description_is_free_to_change(bank, monkeypatch):
    fields, _, ref_0150 = CASES["transaction_code_only"]
    monkeypatch.setattr(eb_svc, "_txn_description", lambda t: "Something better")
    row = eb_svc._txn_to_row(_txn(**fields), UID, "EUR")
    assert row["description"] == "Something better"
    assert row["external_refs"][0]["ref"] == ref_0150


def test_a_stale_handle_never_hides_a_new_transaction(bank):
    # Enable Banking may hand an old transaction_id to another row on a later listing.
    _book(bank["account"], f"{UID}:tx-handle-7", on=days_ago(40), amount=99.0)
    bank["txns"] = [_txn(transaction_id="tx-handle-7", remittance_information=["ACME CAFE"])]

    result = connector_fetch.fetch("enable_banking")

    assert result["counts"]["already_booked"] == 0
    assert result["counts"]["returned"] == 1


def test_identical_rows_keep_the_numbers_0149_gave_them(bank):
    fields, ref_0149, _ = CASES["transaction_code_only"]
    _book(bank["account"], ref_0149)
    _book(bank["account"], f"{ref_0149}#2")
    bank["txns"] = [_txn(**fields), _txn(**fields), _txn(**fields)]

    result = connector_fetch.fetch("enable_banking")

    assert result["counts"]["already_booked"] == 2
    assert result["counts"]["returned"] == 1


def test_numbering_counts_aliases_among_themselves():
    rows = [
        {"external_refs": [{"ref": "derived:new"}], "ref_aliases": ["derived:old"]},
        {"external_refs": [{"ref": "derived:new"}], "ref_aliases": ["derived:old"]},
        {"external_refs": [{"ref": "derived:other"}], "ref_aliases": ["acme:handle"]},
    ]
    connector_window.number_repeated_refs(rows)
    assert [r["ref_aliases"] for r in rows] == [
        ["derived:old"],
        ["derived:old#2"],
        ["acme:handle"],
    ]


def test_a_row_keyed_anew_is_not_reported_vanished_under_its_old_ref(bank, monkeypatch):
    fields, ref_0149, _ = CASES["spaced_remittance"]
    seen: dict = {}

    def note_missing(source, since, before, seen_keys, account=None):
        seen["keys"] = set(seen_keys)
        return []

    monkeypatch.setattr(connector_snapshots, "note_missing", note_missing)
    bank["txns"] = [_txn(**fields)]
    connector_fetch.fetch("enable_banking")
    assert ref_0149 in seen["keys"]


@pytest.mark.flags(bank_data_sync=True)
def test_the_bank_sync_does_not_book_a_0149_row_again(bank):
    fields, ref_0149, _ = CASES["spaced_remittance"]
    _book(bank["account"], ref_0149)
    bank["txns"] = [_txn(**fields)]
    settings.save("enable_banking", mode="data")

    result = runner.run_now(
        PROVIDERS["enable_banking"], "manual", runner.opted_accounts("enable_banking")
    )

    assert result["counts"]["booked"] == 0
    assert result["counts"]["already_booked"] == 1


# ── the other connectors that hash text: their ref text is pinned the same way ──────────


def test_other_connectors_keep_their_ref_text():
    gc = gc_svc._txn_to_row(
        {"transactionAmount": {"amount": "-4.20", "currency": "EUR"}, "bookingDate": ON},
        "gc-1",
        "EUR",
        pending=False,
    )
    assert gc["external_refs"][0]["ref"] == derived_ref("gc-1", ON, -4.2, "EUR", "GoCardless")

    sf = sf_svc._txn_to_row(
        {"amount": "-4.20", "posted": 1767225600, "payee": " ACME "}, "sf-1", "USD"
    )
    assert sf["external_refs"][0]["ref"] == derived_ref("sf-1", sf["date"], -4.2, "USD", "ACME")

    lm = lm_svc._txn_to_row({"amount": "4.20", "date": ON, "original_name": "ACME"}, "lm-1", "USD")
    assert lm["external_refs"][0]["ref"] == derived_ref("lm-1", ON, -4.2, "USD", "ACME")

    pl = plaid_svc._txn_to_row(
        {"amount": 4.2, "date": ON, "iso_currency_code": "USD"}, {"currency": "USD"}, "pl-1"
    )
    assert pl["external_refs"][0]["ref"] == derived_ref("pl-1", ON, -4.2, "USD", "Plaid")
