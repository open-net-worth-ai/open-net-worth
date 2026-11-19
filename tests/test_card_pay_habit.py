"""A card bill reminder lands before the owner's usual payment day, never later than a week out."""

from datetime import date

from backend.services.card_pay_habit import payment_habit

CARD = "acc_card"


def _stmt(statement_date: str, due: str, account_id: str = CARD) -> dict:
    return {
        "account_id": account_id,
        "import_meta": {"statement_date": statement_date, "payment_due_date": due},
    }


def _pay(day: str, to: str = CARD, **extra) -> dict:
    return {"kind": "transfer", "to_account_id": to, "date": day, "to_amount": "100", **extra}


RECORDS = [
    _stmt("2026-07-23", "2026-08-17"),
    _stmt("2026-08-23", "2026-09-17"),
    _stmt("2026-09-23", "2026-10-19"),
    _stmt("2026-10-23", "2026-11-17"),  # not yet due — not part of the habit
]


def test_an_early_payer_is_reminded_earlier_than_a_week():
    txns = [_pay("2026-08-08"), _pay("2026-09-08"), _pay("2026-10-06")]
    habit = payment_habit(CARD, date(2026, 11, 12), records=RECORDS, txns=txns)
    assert habit["lead_days_last"] == 13
    assert habit["lead_days_avg"] == 11  # ceil((9 + 9 + 13) / 3)
    assert habit["remind_days_before_due"] == 13


def test_a_late_payer_still_gets_a_week():
    txns = [_pay("2026-08-15"), _pay("2026-09-16"), _pay("2026-10-20")]
    habit = payment_habit(CARD, date(2026, 11, 12), records=RECORDS, txns=txns)
    assert habit["lead_days_last"] == 0  # paid a day late — counts as zero lead
    assert habit["remind_days_before_due"] == 7


def test_no_history_falls_back_to_a_week():
    habit = payment_habit(CARD, date(2026, 11, 12), records=[], txns=[])
    assert habit == {
        "lead_days_last": None,
        "lead_days_avg": None,
        "cycles_read": 0,
        "remind_days_before_due": 7,
    }


def test_other_cards_deleted_rows_and_non_transfers_are_ignored():
    txns = [
        _pay("2026-10-01", to="acc_other"),
        _pay("2026-10-02", deleted=True),
        {"kind": "expense", "from_account_id": CARD, "date": "2026-10-03"},
        _pay("2026-10-15"),
    ]
    records = [_stmt("2026-09-23", "2026-10-19"), _stmt("2026-09-23", "2026-10-19", "acc_other")]
    habit = payment_habit(CARD, date(2026, 11, 12), records=records, txns=txns)
    assert habit["lead_days_last"] == 4
    assert habit["cycles_read"] == 1
