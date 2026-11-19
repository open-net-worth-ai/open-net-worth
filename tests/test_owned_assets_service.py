"""
Unit tests for backend.services.owned_assets (+ owned_assets_valuation) — the Assets tab
domain's ONE write choke and its net-worth valuation helper (ADR
2026-09-06-assets-tab-faithful-port).

Real stores, db_isolated (fresh migrated DB per test) — no live data, no network. Base
currency in a fresh instance is "SGD" (backend.core.fx's bare fallback), so every fixture
below stays in SGD and every conversion in the code under test is a same-currency identity
round — no FX cache file needed.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.account_store import AccountStore
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.services import owned_assets as svc
from backend.services import owned_assets_valuation as valuation


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def _account(**kw):
    defaults = {"currency": "SGD"}
    defaults.update(kw)
    return AccountStore().create(defaults)


def _institution_id(name: str) -> str:
    from backend.db.institution_store import InstitutionStore

    return InstitutionStore().resolve_or_create(name)["id"]


# ── create ───────────────────────────────────────────────────────────────────────────────


def test_create_projects_a_purchase_point_from_price_and_date(isolated):
    """One valuation is stored; the purchase point is derived from cost + cost_date on read,
    and no system "Added" fact is written."""
    asset = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "88 Marina Rise",
            "category": "property",
            "value": 1150000,
            "cost": 900000,
            "cost_date": "2019-03-15",
            "note": "lark-refinance-2026.pdf",
        }
    )
    assert asset["value"] == 1150000
    assert asset["valued_on"] == date.today().isoformat()
    assert [(m["kind"], m["date"], m["value"]) for m in asset["marks"]] == [
        ("valuation", date.today().isoformat(), 1150000),
        ("purchase", "2019-03-15", 900000),
    ]
    assert asset["marks"][0]["note"] == "lark-refinance-2026.pdf"
    assert asset["marks"][1]["id"] == "purchase"

    stored = OwnedAssetsStore().find_by_id(asset["id"])
    assert [m["value"] for m in stored["marks"]] == [1150000]
    assert "kind" not in stored["marks"][0]
    assert all(label != "Added" for label, _ in stored["facts"])


def test_create_price_without_date_has_no_purchase_point(isolated):
    """A price with no date is not a dated fact — it writes no today-dated purchase point."""
    asset = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "Fresh Buy",
            "category": "watch",
            "value": 15000,
            "cost": 10000,
        }
    )
    assert asset["value"] == 15000
    assert asset["cost"] == 10000 and asset["cost_date"] is None
    assert [m["kind"] for m in asset["marks"]] == ["valuation"]


def test_create_first_value_note_defaults_empty(isolated):
    asset = svc.apply_owned_asset_change(
        {"op": "create", "name": "Volvo XC60", "category": "vehicle", "value": 52000}
    )
    assert asset["marks"][0]["note"] == ""
    assert asset["marks"][0]["edited_at"] is None
    assert "cadence_months" not in asset


def test_create_projects_typed_details_without_review_state(isolated):
    """Catches typed details being dropped or deleted review fields leaking on reads."""
    asset = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "Endowment",
            "category": "policy",
            "value": 42000,
            "insured": True,
            "maturity_date": "2031-06-17",
        }
    )
    assert asset["insured"] is True
    assert asset["maturity_date"] == "2031-06-17"
    assert "cadence_months" not in asset
    assert "automation_id" not in asset
    assert "months_old" not in asset
    assert "automation" not in asset


def test_create_rejects_month_only_purchase_and_maturity_dates(isolated):
    """Catches the old month-only parser silently inventing the first day of a month."""
    for field in ("cost_date", "maturity_date"):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            svc.apply_owned_asset_change(
                {
                    "op": "create",
                    "name": "Dated asset",
                    "category": "other",
                    "value": 1,
                    field: "2031-06",
                }
            )


def test_create_unknown_category_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "create", "name": "Mystery", "category": "spaceship", "value": 1}
        )


def test_create_requires_positive_value(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "create", "name": "Nothing", "category": "other", "value": 0}
        )


# ── loan_account_id validation ───────────────────────────────────────────────────────────


def test_create_with_valid_mortgage_loan_account_id(isolated):
    mortgage = _account(name="Lark Home Loan", type="loan", kind="liability")
    asset = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "88 Marina Rise",
            "category": "property",
            "value": 1_000_000,
            "loan_account_id": mortgage["id"],
        }
    )
    assert asset["loan_account_id"] == mortgage["id"]


def test_create_with_bogus_loan_account_id_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "House",
                "category": "property",
                "value": 1_000_000,
                "loan_account_id": "acc_does_not_exist",
            }
        )


def test_create_with_asset_kind_loan_account_id_raises(isolated):
    checking = _account(name="DBS", type="cash", kind="asset")
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "House",
                "category": "property",
                "value": 1_000_000,
                "loan_account_id": checking["id"],
            }
        )


def test_create_with_non_loan_liability_loan_account_id_raises(isolated):
    card = _account(name="Amex", type="credit", kind="liability")
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "House",
                "category": "property",
                "value": 1_000_000,
                "loan_account_id": card["id"],
            }
        )


def test_create_with_blank_loan_account_id_clears_it(isolated):
    asset = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "House",
            "category": "property",
            "value": 1_000_000,
            "loan_account_id": "",
        }
    )
    assert asset["loan_account_id"] is None


def test_update_loan_account_id_validated_and_clearable(isolated):
    mortgage = _account(name="Lark Home Loan", type="loan", kind="liability")
    created = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "House",
            "category": "property",
            "value": 1_000_000,
            "loan_account_id": mortgage["id"],
        }
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "update",
                "id": created["id"],
                "changes": {"loan_account_id": "acc_does_not_exist"},
            }
        )
    cleared = svc.apply_owned_asset_change(
        {"op": "update", "id": created["id"], "changes": {"loan_account_id": None}}
    )
    assert cleared["loan_account_id"] is None


# ── update ───────────────────────────────────────────────────────────────────────────────


def test_update_never_touches_marks_or_value(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Rolex", "category": "watch", "value": 14800}
    )
    updated = svc.apply_owned_asset_change(
        {
            "op": "update",
            "id": created["id"],
            "changes": {"name": "Rolex Submariner", "notes": "serviced 2024"},
        }
    )
    assert updated["name"] == "Rolex Submariner"
    assert updated["notes"] == "serviced 2024"
    assert updated["value"] == 14800  # untouched
    assert updated["marks"] == created["marks"]  # untouched


@pytest.mark.parametrize("field", ["cadence_months", "automation_id", "automate"])
def test_create_rejects_retired_review_fields(isolated, field):
    with pytest.raises(ValidationError, match="retired"):
        svc.apply_owned_asset_change(
            {"op": "create", "name": "Thing", "category": "art", "value": 100, field: True}
        )


def test_update_typed_maturity_date_replaces_existing(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "GE Endowment", "category": "policy", "value": 41200}
    )
    once = svc.apply_owned_asset_change(
        {"op": "update", "id": created["id"], "changes": {"maturity_date": "2031-06-01"}}
    )
    assert once["maturity_date"] == "2031-06-01"
    twice = svc.apply_owned_asset_change(
        {"op": "update", "id": created["id"], "changes": {"maturity_date": "2032-06-01"}}
    )
    assert twice["maturity_date"] == "2032-06-01"
    assert all(f[0].casefold() != "matures" for f in twice["facts"])


def test_update_missing_asset_raises(isolated):
    with pytest.raises(NotFoundError):
        svc.apply_owned_asset_change({"op": "update", "id": "oa_nope", "changes": {"name": "x"}})


# ── add_mark ─────────────────────────────────────────────────────────────────────────────


def test_add_mark_prepends(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Gold", "category": "metal", "value": 34300}
    )
    updated = svc.apply_owned_asset_change(
        {"op": "add_mark", "id": created["id"], "value": 38400, "note": "spot"}
    )
    assert updated["value"] == 38400
    assert len(updated["marks"]) == 2
    assert updated["marks"][0]["note"] == "spot"


def test_add_mark_requires_positive_value(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Gold", "category": "metal", "value": 100}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change({"op": "add_mark", "id": created["id"], "value": 0})


def test_project_without_marks_has_no_value_or_review_state():
    projected = svc.project({"marks": []})
    assert projected["value"] is None
    assert "months_old" not in projected


# ── valuation helper ─────────────────────────────────────────────────────────────────────


def test_value_as_of_newest_on_or_before(isolated):
    asset = OwnedAssetsStore().create(
        {
            "name": "House",
            "category": "property",
            "currency": "SGD",
            "marks": [
                {"date": "2024-01-01", "value": 100},
                {"date": "2025-01-01", "value": 110},
                {"date": "2026-01-01", "value": 120},
            ],
        }
    )
    assert valuation.value_as_of(asset, None) == 120
    assert valuation.value_as_of(asset, "2025-06-01") == 110
    assert valuation.value_as_of(asset, "2023-01-01") is None  # predates every mark


def test_band_of_mapping():
    assert valuation.band_of("property") == "property"
    assert valuation.band_of("metal") == "commodity"
    assert valuation.band_of("vehicle") == "other_asset"
    assert valuation.band_of("c_vinyl_ab12") == "other_asset"
    assert valuation.band_of(None) == "other_asset"


def test_values_by_band_share_adjusted_and_banded(isolated):
    OwnedAssetsStore().create(
        {
            "name": "House",
            "category": "property",
            "currency": "SGD",
            "share_pct": 50,
            "marks": [{"date": "2026-01-01", "value": 1000}],
        }
    )
    OwnedAssetsStore().create(
        {
            "name": "Gold",
            "category": "metal",
            "currency": "SGD",
            "marks": [{"date": "2026-01-01", "value": 500}],
        }
    )
    OwnedAssetsStore().create(
        {
            "name": "Watch",
            "category": "watch",
            "currency": "SGD",
            "marks": [{"date": "2026-01-01", "value": 200}],
        }
    )
    bands = valuation.values_by_band("SGD")
    assert bands["property"] == 500.0  # 1000 × 50%
    assert bands["commodity"] == 500.0
    assert bands["other_asset"] == 200.0


def test_values_by_band_excludes_archived_today(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Old car",
            "category": "vehicle",
            "currency": "SGD",
            "marks": [{"date": "2020-01-01", "value": 100}],
        }
    )
    store.archive(asset["id"], {"disposed_on": "2026-01-01"})
    assert valuation.values_by_band("SGD") == {}


def test_values_by_band_as_of_keeps_row_disposed_after_that_date(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Car",
            "category": "vehicle",
            "currency": "SGD",
            "marks": [{"date": "2020-01-01", "value": 100}],
        }
    )
    store.archive(asset["id"], {"disposed_on": "2026-06-01"})
    # As of a date BEFORE the disposal, the asset was still owned and still worth something.
    assert valuation.values_by_band("SGD", as_of="2026-01-01") == {"other_asset": 100.0}
    # As of ON/AFTER the disposal date, it's gone.
    assert valuation.values_by_band("SGD", as_of="2026-06-01") == {}


# ── dispose ──────────────────────────────────────────────────────────────────────────────


def test_dispose_posts_one_transaction_and_archives(isolated):
    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    disposed = svc.apply_owned_asset_change(
        {
            "op": "dispose",
            "id": created["id"],
            "proceeds": 4500,
            "account_id": cash["id"],
            "date": "2026-09-01",
        }
    )
    assert disposed["archived"] is True
    assert disposed["disposal_proceeds"] == 4500
    assert disposed["disposal_txn_id"]

    from backend.services import ledger as ledger_service

    hydrated = ledger_service.account_with_balance(AccountStore().find_by_id(cash["id"]))
    assert hydrated["balance"] == 4500

    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(txns) == 1
    assert txns[0]["external_refs"] == [{"source": "asset_disposal", "ref": created["id"]}]


def test_dispose_idempotent_on_retry(isolated):
    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    change = {
        "op": "dispose",
        "id": created["id"],
        "proceeds": 4500,
        "account_id": cash["id"],
        "date": "2026-09-01",
    }
    first = svc.apply_owned_asset_change(change)
    second = svc.apply_owned_asset_change(change)
    assert first["disposal_txn_id"] == second["disposal_txn_id"]

    from backend.services import ledger as ledger_service

    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(txns) == 1  # NOT doubled


def test_dispose_on_already_archived_with_different_inputs_raises(isolated):
    """SHOULD regression: re-running dispose on an archived asset with DIFFERENT inputs
    (a different account, proceeds or date) must refuse — not silently rewrite the disposal
    record. Only a byte-identical retry is a safe no-op."""
    cash = _account(name="DBS", type="cash", kind="asset")
    other_cash = _account(name="OCBC", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    disposed = svc.apply_owned_asset_change(
        {
            "op": "dispose",
            "id": created["id"],
            "proceeds": 4500,
            "account_id": cash["id"],
            "date": "2026-09-01",
        }
    )

    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": 4500,
                "account_id": other_cash["id"],  # different account
                "date": "2026-09-01",
            }
        )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": 9999,  # different proceeds
                "account_id": cash["id"],
                "date": "2026-09-01",
            }
        )

    # The original disposal record is untouched — never re-archived after the mismatch.
    unchanged = svc.get_asset(created["id"])
    assert unchanged["disposal_proceeds"] == disposed["disposal_proceeds"]
    assert unchanged["disposal_account_id"] == disposed["disposal_account_id"]
    assert unchanged["disposed_on"] == disposed["disposed_on"]
    assert unchanged["disposal_txn_id"] == disposed["disposal_txn_id"]

    from backend.services import ledger as ledger_service

    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert len(txns) == 1  # no second transaction posted


def test_dispose_zero_proceeds_archives_no_transaction(isolated):
    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Junk", "category": "other", "value": 1}
    )
    disposed = svc.apply_owned_asset_change(
        {"op": "dispose", "id": created["id"], "proceeds": 0, "account_id": cash["id"]}
    )
    assert disposed["archived"] is True
    assert disposed["disposal_txn_id"] is None

    from backend.services import ledger as ledger_service

    assert ledger_service.transactions.all() == []


def test_dispose_refuses_non_cash_account(isolated):
    brokerage = _account(name="IBKR", type="investment", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": 100,
                "account_id": brokerage["id"],
            }
        )


# ── delete ───────────────────────────────────────────────────────────────────────────────


def test_update_rejects_retired_review_fields(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Gold", "category": "metal", "value": 100}
    )
    with pytest.raises(ValidationError, match="retired"):
        svc.apply_owned_asset_change(
            {"op": "update", "id": created["id"], "changes": {"automate": False}}
        )


def test_delete_missing_asset_raises(isolated):
    with pytest.raises(NotFoundError):
        svc.apply_owned_asset_change({"op": "delete", "id": "oa_nope"})


# ── loans / cash accounts projections ───────────────────────────────────────────────────


def test_loans_projection_uses_abs_balance(isolated):
    mortgage = _account(
        name="Lark Home Loan", type="loan", kind="liability", apr=3.1, payoff_date="2044-01-01"
    )
    from backend.services import ledger as ledger_service

    ledger_service.set_account_opening(mortgage["id"], amount=-612000.0)

    rows = svc.loans()
    assert len(rows) == 1
    assert rows[0]["id"] == mortgage["id"]
    assert rows[0]["balance"] == 612000.0  # abs() of the raw-negative liability balance
    assert rows[0]["rate_label"] == "3.1%"
    assert rows[0]["term_label"] == "to 2044"


def test_loans_projection_excludes_non_loan_liability_types(isolated):
    _account(name="Amex", type="credit", kind="liability")
    assert svc.loans() == []


def test_cash_accounts_excludes_brokerage(isolated):
    checking = _account(name="DBS", type="cash", kind="asset")
    _account(name="IBKR", type="investment", kind="asset")
    rows = svc.cash_accounts()
    assert [r["id"] for r in rows] == [checking["id"]]


# ── summary ──────────────────────────────────────────────────────────────────────────────


def test_summary_loads_fx_cache_once_for_the_asset_gain_loop(isolated, monkeypatch):
    """NIT regression: the per-asset unrealized-gain fold in `summary()` must load FX rates
    ONCE and thread them through `to_base`, not re-read the cache (rates file + profile row)
    on every asset — mirrors `ledger.net_worth()`'s own `rates = load_fx_cache()` convention."""
    for i in range(3):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": f"Thing {i}",
                "category": "other",
                "value": 1000 + i,
                "cost": 500,
            }
        )

    calls = {"count": 0}
    original = svc.load_fx_cache

    def _spy():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(svc, "load_fx_cache", _spy)

    svc.summary()
    assert calls["count"] == 1


def test_summary_cards_rows_rule(isolated, monkeypatch):
    """The Assets tab's two-or-more-kinds rule: a card shows breakdown rows only when its
    headline is made of two or more account KINDS — a single row would restate the
    headline. `cash` splits into cash-on-hand + fixed deposits only once a fixed_deposit
    account exists; `investments` splits into positions + settlement cash only once
    settlement cash is non-zero; `retirement`/`receivable` appear only once an account of
    that type exists at all."""
    from backend.services import ledger as ledger_service

    dbs = _account(name="DBS", type="cash")
    # Live holdings: its balance is settlement cash (tests/test_untracked_holdings_band.py has
    # the Brokerage total side).
    ibkr = _account(name="IBKR", type="investment", value_source="holdings")
    ledger_service.set_account_opening(dbs["id"], amount=37756.31)
    ledger_service.set_account_opening(ibkr["id"], amount=-11269.0)

    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 0.0,
            "book": {
                "grand": 242279.0,
                "total_pl": 24749.24,
                "total_pl_pct": 11.4,
                "rows": [{"account_id": ibkr["id"], "value": 242279.0, "pl": 24749.24}],
            },
        },
    )

    s = svc.summary()
    inv = s["classes"]["investments"]
    assert inv["value"] == pytest.approx(231010.0)
    assert [r["key"] for r in inv["rows"]] == ["positions", "settlement_cash"]
    assert inv["rows"][0]["pct"] == 11.3774
    assert inv["gain_pct"] is None
    assert s["classes"]["cash"]["rows"] is None
    assert s["classes"]["cash"]["value"] == pytest.approx(37756.31)
    assert s["classes"]["retirement"] is None
    assert s["classes"]["receivable"] is None

    _account(name="FD", type="fixed_deposit")
    s = svc.summary()
    assert [r["key"] for r in s["classes"]["cash"]["rows"]] == ["cash_on_hand", "fixed_deposits"]

    retirement = _account(name="CPF", type="retirement")
    ledger_service.set_account_opening(retirement["id"], amount=50000.0)
    s = svc.summary()
    # All cash, nothing held: one figure — no rows, no return.
    assert s["classes"]["retirement"] == {
        "value": pytest.approx(50000.0),
        "count_label": "1 accounts",
        "gain": None,
        "gain_pct": None,
        "rows": None,
    }


def test_retirement_holdings_move_between_asset_cards_and_composition(isolated, monkeypatch):
    from backend.services import ledger as ledger_service
    from backend.services import positions as positions_service

    roth = _account(name="Roth IRA", type="retirement")
    ledger_service.set_account_opening(roth["id"], amount=10)
    brokerage = _account(name="Brokerage", type="investment")
    ledger_service.set_account_opening(brokerage["id"], amount=20)
    monkeypatch.setattr(
        positions_service,
        "hydrated",
        lambda: [
            {
                "account_id": roth["id"],
                "asset_class": "equity",
                "quantity": 2,
                "price": 50,
                "currency": "SGD",
            },
            {
                "account_id": brokerage["id"],
                "asset_class": "equity",
                "quantity": 3,
                "price": 40,
                "currency": "SGD",
            },
        ],
    )
    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 30,
            "book": {
                "grand": 220,
                "total_pl": 30,
                "total_pl_pct": 15.7895,
                "rows": [
                    {"account_id": roth["id"], "value": 100, "pl": 10},
                    {"account_id": brokerage["id"], "value": 120, "pl": 20},
                ],
            },
        },
    )

    summary = svc.summary("SGD")

    assert summary["net_worth"] == 250
    assert summary["classes"]["retirement"]["value"] == 110
    investments = summary["classes"]["investments"]
    assert investments["value"] == 140
    assert investments["gain"] == 20
    assert investments["value"] + summary["classes"]["retirement"]["value"] == summary["own"]
    # Holdings AND cash in the retirement account: split like Investments, the return (10 on
    # a cost of 90) on the Positions row (owner 2026-09-29: "why is this one not showing the
    # breakdown of cash and portfolio?").
    retirement = summary["classes"]["retirement"]
    assert retirement["rows"] == [
        {"key": "positions", "label": "Positions", "value": 100, "pct": 11.1111},
        {"key": "cash", "label": "Cash", "value": 10},
    ]
    assert retirement["gain"] == 10
    assert retirement["gain_pct"] is None
    assert sum(r["value"] for r in retirement["rows"]) == retirement["value"]
    assert investments["rows"][0] == {
        "key": "positions",
        "label": "Positions",
        "value": 120,
        "pct": 20,
    }
    assert {r["key"]: r["value"] for r in summary["composition"]["rows"]} == {
        "retirement": 110,
        "investments": 140,
    }
    assert summary["composition"]["total"] == summary["own"]


def test_summary_debt_rows_two_or_more_kinds_rule(isolated, monkeypatch):
    """`debt` mirrors a class card's own "two or more kinds" rule: null with a single
    liability TYPE (a breakdown would just restate the "What you owe" KPI), populated —
    sorted largest first, positive magnitudes — once a second type exists."""
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 0.0,
            "book": {"grand": 0.0, "total_pl": 0.0, "total_pl_pct": 0.0, "rows": []},
        },
    )
    mortgage = _account(name="Mortgage", type="loan")
    ledger_service.set_account_opening(mortgage["id"], amount=-300000.0)

    assert svc.summary()["debt"] is None

    card = _account(name="Amex", type="credit")
    ledger_service.set_account_opening(card["id"], amount=-2000.0)

    assert svc.summary()["debt"] == [
        {"key": "loan", "label": "Loans", "value": pytest.approx(300000.0)},
        {"key": "credit", "label": "Credit", "value": pytest.approx(2000.0)},
    ]


def test_debt_card_is_what_you_owe_with_the_rails_rows(isolated, monkeypatch):
    """The Debt class card is Receivables' counterpart: absent with no credit/loan account,
    then exactly the "What you owe" KPI, broken into Loans/Credit once both exist."""
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 0.0,
            "book": {"grand": 0.0, "total_pl": 0.0, "total_pl_pct": 0.0, "rows": []},
        },
    )
    assert svc.summary()["classes"]["debt"] is None

    mortgage = _account(name="Mortgage", type="loan")
    ledger_service.set_account_opening(mortgage["id"], amount=-300000.0)
    s = svc.summary()
    assert s["classes"]["debt"] == {
        "value": pytest.approx(300000.0),
        "count_label": "1 accounts",
        "rows": None,
    }

    card = _account(name="Amex", type="credit")
    ledger_service.set_account_opening(card["id"], amount=-2000.0)
    s = svc.summary()
    debt = s["classes"]["debt"]
    assert debt["value"] == s["owe"] == pytest.approx(302000.0)
    assert debt["rows"] == s["debt"]


def test_cash_card_counts_and_yields_every_account_its_headline_spans(
    isolated, monkeypatch, fx_rates
):
    """The card's `value` spans cash + fixed deposits in EVERY currency, so its count, its
    sub-label and its blended APY must span the same set. All three came off
    `cash_accounts()` — base-currency `cash` only, a list that exists for the dispose-proceeds
    picker's own FX reason — so a USD wallet and every fixed deposit were missing from the
    count, and a fixed deposit's APY never reached the yield label."""
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 0.0,
            "book": {"grand": 0.0, "total_pl": 0.0, "total_pl_pct": 0.0, "rows": []},
        },
    )
    for i in range(3):
        acct = _account(
            name=f"DBS {i}", type="cash", institution_id=_institution_id("DBS"), apy=0.05
        )
        ledger_service.set_account_opening(acct["id"], amount=1000.0)
    usd = _account(
        name="Wise USD",
        type="cash",
        currency="USD",
        institution_id=_institution_id("Wise"),
        apy=0.05,
    )
    ledger_service.set_account_opening(usd["id"], amount=1000.0)
    for i in range(2):
        fd = _account(
            name=f"Term {i}", type="fixed_deposit", institution_id=_institution_id("OCBC"), apy=3.5
        )
        ledger_service.set_account_opening(fd["id"], amount=1000.0)

    card = svc.summary()["classes"]["cash"]
    assert card["count_label"] == "6 accounts"
    assert card["sub"].startswith("6 accounts · ")
    assert "OCBC" in card["sub"]
    # 3 SGD accounts and the 1000 USD wallet (1,350 SGD at the fixture's rate) at 0.05%, 2 SGD
    # deposits of 1,000 at 3.5% → the deposits pull the blend to 1.1%, nowhere near the
    # cash-only 0.05% the label used to show.
    assert card["yield_label"] == "1.1% blended"


# ── categories ───────────────────────────────────────────────────────────────────────────


def test_categories_inserts_customs_before_other(isolated):
    svc.create_category("Vinyl & hi-fi")
    keys = [c["key"] for c in svc.categories()]
    assert keys[-1] == "other"
    assert any(k.startswith("c_vinyl") for k in keys)


def test_create_category_idempotent_against_builtin(isolated):
    cat = svc.create_category("property")
    assert cat["key"] == "property"
    assert cat["custom"] is False
    assert svc.categories() == svc.categories()  # no duplicate minted


def test_create_category_label_over_limit_raises(isolated):
    with pytest.raises(ValidationError):
        svc.create_category("x" * (svc.MAX_CATEGORY_LABEL + 1))


def test_create_asset_name_over_120_chars_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "create", "name": "x" * 121, "category": "other", "value": 1}
        )


def test_update_asset_name_over_120_chars_raises(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 1}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "update", "id": created["id"], "changes": {"name": "x" * 121}}
        )


# ── H1: NaN/Infinity are refused, not silently poisoning money math ────────────────────────


def test_create_value_nan_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "create", "name": "Bad", "category": "other", "value": float("nan")}
        )


def test_create_cost_infinite_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Bad",
                "category": "other",
                "value": 100,
                "cost": float("inf"),
            }
        )


def test_create_share_pct_nan_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Bad",
                "category": "other",
                "value": 100,
                "share_pct": float("nan"),
            }
        )


def test_update_cost_nan_raises(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 100}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "update", "id": created["id"], "changes": {"cost": float("nan")}}
        )


def test_add_mark_value_infinite_raises(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 100}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change({"op": "add_mark", "id": created["id"], "value": float("inf")})


def test_dispose_proceeds_nan_raises(isolated):
    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 100}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": float("nan"),
                "account_id": cash["id"],
            }
        )


# ── H3: dispose honours the ledger's own idempotency no-op ─────────────────────────────────


def test_dispose_honours_ledger_idempotent_no_op_without_archiving(isolated, monkeypatch):
    """H3 regression: when ``ledger.create_transaction`` reports
    ``meta_out["idempotent_existing_id"]`` — the external_refs pair was already booked by
    an earlier commit — this call must NOT call ``store.archive`` with its OWN inputs.

    The real trigger is a genuine race (two concurrent dispose calls both read the asset
    as not-yet-archived before either commits; the loser's ``create_transaction`` call
    then finds the winner's row already booked) — not reproducible in a synchronous unit
    test, so the ledger call is stubbed to report exactly the no-op a real race would
    produce, and ``store.archive`` is spied to prove it is never reached."""
    from backend.db.owned_assets_store import OwnedAssetsStore

    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    asset_id = created["id"]

    archive_calls = []
    original_archive = OwnedAssetsStore.archive

    def _spy_archive(self, id_val, disposal, *, conn=None):
        archive_calls.append(disposal)
        return original_archive(self, id_val, disposal, conn=conn)

    monkeypatch.setattr(OwnedAssetsStore, "archive", _spy_archive)

    def _fake_create_transaction(data, *, conn=None, meta_out=None, **kwargs):
        if meta_out is not None:
            meta_out["idempotent_existing_id"] = "txn_winner123"
        return {"id": "txn_winner123", **data}

    monkeypatch.setattr(svc.ledger_service, "create_transaction", _fake_create_transaction)

    result = svc.apply_owned_asset_change(
        {
            "op": "dispose",
            "id": asset_id,
            "proceeds": 4500,
            "account_id": cash["id"],
            "date": "2026-09-01",
        }
    )

    assert archive_calls == []  # never rewrote the disposal record
    assert result["archived"] is False  # unchanged — whoever actually won already archived it
    assert result["disposal_txn_id"] is None


# ── M1: dispose never relabels a base-currency amount into another currency ────────────────


def test_cash_accounts_excludes_foreign_currency_account(isolated):
    _account(name="DBS", type="cash", kind="asset", currency="SGD")
    _account(name="Chase USD", type="cash", kind="asset", currency="USD")
    rows = svc.cash_accounts()
    assert [r["name"] for r in rows] == ["DBS"]


def test_dispose_refuses_foreign_currency_account(isolated):
    usd = _account(name="Chase USD", type="cash", kind="asset", currency="USD")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": 100,
                "account_id": usd["id"],
            }
        )


# ── M2: dates are validated ─────────────────────────────────────────────────────────────


def test_create_cost_date_non_iso_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "cost": 50,
                "cost_date": "yesterday",
            }
        )


def test_create_cost_date_invalid_calendar_date_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "cost": 50,
                "cost_date": "2019-13-01",
            }
        )


def test_create_cost_date_year_month_is_rejected(isolated):
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "cost": 50,
                "cost_date": "2019-03",
            }
        )


@pytest.mark.parametrize("bad", ["20260910", "2026-W37-4"])
def test_create_cost_date_rejects_non_extended_iso_forms(isolated, bad):
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "cost": 50,
                "cost_date": bad,
            }
        )


def test_add_mark_future_date_raises(isolated):
    from datetime import date, timedelta

    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 100}
    )
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {"op": "add_mark", "id": created["id"], "value": 200, "date": tomorrow}
        )


def test_dispose_future_date_raises(isolated):
    from datetime import date, timedelta

    cash = _account(name="DBS", type="cash", kind="asset")
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Camera", "category": "other", "value": 5000}
    )
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": created["id"],
                "proceeds": 100,
                "account_id": cash["id"],
                "date": tomorrow,
            }
        )


# ── M3: length bounds ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field,limit",
    [
        ("name", svc.MAX_NAME),
        ("description", svc.MAX_DESCRIPTION),
        ("notes", svc.MAX_NOTES),
        ("share_note", svc.MAX_SHARE_NOTE),
    ],
)
def test_create_field_over_limit_raises(isolated, field, limit):
    payload = {"op": "create", "name": "Fine", "category": "other", "value": 100}
    payload[field] = "x" * (limit + 1)
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(payload)


def test_create_too_many_facts_raises(isolated):
    facts = [[f"k{i}", "v"] for i in range(svc.MAX_FACTS + 1)]
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "facts": facts,
            }
        )


def test_create_fact_field_over_limit_raises(isolated):
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "create",
                "name": "Fine",
                "category": "other",
                "value": 100,
                "facts": [["Label", "x" * (svc.MAX_FACT_FIELD + 1)]],
            }
        )


def test_add_mark_note_over_limit_raises(isolated):
    created = svc.apply_owned_asset_change(
        {"op": "create", "name": "Fine", "category": "other", "value": 100}
    )
    with pytest.raises(ValidationError):
        svc.apply_owned_asset_change(
            {
                "op": "add_mark",
                "id": created["id"],
                "value": 200,
                "note": "x" * (svc.MAX_MARK_NOTE + 1),
            }
        )


# ── Validation at the choke: every refusal is one catalogue sentence + its field ──────────


def _create_watch(**kw):
    payload = {"op": "create", "name": "Rolex", "category": "watch", "value": 15000}
    payload.update(kw)
    return svc.apply_owned_asset_change(payload)


def _update(asset_id, **changes):
    return svc.apply_owned_asset_change({"op": "update", "id": asset_id, "changes": changes})


@pytest.mark.parametrize("share", [150, -1, "abc"])
def test_create_refuses_share_outside_0_to_100(isolated, share):
    """Regression: 150 used to be silently stored as 100% (the store clamped it)."""
    with pytest.raises(ValidationError) as exc:
        _create_watch(share_pct=share)
    assert exc.value.message == "Your share must be between 0 and 100%."
    assert exc.value.context == {"field": "share_pct"}
    assert OwnedAssetsStore().all() == []


@pytest.mark.parametrize("share", [150, -1, "abc", None])
def test_update_refuses_share_outside_0_to_100(isolated, share):
    created = _create_watch()
    with pytest.raises(ValidationError) as exc:
        _update(created["id"], share_pct=share)
    assert exc.value.context == {"field": "share_pct"}
    assert svc.get_asset(created["id"])["share_pct"] == 100


@pytest.mark.parametrize("cost", [0, -100, "abc", True])
def test_create_refuses_non_positive_cost(isolated, cost):
    with pytest.raises(ValidationError) as exc:
        _create_watch(cost=cost, cost_date="2020-01-01")
    assert exc.value.message == (
        "The price must be more than 0 — leave it blank if you don't know it."
    )
    assert exc.value.context == {"field": "cost"}


def test_update_non_numeric_cost_is_a_validation_error(isolated):
    """Regression: float("abc") escaped the choke as a bare ValueError — a 500."""
    created = _create_watch()
    with pytest.raises(ValidationError) as exc:
        _update(created["id"], cost="abc")
    assert exc.value.context == {"field": "cost"}


def test_update_refuses_uneditable_keys(isolated):
    """Unknown PATCH keys used to be silently dropped — the edit looked saved and wasn't."""
    created = _create_watch()
    with pytest.raises(ValidationError) as exc:
        _update(created["id"], value=1)
    assert exc.value.message == "The value can't be edited here — record a new value instead."
    assert exc.value.context == {"field": "value"}
    with pytest.raises(ValidationError) as exc:
        _update(created["id"], name="Renamed", marks=[])
    assert exc.value.message == "'marks' can't be changed on an asset."
    assert exc.value.context == {"field": "marks"}
    assert svc.get_asset(created["id"])["name"] == "Rolex"


def test_create_refusals_name_their_field(isolated):
    cases = [
        ({"name": "  "}, "name", "Give it a name (up to 120 characters)."),
        (
            {"category": "spaceship"},
            "category",
            "Unknown category 'spaceship' — pick one from the list.",
        ),
        ({"value": 0}, "value", "The value must be more than 0."),
        ({"value": "abc"}, "value", "The value must be more than 0."),
        ({"cost_date": "2019-03"}, "cost_date", "Use a full date (YYYY-MM-DD)."),
        ({"cost_date": "2999-01-01"}, "cost_date", "The purchase date can't be in the future."),
        (
            {"loan_account_id": "acc_nope"},
            "loan_account_id",
            "That account isn't one of your active loans.",
        ),
    ]
    for override, field, detail in cases:
        with pytest.raises(ValidationError) as exc:
            _create_watch(**override)
        assert (exc.value.context.get("field"), exc.value.message) == (field, detail), override


def test_update_moves_and_clears_the_purchase_point(isolated):
    created = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "88 Marina Rise",
            "category": "property",
            "value": 1150000,
            "cost": 900000,
            "cost_date": "2019-03-15",
        }
    )
    moved = _update(created["id"], cost_date="2020-01-02")
    assert moved["marks"][-1] == {**created["marks"][-1], "date": "2020-01-02"}
    repriced = _update(created["id"], cost=950000)
    assert (repriced["marks"][-1]["kind"], repriced["marks"][-1]["value"]) == ("purchase", 950000)
    cleared = _update(created["id"], cost=None)
    assert [m["kind"] for m in cleared["marks"]] == ["valuation"]
    assert cleared["value"] == created["value"] == 1150000


def test_update_refuses_cost_date_after_first_value(isolated):
    created = _create_watch(cost=10000, cost_date="2019-01-01")
    svc.apply_owned_asset_change(
        {"op": "add_mark", "id": created["id"], "value": 12000, "date": "2020-06-01"}
    )
    with pytest.raises(ConflictError) as exc:
        _update(created["id"], cost_date="2021-01-01")
    assert exc.value.message == (
        "The purchase date can't be after 1 Jun 2020, the first value you recorded. "
        "Record a value on or after the purchase date, then remove the older one."
    )
    assert exc.value.context == {"reason": "after_first_value", "field": "cost_date"}
    assert svc.get_asset(created["id"])["cost_date"] == "2019-01-01"
    assert _update(created["id"], cost_date="2020-06-01")["cost_date"] == "2020-06-01"


def test_update_refuses_cost_date_after_the_sale(isolated):
    """Code review R2: a changed purchase date was checked against the first value only, so
    Bought on could move past Sold on — here, a watch entered today to record a 2020 sale."""
    cash = _account(name="DBS", type="cash", kind="asset")
    watch = _create_watch(cost=10000, cost_date="2019-01-01")
    svc.apply_owned_asset_change(
        {
            "op": "dispose",
            "id": watch["id"],
            "proceeds": 0,
            "account_id": cash["id"],
            "date": "2020-06-01",
        }
    )
    with pytest.raises(ConflictError) as exc:
        _update(watch["id"], cost_date="2021-01-01")
    assert exc.value.message == "The purchase date can't be after the sale (1 Jun 2020)."
    assert exc.value.context == {"reason": "after_sale", "field": "cost_date"}
    assert svc.get_asset(watch["id"])["cost_date"] == "2019-01-01"
    assert _update(watch["id"], cost_date="2020-06-01")["cost_date"] == "2020-06-01"


def test_update_unchanged_cost_date_on_legacy_violation_is_allowed(isolated):
    """A row written before the purchase-first rule may already hold a value dated before its
    purchase. It stays editable: the rule runs only on a CHANGED cost_date."""
    legacy = OwnedAssetsStore().create(
        {
            "name": "Legacy watch",
            "category": "watch",
            "currency": "SGD",
            "cost": 100.0,
            "cost_date": "2022-01-01",
            "marks": [{"date": "2021-01-01", "value": 90}, {"date": "2026-01-01", "value": 150}],
        }
    )
    edited = _update(legacy["id"], cost_date="2022-01-01", notes="still editable", cost=120)
    assert (edited["notes"], edited["cost"]) == ("still editable", 120)
    with pytest.raises(ConflictError):
        _update(legacy["id"], cost_date="2022-02-01")


def test_add_mark_refuses_date_before_purchase(isolated):
    created = _create_watch(cost=10000, cost_date="2019-03-15")
    with pytest.raises(ConflictError) as exc:
        svc.apply_owned_asset_change(
            {"op": "add_mark", "id": created["id"], "value": 11000, "date": "2019-03-14"}
        )
    assert exc.value.message == "A value can't be dated before the purchase (15 Mar 2019)."
    assert exc.value.context == {"reason": "before_purchase", "field": "date"}
    on_the_day = svc.apply_owned_asset_change(
        {"op": "add_mark", "id": created["id"], "value": 11000, "date": "2019-03-15"}
    )
    assert len(on_the_day["marks"]) == 3  # today's value, the new one, the purchase point

    policy = svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": "GE Endowment",
            "category": "policy",
            "value": 42000,
            "cost": 18000,
            "cost_date": "2019-06-01",
        }
    )
    with pytest.raises(ConflictError) as exc:
        svc.apply_owned_asset_change(
            {"op": "add_mark", "id": policy["id"], "value": 20000, "date": "2019-05-31"}
        )
    assert exc.value.message == "A value can't be dated before the policy started (1 Jun 2019)."


def test_missing_asset_refusal_names_it(isolated):
    with pytest.raises(NotFoundError) as exc:
        svc.get_asset("oa_nope")
    assert exc.value.message == "That asset ('oa_nope') no longer exists."
    with pytest.raises(NotFoundError) as exc:
        _update("oa_nope", name="x")
    assert exc.value.message == "That asset ('oa_nope') no longer exists."


# ── Correct or remove a value ────────────────────────────────────────────────────────────


def _watch_with_two_values():
    """Today's 15,000 valuation, a 2021 valuation of 12,000, and a 2019 purchase point."""
    created = _create_watch(cost=10000, cost_date="2019-03-15")
    svc.apply_owned_asset_change(
        {
            "op": "add_mark",
            "id": created["id"],
            "value": 12000,
            "date": "2021-05-01",
            "note": "Chrono24",
        }
    )
    return svc.get_asset(created["id"])


def _mark_on(asset: dict, day: str) -> dict:
    return next(m for m in asset["marks"] if m["date"] == day and m["kind"] == "valuation")


def _correct(asset_id, mark_id, **changes):
    return svc.apply_owned_asset_change(
        {"op": "correct_mark", "id": asset_id, "mark_id": mark_id, **changes}
    )


def _remove(asset_id, mark_id):
    return svc.apply_owned_asset_change({"op": "remove_mark", "id": asset_id, "mark_id": mark_id})


def test_correct_mark_fixes_value_date_note_and_stamps_edited_at(isolated):
    asset = _watch_with_two_values()
    older = _mark_on(asset, "2021-05-01")
    fixed = _correct(asset["id"], older["id"], value=12500, date="2021-06-01", note="Chrono24 ✓")
    point = next(m for m in fixed["marks"] if m["id"] == older["id"])
    assert (point["value"], point["date"], point["note"]) == (12500, "2021-06-01", "Chrono24 ✓")
    assert point["created_at"] == older["created_at"]
    assert point["edited_at"] and point["kind"] == "valuation"
    assert fixed["value"] == asset["value"] == 15000  # an older value leaves today's alone
    assert _correct(asset["id"], older["id"], value=12500) == fixed  # identical: no-op


def test_correct_mark_refuses_purchase_point(isolated):
    asset = _watch_with_two_values()
    assert asset["marks"][-1]["id"] == "purchase"
    with pytest.raises(ConflictError) as exc:
        _correct(asset["id"], "purchase", value=1)
    assert exc.value.message == (
        "The purchase point comes from the purchase price and date — change or clear them "
        "in Purchase."
    )
    assert exc.value.context == {"reason": "purchase_point"}
    with pytest.raises(NotFoundError) as exc:
        _correct(asset["id"], "mk_gone", value=1)
    assert exc.value.message == "That value no longer exists."


def test_correct_mark_allows_the_last_valuation(isolated):
    created = _create_watch()
    fixed = _correct(created["id"], created["marks"][0]["id"], value=16000)
    assert fixed["value"] == 16000
    assert fixed["marks"][0]["edited_at"]


def test_correct_mark_refuses_date_before_purchase(isolated):
    asset = _watch_with_two_values()
    older = _mark_on(asset, "2021-05-01")
    with pytest.raises(ConflictError) as exc:
        _correct(asset["id"], older["id"], date="2019-03-14")
    assert exc.value.message == "A value can't be dated before the purchase (15 Mar 2019)."
    assert exc.value.context == {"reason": "before_purchase", "field": "date"}
    with pytest.raises(ValidationError) as exc:
        _correct(asset["id"], older["id"], date="2999-01-01")
    assert exc.value.context == {"field": "date"}
    with pytest.raises(ValidationError) as exc:
        _correct(asset["id"], older["id"], value=0)
    assert exc.value.context == {"field": "value"}

    # A legacy value already dated before the purchase keeps its date through a fix of its
    # amount: the rule runs on a CORRECTED date only.
    legacy = OwnedAssetsStore().create(
        {
            "name": "Legacy watch",
            "category": "watch",
            "currency": "SGD",
            "cost": 100.0,
            "cost_date": "2022-01-01",
            "marks": [{"date": "2021-01-01", "value": 90}, {"date": "2026-01-01", "value": 150}],
        }
    )
    early = next(m for m in legacy["marks"] if m["date"] == "2021-01-01")
    assert _mark_on(_correct(legacy["id"], early["id"], value=95), "2021-01-01")["value"] == 95


def test_remove_mark_refuses_purchase_point_and_last_value(isolated):
    asset = _watch_with_two_values()
    with pytest.raises(ConflictError) as exc:
        _remove(asset["id"], "purchase")
    assert exc.value.context == {"reason": "purchase_point"}

    only = _remove(asset["id"], _mark_on(asset, "2021-05-01")["id"])
    with pytest.raises(ConflictError) as exc:
        _remove(asset["id"], only["marks"][0]["id"])
    assert exc.value.message == (
        "An asset needs at least one value. Record a new value first, or delete the asset."
    )
    assert exc.value.context == {"reason": "last_value"}
    with pytest.raises(NotFoundError):
        _remove(asset["id"], "mk_gone")
    assert svc.get_asset(asset["id"])["value"] == 15000


def test_remove_mark_falls_back_to_next_valuation(isolated):
    asset = _watch_with_two_values()
    after = _remove(asset["id"], asset["marks"][0]["id"])
    assert (after["value"], after["valued_on"]) == (12000, "2021-05-01")
    assert [m["kind"] for m in after["marks"]] == ["valuation", "purchase"]


# ── One loan, one live asset ─────────────────────────────────────────────────────────────


def _loan(name="Lark Mortgage"):
    return _account(name=name, type="loan", kind="liability")


def _home(loan_id, name="88 Marina Rise"):
    return svc.apply_owned_asset_change(
        {
            "op": "create",
            "name": name,
            "category": "property",
            "value": 1_000_000,
            "loan_account_id": loan_id,
        }
    )


def test_loan_secures_one_live_asset(isolated):
    """Regression: one loan could secure two assets — a car showed −241% equity."""
    loan = _loan()
    home = _home(loan["id"])
    holder = {"id": home["id"], "name": "88 Marina Rise"}
    with pytest.raises(ConflictError) as exc:
        _home(loan["id"], name="Holiday flat")
    assert exc.value.message == (
        "Lark Mortgage already secures “88 Marina Rise”. A loan can secure one asset — "
        "unlink it there first."
    )
    assert exc.value.context == {
        "reason": "loan_in_use",
        "field": "loan_account_id",
        "holder": holder,
    }
    assert [a["name"] for a in OwnedAssetsStore().all()] == ["88 Marina Rise"]

    car = _create_watch(name="Volvo XC60", category="vehicle")
    with pytest.raises(ConflictError) as exc:
        _update(car["id"], loan_account_id=loan["id"])
    assert exc.value.context["holder"] == holder
    assert svc.get_asset(car["id"])["loan_account_id"] is None


def test_relinking_the_same_loan_is_not_refused(isolated):
    loan = _loan()
    home = _home(loan["id"])
    again = _update(home["id"], loan_account_id=loan["id"], name="88 Marina Rise #12-01")
    assert (again["loan_account_id"], again["name"]) == (loan["id"], "88 Marina Rise #12-01")

    # Legacy data may already hold a double link; each holder stays editable as long as
    # its own link doesn't change.
    legacy = OwnedAssetsStore().create(
        {
            "name": "Old flat",
            "category": "property",
            "currency": "SGD",
            "loan_account_id": loan["id"],
        }
    )
    edited = _update(legacy["id"], notes="linked before the rule", loan_account_id=loan["id"])
    assert edited["notes"] == "linked before the rule"


def test_an_unchanged_link_to_a_closed_loan_still_saves(isolated):
    """Code review R4: the Ownership editor re-sends loan_account_id with every save, and the
    loan was checked even when the link hadn't changed — once that loan was archived, the
    asset's share could not be saved at all. Making a NEW link to it is still refused."""
    loan = _loan()
    home = _home(loan["id"])
    AccountStore().update_by_id(loan["id"], {"archived": True})

    saved = _update(home["id"], share_pct=50, share_note="Joint", loan_account_id=loan["id"])
    assert (saved["share_pct"], saved["loan_account_id"]) == (50, loan["id"])

    car = _create_watch(name="Volvo XC60", category="vehicle")
    with pytest.raises(ValidationError) as exc:
        _update(car["id"], loan_account_id=loan["id"])
    assert exc.value.message == "That account isn't one of your active loans."


def test_dispose_releases_the_loan(isolated):
    """Selling clears the link — a sold asset used to keep its loan, so deleting the loan
    account was refused with a pointer to a screen the owner can no longer reach."""
    from backend.services import ledger as ledger_service

    cash = _account(name="DBS", type="cash", kind="asset")
    loan = _loan("Car loan")
    car = _create_watch(name="Volvo XC60", category="vehicle", loan_account_id=loan["id"])
    sold = svc.apply_owned_asset_change(
        {"op": "dispose", "id": car["id"], "proceeds": 30000, "account_id": cash["id"]}
    )
    assert sold["archived"] is True
    assert sold["loan_account_id"] is None
    assert next(r for r in svc.loans() if r["id"] == loan["id"])["held_by"] is None
    assert ledger_service.delete_account(loan["id"]) is True


def test_a_sold_asset_takes_no_loan(isolated):
    """A sold asset's loan link is null (selling clears it); an edit may not put one back —
    it would also block deleting that loan's account again."""
    cash = _account(name="DBS", type="cash", kind="asset")
    loan = _loan("Car loan")
    car = _create_watch(name="Volvo XC60", category="vehicle")
    svc.apply_owned_asset_change(
        {"op": "dispose", "id": car["id"], "proceeds": 0, "account_id": cash["id"]}
    )
    with pytest.raises(ValidationError) as exc:
        _update(car["id"], loan_account_id=loan["id"])
    assert exc.value.message == "A sold asset can't secure a loan."
    assert exc.value.context == {"field": "loan_account_id"}
    assert svc.get_asset(car["id"])["loan_account_id"] is None
    assert _update(car["id"], notes="Sold to a friend")["notes"] == "Sold to a friend"


def test_dispose_refuses_sale_before_purchase(isolated):
    from backend.services import ledger as ledger_service

    cash = _account(name="DBS", type="cash", kind="asset")
    asset = _create_watch(cost=10000, cost_date="2019-03-15")
    change = {"op": "dispose", "id": asset["id"], "proceeds": 9000, "account_id": cash["id"]}
    with pytest.raises(ConflictError) as exc:
        svc.apply_owned_asset_change({**change, "date": "2019-03-14"})
    assert exc.value.message == "The sale date can't be before the purchase (15 Mar 2019)."
    assert exc.value.context == {"reason": "before_purchase", "field": "date"}
    assert svc.get_asset(asset["id"])["archived"] is False
    assert ledger_service.transactions.all() == []

    sold = svc.apply_owned_asset_change({**change, "date": "2019-03-15"})
    assert sold["disposed_on"] == "2019-03-15"


def test_loans_projection_names_the_holder(isolated):
    mortgage, car_loan, spare = _loan("Lark Mortgage"), _loan("Car loan"), _loan("Spare")
    home = _home(mortgage["id"])
    car = _create_watch(name="Volvo XC60", category="vehicle", loan_account_id=car_loan["id"])
    # A legacy double link names the OLDEST live holder.
    older = OwnedAssetsStore().create(
        {
            "name": "Old car",
            "category": "vehicle",
            "currency": "SGD",
            "loan_account_id": car_loan["id"],
            "created_at": "2001-01-01T00:00:00+00:00",
        }
    )
    held_by = {r["id"]: r["held_by"] for r in svc.loans()}
    assert held_by == {
        mortgage["id"]: {"id": home["id"], "name": "88 Marina Rise"},
        car_loan["id"]: {"id": older["id"], "name": "Old car"},
        spare["id"]: None,
    }
    OwnedAssetsStore().archive(older["id"], {"disposed_on": "2020-01-01"})
    held_by = {r["id"]: r["held_by"] for r in svc.loans()}
    assert held_by[car_loan["id"]] == {"id": car["id"], "name": "Volvo XC60"}


# ── Composition + share value (server-side; the client never divides) ──────────────────────


@pytest.fixture
def offline(monkeypatch):
    """No live price fetch — every market figure is last-known, as in any offline test."""
    from backend.services import prices as prices_service

    def _fail(*_args, **_kwargs):
        raise RuntimeError("offline (test)")

    monkeypatch.setattr(prices_service, "fetch_live", _fail)


def _opened(name: str, type_: str, amount: float, **kw) -> dict:
    from backend.services import ledger as ledger_service

    account = _account(name=name, type=type_, **kw)
    ledger_service.set_account_opening(account["id"], amount=amount)
    return account


def _composition():
    return svc.summary()["composition"]


def test_project_share_value_is_native_and_rounded(isolated):
    joint = _create_watch(name="Joint flat", category="property", value=900_001, share_pct=33.3)
    assert joint["share_value"] == 299_700.33  # 900,001 × 33.3%, rounded to the cent
    usd = OwnedAssetsStore().create(
        {
            "name": "NYC condo",
            "category": "property",
            "currency": "USD",
            "share_pct": 50,
            "marks": [{"date": "2026-01-01", "value": 1_000_000}],
        }
    )
    assert svc.get_asset(usd["id"])["share_value"] == 500_000.0  # USD — never converted
    assert svc.project({"marks": []})["share_value"] is None


def test_summary_composition_sums_to_100_with_a_half_owned_property(isolated, offline):
    """Regression: the rail divided the WHOLE property's value by a share-adjusted total and
    read "Property 151%"."""
    _opened("DBS", "cash", 100_000.0)
    _create_watch(name="Joint flat", category="property", value=900_000, share_pct=50)
    assert _composition() == {
        "total": 550_000.0,
        "rows": [
            {"key": "property", "kind": "category", "value": 450_000.0, "pct": 82},
            {"key": "cash", "kind": "class", "value": 100_000.0, "pct": 18},
        ],
    }


def test_summary_composition_rows_reconcile_to_own(isolated, offline):
    _opened("DBS", "cash", 12_345.67)
    _opened("Term deposit", "fixed_deposit", 20_000.0)
    _opened("CPF", "retirement", 50_000.0)
    _opened("Loan to Sam", "receivable", 1_000.0)
    _opened("IBKR", "investment", 5_000.0)
    _opened("Mortgage", "loan", -300_000.0)
    cash = _account(name="OCBC", type="cash")
    _create_watch(name="Flat", category="property", value=600_000, share_pct=50)
    _create_watch(name="Rolex", value=15_000)
    _create_watch(name="Gold", category="metal", value=8_000)
    sold = _create_watch(name="Old car", category="vehicle", value=9_000)
    svc.apply_owned_asset_change(
        {"op": "dispose", "id": sold["id"], "proceeds": 0, "account_id": cash["id"]}
    )

    summary = svc.summary()
    comp = summary["composition"]
    assert {r["key"]: r["value"] for r in comp["rows"]} == {
        "cash": 32_345.67,
        "investments": 5_000.0,
        "retirement": 50_000.0,
        "receivable": 1_000.0,
        "property": 300_000.0,
        "watch": 15_000.0,
        "metal": 8_000.0,
    }
    assert comp["total"] == summary["own"]
    assert sum(r["pct"] for r in comp["rows"]) == 100
    assert [r["value"] for r in comp["rows"]] == sorted(
        (r["value"] for r in comp["rows"]), reverse=True
    )


def test_composition_rows_sum_to_own_to_the_cent():
    """Code review R5: each row is rounded on its own and the categories are converted at the
    page's FX snapshot, while `own` comes from net worth's own fold — so the rows could miss
    `own` by a few cents. The largest row absorbs the difference; the rows sum to `own`."""
    from backend.services import owned_assets_summary

    breakdown = {
        "account_classes": {"cash": 12_345.67, "retirement": 50_000.0},
        "market_positions": 1_000.13,
        "options_mark": 0.0,
    }
    comp = owned_assets_summary._composition(breakdown, [], "SGD", {}, own=63_345.81)
    assert comp["total"] == 63_345.81
    assert {r["key"]: r["value"] for r in comp["rows"]} == {
        "retirement": 50_000.01,
        "cash": 12_345.67,
        "investments": 1_000.13,
    }
    exact = owned_assets_summary._composition(breakdown, [], "SGD", {}, own=63_345.80)
    assert {r["key"]: r["value"] for r in exact["rows"]}["retirement"] == 50_000.0


def test_summary_composition_drops_non_positive_classes(isolated, offline):
    """Margin makes the investments class negative: it isn't part of what you own, so it is
    left out — the one way `total` can exceed `own`."""
    _opened("DBS", "cash", 10_000.0)
    _opened("IBKR", "investment", -5_000.0)
    _account(name="CPF", type="retirement")  # exists, holds nothing
    _create_watch(name="Rolex", value=5_000)
    summary = svc.summary()
    comp = summary["composition"]
    assert [(r["key"], r["pct"]) for r in comp["rows"]] == [("cash", 67), ("watch", 33)]
    assert comp["total"] == 15_000.0 > summary["own"] == 10_000.0


def test_summary_composition_of_an_empty_book(isolated, offline):
    assert _composition() == {"total": 0.0, "rows": []}


def test_summary_composition_largest_remainder_is_deterministic(isolated, offline):
    from backend.services import owned_assets_summary

    for name, category in (("Rolex", "watch"), ("Gold", "metal"), ("Print", "art")):
        _create_watch(name=name, category=category, value=1_000)
    # Three equal thirds: the spare point goes by larger value, then by key — art first.
    assert [(r["key"], r["pct"]) for r in _composition()["rows"]] == [
        ("art", 34),
        ("metal", 33),
        ("watch", 33),
    ]
    # Equal remainders (0.5 each): the larger value takes the spare point.
    pcts = owned_assets_summary._largest_remainder_pcts([("a", 1.0), ("b", 3.0), ("c", 196.0)])
    assert pcts == [0, 2, 98]
    assert owned_assets_summary._largest_remainder_pcts([]) == []


def test_summary_composition_base_converts_a_foreign_currency_asset(
    isolated, offline, tmp_path, monkeypatch
):
    from backend.services import fx_cache

    monkeypatch.setattr(fx_cache, "_FX_CACHE_FILE", tmp_path / "fx.json")
    fx_cache.save_fx_cache({"USD": 1.35})
    _opened("DBS", "cash", 100_000.0)
    OwnedAssetsStore().create(
        {
            "name": "NYC condo",
            "category": "property",
            "currency": "USD",
            "share_pct": 50,
            "marks": [{"date": "2026-01-01", "value": 200_000}],
        }
    )
    summary = svc.summary()
    rows = {r["key"]: r for r in summary["composition"]["rows"]}
    assert rows["property"]["value"] == 135_000.0  # 200,000 USD × 50% × 1.35
    assert summary["composition"]["total"] == summary["own"]


def test_page_gives_each_asset_its_figures_in_the_base_currency(
    isolated, offline, tmp_path, monkeypatch
):
    """Owner 2026-09-28: the register stacks a category of 4+ assets into one card with its
    total and gain, and orders the cards by value — sums and comparisons that must be in ONE
    currency, converted at the page's own rates the way net worth converts them."""
    from backend.services import fx_cache

    monkeypatch.setattr(fx_cache, "_FX_CACHE_FILE", tmp_path / "fx.json")
    fx_cache.save_fx_cache({"USD": 1.35})
    OwnedAssetsStore().create(
        {
            "name": "NYC condo",
            "category": "property",
            "currency": "USD",
            "share_pct": 50,
            "cost": 150_000,
            "cost_date": "2020-01-01",
            "marks": [{"date": "2026-01-01", "value": 200_000}],
        }
    )
    _create_watch(value=15_000)
    by_name = {a["name"]: a for a in svc.get_assets_page()["assets"]}
    condo, watch = by_name["NYC condo"], by_name["Rolex"]
    assert condo["value"] == 200_000  # its own currency, as ever
    assert condo["base_value"] == 270_000.0  # 200,000 USD × 1.35
    assert condo["base_share_value"] == 135_000.0  # × your 50%
    assert condo["base_share_cost"] == 101_250.0  # 150,000 USD × 1.35 × 50%
    assert watch["base_value"] == watch["base_share_value"] == 15_000.0
    assert watch["base_share_cost"] is None  # no purchase price, no cost to count


# ── Attachments: a removed or replaced file goes with its row, unless shared ──────────────


@pytest.fixture
def attachment_dir(tmp_path, monkeypatch):
    import backend.db.attachment_store as attachment_store
    from backend.services import attachments as attachments_service

    directory = tmp_path / "attachments"
    directory.mkdir()
    monkeypatch.setattr(attachment_store, "ATTACHMENTS_DIR", directory)
    attachments_service._cache_drop()
    yield
    attachments_service._cache_drop()


def _upload(name: str, channel: str = "assets:docs") -> str:
    from backend.services.attachments import create_attachment

    return create_attachment(
        {"kind": "text", "summary": name, "format": "txt"},
        content=name.encode(),
        filename=name,
        media_type="text/plain",
        source_channel=channel,
    )


def _stored(ref_id: str) -> bool:
    from backend.services.attachments import get_attachment_meta

    return get_attachment_meta(ref_id) is not None


def _doc(op: str, asset_id: str, ref_id: str, **extra):
    return svc.apply_owned_asset_change({"op": op, "id": asset_id, "ref_id": ref_id, **extra})


def test_remove_doc_deletes_the_attachment_unless_shared(isolated, attachment_dir):
    """Removing a document used to drop only the row; the file stayed in Settings → Uploaded
    files. It now goes too — unless another owned asset still references it."""
    home = _create_watch(name="Flat", category="property")
    car = _create_watch(name="Car", category="vehicle")
    deed, policy = _upload("deed.txt"), _upload("policy.txt")
    _doc("add_doc", home["id"], deed, name="Deed")
    _doc("add_doc", home["id"], policy, name="Policy")
    _doc("add_doc", car["id"], policy, name="Policy")

    after = _doc("remove_doc", home["id"], deed)
    assert [d["ref_id"] for d in after["docs"]] == [policy]
    assert not _stored(deed)
    _doc("remove_doc", home["id"], policy)
    assert _stored(policy)  # the car still uses it
    _doc("remove_doc", car["id"], policy)
    assert not _stored(policy)


def test_remove_doc_survives_a_failed_file_delete(isolated, attachment_dir, monkeypatch, caplog):
    """The file delete is best-effort AFTER the asset write commits: a failure leaves an
    orphan in Settings, never a broken document link or a failed removal."""
    from backend.services import attachments as attachments_service

    home = _create_watch(name="Flat", category="property")
    deed = _upload("deed.txt")
    _doc("add_doc", home["id"], deed, name="Deed")

    def _broken(_ref_id):
        raise OSError("disk full")

    monkeypatch.setattr(attachments_service, "delete_attachment", _broken)
    after = _doc("remove_doc", home["id"], deed)
    assert after["docs"] == []
    assert _stored(deed)
    assert deed in caplog.text


def test_set_photo_deletes_the_replaced_attachment(isolated, attachment_dir):
    first, second, third = _upload("a.png"), _upload("b.png"), _upload("c.png")
    watch, spare = _create_watch(), _create_watch(name="Spare")

    def photo(asset, ref):
        return svc.apply_owned_asset_change({"op": "set_photo", "id": asset["id"], "ref": ref})

    photo(watch, first)
    assert photo(watch, second)["photo_ref"] == second
    assert not _stored(first)
    photo(watch, second)  # the same photo again deletes nothing
    assert _stored(second)
    photo(spare, second)
    photo(watch, third)
    assert _stored(second)  # the spare still shows it


def test_add_doc_cuts_the_name_to_200_characters(isolated, attachment_dir):
    watch = _create_watch()
    added = _doc("add_doc", watch["id"], _upload("long.txt"), name="x" * 250)
    assert added["docs"][0]["name"] == "x" * 200


def test_renaming_a_document_is_held_to_the_same_200_characters(isolated, attachment_dir):
    """Code review nit: a rename wrote a name of any length. A typed name is refused rather
    than cut, like every other text an asset holds."""
    watch = _create_watch()
    ref = _upload("receipt.txt")
    _doc("add_doc", watch["id"], ref, name="Receipt")
    with pytest.raises(ValidationError) as exc:
        _doc("update_doc", watch["id"], ref, changes={"name": "x" * 201})
    assert exc.value.message == "A document name can be up to 200 characters."
    assert exc.value.context == {"field": "name"}
    assert svc.get_asset(watch["id"])["docs"][0]["name"] == "Receipt"
    renamed = _doc("update_doc", watch["id"], ref, changes={"name": "x" * 200})
    assert renamed["docs"][0]["name"] == "x" * 200


def test_each_document_says_what_file_it_is(isolated, attachment_dir):
    """Owner 2026-09-28 ("we should show preview for the file too!"): the page previews a file
    by its type, and a renamed doc's name ("Payment Schedule") no longer says what it is — so
    every projected doc carries its file's own name, media type and size; one whose file is
    gone is still left out."""
    watch = _create_watch()
    ref = _upload("IMG_1234.txt")
    gone = _upload("gone.txt")
    _doc("add_doc", watch["id"], ref, name="IMG_1234.txt")
    _doc("add_doc", watch["id"], gone, name="gone.txt")
    _doc("update_doc", watch["id"], ref, changes={"name": "Payment Schedule"})
    from backend.services.attachments import delete_attachment

    delete_attachment(gone)
    [listed] = [a for a in svc.list_assets() if a["id"] == watch["id"]]
    for asset in (listed, svc.get_asset(watch["id"])):
        [doc] = asset["docs"]
        assert doc["name"] == "Payment Schedule"
        assert doc["filename"] == "IMG_1234.txt"
        assert doc["media_type"] == "text/plain"
        assert doc["size_bytes"] == len(b"IMG_1234.txt")


def test_renaming_a_document_refuses_a_blank_name_and_tidies_spaces(isolated, attachment_dir):
    """Owner 2026-09-28 ("why can't i rename it?"): the Attachments tab now renames a file. A
    blank name is refused (it would leave the row nameless), as Documents' own rename refuses
    it; runs of spaces collapse, the way Documents cleans a title."""
    watch = _create_watch()
    ref = _upload("df25fa9c6f98.jpeg")
    _doc("add_doc", watch["id"], ref, name="df25fa9c6f98.jpeg")
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValidationError) as exc:
            _doc("update_doc", watch["id"], ref, changes={"name": blank})
        assert exc.value.message == "Enter a name."
        assert exc.value.context == {"field": "name"}
    assert svc.get_asset(watch["id"])["docs"][0]["name"] == "df25fa9c6f98.jpeg"
    renamed = _doc("update_doc", watch["id"], ref, changes={"name": "  SPA   page 1 "})
    assert renamed["docs"][0]["name"] == "SPA page 1"
    # The Lucius toggle alone never touches the name.
    toggled = _doc("update_doc", watch["id"], ref, changes={"lucius_readable": False})
    assert toggled["docs"][0]["name"] == "SPA page 1"


@pytest.fixture
def deep_attachment_dir(tmp_path, monkeypatch):
    """ATTACHMENTS_DIR two levels below a data folder holding the database, as in the real
    layout — so a ref that climbs out of the directory would reach something that matters."""
    import backend.db.attachment_store as attachment_store
    from backend.services import attachments as attachments_service

    data = tmp_path / "data"
    directory = data / "files" / "attachments"
    directory.mkdir(parents=True)
    (data / "onw.db").write_text("the database")
    monkeypatch.setattr(attachment_store, "ATTACHMENTS_DIR", directory)
    attachments_service._cache_drop()
    yield data
    attachments_service._cache_drop()


def test_a_crafted_attachment_ref_deletes_nothing(isolated, deep_attachment_dir):
    """Security review S1: a restore could plant an attachment row `../../*` or `*` and point
    an asset at it; replacing the photo or removing the document then deleted files by it.
    Asset ops delete only their own well-formed uploads — a crafted row is left for the owner."""
    from backend.db.attachment_store import AttachmentStore

    statement = _upload("statement.txt")
    for crafted in ("../../*", "*"):
        AttachmentStore().insert(
            {"id": crafted, "filename": "x.txt", "source_channel": "assets:docs"}
        )
    watch = OwnedAssetsStore().create(
        {
            "name": "Watch",
            "category": "watch",
            "currency": "SGD",
            "photo_ref": "../../*",
            "docs": [{"ref_id": "*", "name": "everything"}],
            "marks": [{"date": "2026-01-01", "value": 100}],
        }
    )

    svc.apply_owned_asset_change({"op": "set_photo", "id": watch["id"], "ref": _upload("new.png")})
    _doc("remove_doc", watch["id"], "*")

    assert (deep_attachment_dir / "onw.db").read_text() == "the database"
    assert _stored(statement)
    assert _stored("../../*") and _stored("*")


def test_an_asset_never_deletes_an_attachment_it_did_not_upload(isolated, attachment_dir):
    """Removing a document from an asset deletes the file only when an asset route uploaded
    it — a chat upload (a bank statement, say) that an asset merely points at stays."""
    statement = _upload("bank.csv", channel="chat:web")
    home = _create_watch(name="Flat", category="property")
    _doc("add_doc", home["id"], statement, name="Bank statement")
    _doc("remove_doc", home["id"], statement)
    assert _stored(statement)


def test_a_document_or_photo_whose_file_is_gone_is_not_shown(isolated, attachment_dir):
    """Security review S2: attachments are outside checkpoints, so an undo can restore an
    asset row whose document or photo was deleted since — and deleting a file in Settings
    leaves the asset's reference too. The projection shows only what still exists."""
    from backend.services.attachments import delete_attachment

    watch = _create_watch()
    kept, gone, photo = _upload("kept.txt"), _upload("gone.txt"), _upload("photo.png")
    _doc("add_doc", watch["id"], kept, name="Kept")
    _doc("add_doc", watch["id"], gone, name="Gone")
    svc.apply_owned_asset_change({"op": "set_photo", "id": watch["id"], "ref": photo})
    delete_attachment(gone)
    delete_attachment(photo)

    for projected in (
        svc.get_asset(watch["id"]),
        svc.list_assets()[0],
        svc.get_assets_page()["assets"][0],
    ):
        assert [d["ref_id"] for d in projected["docs"]] == [kept]
        assert projected["photo_ref"] is None
    stored = OwnedAssetsStore().find_by_id(watch["id"])
    assert (len(stored["docs"]), stored["photo_ref"]) == (2, photo)  # nothing rewritten


def test_retirement_card_all_holdings_carries_its_return_on_the_card(isolated, monkeypatch):
    """No cash beside the holdings: one kind, so no rows — the return sits on the card."""
    from backend.services import ledger as ledger_service

    ira = _account(name="IRA", type="retirement")
    ledger_service.set_account_opening(ira["id"], amount=0)
    monkeypatch.setattr(
        svc.portfolio_analytics_service,
        "market_book",
        lambda base: {
            "upl": 25,
            "book": {
                "grand": 125,
                "total_pl": 25,
                "total_pl_pct": 25.0,
                "rows": [{"account_id": ira["id"], "value": 125, "pl": 25}],
            },
        },
    )

    retirement = svc.summary("SGD")["classes"]["retirement"]

    assert retirement["value"] == 125
    assert retirement["rows"] is None
    assert retirement["gain"] == 25
    assert retirement["gain_pct"] == 25.0
