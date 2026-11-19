"""A bank row the bank left without wording books with an EMPTY description, never the
connector's name as a placeholder — and a row with no id of its own keeps EXACTLY the ref it
had before: the ref is still keyed on the old placeholder text, so a re-fetch never sees an
already-booked row as new."""

from backend.services.aggregation import enable_banking, gocardless, lunch_money, plaid, simplefin
from backend.services.aggregation.providers import derived_ref


def test_enable_banking():
    row = enable_banking._txn_to_row(
        {
            "booking_date": "2026-11-12",
            "status": "BOOK",
            "transaction_amount": {"amount": "12.00", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
        },
        "EB-CHK",
        "EUR",
    )
    assert row["description"] == ""
    assert row["external_refs"][0]["ref"] == derived_ref(
        "EB-CHK", "2026-11-12", row["amount"], "EUR", "Enable Banking"
    )


def test_gocardless():
    row = gocardless._txn_to_row(
        {"bookingDate": "2026-11-12", "transactionAmount": {"amount": "-12.00", "currency": "EUR"}},
        "GC-CHK",
        "EUR",
        pending=False,
    )
    assert row["description"] == ""
    assert row["external_refs"][0]["ref"] == derived_ref(
        "GC-CHK", "2026-11-12", row["amount"], "EUR", "GoCardless"
    )


def test_simplefin():
    row = simplefin._txn_to_row({"posted": 1762905600, "amount": "-12.00"}, "SF-CHK", "USD")
    assert row["description"] == ""
    assert row["external_refs"][0]["ref"] == derived_ref(
        "SF-CHK", row["date"], row["amount"], "USD", "SimpleFIN"
    )


def test_lunch_money():
    row = lunch_money._txn_to_row(
        {
            "date": "2026-11-12",
            "amount": "5.00",
            "is_group_parent": False,
            "is_split_parent": False,
        },
        "plaid:1",
        "USD",
    )
    assert row["description"] == ""
    assert row["external_refs"][0]["ref"] == derived_ref(
        "plaid:1", "2026-11-12", row["amount"], "USD", "Lunch Money"
    )


def test_plaid():
    assert plaid._txn_description({}) == ""
