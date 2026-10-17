"""Money-flow (Sankey) graph: every column must reconcile to the same total, or the chart lies.

The load-bearing assertions are the per-column sums and the per-node in == out balance — a
Sankey whose columns don't add up silently misstates where the money went.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import cash_flow_graph


def _row(key, label, amount, group_id, group, group_index=0):
    return {
        "key": key,
        "category_ids": [key] if key else [],
        "label": label,
        "color": "",
        "group_id": group_id,
        "group": group,
        "group_index": group_index,
        "amount": amount,
    }


def _inc(key, label, amount):
    """An income row — income belongs to no group, so it carries no group fields."""
    return {"key": key, "category_ids": [key], "label": label, "color": "", "amount": amount}


def _summary(income_rows, expense_rows):
    return {"ym": "2026-09", "income_rows": income_rows, "expense_rows": expense_rows}


SAVED_MONTH = _summary(
    [
        _inc("cat_salary", "Salary", 8400.0),
        _inc("cat_bonus", "Bonus", 1200.0),
        _inc("cat_div", "Dividends", 380.0),
        _inc("cat_int", "Interest", 140.0),
    ],
    [
        _row("cat_rent", "Rent", 2600.0, "g_home", "Housing", 1),
        _row("cat_util", "Utilities", 300.0, "g_home", "Housing", 1),
        _row("cat_groc", "Groceries", 900.0, "g_living", "Everyday living", 2),
        _row("cat_gum", "Gum", 12.5, "g_living", "Everyday living", 2),
        _row("cat_mints", "Mints", 7.5, "g_living", "Everyday living", 2),
        _row(None, "Uncategorized", 400.0, "__unsorted__", "Uncategorized", 9),
    ],
)

OVERSPENT_MONTH = _summary(
    [_inc("cat_salary", "Salary", 3000.0)],
    [
        _row("cat_rent", "Rent", 2600.0, "g_home", "Housing", 1),
        _row("cat_travel", "Travel", 1400.0, "g_fun", "Lifestyle", 3),
    ],
)


def _column_sums(graph):
    sums = defaultdict(float)
    for n in graph["nodes"]:
        sums[n["column"]] += n["amount"]
    return sums


def _assert_balanced(graph):
    """Every column carries the whole flow — except the full view's category column, which is
    spending only (Saved ends a column earlier) — and every node's in-links and out-links
    each equal its own amount."""
    total, last = graph["flow_total"], graph["columns"] - 1
    for col, amount in _column_sums(graph).items():
        # The category column (last, when there is one) carries spending only — Saved ends a
        # column earlier.
        categories_col = last if graph["columns"] > 3 else None
        expected = graph["spend_total"] if col == categories_col else total
        assert amount == pytest.approx(expected, abs=0.02), f"column {col} does not reconcile"
    inflow, outflow = defaultdict(float), defaultdict(float)
    for link in graph["links"]:
        outflow[link["source"]] += link["amount"]
        inflow[link["target"]] += link["amount"]
    for n in graph["nodes"]:
        if n["column"] > 0:
            assert inflow[n["id"]] == pytest.approx(n["amount"], abs=0.02), n["id"]
        ends_here = n["column"] == last or n["kind"] == "saved"
        if not ends_here:
            assert outflow[n["id"]] == pytest.approx(n["amount"], abs=0.02), n["id"]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("summary", [SAVED_MONTH, OVERSPENT_MONTH])
def test_every_column_reconciles_to_the_flow_total(summary, compact):
    _assert_balanced(cash_flow_graph.build_graph(summary, compact=compact))


def test_a_surplus_month_ends_in_a_saved_node():
    graph = cash_flow_graph.build_graph(SAVED_MONTH)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert graph["income_total"] == 10120.0
    assert graph["spend_total"] == 4220.0
    assert by_id["saved"]["amount"] == 5900.0
    assert by_id["saved"]["column"] == 2
    assert by_id["total"]["label"] == "Income"
    assert graph["savings_rate_pct"] == 58.3
    assert "shortfall" not in by_id


def test_an_overspent_month_is_covered_by_a_from_savings_node_not_hidden():
    graph = cash_flow_graph.build_graph(OVERSPENT_MONTH)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert graph["shortfall"] == 1000.0
    assert by_id["shortfall"]["amount"] == 1000.0
    assert by_id["shortfall"]["column"] == 0
    assert by_id["total"]["label"] == "Spent"
    assert by_id["total"]["amount"] == 4000.0
    assert "saved" not in by_id
    assert graph["savings_rate_pct"] == 0.0


def test_categories_under_two_percent_fold_into_their_groups_other():
    graph = cash_flow_graph.build_graph(SAVED_MONTH)
    labels = {n["label"]: n for n in graph["nodes"] if n["column"] == 3}
    assert "Gum" not in labels and "Mints" not in labels
    assert labels["Other everyday living"]["amount"] == 20.0
    # Interest is 1.4% of the flow: it folds on the income side too, into one "Other income".
    # Income is never folded by size (see test_small_income_sources_are_named_not_folded_into_other_income).
    assert "Interest" in {n["label"] for n in graph["nodes"]}


def test_spend_category_nodes_carry_their_ledger_categories_for_drill_down():
    graph = cash_flow_graph.build_graph(SAVED_MONTH)
    rent = next(n for n in graph["nodes"] if n["label"] == "Rent")
    unsorted = next(
        n for n in graph["nodes"] if n["label"] == "Uncategorized spending" and n["column"] == 3
    )
    assert rent["category_ids"] == ["cat_rent"]
    assert rent["uncategorized"] is False
    assert unsorted["category_ids"] == []
    assert unsorted["uncategorized"] is True


def test_an_envelope_row_drills_to_its_categories_never_to_the_envelope_id():
    """Regression: the envelope id (env_…) was sent as a category, so the register matched
    nothing and showed "Category: Uncategorized"."""
    from backend.services.reports_rollup import _row_category_ids

    assert _row_category_ids({"key": "env_1", "category_ids": ["cat_a", "cat_b"]}) == [
        "cat_a",
        "cat_b",
    ]
    assert _row_category_ids({"key": "env_1", "category_ids": []}) == []
    assert _row_category_ids({"key": "cat_x", "category_ids": None}) == ["cat_x"]
    assert _row_category_ids({"key": None}) == []


def test_group_colour_follows_planning_order_not_this_months_rank():
    graph = cash_flow_graph.build_graph(SAVED_MONTH)
    slots = {n["label"]: n["color_slot"] for n in graph["nodes"] if n["kind"] == "spend_group"}
    # Housing is the bigger group AND first in Planning order; Uncategorized is last in Planning.
    assert slots == {"Housing": 0, "Everyday living": 1, "Uncategorized spending": 2}


def test_compact_is_three_columns_with_capped_income():
    many = _summary(
        [_inc(f"cat_{i}", f"Source {i}", 1000.0 - i) for i in range(6)],
        [_row("cat_rent", "Rent", 500.0, "g_home", "Housing")],
    )
    graph = cash_flow_graph.build_graph(many, compact=True)
    assert graph["columns"] == 3
    assert {n["column"] for n in graph["nodes"]} == {0, 1, 2}
    income_labels = [n["label"] for n in graph["nodes"] if n["kind"] == "income"]
    assert income_labels == ["Source 0", "Source 1", "Source 2", "Other income"]
    _assert_balanced(graph)


def test_an_empty_month_is_an_empty_graph_not_an_error():
    graph = cash_flow_graph.build_graph(_summary([], []))
    assert graph["flow_total"] == 0
    assert graph["links"] == []
    assert graph["savings_rate_pct"] is None


def test_default_month_is_the_last_full_month():
    assert cash_flow_graph.default_month(date(2026, 10, 1)) == "2026-09"
    assert cash_flow_graph.default_month(date(2026, 1, 15)) == "2025-12"


def test_endpoint_rejects_a_malformed_month_and_serves_a_valid_one(monkeypatch):
    monkeypatch.setattr(cash_flow_graph, "month_summary", lambda ym: SAVED_MONTH)
    client = TestClient(app)
    assert client.get("/api/cashflow/flow?month=2026-13").status_code == 400
    body = client.get("/api/cashflow/flow?month=2026-09").json()
    assert body["month"] == "2026-09"
    assert body["columns"] == 4
    assert client.get("/api/cashflow/flow?month=2026-09&compact=true").json()["columns"] == 3


def test_folding_inside_a_group_called_other_does_not_say_other_other():
    summary = _summary(
        [],
        [
            _row("cat_a", "Alpha", 9000.0, "g_other", "Others"),
            _row("cat_misc", "Misc", 50.0, "g_other", "Others"),
            _row("cat_odds", "Odds", 40.0, "g_other", "Others"),
            _row("cat_b", "Beta", 3000.0, "g_work", "Work"),
        ],
    )
    labels = [n["label"] for n in cash_flow_graph.build_graph(summary)["nodes"]]
    assert "Other" in labels
    assert not any(label.lower().startswith("other other") for label in labels)


def test_each_groups_category_shares_add_up_to_the_whole_group():
    graph = cash_flow_graph.build_graph(SAVED_MONTH)
    per_group = defaultdict(float)
    parent = {link["target"]: link["source"] for link in graph["links"]}
    for n in graph["nodes"]:
        if n["kind"] == "spend":
            per_group[parent[n["id"]]] += n["group_pct"]
    assert per_group and all(v == pytest.approx(100, abs=0.2) for v in per_group.values())


ONE_SOURCE_PER_GROUP = _summary(
    [_inc("cat_salary", "Salary", 9000.0), _inc("cat_div", "Dividends", 600.0)],
    [_row("cat_rent", "Rent", 3000.0, "g_home", "Housing")],
)


def test_income_is_always_flat_and_the_full_graph_is_always_four_columns():
    """Income categories belong to no group: they link straight to the total, however many
    sources there are — no income-group column, ever."""
    for summary in (ONE_SOURCE_PER_GROUP, SAVED_MONTH, OVERSPENT_MONTH):
        graph = cash_flow_graph.build_graph(summary)
        assert graph["columns"] == 4
        assert {n["kind"] for n in graph["nodes"]} <= {
            "income",
            "total",
            "shortfall",
            "spend_group",
            "spend",
            "saved",
        }
        by_id = {n["id"]: n for n in graph["nodes"]}
        assert by_id["total"]["column"] == 1
        assert not any(n["id"].startswith("ig:") for n in graph["nodes"])
        for n in graph["nodes"]:
            if n["kind"] == "income":
                assert n["column"] == 0
        _assert_balanced(graph)
    graph = cash_flow_graph.build_graph(ONE_SOURCE_PER_GROUP)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["sg:g_home"]["column"] == 2
    assert by_id["sp:cat_rent"]["column"] == 3
    assert {"source": "inc:cat_salary", "target": "total", "amount": 9000.0} in graph["links"]


def test_income_rows_that_still_carry_a_group_are_drawn_flat():
    row = {**_inc("cat_salary", "Salary", 5000.0), "group_id": "grp_x", "group": "__income__"}
    graph = cash_flow_graph.build_graph(
        _summary([row], [_row("cat_rent", "Rent", 2000.0, "g", "Home")])
    )
    labels = {n["label"] for n in graph["nodes"]}
    assert "__income__" not in labels
    assert graph["columns"] == 4


def test_an_overspent_month_still_reconciles_with_the_shortfall_linked_to_the_total():
    summary = _summary(
        ONE_SOURCE_PER_GROUP["income_rows"],
        [_row("cat_rent", "Rent", 12000.0, "g_home", "Housing")],
    )
    graph = cash_flow_graph.build_graph(summary)
    assert graph["columns"] == 4
    assert {"source": "shortfall", "target": "total", "amount": 2400.0} in graph["links"]
    _assert_balanced(graph)


def test_small_income_sources_are_named_not_folded_into_other_income():
    """Owner: S$15 of dividends showed as an anonymous "Other income"."""
    summary = _summary(
        [
            _row("cat_salary", "Salary", 16500.0, "g", "Income"),
            _row("cat_div", "Dividends", 14.64, "g", "Income"),
            _row("cat_int", "Interest", 0.40, "g", "Income"),
        ],
        [_row("cat_rent", "Rent", 3000.0, "g_home", "Housing")],
    )
    labels = [
        n["label"] for n in cash_flow_graph.build_graph(summary)["nodes"] if n["kind"] == "income"
    ]
    assert labels == ["Salary", "Dividends", "Interest"]


def test_uncategorized_money_is_named_for_its_side():
    """Persona QA 2026-10-14: unsorted salary read "Uncategorized S$5,404" on the income side and
    unsorted spending read "Spending S$218" (history rows have no group, so the compact card
    showed the fallback group name)."""
    history_row = _row("", "Uncategorized", 218.0, "", "", -1)
    month = _summary(
        [_inc("", "Uncategorized income", 5404.0)],
        [history_row],
    )
    graph = cash_flow_graph.build_graph(month, compact=True)
    labels = {n["label"] for n in graph["nodes"]}
    assert "Uncategorized income" in labels
    assert "Uncategorized spending" in labels
    assert "Spending" not in labels


def test_a_group_with_real_categories_keeps_its_own_name():
    month = _summary(
        [_inc("cat_salary", "Salary", 3000.0)],
        [
            _row("cat_rent", "Rent", 1000.0, "g_home", "Housing", 1),
            _row("", "Uncategorized", 100.0, "", "", -1),
            _row("cat_loose", "Loose", 50.0, "", "", -1),
        ],
    )
    groups = [
        n["label"]
        for n in cash_flow_graph.build_graph(month)["nodes"]
        if n["kind"] == "spend_group"
    ]
    assert "Housing" in groups
    assert "Spending" in groups  # history-only rows of a real, ungrouped category


def _spend_labels(summary):
    return {
        n["label"]: n for n in cash_flow_graph.build_graph(summary)["nodes"] if n["column"] == 3
    }


def test_a_single_small_category_is_named_never_folded_into_other_alone():
    """An "Other" holding one category only hides its name (owner: "why so many Other?")."""
    summary = _summary(
        [_inc("cat_salary", "Salary", 10000.0)],
        [
            _row("cat_rent", "Rent", 3000.0, "g_home", "Housing"),
            _row("cat_wifi", "Wifi", 40.0, "g_home", "Housing"),
        ],
    )
    labels = _spend_labels(summary)
    assert labels["Wifi"]["amount"] == 40.0
    assert "Other housing" not in labels


def test_a_group_whose_every_category_is_small_names_them_instead_of_one_other():
    summary = _summary(
        [_inc("cat_salary", "Salary", 10000.0)],
        [
            _row("cat_rent", "Rent", 5000.0, "g_home", "Housing"),
            _row("cat_book", "Books", 60.0, "g_fun", "Wants"),
            _row("cat_film", "Films", 50.0, "g_fun", "Wants"),
            _row("cat_game", "Games", 40.0, "g_fun", "Wants"),
        ],
    )
    labels = _spend_labels(summary)
    assert {"Books", "Films", "Games"} <= labels.keys()
    assert "Other wants" not in labels


def test_spend_outside_the_budget_is_named_category_by_category():
    """Planning's derived Unbudgeted group: categories with no envelope. Each is named however
    small, and the group reads "Not in budget", never as a kind of spending."""
    summary = _summary(
        [_inc("cat_salary", "Salary", 10000.0)],
        [
            _row("cat_rent", "Rent", 5000.0, "g_home", "Housing"),
            _row("cat_taxi", "Taxi", 120.0, "__unbudgeted__", "Unbudgeted"),
            _row("cat_gift", "Gifts", 76.0, "__unbudgeted__", "Unbudgeted"),
            _row("cat_stamp", "Stamps", 3.0, "__unbudgeted__", "Unbudgeted"),
        ],
    )
    graph = cash_flow_graph.build_graph(summary)
    groups = {n["label"] for n in graph["nodes"] if n["kind"] == "spend_group"}
    assert "Not in budget" in groups and "Unbudgeted" not in groups
    labels = _spend_labels(summary)
    assert {"Taxi", "Gifts", "Stamps"} <= labels.keys()
    assert not any(label.startswith("Other") for label in labels)
