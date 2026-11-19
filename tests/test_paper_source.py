"""Where a paper portfolio came from, as its header says it: nothing for a cash start, a copy's
source linked only while it still exists, and a Start over of a copy whose source was unfollowed
says so instead of failing with the follow lookup's text."""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError
from backend.db.paper_store import PaperPortfolioStore
from backend.services.paper_trading import views
from backend.services.paper_trading.service import apply_paper_change


def _new(**kw):
    return apply_paper_change(
        {"op": "create", "name": "R", "cash": 10_000, "home_currency": "USD", **kw}
    )["portfolio"]


def test_a_cash_start_names_no_source(paper_db):
    row = _new()
    assert views.source(row) is None
    assert views.source_label(row) == ""


def test_a_copy_of_an_unfollowed_portfolio_keeps_its_label_without_a_link(paper_db):
    row = {
        **_new(),
        "origin": {"kind": "followed", "ref": "fol_gone", "label": "Copies ACME Fund"},
    }
    assert views.source(row) == {"kind": "followed", "label": "Copies ACME Fund"}


def test_an_automation_that_was_deleted_is_named_without_a_link(paper_db):
    row = {**_new(), "created_by": "automation:aut_gone"}
    assert views.source(row) == {
        "kind": "automation",
        "label": "Run by an Automation",
        "automation_id": None,
    }


def test_start_over_of_an_unfollowed_copy_says_why_and_keeps_the_run(paper_db):
    pid = _new()["id"]
    PaperPortfolioStore().update_by_id(
        pid,
        {
            "origin": {"kind": "followed", "ref": "fol_gone", "label": "Copies ACME Fund"},
            "start_spec": {"start_with": "followed", "cash": 10_000, "followed_id": "fol_gone"},
        },
    )
    with pytest.raises(ConflictError, match="you no longer follow that portfolio"):
        apply_paper_change({"op": "start_over", "id": pid})
    active = [p["id"] for p in PaperPortfolioStore().all() if p["status"] == "active"]
    assert active == [pid]
