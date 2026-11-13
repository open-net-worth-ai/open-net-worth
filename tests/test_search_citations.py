"""A price alert's search cites only what a search tool really returned (owner, 2026-11-13: the
push said what a search found but carried no link to it). A link the model writes on its own, or
copies out of a page's text, is never cited. Synthetic data only."""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.monitoring.price_alert_run as run
from backend.ai.tools import misc
from backend.core import search_citations
from backend.core.headless_turn import register_runner

_RESULTS = [
    {"url": "https://www.wire.example.com/acme-review", "title": "Regulator reviews Acme"},
    {"url": "https://x.com/someone/status/1", "title": "Acme chatter"},
]


@pytest.fixture(autouse=True)
def _clean():
    run._runs_by_day.clear()
    yield
    register_runner(None)
    run._runs_by_day.clear()


def test_cited_keeps_only_returned_urls_in_order():
    with search_citations.collecting() as seen:
        search_citations.record(_RESULTS)
    text = (
        "Wire reports a review.\nSources: https://x.com/someone/status/1, "
        "https://evil.example.org/phish https://www.wire.example.com/acme-review."
    )
    cited = search_citations.cited(text, seen)
    assert [c["url"] for c in cited] == [
        "https://x.com/someone/status/1",
        "https://www.wire.example.com/acme-review",
    ]
    assert cited[1]["outlet"] == "wire.example.com"
    assert cited[1]["title"] == "Regulator reviews Acme"


def test_record_outside_a_run_keeps_nothing():
    search_citations.record(_RESULTS)
    with search_citations.collecting() as seen:
        pass
    assert seen == {}


def test_without_urls_drops_the_sources_line_and_inline_links():
    text = "Wire reports a review (https://a.example.com/x).\nSources: https://a.example.com/x"
    assert search_citations.without_urls(text) == "Wire reports a review."


def test_the_web_search_tool_records_what_it_returned(monkeypatch):
    async def fake_results(provider, query, max_results):
        return list(_RESULTS)

    monkeypatch.setattr(misc, "search_results", fake_results)
    monkeypatch.setattr(misc, "get_search_provider", lambda: object())

    async def go():
        with search_citations.collecting() as seen:
            await misc.web_search({"query": "acme"})
        return seen

    assert set(asyncio.run(go())) == {r["url"] for r in _RESULTS}


def test_a_search_run_cites_the_results_its_tools_returned_and_nothing_else():
    async def runner(prompt, **kwargs):
        search_citations.record(_RESULTS)
        return (
            "Wire reports a regulator's review of Acme.\n"
            "Sources: https://www.wire.example.com/acme-review https://planted.example.net/a"
        )

    register_runner(runner)
    found = asyncio.run(run.search("p", model=""))
    assert found.text == "Wire reports a regulator's review of Acme."
    assert [s["url"] for s in found.sources] == ["https://www.wire.example.com/acme-review"]


def test_the_search_turn_is_asked_for_the_urls_it_relied_on():
    prompt = run.search_prompt("ACME", "ACME", "<=", 234.6, "")
    assert 'starting "Sources:"' in prompt
    assert "No links" not in prompt


def test_the_narration_attributes_and_never_says_unverified():
    block = run.findings_block("Wire reports a review.")
    assert "naming the outlet" in block
    assert 'Never say "web search" or "unverified"' in block


def test_tool_result_is_unchanged_by_recording(monkeypatch):
    async def fake_results(provider, query, max_results):
        return list(_RESULTS)

    monkeypatch.setattr(misc, "search_results", fake_results)
    monkeypatch.setattr(misc, "get_search_provider", lambda: object())
    text, _ = asyncio.run(misc.web_search({"query": "acme"}))
    assert [r["url"] for r in json.loads(text)] == [r["url"] for r in _RESULTS]
