"""Lucius's paper tools (PRD acceptance criterion 11): every result says ``simulated: true`` and
names its paper portfolio, a batch places sells before buys, the flag off refuses, and the
permission seed — paper writes Always allow, delete Ask — is read from the declarations."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai._confirm_gate_v2 import SIMULATION_DOMAINS, needs_approval
from backend.ai.tools import execute_tool
from backend.ai.tools._confirm_gate_v2 import is_gated_tool
from backend.ai.tools.registry import is_destructive, written_domains
from backend.db.paper_store import PaperPortfolioStore
from backend.services.paper_trading import qa_quotes, service
from tests.flags import flags

PAPER_TOOLS = (
    "get_paper_portfolios",
    "get_paper_results",
    "get_paper_activity",
    "preview_paper_orders",
    "create_paper_portfolio",
    "update_paper_portfolios",
    "delete_paper_portfolios",
    "place_paper_orders",
    "cancel_paper_orders",
    "record_paper_marks",
)
WRITES = PAPER_TOOLS[4:]


def call(_tool, **inp):
    text, _ = asyncio.run(execute_tool(_tool, inp))
    return json.loads(text)


def _walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def test_every_paper_tool_result_says_simulated(paper_db):
    made = call("create_paper_portfolio", name="Zone", cash=5000, home_currency="USD")
    assert made["simulated"] is True and made["portfolio"]["name"] == "Zone"
    placed = call(
        "place_paper_orders",
        portfolio="Zone",
        orders=[{"asset": "inst:inst_acme", "side": "buy", "quantity": 3, "note": "zone"}],
    )
    assert placed["simulated"] is True and placed["portfolio"] == "Zone"
    assert placed["results"][0]["summary"].startswith("Paper buy filled")
    outs = [
        made,
        placed,
        call("get_paper_portfolios"),
        call("get_paper_portfolios", portfolio="Zone"),
        call("get_paper_results", portfolios=["Zone"]),
        call("get_paper_activity", portfolio="Zone"),
        call(
            "preview_paper_orders",
            portfolio="Zone",
            orders=[{"asset": "inst:inst_acme", "side": "sell", "quantity": 1}],
        ),
        call("update_paper_portfolios", updates=[{"portfolio": "Zone", "name": "Zone 2"}]),
        call("cancel_paper_orders", order_ids=["pord_missing"]),
        call(
            "record_paper_marks", portfolio="Zone 2", marks=[{"asset_key": "ctr:nope", "price": 1}]
        ),
    ]
    for out in outs:
        assert out.get("simulated") is True, out


def test_results_score_trades_by_the_thesis_stance_stored_on_the_fill(paper_db):
    from backend.services import investment_theses

    investment_theses.apply_thesis_change(
        {"op": "set", "instrument_id": "inst_acme", "fields": {"buy_below": 105, "buy_above": 90}}
    )
    call("create_paper_portfolio", name="Z", cash=5000, home_currency="USD")
    call(
        "place_paper_orders",
        portfolio="Z",
        orders=[{"asset": "inst:inst_acme", "side": "buy", "quantity": 1}],
    )
    qa_quotes.set_state(prices={"ACME": 110.0})
    [res] = call("get_paper_results", portfolios=["Z"])["results"]
    [stance] = res["trades"]["by_thesis_stance"]
    assert stance["thesis"] == "In your buy zone" and stance["hit_rate"] == 1.0
    assert res["trades"]["count"] == 1


def test_a_batch_places_sells_before_buys(paper_db):
    call("create_paper_portfolio", name="B", cash=150, home_currency="USD")
    call(
        "place_paper_orders",
        portfolio="B",
        orders=[{"asset": "inst:inst_acme", "side": "buy", "quantity": 1}],
    )
    out = call(
        "place_paper_orders",
        portfolio="B",
        orders=[
            {"asset": "inst:inst_coin", "side": "buy", "quantity": 2},  # needs the sale's cash
            {"asset": "inst:inst_acme", "side": "sell", "quantity": 1},
        ],
    )
    assert [r["order"]["side"] for r in out["results"]] == ["sell", "buy"]
    assert all(r["ok"] for r in out["results"])


def test_orders_placed_by_lucius_say_so(paper_db):
    call("create_paper_portfolio", name="L", cash=500, home_currency="USD")
    call(
        "place_paper_orders",
        portfolio="L",
        orders=[{"asset": "inst:inst_acme", "side": "buy", "quantity": 1}],
    )
    pid = PaperPortfolioStore().all()[0]["id"]
    assert service.portfolio_orders(pid)[0]["origin"] == "lucius"


def test_paper_tools_refuse_while_the_flag_is_off(paper_db):
    with flags(paper_trading=False):
        for name in PAPER_TOOLS:
            text, _ = asyncio.run(
                execute_tool(
                    name,
                    {
                        "portfolio": "x",
                        "orders": [],
                        "order_ids": [],
                        "updates": [],
                        "portfolios": [],
                        "marks": [],
                    },
                )
            )
            out = json.loads(text)
            assert "switched off" in out["error"] and out["simulated"] is True, name


def test_paper_writes_are_seeded_always_allow_and_delete_asks():
    assert SIMULATION_DOMAINS == frozenset({"paper"})
    for name in WRITES:
        assert written_domains(name) == ("paper",), name
    for name in PAPER_TOOLS:
        assert is_gated_tool(name) is (name == "delete_paper_portfolios"), name
    assert is_destructive("delete_paper_portfolios")


@pytest.mark.parametrize(
    "domains, destructive, asks",
    [
        (("paper",), False, False),
        (("paper",), True, True),
        (("paper", "memory"), False, False),
        (("paper", "ledger"), False, True),  # mixed with a real domain still asks
        (("ledger",), False, True),
        ((), False, True),
    ],
)
def test_the_simulation_seed_rule(domains, destructive, asks):
    assert needs_approval("t", writes=True, domains=domains, destructive=destructive) is asks


def test_paper_tools_have_no_connector_and_no_external_effect():
    from backend.ai.tools.registry import _REGISTRY

    for name in PAPER_TOOLS:
        t = _REGISTRY[name]
        assert t.connector is None and not t.external_effect, name


def test_paper_writes_are_refused_on_the_public_demo():
    from backend.ai.tools.registry import _DEMO_REFUSED_TOOLS

    assert set(WRITES) <= _DEMO_REFUSED_TOOLS
    assert not set(PAPER_TOOLS[:4]) & _DEMO_REFUSED_TOOLS


def test_the_paper_rule_is_in_the_prompt_only_while_the_flag_is_on():
    from backend.ai import prompts

    with flags(paper_trading=True):
        assert prompts._paper_rule() == prompts.PAPER_RULE
        assert "never the owner's money" in prompts.PAPER_RULE
    with flags(paper_trading=False):
        assert prompts._paper_rule() == ""


def test_a_net_worth_answer_after_paper_trades_holds_no_paper_figure(paper_db):
    before = call("get_net_worth")
    call("create_paper_portfolio", name="Big", cash=9_999_999, home_currency="USD")
    call(
        "place_paper_orders",
        portfolio="Big",
        orders=[{"asset": "inst:inst_acme", "side": "buy", "quantity": 1000}],
    )
    after = call("get_net_worth")
    assert after == before
    assert "9999999" not in json.dumps(after) and "paper" not in json.dumps(after).lower()
