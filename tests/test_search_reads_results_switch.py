"""The ``search_reads_results`` switch (``ai/search/switch.py``), in both modes.

Off must be exactly what main ships: a search returns the engine's snippets and never fetches a
page, ``x_search`` is offered everywhere, and an Automation's ``always_allowed`` is read as written.
On: the top results carry page excerpts, ``x_search`` is out of every offer, and an Automation entry
for it counts as one for ``web_search``. Nothing stored changes either way, so on -> off -> on is
safe. Offline: providers are stubs, DNS is faked, nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import assistant
from backend.ai.automation_runner_adapter import _canonical_always_allowed_tools
from backend.ai.search import switch
from backend.ai.tools import _skill_scope, execute_tool
from backend.ai.tools._extra_groups_scope import set_extra_names
from backend.ai.tools._groups import _candidate_schemas, _search_tools, find_tools
from backend.ai.tools.catalog import build_tool_catalog
from backend.ai.tools.misc import web_search, x_search
from backend.core import feature_flags
from backend.db.search_config_store import SearchConfigStore
from tests.flags import flags

_QUERY = "Nvidia earnings guidance"
_PAGE = (
    "Nvidia reported record revenue and raised its earnings guidance for the next quarter, "
    "citing demand for Blackwell chips."
)


@pytest.fixture(autouse=True)
def _public_dns_and_clean_scopes(monkeypatch):
    monkeypatch.setattr(
        "backend.core.net_guard.socket.getaddrinfo",
        lambda host, *a, **kw: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    _skill_scope.clear_scope()
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_names(frozenset())


class _Provider:
    def __init__(self, urls: list[str]):
        self.rows = [
            {"url": u, "title": f"t{i}", "content": f"snippet {i}"} for i, u in enumerate(urls)
        ]
        self.searched: list[tuple[str, int]] = []
        self.fetched: list[str] = []
        self.provider_id = ""

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        self.searched.append((query, max_results))
        return [dict(r) for r in self.rows]

    async def fetch_page(self, url: str) -> str:
        self.fetched.append(url)
        return _PAGE


_URLS = [f"https://site{i}.example/a" for i in range(6)]


def _on() -> bool:
    return switch.reads_results()


def _catalog_names() -> str:
    return build_tool_catalog()


# ── the flag itself ──────────────────────────────────────────────────────────────


def test_the_flag_is_declared_off_by_default_and_expires():
    flag = feature_flags.get("search_reads_results")
    assert flag.default is False
    assert flag.kind == "release" and flag.owner == "owner"
    assert flag.remove_by.isoformat() == "2027-01-31"


# ── search tools: snippets off, excerpts on ──────────────────────────────────────


@pytest.mark.flag_both("search_reads_results")
def test_web_search_returns_snippets_off_and_excerpts_on(monkeypatch):
    provider = _Provider(_URLS)
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: provider)

    text, _ = asyncio.run(web_search({"query": _QUERY, "max_results": 6}))
    rows = json.loads(text)  # still the flat list the chat UI's source chips parse

    assert provider.searched == [(_QUERY, 6)]
    assert [r["content"] for r in rows] == [f"snippet {i}" for i in range(6)]
    if _on():
        assert provider.fetched and len(provider.fetched) == 3  # the top three
        assert all("raised its earnings guidance" in r["excerpt"] for r in rows[:3])
        assert all("excerpt" not in r for r in rows[3:])
    else:
        assert provider.fetched == []
        assert [set(r) for r in rows] == [{"url", "title", "content"}] * 6


@pytest.mark.flag_both("search_reads_results")
def test_a_per_provider_search_tool_follows_the_same_switch(monkeypatch):
    SearchConfigStore().update_provider("tavily", {"enabled": True, "api_key": "k"})
    provider = _Provider(_URLS[:2])
    monkeypatch.setattr(
        "backend.ai.tools.web_search_providers.build_provider", lambda pid, cfg: provider
    )

    text, _ = asyncio.run(execute_tool("tavily_search", {"query": _QUERY}))
    rows = json.loads(text)

    assert provider.searched == [(_QUERY, 8)]
    assert ("excerpt" in rows[0]) is _on()
    assert (len(provider.fetched) == 2) is _on()


@pytest.mark.flag_both("search_reads_results")
def test_a_failed_page_read_never_fails_the_search(monkeypatch):
    provider = _Provider(_URLS[:1])

    async def _boom(url):
        raise RuntimeError("boom")

    provider.fetch_page = _boom
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: provider)
    rows = json.loads(asyncio.run(web_search({"query": _QUERY}))[0])
    assert rows[0]["content"] == "snippet 0"
    assert rows[0].get("excerpt_unavailable") == ("error" if _on() else None)


@pytest.mark.flag_both("search_reads_results")
def test_worst_case_result_size_is_bounded(monkeypatch):
    big_page = "\n\n".join(
        f"Paragraph {i}: Nvidia earnings guidance was discussed at length on the call by {i} analysts."
        for i in range(500)
    )
    provider = _Provider([f"https://site{i}.example/a" for i in range(15)])

    async def _big(url):
        provider.fetched.append(url)
        return big_page

    provider.fetch_page = _big
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: provider)

    rows = json.loads(asyncio.run(web_search({"query": _QUERY, "max_results": 15}))[0])
    excerpts = [r["excerpt"] for r in rows if "excerpt" in r]
    assert len(excerpts) == (3 if _on() else 0)
    assert sum(len(e) for e in excerpts) <= 3 * 2_500


def test_the_tool_schemas_are_main_s_with_the_flag_either_way():
    """The schema is built once at import, so it cannot differ by flag: no model-facing knob."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "web_search")["input_schema"]
    assert set(schema["properties"]) == {"query", "max_results"}


# ── x_search: out of every offer on, exactly main's off ──────────────────────────


@pytest.mark.flag_both("search_reads_results")
def test_x_search_is_offered_only_while_the_flag_is_off():
    offered = not _on()

    assert ("x_search" in switch.retired_tools()) is _on()
    assert ("x_search" in _catalog_names()) is offered
    assert ("x_search" in {s["name"] for s in _candidate_schemas()}) is offered
    assert ("x_search" in _search_tools("search X twitter posts sentiment")) is offered

    text, _ = asyncio.run(find_tools({"names": ["x_search"]}))
    loaded = {entry["name"] for entry in json.loads(text).get("loaded", [])}
    assert ("x_search" in loaded) is offered


@pytest.mark.flag_both("search_reads_results")
def test_x_search_is_out_of_an_explicit_offer_and_a_sessions_loaded_names_only_when_on():
    _skill_scope.set_offer_scope({"web_search", "x_search"})
    assert (_skill_scope.offer_tool_names() == {"web_search"}) is _on()
    assert ("x_search" in _skill_scope.offer_tool_names()) is (not _on())

    _skill_scope.clear_scope()
    set_extra_names(frozenset({"x_search"}))  # loaded in an earlier turn, persisted in session meta
    core_offer = {t["name"] for t in assistant._build_core_offer()}
    assert ("x_search" in core_offer) is (not _on())


@pytest.mark.flag_both("search_reads_results")
def test_x_search_still_runs_when_called_by_name_with_main_s_behaviour(monkeypatch):
    """Retired from the offer, not removed: it is main's code, untouched, snippets only."""
    provider = _Provider(["https://x.com/u/status/1"])
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: provider)
    rows = json.loads(asyncio.run(x_search({"query": "$NVDA"}))[0])
    assert "site:x.com" in provider.searched[0][0]
    assert provider.fetched == []
    assert set(rows[0]) == {"url", "title", "content"}


# ── Automation always_allowed ────────────────────────────────────────────────────


def _allowed(*tools: str) -> list[dict]:
    return [{"source": "test", "description": "d", "tools": list(tools)}]


@pytest.mark.flag_both("search_reads_results")
def test_an_automation_entry_for_x_search_counts_as_one_for_web_search():
    canonical = _canonical_always_allowed_tools(_allowed("x_search"))
    assert ("web_search" in canonical) is _on()
    assert "x_search" in canonical  # the entry itself is never rewritten or dropped

    assert _canonical_always_allowed_tools(_allowed("web_search")) == {"web_search"}
    assert _canonical_always_allowed_tools(_allowed("calculate")) == {"calculate"}
    assert _canonical_always_allowed_tools(_allowed("not_a_tool")) == frozenset()


def test_with_stand_ins_adds_nothing_unasked_and_does_not_mutate():
    names = {"calculate"}
    with flags(search_reads_results=True):
        assert switch.with_stand_ins(names) == {"calculate"}
        original = {"x_search"}
        assert switch.with_stand_ins(original) == {"x_search", "web_search"}
        assert original == {"x_search"}


def test_nothing_stored_is_touched_and_on_off_on_is_safe():
    stored = _allowed("x_search")
    with flags(search_reads_results=True):
        on_first = _canonical_always_allowed_tools(stored)
    with flags(search_reads_results=False):
        off = _canonical_always_allowed_tools(stored)
        assert "x_search" in {t for d in stored for t in d["tools"]}
        assert "web_search" not in off and "x_search" in _catalog_names()
    with flags(search_reads_results=True):
        on_again = _canonical_always_allowed_tools(stored)
        assert "x_search" not in _catalog_names()
    assert on_first == on_again == {"x_search", "web_search"}
    assert stored == _allowed("x_search")


# ── every tool is still reachable, and MCP is untouched ──────────────────────────


@pytest.mark.flag_both("search_reads_results")
def test_every_registered_tool_is_in_the_catalog_unless_hidden():
    from backend.ai.tools import TOOL_SCHEMAS
    from backend.ai.tools._connector_gate import hidden_connector_tools
    from backend.ai.tools.catalog import hidden_tools
    from tests.test_tool_catalog import _catalog_names

    expected = {s["name"] for s in TOOL_SCHEMAS} - hidden_tools() - hidden_connector_tools()
    assert expected <= set(_catalog_names(build_tool_catalog()))
    assert ("x_search" in hidden_tools()) is _on()


@pytest.mark.flag_both("search_reads_results")
def test_mcp_inbound_still_lists_x_search():
    from backend.mcp.adapter import mcp_tool_descriptors

    assert "x_search" in {d["name"] for d in mcp_tool_descriptors()}


# ── the flag is read once per turn, and an unreadable flag hides nothing ─────────


def test_a_turn_reads_the_flag_once_and_a_restore_unpins_it(monkeypatch):
    from backend.ai.tools.catalog import hidden_tools

    reads: list[int] = []
    real = switch.enabled
    monkeypatch.setattr(switch, "enabled", lambda name: reads.append(1) or real(name))

    with flags(search_reads_results=True):
        snap = _skill_scope.snapshot_scope()
        _skill_scope.set_turn_retired(switch.retired_tools())  # what assistant.run does
        assert len(reads) == 1
        _skill_scope.set_offer_scope({"web_search", "x_search"})
        for _ in range(5):
            assert _skill_scope.offer_tool_names() == {"web_search"}
            assert "x_search" in hidden_tools()
            build_tool_catalog()
            _candidate_schemas()
        assert len(reads) == 1  # no offer build or dispatch touched the flag again

        with flags(search_reads_results=False):  # flipped mid-turn: the turn keeps its answer
            assert "x_search" in hidden_tools()
        _skill_scope.restore_scope(snap)

    assert _skill_scope.turn_retired() is None
    with flags(search_reads_results=False):
        assert "x_search" not in hidden_tools()


def test_an_unreadable_flag_hides_nothing_and_does_not_widen_find_tools(monkeypatch):
    from backend.db.app_config_store import AppConfigStore

    def _broken(self):
        raise RuntimeError("store down")

    monkeypatch.setattr(AppConfigStore, "get_feature_flags", _broken)

    assert switch.reads_results() is False
    assert switch.retired_tools() == frozenset()
    assert switch.with_stand_ins({"x_search"}) == {"x_search"}

    names = {s["name"] for s in _candidate_schemas()}  # does not raise
    assert "x_search" in names  # nothing retired
    assert "plaid_fetch_transactions" not in names  # connector-gated tools stay gated
    assert "serper_search" not in names
    text, _ = asyncio.run(find_tools({"names": ["plaid_fetch_transactions"]}))
    assert "plaid_fetch_transactions" not in {e["name"] for e in json.loads(text).get("loaded", [])}

    provider = _Provider(_URLS[:2])
    rows = asyncio.run(switch.search_results(provider, _QUERY, 8))
    assert provider.fetched == [] and "excerpt" not in rows[0]  # snippets, as when off


# ── the stand-in grant never overrides a stored Ask or Block ─────────────────────


@pytest.mark.parametrize(
    "stored,granted",
    [
        (None, True),
        ("always_allow", True),
        ("needs_approval", False),
        ("blocked", False),
    ],
)
def test_the_stand_in_is_not_granted_over_a_stored_ask_or_block(stored, granted):
    from backend.ai.tools._connector_gate import permission_owner
    from backend.services import tool_permissions

    if stored:
        tool_permissions.set_tool_permission(permission_owner("web_search"), "web_search", stored)
    with flags(search_reads_results=True):
        canonical = _canonical_always_allowed_tools(_allowed("x_search"))
    assert ("web_search" in canonical) is granted
    assert "x_search" in canonical  # the entry itself is the owner's, as written
