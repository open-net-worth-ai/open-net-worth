"""The duplicate refusal the model reads names the tool's own escape hatch.

ADR 2026-09-02 follow-up. The refusal used to say "retry with force=true" — ``force`` was the
service kwarg and the REST body flag, while every tool takes ``allow_duplicate``. A model that
took the hint literally sent ``force: true``, which no tool reads, and was refused again with
the same advice: a loop, or a give-up on a charge the user just confirmed was genuinely
separate. The flag is now ``allow_duplicate`` at every layer, so there is one name to say.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import ledger as tool_mod
from backend.ai.tools.ledger import add_transactions
from backend.core.errors import DuplicateTransactionError, duplicate_hint
from backend.services import ledger as L

ACCOUNT = "Meridian Bank Rewards Card"


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_h", "name": ACCOUNT, "kind": "liability", "currency": "SGD"})


def _row(description="7-Eleven", amount=18.40):
    return {
        "date": "2026-08-09",
        "description": description,
        "amount": amount,
        "currency": "SGD",
        "direction": "debit",
        "account_id": ACCOUNT,
    }


def _refusal(existing_id="txn_old") -> DuplicateTransactionError:
    """Composed exactly as ``services.ledger._duplicate_error`` composes it: the head sentence
    plus the shared ``duplicate_hint`` — so the text under test is the real hint, not a stub."""
    return DuplicateTransactionError(
        "Refused: this looks like a transaction already in your ledger. "
        "same account, same amount, same day."
        + duplicate_hint(existing_id, facts=["2026-08-09", "18.40 SGD", "'7-Eleven'"]),
        existing_id=existing_id,
        existing={
            "id": existing_id,
            "date": "2026-08-09",
            "amount": 18.40,
            "currency": "SGD",
            "description": "7-Eleven",
            "account_id": "acc_h",
        },
    )


async def _add(transactions, **extra):
    out, _ = await add_transactions({"transactions": transactions, **extra})
    return json.loads(out)


@pytest.mark.asyncio
async def test_single_row_refusal_names_allow_duplicate(monkeypatch):
    def refuse(data, **kw):
        raise _refusal()

    monkeypatch.setattr(tool_mod.svc, "create_transaction", refuse)

    out = await _add([_row()])

    assert out["ok"] is False
    assert out["duplicate_of"] == "txn_old"
    for text in (out["error"], out["results"][0]["error"]):
        assert "allow_duplicate=true" in text
        assert "force=true" not in text


@pytest.mark.asyncio
async def test_bulk_refused_rows_name_allow_duplicate(monkeypatch):
    def refuse_every_row(rows, **kw):
        return [], [
            {
                **r,
                "_batch_index": i,
                "_duplicate_of": "txn_old",
                "_duplicate_reason": _refusal().message,
            }
            for i, r in enumerate(rows)
        ]

    monkeypatch.setattr(tool_mod.svc, "bulk_create_transactions", refuse_every_row)

    out = await _add([_row(), _row("KOPITIAM", 4.50)])

    assert out["refused_duplicates"] == 2
    assert "allow_duplicate=true" in out["refused_note"]
    assert "force=true" not in out["refused_note"]
    refused_rows = [r for r in out["results"] if r["status"] == "refused"]
    assert len(refused_rows) == 2
    for r in refused_rows:
        assert "allow_duplicate=true" in r["error"]
        assert "force=true" not in r["error"]
