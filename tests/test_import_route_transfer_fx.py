"""A statement row routed as a transfer to an account in ANOTHER currency — through Tell Lucius
(a ``route_transfer`` op) or the review's Type picker (``user_routes``, the same op).

The statement's figure stays on the statement account's leg; the other leg is derived by the
ledger choke, shown in the review before anything books, and never trusted as a fact: the
duplicate check and a Link both judge the statement's leg alone.

LLM stubbed via the seam; stores isolated. All accounts and amounts are synthetic."""

from __future__ import annotations

import json

import pytest

from backend.core import fx as core_fx
from backend.core import llm_seam
from backend.core.errors import MissingFxRateError
from backend.db.category_store import CategoryStore
from backend.db.statement_store import StatementStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import lucius_pass
from backend.services.statements.importer import routed_other_leg

_RATES = {"SGD": 1.0, "USD": 1.35}  # 1 USD = 1.35 SGD; THB deliberately has no rate

_CSV = (
    "Date,Description,Amount\n"
    "2026-07-10,TOPUP HARBOR USD 240710,-135\n"
    "2026-07-12,RETURN FROM HARBOR USD 240712,270\n"
    "2026-07-13,CINEBAR TICKETS,-30\n"
)
_MAPPING = {"date": "Date", "description": "Description", "amount": "Amount"}
_INSTRUCTIONS = "Rows mentioning HARBOR are transfers to my Harbor USD account"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: dict(_RATES))
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    for acct in (
        {"id": "bank", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"},
        {"id": "sav", "name": "Meridian Savings", "kind": "asset", "currency": "SGD"},
        {"id": "harbor", "name": "Harbor USD", "kind": "asset", "currency": "USD"},
        {"id": "siam", "name": "Siam THB", "kind": "asset", "currency": "THB"},
    ):
        ledger_service.accounts.insert(acct)


def _stub_lucius(monkeypatch, routes: dict[int, str]) -> None:
    reply = json.dumps(
        {
            "decisions": [
                {"row": row, "action": "route_transfer", "account_id": acct, "reason": "own money"}
                for row, acct in routes.items()
            ]
        }
    )

    async def _complete(prompt, **kwargs):
        if kwargs.get("system") == lucius_pass._VERIFY_SYSTEM:
            return '{"approved": true, "issues": []}'
        if kwargs.get("source") == lucius_pass.PASS_SOURCE:
            return reply
        return "[]"

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _preview(monkeypatch, routes: dict[int, str]) -> dict:
    _stub_lucius(monkeypatch, routes)
    return svc.decision_preview(
        _CSV.encode(),
        "csv",
        "bank",
        filename="bank.csv",
        mapping=dict(_MAPPING),
        instructions=_INSTRUCTIONS,
    )


def _row(res: dict, index: int) -> dict:
    return next(r for r in res["rows"] if r["index"] == index)


def _live() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def _book_only(res: dict, *rows: int) -> dict:
    decisions = [{"row": row, "action": "create"} for row in rows]
    return svc.decision_commit(
        res["statement_id"], "bank", decisions=decisions, default_action="skip"
    )


def _book_existing(**kw) -> dict:
    return ledger_service.create_transaction({"kind": "transfer", "source": "manual", **kw})


# ── Routing across currencies ────────────────────────────────────────────────────────────


def test_outbound_row_keeps_the_statement_figure_and_derives_the_other_leg(monkeypatch):
    res = _preview(monkeypatch, {0: "harbor"})
    assert _row(res, 0)["lucius"]["op"] == "route_transfer"
    _book_only(res, 0)
    (txn,) = _live()
    assert txn["kind"] == "transfer"
    assert (txn["from_account_id"], txn["from_amount"], txn["from_currency"]) == (
        "bank",
        135.0,
        "SGD",
    )
    assert (txn["to_account_id"], txn["to_currency"]) == ("harbor", "USD")
    assert txn["to_amount"] == pytest.approx(100.0, abs=0.01)


def test_inbound_row_makes_the_other_account_the_source(monkeypatch):
    res = _preview(monkeypatch, {1: "harbor"})
    _book_only(res, 1)
    (txn,) = _live()
    assert (txn["to_account_id"], txn["to_amount"], txn["to_currency"]) == ("bank", 270.0, "SGD")
    assert (txn["from_account_id"], txn["from_currency"]) == ("harbor", "USD")
    assert txn["from_amount"] == pytest.approx(200.0, abs=0.01)


def test_the_picker_routes_across_currencies_too(monkeypatch):
    first = _preview(monkeypatch, {})
    assert _row(first, 0).get("routed") is None
    StatementStore().update_by_id(first["statement_id"], {"user_routes": {"0": "harbor"}})
    res = _preview(monkeypatch, {})
    assert _row(res, 0)["routed"]["to_account_id"] == "harbor"
    _book_only(res, 0)
    (txn,) = _live()
    assert txn["to_currency"] == "USD" and txn["to_amount"] == pytest.approx(100.0, abs=0.01)


def test_the_review_shows_exactly_what_the_commit_books(monkeypatch):
    res = _preview(monkeypatch, {0: "harbor"})
    other = _row(res, 0)["routed"]["other_leg"]
    assert other["account_name"] == "Harbor USD"
    assert other["currency"] == "USD"
    _book_only(res, 0)
    (txn,) = _live()
    assert other["amount"] == txn["to_amount"]


def test_the_review_row_stays_in_the_statement_figure(monkeypatch):
    row = _row(_preview(monkeypatch, {0: "harbor"}), 0)
    assert row["amount"] == -135.0
    assert row["currency"] == "SGD"


def test_a_same_currency_route_shows_no_converted_leg(monkeypatch):
    row = _row(_preview(monkeypatch, {0: "sav"}), 0)
    assert row["routed"]["to_account_id"] == "sav"
    assert row["routed"].get("other_leg") is None


def test_reimporting_dedups_the_routed_transfer(monkeypatch):
    _book_only(_preview(monkeypatch, {0: "harbor"}), 0)
    again = _preview(monkeypatch, {0: "harbor"})
    out = svc.decision_commit(again["statement_id"], "bank", decisions=[], default_action="skip")
    assert out["created"] == 0
    assert len(_live()) == 1


def test_a_commit_with_no_decisions_books_the_converted_transfer_once(monkeypatch):
    res = _preview(monkeypatch, {0: "harbor", 1: "harbor"})
    svc.decision_commit(res["statement_id"], "bank", decisions=[], default_action="create")
    transfers = [t for t in _live() if t["kind"] == "transfer"]
    assert sorted((t["from_currency"], t["to_currency"]) for t in transfers) == [
        ("SGD", "USD"),
        ("USD", "SGD"),
    ]
    assert len(_live()) == 3  # both transfers + the plain row, nothing doubled
    again = _preview(monkeypatch, {0: "harbor", 1: "harbor"})
    svc.decision_commit(again["statement_id"], "bank", decisions=[], default_action="create")
    assert len(_live()) == 3


def test_the_tie_out_keeps_routed_rows_on_the_statements_own_account(monkeypatch):
    res = _preview(monkeypatch, {0: "harbor", 1: "harbor"})
    (entry,) = res["ledger_check"]["totals"]
    assert entry["account_id"] == "bank"
    assert entry["currency"] == "SGD"
    assert entry["statement_net"] == 105.0  # -135 + 270 - 30, all in the statement's currency


def test_an_unavailable_conversion_drops_the_estimate_rather_than_the_review(monkeypatch):
    res = _preview(monkeypatch, {})
    assert _row(res, 0).get("routed") is None

    def _no_rate(*_args, **_kwargs):
        raise MissingFxRateError("USD", "SGD")

    monkeypatch.setattr(ledger_service, "resolve_transaction_row", _no_rate)
    draft = {
        "kind": "transfer",
        "from_account_id": "bank",
        "from_currency": "SGD",
        "to_account_id": "harbor",
        "to_currency": "SGD",
    }
    assert routed_other_leg(draft) is None


# ── No rate: never relabel, and never stay silent ────────────────────────────────────────


def test_no_rate_leaves_a_plain_row_and_says_why(monkeypatch):
    row = _row(_preview(monkeypatch, {0: "siam"}), 0)
    assert row.get("routed") is None
    assert row.get("lucius") is None
    (flag,) = [f for f in row["flags"] if f["type"] == "transfer_no_rate"]
    assert "SGD" in flag["reason"] and "THB" in flag["reason"] and "Siam THB" in flag["reason"]


def test_no_rate_books_the_plain_row_when_the_owner_keeps_it(monkeypatch):
    _book_only(_preview(monkeypatch, {0: "siam"}), 0)
    (txn,) = _live()
    assert txn["kind"] == "expense" and txn["from_currency"] == "SGD"
    assert not txn.get("to_account_id")


# ── The other leg is an estimate: it can't decide a match, and a Link can't overwrite it ──


def test_a_booked_transfer_at_the_banks_own_rate_is_still_recognised(monkeypatch):
    booked = _book_existing(
        date="2026-07-10",
        description="Top up Harbor",
        from_account_id="bank",
        from_amount=135.0,
        from_currency="SGD",
        to_account_id="harbor",
        to_amount=97.5,  # the bank's real rate, nowhere near today's 100.00
        to_currency="USD",
    )
    res = _preview(monkeypatch, {0: "harbor"})
    (flag,) = [f for f in _row(res, 0)["flags"] if f["type"] == "transfer_leg_echo"]
    assert flag["existing_id"] == booked["id"]
    svc.decision_commit(res["statement_id"], "bank", decisions=[], default_action="skip")
    assert [t["id"] for t in _live()] == [booked["id"]]


def test_a_different_statement_figure_is_not_recognised_as_the_booked_transfer(monkeypatch):
    _book_existing(
        date="2026-07-10",
        description="Another move",
        from_account_id="bank",
        from_amount=90.0,
        from_currency="SGD",
        to_account_id="harbor",
        to_amount=66.0,
        to_currency="USD",
    )
    res = _preview(monkeypatch, {0: "harbor"})
    assert not [f for f in _row(res, 0)["flags"] if f["type"] == "transfer_leg_echo"]


@pytest.mark.parametrize(
    ("row", "legs", "statement_leg", "other_leg"),
    [
        (
            0,
            {
                "from_account_id": "bank",
                "from_amount": 135.0,
                "from_currency": "SGD",
                "to_account_id": "harbor",
                "to_amount": 97.5,
                "to_currency": "USD",
            },
            "from",
            "to",
        ),
        (
            1,
            {
                "from_account_id": "harbor",
                "from_amount": 195.0,
                "from_currency": "USD",
                "to_account_id": "bank",
                "to_amount": 270.0,
                "to_currency": "SGD",
            },
            "to",
            "from",
        ),
    ],
)
def test_linking_never_touches_the_other_accounts_real_figure(
    monkeypatch, row, legs, statement_leg, other_leg
):
    date = "2026-07-10" if row == 0 else "2026-07-12"
    booked = _book_existing(date=date, description="Harbor move", **legs)
    res = _preview(monkeypatch, {row: "harbor"})
    svc.decision_commit(
        res["statement_id"],
        "bank",
        decisions=[{"row": row, "action": "merge", "existing_id": booked["id"]}],
        default_action="skip",
    )
    (txn,) = _live()
    assert txn["id"] == booked["id"]
    assert txn[f"{other_leg}_amount"] == legs[f"{other_leg}_amount"]
    assert txn[f"{other_leg}_currency"] == legs[f"{other_leg}_currency"]
    assert txn[f"{statement_leg}_amount"] == legs[f"{statement_leg}_amount"]
    assert txn["cleared"] is True
