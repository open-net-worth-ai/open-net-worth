"""One account in the book holds at most one account from each connector (owner, 2026-11-14).

Live: Nordea •••3555 and •••7469 were both linked into one book account. Their money mixed, the
bank balance check could not hold, and the sync read that account's history once — •••7469's
was never read. ``services.accounts.assert_one_link_per_source`` refuses the second link on
create and on update; an account that already holds two (before this rule) can still be saved
and unlinked down to one.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "EUR")


def _link(ref: str, mask: str, source: str = "enable_banking") -> dict:
    return {"source": source, "ref": ref, "name": "ACME Current", "mask": mask}


def _account(**extra) -> dict:
    return accounts_service.create_account(
        {"name": "ACME Current", "type": "cash", "currency": "EUR", "initial_balance": 0, **extra}
    )


def test_an_account_cannot_be_created_with_two_accounts_from_one_connector():
    with pytest.raises(ValidationError, match="only one Enable Banking account"):
        _account(external_ids=[_link("acme-1", "1111"), _link("acme-2", "2222")])
    assert ledger_service.accounts.all() == []


def test_a_second_link_from_the_same_connector_is_refused():
    acct = _account(external_ids=[_link("acme-1", "1111")])
    with pytest.raises(ValidationError, match="Unlink the other first"):
        accounts_service.update_account(
            acct["id"], {"external_ids": [_link("acme-1", "1111"), _link("acme-2", "2222")]}
        )


def test_another_connector_may_still_link_the_same_account():
    acct = _account(external_ids=[_link("acme-1", "1111")])
    updated = accounts_service.update_account(
        acct["id"],
        {"external_ids": [_link("acme-1", "1111"), _link("gc-1", "1111", source="gocardless")]},
    )
    assert {e["source"] for e in updated["external_ids"]} == {"enable_banking", "gocardless"}


def test_an_account_already_holding_two_can_be_unlinked_down_to_one():
    acct = _account(external_ids=[_link("acme-1", "1111")])
    # Written before the rule, straight into the store.
    two = [_link("acme-1", "1111"), _link("acme-2", "2222")]
    ledger_service.accounts.update_by_id(acct["id"], {"external_ids": two})
    updated = accounts_service.update_account(acct["id"], {"external_ids": [two[0]]})
    assert [e["ref"] for e in updated["external_ids"]] == ["acme-1"]
