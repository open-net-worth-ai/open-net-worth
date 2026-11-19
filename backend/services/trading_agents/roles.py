"""Which role of a run a model call belongs to — the cost panel's "by role" breakdown.

The worker reports the TOP-LEVEL LangGraph node a call ran under (an analyst is a sub-graph, so its
inner ``agent`` / ``tools`` nodes inherit the analyst's name — see the worker); this is the one
table that turns that node name into a role. Node names are TradingAgents' own, read from the
pinned commit (``docs/specs/trading-agents-pinned-findings.md``): a bump of the pin re-checks them
against ``tests/fixtures/trading_agents/ta_nodes.json``, which fails on a node this table lacks.
"""

from __future__ import annotations

from backend.services.trading_agents.runs import ROLE_OTHER

ROLE_BY_NODE: dict[str, str] = {
    "Market Analyst": "analyst_market",
    "Sentiment Analyst": "analyst_sentiment",
    "News Analyst": "analyst_news",
    "Fundamentals Analyst": "analyst_fundamentals",
    "Bull Researcher": "research_debate",
    "Bear Researcher": "research_debate",
    "Research Manager": "research_manager",
    "Trader": "trader",
    "Aggressive Analyst": "risk_debate",
    "Conservative Analyst": "risk_debate",
    "Neutral Analyst": "risk_debate",
    "Portfolio Manager": "portfolio_manager",
    "Memory Log": "memory",
}


def role_for(node: object) -> str:
    """The role of ``node``; ``other`` for a node nobody mapped (or no node at all)."""
    return ROLE_BY_NODE.get(node, ROLE_OTHER) if isinstance(node, str) else ROLE_OTHER
