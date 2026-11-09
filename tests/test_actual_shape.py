"""Actual's tables -> the flat YNAB plan shape the sync engine already reads.

``shape`` is pure: these tests build ``Rows`` by hand. Every name and number is made up.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services.actual_sync import shape
from backend.services.actual_sync.shape import Rows
from backend.services.ynab_migration import mapping, source

TODAY = "2026-11-05"


def acct(id, name, off=0, closed=0, tomb=0):
    return {"id": id, "name": name, "offbudget": off, "closed": closed, "tombstone": tomb}


def txn(id, a, amount, date, **kw):
    row = {
        "id": id,
        "isParent": 0,
        "isChild": 0,
        "acct": a,
        "category": None,
        "amount": amount,
        "description": None,
        "notes": None,
        "date": date,
        "starting_balance_flag": 0,
        "transferred_id": None,
        "tombstone": 0,
        "cleared": 0,
        "parent_id": None,
        "reconciled": 0,
    }
    row.update(kw)
    return row


def child(id, parent, a, amount, date, **kw):
    return txn(id, a, amount, date, isChild=1, parent_id=parent, **kw)


def payee(id, name, transfer=None, tomb=0):
    return {"id": id, "name": name, "transfer_acct": transfer, "tombstone": tomb}


def cat(id, name, group, income=0, hidden=0, tomb=0):
    return {
        "id": id,
        "name": name,
        "cat_group": group,
        "is_income": income,
        "hidden": hidden,
        "tombstone": tomb,
    }


def group(id, name, income=0, hidden=0, tomb=0):
    return {"id": id, "name": name, "is_income": income, "hidden": hidden, "tombstone": tomb}


def budget(month, category, amount, carry=0):
    return {"month": month, "category": category, "amount": amount, "carryover": carry}


def identity(rows):
    return [{"id": r["id"], "transferId": r["id"]} for r in rows]


def build(**kw) -> Rows:
    rows = Rows(today=TODAY, **kw)
    if not rows.category_mapping:
        rows.category_mapping = identity(rows.categories)
    if not rows.payee_mapping:
        rows.payee_mapping = [{"id": p["id"], "targetId": p["id"]} for p in rows.payees]
    return rows


def base_rows(**extra) -> Rows:
    kw = dict(
        accounts=[acct("a1", "ACME Checking"), acct("a2", "ACME Savings", off=1)],
        payees=[
            payee("p1", "Corner Market"),
            payee("pt1", None, transfer="a1"),
            payee("pt2", None, transfer="a2"),
        ],
        category_groups=[group("g1", "Usual"), group("gi", "Income", income=1)],
        categories=[
            cat("c_food", "Food", "g1"),
            cat("c_fun", "Fun", "g1"),
            cat("c_pay", "Salary", "gi", income=1),
        ],
    )
    kw.update(extra)
    return build(**kw)


def plan_of(**extra) -> dict:
    return shape.plan_from_rows(base_rows(**extra))


def one(items, id):
    return next(i for i in items if i["id"] == id)


def by_month(plan, month):
    return next(m for m in plan["months"] if m["month"] == month)


# ── translation ──────────────────────────────────────────────────────────────


def test_cents_become_milliunits():
    plan = plan_of(
        transactions=[txn("t1", "a1", -2599, 20261013, category="c_food")],
        budgets=[budget(202610, "c_food", 50000)],
    )
    assert one(plan["transactions"], "t1")["amount"] == -25990
    assert one(plan["accounts"], "a1")["balance"] == -25990
    month = by_month(plan, "2026-10-01")
    food = one(month["categories"], "c_food")
    assert (food["budgeted"], food["activity"]) == (500000, -25990)


def test_a_fractional_amount_is_refused_not_rounded():
    with pytest.raises(ValidationError):
        plan_of(transactions=[txn("t1", "a1", -25.5, 20261013)])


def test_int_date_to_iso():
    plan = plan_of(transactions=[txn("t1", "a1", -100, 20240229)])
    assert one(plan["transactions"], "t1")["date"] == "2024-02-29"


@pytest.mark.parametrize("bad", [20261340, 2026, None, "soon"])
def test_a_bad_date_is_refused_or_not_live(bad):
    rows = base_rows(transactions=[txn("t1", "a1", -100, bad)])
    if bad is None:
        assert shape.plan_from_rows(rows)["transactions"] == []
    else:
        with pytest.raises(ValidationError):
            shape.plan_from_rows(rows)


def test_split_is_flat_with_transaction_id():
    plan = plan_of(
        transactions=[
            txn("s1", "a1", -10000, 20240606, isParent=1, description="p1"),
            child("s1a", "s1", "a1", -7000, 20240606, category="c_food", notes="veg"),
            child("s1b", "s1", "a1", -3000, 20240606, category="c_fun"),
        ]
    )
    assert [t["id"] for t in plan["transactions"]] == ["s1"]
    assert "subtransactions" not in plan["transactions"][0]
    lines = {s["id"]: s for s in plan["subtransactions"]}
    assert set(lines) == {"s1a", "s1b"}
    assert lines["s1a"]["transaction_id"] == "s1" and lines["s1a"]["memo"] == "veg"
    assert (lines["s1a"]["amount"], lines["s1b"]["amount"]) == (-70000, -30000)
    assert plan["transactions"][0]["category_id"] is None
    nested = source.nest_transactions(plan)
    assert [s["id"] for s in nested[0]["subtransactions"]] == ["s1a", "s1b"]
    assert nested[0]["payee_name"] == "Corner Market"
    assert one(plan["accounts"], "a1")["balance"] == -100000


def test_a_parents_amount_is_the_sum_of_its_live_lines():
    """Actual's own balance and budget read the lines, never the parent's stored figure."""
    plan = plan_of(
        transactions=[
            txn("s1", "a1", -9999, 20240606, isParent=1),
            child("s1a", "s1", "a1", -7000, 20240606, category="c_food"),
            child("s1b", "s1", "a1", -3000, 20240606, category="c_fun", tombstone=1),
            txn("s2", "a1", -5000, 20240607, isParent=1),
        ]
    )
    assert one(plan["transactions"], "s1")["amount"] == -70000
    assert one(plan["transactions"], "s2")["amount"] == 0
    assert one(plan["accounts"], "a1")["balance"] == -70000


def test_dead_rows_are_not_live():
    plan = plan_of(
        accounts=[acct("a1", "ACME Checking"), acct("a9", "Old", tomb=1)],
        transactions=[
            txn("t1", "a1", -100, 20240601, tombstone=1),
            txn("t2", "a9", -100, 20240601),
            txn("s1", "a1", -300, 20240602, isParent=1, tombstone=1),
            child("s1a", "s1", "a1", -300, 20240602),
            child("orphan", "gone", "a1", -50, 20240603),
            txn("t3", "a1", -700, 20240604),
        ],
    )
    assert [t["id"] for t in plan["transactions"]] == ["t3"]
    assert plan["subtransactions"] == []
    assert one(plan["accounts"], "a9")["deleted"] is True
    assert one(plan["accounts"], "a1")["balance"] == -7000


def test_account_fields_and_future_rows_count_in_the_balance():
    plan = plan_of(
        accounts=[acct("a1", "ACME Checking"), acct("a2", "ACME Savings", off=1, closed=1)],
        transactions=[
            txn("t1", "a1", 100000, 20261001, cleared=1),
            txn("t2", "a1", -2000, 20261002, cleared=0),
            txn("t3", "a1", -500, 20270101, cleared=1),
            txn("t4", "a1", 300, 20261003, cleared=0, reconciled=1),
        ],
    )
    a1, a2 = one(plan["accounts"], "a1"), one(plan["accounts"], "a2")
    assert a1["type"] == "checking" and a1["on_budget"] is True
    assert (a1["closed"], a1["deleted"]) == (False, False)
    assert a1["balance"] == (100000 - 2000 - 500 + 300) * 10
    assert a1["cleared_balance"] == (100000 - 500 + 300) * 10
    assert a2["on_budget"] is False and a2["closed"] is True and a2["balance"] == 0
    assert a1["transfer_payee_id"] == "pt1" and a2["transfer_payee_id"] == "pt2"


def test_transfer_links():
    plan = plan_of(
        transactions=[
            txn("x1", "a1", -25000, 20240607, description="pt2", transferred_id="x2"),
            txn("x2", "a2", 25000, 20240607, description="pt1", transferred_id="x1"),
        ]
    )
    out, inn = one(plan["transactions"], "x1"), one(plan["transactions"], "x2")
    assert out["transfer_transaction_id"] == "x2" and inn["transfer_transaction_id"] == "x1"
    assert out["transfer_account_id"] == "a2" and inn["transfer_account_id"] == "a1"
    payees = {p["id"]: p for p in plan["payees"]}
    assert payees["pt2"]["transfer_account_id"] == "a2"
    assert payees["pt2"]["name"] == "ACME Savings"
    assert payees["p1"]["transfer_account_id"] is None
    nested = source.nest_transactions(plan)
    assert one(nested, "x1")["payee_name"] == "ACME Savings"


def test_a_split_line_can_be_a_transfer():
    plan = plan_of(
        transactions=[
            txn("s1", "a1", -10000, 20240606, isParent=1),
            child("s1a", "s1", "a1", -4000, 20240606, description="pt2", transferred_id="m1"),
            child("s1b", "s1", "a1", -6000, 20240606, category="c_food"),
            txn("m1", "a2", 4000, 20240606, description="pt1", transferred_id="s1a"),
        ]
    )
    line = one(plan["subtransactions"], "s1a")
    assert line["transfer_account_id"] == "a2" and line["transfer_transaction_id"] == "m1"


def test_cleared_and_reconciled():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -1, 20240601, cleared=0),
            txn("t2", "a1", -1, 20240601, cleared=1),
            txn("t3", "a1", -1, 20240601, cleared=1, reconciled=1),
        ]
    )
    got = {t["id"]: t["cleared"] for t in plan["transactions"]}
    assert got == {"t1": "uncleared", "t2": "cleared", "t3": "reconciled"}


def test_is_income_kept():
    plan = plan_of()
    assert one(plan["categories"], "c_pay")["is_income"] is True
    assert one(plan["categories"], "c_food")["is_income"] is False
    assert one(plan["category_groups"], "gi")["is_income"] is True
    assert one(plan["categories"], "c_food")["category_group_id"] == "g1"


def test_hidden_and_deleted_carry_through_with_the_note():
    plan = plan_of(
        category_groups=[group("g1", "Usual", hidden=1), group("g2", "Gone", tomb=1)],
        categories=[
            cat("c1", "Food", "g1", hidden=1),
            cat("c2", "Old", "g2", tomb=1),
        ],
        notes=[{"id": "c1", "note": "ONW 2026-10: assigned 5 (was 4)"}, {"id": "zzz", "note": "x"}],
    )
    c1, c2 = one(plan["categories"], "c1"), one(plan["categories"], "c2")
    assert c1["hidden"] is True and c1["deleted"] is False and c1["note"].startswith("ONW 2026-10")
    assert c2["deleted"] is True and c2["note"] is None
    assert one(plan["category_groups"], "g1")["hidden"] is True
    assert one(plan["category_groups"], "g2")["deleted"] is True


def test_starting_balances_category_is_internal():
    plan = plan_of(
        categories=[
            cat("c_sb", "Starting Balances", "gi", income=1),
            cat("c_pay", "Salary", "gi", income=1),
            cat("c_food", "Food", "g1"),
        ],
        transactions=[txn("o1", "a1", 500000, 20261105, category="c_sb", starting_balance_flag=1)],
    )
    assert one(plan["categories"], "c_sb")["internal"] is True
    assert one(plan["categories"], "c_pay")["internal"] is False
    assert one(plan["categories"], "c_food")["internal"] is False
    plan_cats = mapping.plan_categories(
        [
            {
                **g,
                "categories": [c for c in plan["categories"] if c["category_group_id"] == g["id"]],
            }
            for g in plan["category_groups"]
        ],
        [],
        [],
    )
    assert plan_cats["skipped"] == {"c_sb": "internal"}
    assert "c_sb" not in [c["id"] for c in plan["months"][0]["categories"]]


def test_opening_row_payee_maps_to_sb():
    plan = plan_of(
        payees=[payee("p1", "Corner Market"), payee("psb", "Starting Balance")],
        transactions=[
            txn("o1", "a1", 500000, 20261105, description="psb", starting_balance_flag=1),
            txn("o2", "a2", 100000, 20261105, description=None, starting_balance_flag=1),
        ],
    )
    nested = source.nest_transactions(plan)
    for t in nested:
        assert t["payee_name"].strip().lower() == mapping.SB_PAYEE
    assert one(nested, "o1")["payee_id"] == "psb"
    assert one(nested, "o2")["payee_id"] not in (None, "psb")


def test_a_renamed_opening_payee_is_still_the_opening():
    plan = plan_of(
        payees=[payee("p9", "Opening")],
        transactions=[txn("o1", "a1", 500000, 20261105, description="p9", starting_balance_flag=1)],
    )
    assert one(source.nest_transactions(plan), "o1")["payee_name"] == "Starting Balance"


def test_category_through_category_mapping():
    """A merged-away category id on a row reads as the category it was merged into."""
    rows = base_rows(
        transactions=[
            txn("t1", "a1", -100, 20240601, category="c_old"),
            txn("t2", "a1", -100, 20240601, category="c_food"),
            txn("t3", "a1", -100, 20240601, category="c_unmapped"),
        ]
    )
    rows.category_mapping += [{"id": "c_old", "transferId": "c_food"}]
    plan = shape.plan_from_rows(rows)
    got = {t["id"]: t["category_id"] for t in plan["transactions"]}
    assert got == {"t1": "c_food", "t2": "c_food", "t3": None}
    month = by_month(plan, "2024-06-01")
    assert one(month["categories"], "c_food")["activity"] == -2000


def test_payee_through_payee_mapping():
    rows = base_rows(
        payees=[payee("p1", "Corner Market"), payee("p_dup", "Corner Mkt")],
        transactions=[txn("t1", "a1", -100, 20240601, description="p_dup")],
    )
    rows.payee_mapping = [
        {"id": "p1", "targetId": "p1"},
        {"id": "p_dup", "targetId": "p1"},
    ]
    plan = shape.plan_from_rows(rows)
    assert one(plan["transactions"], "t1")["payee_id"] == "p1"
    assert source.nest_transactions(plan)[0]["payee_name"] == "Corner Market"


def test_text_goes_through_clean():
    plan = plan_of(
        accounts=[acct("a1", "ACME «Checking»\x00")],
        payees=[payee("p1", "Corner» Market\x07")],
        transactions=[txn("t1", "a1", -100, 20240601, description="p1", notes="  «memo» ")],
    )
    assert one(plan["accounts"], "a1")["name"] == "ACME Checking"
    assert one(plan["payees"], "p1")["name"] == "Corner Market"
    assert one(plan["transactions"], "t1")["memo"] == "memo"


# ── months, Assigned and the available port ─────────────────────────────────


def month_cat(plan, month, cid):
    return one(by_month(plan, month)["categories"], cid)


def test_available_port():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -3000, 20260901, category="c_food"),
            txn("t2", "a1", -8000, 20261001, category="c_food"),
            txn("t3", "a1", -1000, 20261101, category="c_food"),
            txn("t4", "a1", -500, 20260915, category="c_fun"),
            txn("t5", "a1", -200, 20261003, category="c_fun"),
            txn("t6", "a1", 90000, 20261004, category="c_pay"),
            txn("t7", "a2", -777, 20261004, category="c_food"),
        ],
        budgets=[
            budget(202609, "c_food", 5000),
            budget(202610, "c_food", 1000),
            budget(202609, "c_fun", 100, carry=1),
        ],
    )
    sep, octo, nov = "2026-09-01", "2026-10-01", "2026-11-01"
    assert [m["month"] for m in plan["months"]] == [sep, octo, nov]
    sept_food = month_cat(plan, sep, "c_food")
    assert (sept_food["budgeted"], sept_food["activity"], sept_food["balance"]) == (
        50000,
        -30000,
        20000,
    )
    oct_food = month_cat(plan, octo, "c_food")
    assert (oct_food["activity"], oct_food["balance"]) == (-80000, 20000 + 10000 - 80000)
    assert oct_food["balance"] < 0
    nov_food = month_cat(plan, nov, "c_food")
    assert nov_food["balance"] == -10000  # the overspend reset to 0 at month end, then -1000


def test_a_carryover_flag_keeps_the_negative():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -3000, 20260901, category="c_food"),
            txn("t2", "a1", -200, 20261001, category="c_food"),
        ],
        budgets=[budget(202609, "c_food", 1000, carry=1), budget(202610, "c_food", 0)],
    )
    assert month_cat(plan, "2026-09-01", "c_food")["balance"] == -20000
    assert month_cat(plan, "2026-10-01", "c_food")["balance"] == -20000 - 2000
    assert month_cat(plan, "2026-09-01", "c_food").get("carryover") is True


def test_the_carry_flag_that_counts_is_last_months():
    plan = plan_of(
        transactions=[txn("t1", "a1", -3000, 20260901, category="c_food")],
        budgets=[budget(202609, "c_food", 1000), budget(202610, "c_food", 0, carry=1)],
    )
    assert month_cat(plan, "2026-10-01", "c_food")["balance"] == 0


def test_income_categories_have_no_balance_and_off_budget_rows_have_no_activity():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", 90000, 20261004, category="c_pay"),
            txn("t2", "a2", -500, 20261004, category="c_food"),
        ]
    )
    pay = month_cat(plan, "2026-10-01", "c_pay")
    assert (pay["activity"], pay["balance"]) == (900000, 0)
    assert month_cat(plan, "2026-10-01", "c_food")["activity"] == 0


def test_months_run_from_the_first_row_to_the_current_month_plus_budget_rows():
    plan = plan_of(
        transactions=[txn("t1", "a1", -100, 20260801, category="c_food")],
        budgets=[budget(202701, "c_food", 100)],
    )
    months = [m["month"] for m in plan["months"]]
    assert months[:4] == ["2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01"]
    assert months[-1] == "2027-01-01"
    assert "2026-12-01" not in months
    assert all(m["month"].endswith("-01") for m in plan["months"])
    assert "to_be_budgeted" not in plan["months"][0]
    assert month_cat(plan, "2027-01-01", "c_food")["budgeted"] == 1000


def test_a_budget_row_before_any_transaction_starts_the_months():
    plan = plan_of(
        transactions=[txn("t1", "a1", -100, 20261001, category="c_food")],
        budgets=[budget(202607, "c_food", 100)],
    )
    assert plan["months"][0]["month"] == "2026-07-01"
    assert month_cat(plan, "2026-09-01", "c_food")["balance"] == 1000


def test_an_empty_book_has_the_current_month():
    plan = plan_of()
    assert [m["month"] for m in plan["months"]] == ["2026-11-01"]


def test_a_bad_budget_month_is_refused():
    with pytest.raises(ValidationError):
        plan_of(budgets=[budget(202613, "c_food", 1)])


def test_tracking_mode_carries_nothing_unless_flagged():
    rows = base_rows(
        transactions=[txn("t1", "a1", -3000, 20260901, category="c_food")],
        budgets=[budget(202609, "c_food", 1000), budget(202610, "c_food", 500)],
        mode="tracking",
    )
    plan = shape.plan_from_rows(rows)
    assert plan["budget_mode"] == "tracking"
    assert month_cat(plan, "2026-10-01", "c_food")["balance"] == 5000


def test_amounts_pass_the_engines_own_validation():
    plan = plan_of(
        transactions=[
            txn("s1", "a1", -10000, 20240606, isParent=1),
            child("s1a", "s1", "a1", -10000, 20240606, category="c_food"),
        ],
        budgets=[budget(202606, "c_food", 100)],
    )
    nested = source.nest_transactions(plan)
    mapping.validate_amounts(plan["accounts"], nested, plan["months"])


# ── delta_against ───────────────────────────────────────────────────────────


def meta_for(plan):
    return {
        "accounts": [],
        "groups": [],
        "categories": [],
        "payees": {p["id"]: p["name"] for p in plan["payees"]},
        "months": [],
    }


def mirror_of(plan):
    return {t["id"]: t for t in source.nest_transactions(plan)}


def split_rows(*lines):
    rows = [txn("s1", "a1", sum(a for _, a in lines), 20240606, isParent=1, description="p1")]
    rows += [
        child(f"s1{i}", "s1", "a1", amt, 20240606, category=cid)
        for i, (cid, amt) in enumerate(lines)
    ]
    return rows


def test_unsplit_row_sends_deleted_lines():
    before = plan_of(transactions=split_rows(("c_food", -700), ("c_fun", -300)))
    mirror, meta = mirror_of(before), meta_for(before)
    after = plan_of(
        transactions=[txn("s1", "a1", -1000, 20240606, description="p1", category="c_food")]
    )
    delta = shape.delta_against(after, mirror, meta)
    assert [t["id"] for t in delta["transactions"]] == ["s1"]
    lines = {s["id"]: s for s in delta["subtransactions"]}
    assert set(lines) == {"s10", "s11"}
    assert all(s["deleted"] is True and s["transaction_id"] == "s1" for s in lines.values())
    from backend.services.ynab_sync import delta as engine_delta

    merged = dict(mirror)
    engine_delta.merge_transactions(delta, merged, meta, full=False)
    assert all(s["deleted"] for s in merged["s1"]["subtransactions"])
    assert merged["s1"]["category_id"] == "c_food"


def test_a_removed_line_is_reported_and_the_rest_stay():
    before = plan_of(transactions=split_rows(("c_food", -700), ("c_fun", -300)))
    after = plan_of(
        transactions=[
            txn("s1", "a1", -700, 20240606, isParent=1, description="p1"),
            child("s10", "s1", "a1", -700, 20240606, category="c_food"),
        ]
    )
    delta = shape.delta_against(after, mirror_of(before), meta_for(before))
    by_id = {s["id"]: s for s in delta["subtransactions"]}
    assert by_id["s11"]["deleted"] is True and not by_id["s10"].get("deleted")


def test_delta_is_only_what_changed_and_categories_months_accounts_stay_full():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -100, 20261001, category="c_food"),
            txn("t2", "a1", -200, 20261002, category="c_food"),
        ]
    )
    delta = shape.delta_against(plan, mirror_of(plan), meta_for(plan))
    assert delta["transactions"] == [] and delta["subtransactions"] == []
    assert delta["payees"] == []
    for key in ("accounts", "category_groups", "categories", "months"):
        assert delta[key] == plan[key]
    changed = plan_of(
        transactions=[
            txn("t1", "a1", -100, 20261001, category="c_food"),
            txn("t2", "a1", -250, 20261002, category="c_food"),
            txn("t3", "a1", -5, 20261003),
        ]
    )
    delta = shape.delta_against(changed, mirror_of(plan), meta_for(plan))
    assert sorted(t["id"] for t in delta["transactions"]) == ["t2", "t3"]


def test_a_removed_row_is_marked_deleted():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -100, 20261001),
            txn("t2", "a1", -200, 20261002),
        ]
    )
    after = plan_of(transactions=[txn("t1", "a1", -100, 20261001)])
    delta = shape.delta_against(after, mirror_of(plan), meta_for(plan))
    assert [(t["id"], t["deleted"]) for t in delta["transactions"]] == [("t2", True)]
    assert delta["transactions"][0]["amount"] == -2000


def test_a_renamed_payee_resends_its_rows_and_the_name():
    plan = plan_of(transactions=[txn("t1", "a1", -100, 20261001, description="p1")])
    renamed = plan_of(
        payees=[payee("p1", "Corner Store")],
        transactions=[txn("t1", "a1", -100, 20261001, description="p1")],
    )
    delta = shape.delta_against(renamed, mirror_of(plan), meta_for(plan))
    assert [t["id"] for t in delta["transactions"]] == ["t1"]
    assert [(p["id"], p["name"]) for p in delta["payees"]] == [("p1", "Corner Store")]


def test_a_first_read_sends_everything():
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -100, 20261001),
            *split_rows(("c_food", -700), ("c_fun", -300)),
        ]
    )
    delta = shape.delta_against(plan, {}, {"payees": {}})
    assert sorted(t["id"] for t in delta["transactions"]) == ["s1", "t1"]
    assert len(delta["subtransactions"]) == 2
    assert len(delta["payees"]) == len(plan["payees"])


def test_a_budget_row_for_a_dead_category_starts_no_month():
    plan = plan_of(
        categories=[cat("c_food", "Food", "g1"), cat("c_old", "Old", "g1", tomb=1)],
        budgets=[budget(209912, "c_old", 100), budget(202610, "c_food", 100)],
    )
    assert [m["month"] for m in plan["months"]] == ["2026-10-01", "2026-11-01"]


def test_a_live_child_under_a_plain_row_is_its_own_row_and_nothing_is_lost():
    """Actual sums every isParent = 0 row, so a stray child counts beside its non-split parent."""
    plan = plan_of(
        transactions=[
            txn("t1", "a1", -1000, 20261003, category="c_food"),
            child("stray", "t1", "a1", -300, 20261003, category="c_fun"),
        ]
    )
    assert plan["subtransactions"] == []
    assert {t["id"]: t["amount"] for t in plan["transactions"]} == {"t1": -10000, "stray": -3000}
    assert one(plan["accounts"], "a1")["balance"] == -13000
    month = by_month(plan, "2026-10-01")
    assert one(month["categories"], "c_food")["activity"] == -10000
    assert one(month["categories"], "c_fun")["activity"] == -3000
    pieces, _, _ = mapping.plan_transactions(
        source.nest_transactions(plan), plan["accounts"], {"categories": [], "skipped": {}}
    )
    assert sorted(p["from"][1] for p in pieces) == [3000, 10000]
