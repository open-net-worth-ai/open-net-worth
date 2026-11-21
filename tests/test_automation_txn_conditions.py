"""Field conditions on a transaction trigger: {field, op, value}, every one must hold."""

from __future__ import annotations

import pytest

from backend.services.automations.events import _matches
from backend.services.automations.schedules import validate_trigger

GRAB = {
    "payee_id": "pay_grab",
    "description": "GRAB* A-1234 SINGAPORE",
    "memo": "Ride to the office",
    "from_account_id": "acc_card",
    "amount": 18.5,
}


def _trigger(*conditions: dict) -> dict:
    return validate_trigger(
        {
            "kind": "event",
            "event": "txn",
            "when": {"conditions": list(conditions)},
            "limit": "each",
        }
    )


def _fires(payload: dict, *conditions: dict) -> bool:
    return _matches("txn", _trigger(*conditions)["when"], payload)


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"field": "payee", "op": "is", "value": "pay_grab"}, True),
        ({"field": "payee", "op": "is", "value": "pay_uber"}, False),
        ({"field": "payee", "op": "is_not", "value": "pay_uber"}, True),
        ({"field": "description", "op": "contains", "value": "grab"}, True),
        ({"field": "description", "op": "contains", "value": "uber, grab"}, True),
        ({"field": "description", "op": "contains", "value": "uber, gojek"}, False),
        ({"field": "description", "op": "not_contains", "value": "uber, gojek"}, True),
        ({"field": "description", "op": "not_contains", "value": "singapore"}, False),
        ({"field": "description", "op": "starts_with", "value": "grab*"}, True),
        ({"field": "description", "op": "starts_with", "value": "a-1234"}, False),
        ({"field": "description", "op": "regex", "value": r"^grab\* a-\d{4}"}, True),
        ({"field": "description", "op": "regex", "value": r"\buber\b"}, False),
        ({"field": "memo", "op": "contains", "value": "office"}, True),
        ({"field": "memo", "op": "contains", "value": "grab"}, False),
        ({"field": "memo", "op": "starts_with", "value": "ride"}, True),
        ({"field": "memo", "op": "regex", "value": "office$"}, True),
    ],
)
def test_each_field_and_operation(condition, expected):
    assert _fires(GRAB, condition) is expected


def test_conditions_combine_with_and():
    payee = {"field": "payee", "op": "is", "value": "pay_grab"}
    assert _fires(GRAB, payee, {"field": "memo", "op": "contains", "value": "office"})
    assert not _fires(GRAB, payee, {"field": "memo", "op": "contains", "value": "airport"})


def test_conditions_sit_beside_the_other_filters():
    when = validate_trigger(
        {
            "kind": "event",
            "event": "txn",
            "when": {
                "direction": "out",
                "over": 50,
                "conditions": [{"field": "payee", "op": "is", "value": "pay_grab"}],
            },
            "limit": "each",
        }
    )["when"]
    assert not _matches("txn", when, GRAB)  # 18.50 is not over 50
    assert _matches("txn", when, {**GRAB, "amount": 75})


def test_a_comma_list_is_stored_as_phrases():
    when = _trigger({"field": "description", "op": "contains", "value": " grabfood, ubereats ,"})
    assert when["when"]["conditions"][0]["value"] == ["grabfood", "ubereats"]


def test_an_empty_condition_list_is_dropped():
    assert "conditions" not in _trigger()["when"]


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "category", "op": "is", "value": "x"},
        {"field": "payee", "op": "contains", "value": "grab"},
        {"field": "description", "op": "is", "value": "grab"},
        {"field": "description", "op": "contains", "value": " , "},
        {"field": "memo", "op": "starts_with", "value": ""},
        {"field": "description", "op": "regex", "value": "(unclosed"},
        {"field": "description", "op": "regex", "value": "a" * 201},
        {"field": "description", "op": "contains", "value": "x", "extra": 1},
    ],
)
def test_bad_conditions_are_refused(condition):
    with pytest.raises(ValueError):
        _trigger(condition)


def test_the_old_contains_box_still_works():
    when = validate_trigger(
        {"kind": "event", "event": "txn", "when": {"contains": "office"}, "limit": "each"}
    )["when"]
    assert _matches("txn", when, GRAB)


# A USD 100 charge on a USD card, worth SGD 135 in the (SGD) base currency.
USD_CHARGE = {
    "from_account_id": "acc_usd",
    "from_amount": 100.0,
    "from_currency": "USD",
    "amount": 100.0,
    "currency": "USD",
    "base_amount": 135.0,
    "base_currency": "SGD",
}


def _txn_when(**when) -> dict:
    return validate_trigger({"kind": "event", "event": "txn", "when": when, "limit": "each"})[
        "when"
    ]


def test_an_amount_on_one_account_is_in_that_accounts_currency():
    # "over 120 on my USD card" means USD 120 — the charge is USD 100, so it does not fire,
    # though it is SGD 135 in the base currency.
    assert not _matches("txn", _txn_when(account="acc_usd", over=120), USD_CHARGE)
    assert _matches("txn", _txn_when(account="acc_usd", over=90), USD_CHARGE)


def test_an_amount_on_any_account_is_in_the_base_currency():
    assert _matches("txn", _txn_when(over=120), USD_CHARGE)  # SGD 135
    assert not _matches("txn", _txn_when(over=150), USD_CHARGE)


# A SGD 1,000 → USD 740 transfer from the bank to the broker.
TRANSFER = {
    "kind": "transfer",
    "from_account_id": "acc_bank",
    "from_amount": 1000.0,
    "from_currency": "SGD",
    "to_account_id": "acc_broker",
    "to_amount": 740.0,
    "to_currency": "USD",
    "base_amount": 1000.0,
    "base_currency": "SGD",
}


def _c(field: str, op: str, value: object) -> dict:
    return {"field": field, "op": op, "value": value}


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        # Account: the transaction touches it, on either side.
        ([_c("account", "is", "acc_bank")], True),
        ([_c("account", "is", "acc_broker")], True),
        ([_c("account", "is", "acc_card")], False),
        ([_c("account", "is_not", "acc_card")], True),
        ([_c("account", "is_not", "acc_broker")], False),
        # Kind.
        ([_c("kind", "is", "transfer")], True),
        ([_c("kind", "is", "expense")], False),
        ([_c("kind", "is_not", "investment")], True),
        # Amount on one account is that account's own leg, in its currency.
        ([_c("account", "is", "acc_broker"), _c("amount", "over", 700)], True),  # USD 740
        ([_c("account", "is", "acc_broker"), _c("amount", "over", 800)], False),
        ([_c("account", "is", "acc_bank"), _c("amount", "over", 800)], True),  # SGD 1,000
        # On any account, the base currency.
        ([_c("amount", "over", 999)], True),
        ([_c("amount", "under", 999)], False),
        # A transfer has no payee: never "is Grab", and never "is not Grab" either.
        ([_c("payee", "is", "pay_grab")], False),
        ([_c("payee", "is_not", "pay_grab")], False),
    ],
)
def test_account_kind_amount_and_payee_on_a_transfer(conditions, expected):
    assert _fires(TRANSFER, *conditions) is expected


def test_amounts_are_absolute_whatever_the_sign():
    refund = {**GRAB, "amount": -18.5, "base_amount": -18.5}
    assert _fires(refund, _c("amount", "over", 10))


@pytest.mark.parametrize(
    "condition",
    [
        _c("kind", "is", "refund"),
        _c("amount", "over", -5),
        _c("amount", "under", "lots"),
        _c("account", "contains", "acc_bank"),
        _c("amount", "contains", 5),
    ],
)
def test_bad_account_kind_or_amount_conditions_are_refused(condition):
    with pytest.raises(ValueError):
        _trigger(condition)


# The ledger gives an investment row exactly one cash leg: out of the account for a buy,
# into it for a sale.
BUY = {"kind": "investment", "from_account_id": "acc_broker", "from_amount": 300.0}
SALE = {"kind": "investment", "to_account_id": "acc_broker", "to_amount": 700.0}


@pytest.mark.parametrize(
    ("payload", "kind", "op", "expected"),
    [
        (BUY, "investment", "is", True),
        (SALE, "investment", "is", True),
        (BUY, "investment_buy", "is", True),
        (SALE, "investment_buy", "is", False),
        (SALE, "investment_sale", "is", True),
        (BUY, "investment_sale", "is", False),
        (BUY, "investment_sale", "is_not", True),
        (SALE, "investment_sale", "is_not", False),
        # Money out of / into an account is not a buy / sale unless it is an investment.
        (GRAB, "investment_buy", "is", False),
        (TRANSFER, "investment_buy", "is", False),
        (TRANSFER, "investment_sale", "is", False),
        (TRANSFER, "investment_sale", "is_not", True),
    ],
)
def test_an_investment_buy_or_sale_is_told_by_its_cash_leg(payload, kind, op, expected):
    assert _fires(payload, _c("kind", op, kind)) is expected


# "Is any of" / "is none of": Account, Kind and Payee take a list of values.
UBER = {**GRAB, "payee_id": "pay_uber"}
GOJEK = {**GRAB, "payee_id": "pay_gojek"}


@pytest.mark.parametrize(
    ("payload", "condition", "expected"),
    [
        (GRAB, _c("payee", "is", ["pay_grab", "pay_uber"]), True),
        (UBER, _c("payee", "is", ["pay_grab", "pay_uber"]), True),
        (GOJEK, _c("payee", "is", ["pay_grab", "pay_uber"]), False),
        (GRAB, _c("payee", "is_not", ["pay_grab", "pay_uber"]), False),
        (GOJEK, _c("payee", "is_not", ["pay_grab", "pay_uber"]), True),
        # "Is none of" still needs a payee: a transfer is never "not Grab or Uber".
        (TRANSFER, _c("payee", "is_not", ["pay_grab", "pay_uber"]), False),
        (TRANSFER, _c("kind", "is", ["expense", "transfer"]), True),
        (TRANSFER, _c("kind", "is", ["expense", "income"]), False),
        (TRANSFER, _c("kind", "is_not", ["expense", "transfer"]), False),
        (TRANSFER, _c("kind", "is_not", ["expense", "income"]), True),
        (BUY, _c("kind", "is", ["investment_buy", "expense"]), True),
        (SALE, _c("kind", "is", ["investment_buy", "expense"]), False),
        (TRANSFER, _c("account", "is", ["acc_card", "acc_broker"]), True),
        (TRANSFER, _c("account", "is", ["acc_card", "acc_other"]), False),
        (TRANSFER, _c("account", "is_not", ["acc_card", "acc_bank"]), False),
        (TRANSFER, _c("account", "is_not", ["acc_card", "acc_other"]), True),
    ],
)
def test_a_list_is_any_of_for_is_and_none_of_for_is_not(payload, condition, expected):
    assert _fires(payload, condition) is expected


def test_two_account_conditions_match_a_transfer_between_them():
    both = (_c("account", "is", "acc_bank"), _c("account", "is", "acc_broker"))
    assert _fires(TRANSFER, *both)
    assert not _fires(GRAB, *both)


def test_a_list_is_trimmed_deduped_and_one_value_is_stored_plain():
    stored = _trigger(
        _c("payee", "is", [" pay_grab ", "pay_uber", "pay_grab"]),
        _c("kind", "is_not", ["transfer"]),
    )["when"]["conditions"]
    assert stored == [_c("payee", "is", ["pay_grab", "pay_uber"]), _c("kind", "is_not", "transfer")]


@pytest.mark.parametrize(
    "condition",
    [
        _c("payee", "is", []),
        _c("payee", "is", [" ", ""]),
        _c("account", "is", ["acc_bank", 5]),
        _c("kind", "is", ["expense", "refund"]),
        _c("payee", "is", [f"pay_{n}" for n in range(51)]),
    ],
)
def test_bad_value_lists_are_refused(condition):
    with pytest.raises(ValueError):
        _trigger(condition)


def test_an_amount_follows_the_account_only_when_one_is_named():
    # USD 740 on the broker leg, SGD 1,000 in base. Exactly one account → its currency.
    assert not _fires(TRANSFER, _c("account", "is", ["acc_broker"]), _c("amount", "over", 800))
    # Any of two accounts, or two "Account is" conditions → the base currency.
    assert _fires(TRANSFER, _c("account", "is", ["acc_broker", "acc_x"]), _c("amount", "over", 800))
    two = (_c("account", "is", "acc_broker"), _c("account", "is", "acc_bank"))
    assert _fires(TRANSFER, *two, _c("amount", "over", 800))
    assert not _fires(TRANSFER, *two, _c("amount", "over", 1200))


# Conditions that can never all hold are refused at save — the same rules and words as the
# editor's warning (txnConditionConflicts.ts), so Lucius learns from the refusal itself.
@pytest.mark.parametrize(
    ("conditions", "message"),
    [
        (
            [_c("payee", "is", "pay_grab"), _c("payee", "is", "pay_uber")],
            "A transaction has one payee, so these can’t both be true — pick both in one "
            "condition instead.",
        ),
        (
            [_c("kind", "is", "expense"), _c("kind", "is", ["income", "transfer"])],
            "A transaction has one kind",
        ),
        (
            [_c("payee", "is", "pay_grab"), _c("payee", "is_not", ["pay_grab", "pay_uber"])],
            "rule out every payee",
        ),
        ([_c("kind", "is", "investment_buy"), _c("kind", "is_not", "investment")], "every kind"),
        ([_c("account", "is", "acc_bank"), _c("account", "is_not", "acc_bank")], "every account"),
        (
            [_c("amount", "over", 100), _c("amount", "under", 50)],
            "An amount can’t be over 100 and under 50 at once, so this can never match.",
        ),
        ([_c("amount", "over", 100), _c("amount", "under", 100)], "over 100 and under 100"),
    ],
)
def test_conditions_that_can_never_all_hold_are_refused(conditions, message):
    with pytest.raises(ValueError, match=message):
        _trigger(*conditions)


@pytest.mark.parametrize(
    "conditions",
    [
        # A transfer between the two touches both.
        [_c("account", "is", "acc_bank"), _c("account", "is", "acc_broker")],
        [_c("payee", "is_not", "pay_grab"), _c("payee", "is_not", "pay_uber")],
        [_c("amount", "over", 50), _c("amount", "under", 100)],
        [_c("payee", "is", ["pay_grab", "pay_uber"]), _c("payee", "is", "pay_uber")],
        [_c("kind", "is", "investment"), _c("kind", "is", "investment_buy")],
        [_c("kind", "is", "investment"), _c("kind", "is_not", "investment_buy")],
    ],
)
def test_conditions_that_can_hold_together_are_kept(conditions):
    assert len(_trigger(*conditions)["when"]["conditions"]) == len(conditions)
