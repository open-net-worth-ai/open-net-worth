"""Unit tests for the mention search engine (services/mentions/search.py).

Tests:
- Ranking: exact label match > startswith > substring
- Cursor pagination: offset slicing + next_cursor
- surface / types filtering
- empty-q stable order
- limit cap
- bad cursor falls back to offset 0
"""

from __future__ import annotations

import base64

import backend.services.mentions.registry as _reg
from backend.services.mentions.search import search
from backend.services.mentions.types import MentionProvider, SearchQuery

# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_provider(type_: str, rows: list[dict]) -> MentionProvider:
    """Build a provider whose search fn returns the given rows regardless of query."""
    return MentionProvider(
        type=type_,
        surfaces=frozenset({"entity"}),
        icon="◆",
        resolve=lambda ref: None,
        search=lambda q: rows,
    )


def _query(
    q: str = "",
    types=None,
    surfaces=None,
    limit: int = 20,
    cursor: str | None = None,
) -> SearchQuery:
    return SearchQuery(q=q, types=types, surfaces=surfaces, limit=limit, cursor=cursor)


# ── Ranking ──────────────────────────────────────────────────────────────────


def test_ranking_exact_before_prefix_before_substring(monkeypatch):
    rows = [
        {
            "type": "t",
            "id": "c",
            "label": "contains apple here",
            "sublabel": "",
            "value": "c",
            "icon": "◆",
        },
        {"type": "t", "id": "b", "label": "apple pie", "sublabel": "", "value": "b", "icon": "◆"},
        {"type": "t", "id": "a", "label": "apple", "sublabel": "", "value": "a", "icon": "◆"},
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    result = search(_query(q="apple", types=frozenset({"t"}), surfaces=frozenset({"entity"})))
    labels = [r["label"] for r in result["items"]]
    assert labels == ["apple", "apple pie", "contains apple here"]


def test_padded_query_ranks_same_as_stripped(monkeypatch):
    """A query with surrounding whitespace ("  apple  ") must rank identically to "apple"."""
    rows = [
        {
            "type": "t",
            "id": "c",
            "label": "contains apple here",
            "sublabel": "",
            "value": "c",
            "icon": "◆",
        },
        {"type": "t", "id": "b", "label": "apple pie", "sublabel": "", "value": "b", "icon": "◆"},
        {"type": "t", "id": "a", "label": "apple", "sublabel": "", "value": "a", "icon": "◆"},
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    result = search(_query(q="  apple  ", types=frozenset({"t"}), surfaces=frozenset({"entity"})))
    labels = [r["label"] for r in result["items"]]
    # exact match must still sort first, not be collapsed to the lowest tier
    assert labels[0] == "apple", f"exact match not first: {labels}"
    assert labels == ["apple", "apple pie", "contains apple here"]


def test_empty_q_preserves_provider_then_input_order(monkeypatch):
    rows = [
        {"type": "t", "id": "1", "label": "Zeta", "sublabel": "", "value": "1", "icon": "◆"},
        {"type": "t", "id": "2", "label": "Alpha", "sublabel": "", "value": "2", "icon": "◆"},
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    result = search(_query(q="", types=frozenset({"t"}), surfaces=frozenset({"entity"})))
    # empty q → rank tier (3,) for all; input order preserved (stable sort)
    ids = [r["id"] for r in result["items"]]
    assert ids == ["1", "2"]


# ── Cursor pagination ─────────────────────────────────────────────────────────


def test_cursor_slices_correctly_and_next_cursor_set(monkeypatch):
    rows = [
        {
            "type": "t",
            "id": str(i),
            "label": f"Item {i}",
            "sublabel": "",
            "value": str(i),
            "icon": "◆",
        }
        for i in range(10)
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    r1 = search(_query(q="", types=frozenset({"t"}), surfaces=frozenset({"entity"}), limit=4))
    assert len(r1["items"]) == 4
    assert r1["next_cursor"] is not None

    # Decode the cursor and verify offset
    offset_val = int(base64.b64decode(r1["next_cursor"].encode()).decode())
    assert offset_val == 4

    r2 = search(
        _query(
            q="",
            types=frozenset({"t"}),
            surfaces=frozenset({"entity"}),
            limit=4,
            cursor=r1["next_cursor"],
        )
    )
    assert len(r2["items"]) == 4
    assert [item["id"] for item in r2["items"]] == ["4", "5", "6", "7"]


def test_last_page_has_no_next_cursor(monkeypatch):
    rows = [
        {
            "type": "t",
            "id": str(i),
            "label": f"Item {i}",
            "sublabel": "",
            "value": str(i),
            "icon": "◆",
        }
        for i in range(3)
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    r = search(_query(q="", types=frozenset({"t"}), surfaces=frozenset({"entity"}), limit=10))
    assert len(r["items"]) == 3
    assert r["next_cursor"] is None


def test_bad_cursor_falls_back_to_offset_zero(monkeypatch):
    rows = [
        {"type": "t", "id": "x", "label": "X", "sublabel": "", "value": "x", "icon": "◆"},
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    r = search(
        _query(
            q="",
            types=frozenset({"t"}),
            surfaces=frozenset({"entity"}),
            cursor="not-valid-base64!!!",
        )
    )
    assert len(r["items"]) == 1
    assert r["items"][0]["id"] == "x"


# ── Filtering ─────────────────────────────────────────────────────────────────


def test_types_filter_excludes_non_matching_providers(monkeypatch):
    rows_a = [{"type": "a", "id": "1", "label": "A", "sublabel": "", "value": "1", "icon": "◆"}]
    rows_b = [{"type": "b", "id": "2", "label": "B", "sublabel": "", "value": "2", "icon": "◆"}]
    monkeypatch.setitem(_reg.REGISTRY, "a", _fake_provider("a", rows_a))
    monkeypatch.setitem(_reg.REGISTRY, "b", _fake_provider("b", rows_b))

    r = search(_query(q="", types=frozenset({"a"}), surfaces=frozenset({"entity"})))
    assert all(item["type"] == "a" for item in r["items"])


def test_surface_filter_excludes_non_matching_providers(monkeypatch):
    # Create a provider that only has surface "dashboard", not "entity"
    dashboard_provider = MentionProvider(
        type="widget",
        surfaces=frozenset({"dashboard"}),
        icon="◆",
        resolve=lambda ref: None,
        search=lambda q: [
            {
                "type": "widget",
                "id": "w1",
                "label": "Widget",
                "sublabel": "",
                "value": "w1",
                "icon": "◆",
            }
        ],
    )
    monkeypatch.setitem(_reg.REGISTRY, "widget", dashboard_provider)

    # Querying with surface=entity must not return widget rows.
    r = search(_query(q="", types=None, surfaces=frozenset({"entity"})))
    assert all(item["type"] != "widget" for item in r["items"])


def test_no_surface_filter_returns_all_providers(monkeypatch):
    rows = [{"type": "t", "id": "1", "label": "T", "sublabel": "", "value": "1", "icon": "◆"}]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    # surfaces=None means no surface filter — all entity providers match
    r = search(_query(q="", types=frozenset({"t"}), surfaces=None))
    assert any(item["id"] == "1" for item in r["items"])


# ── Limit cap ─────────────────────────────────────────────────────────────────


def test_limit_is_capped_at_50(monkeypatch):
    rows = [
        {"type": "t", "id": str(i), "label": f"I{i}", "sublabel": "", "value": str(i), "icon": "◆"}
        for i in range(100)
    ]
    monkeypatch.setitem(_reg.REGISTRY, "t", _fake_provider("t", rows))
    r = search(_query(q="", types=frozenset({"t"}), surfaces=frozenset({"entity"}), limit=99))
    assert len(r["items"]) <= 50


# ── Provider failure isolation ────────────────────────────────────────────────


def test_provider_exception_is_isolated(monkeypatch):
    def _bad_search(q):
        raise RuntimeError("explode")

    bad = MentionProvider(
        type="bad",
        surfaces=frozenset({"entity"}),
        icon="◆",
        resolve=lambda ref: None,
        search=_bad_search,
    )
    good_rows = [
        {"type": "good", "id": "1", "label": "Good", "sublabel": "", "value": "1", "icon": "◆"}
    ]
    good = _fake_provider("good", good_rows)
    monkeypatch.setitem(_reg.REGISTRY, "bad", bad)
    monkeypatch.setitem(_reg.REGISTRY, "good", good)

    r = search(_query(q="", types=frozenset({"bad", "good"}), surfaces=frozenset({"entity"})))
    assert any(item["id"] == "1" for item in r["items"])
    assert all(item["type"] != "bad" for item in r["items"])


# ── Type qualifier ("@payee vietnam airlines") ───────────────────────────────


def _recording_provider(type_: str, seen: dict[str, str]) -> MentionProvider:
    """A provider that records the text it was asked for and returns one row named by it."""

    def _search(q: SearchQuery) -> list[dict]:
        seen[type_] = q.q
        return [{"type": type_, "id": type_, "label": "Vietnam Airlines"}]

    return MentionProvider(
        type=type_, surfaces=frozenset({"entity"}), icon="◆", resolve=lambda r: None, search=_search
    )


def test_leading_type_word_scopes_that_type_to_the_rest(monkeypatch):
    """A type named first searches the rest of the phrase; every other type still gets the
    whole phrase, so a name that merely starts with a type word keeps matching as typed."""
    seen: dict[str, str] = {}
    for t in ("payee", "transaction"):
        monkeypatch.setitem(_reg.REGISTRY, t, _recording_provider(t, seen))

    search(_query(q="payee vietnam airlines", types=frozenset({"payee", "transaction"})))
    assert seen == {"payee": "vietnam airlines", "transaction": "payee vietnam airlines"}

    search(_query(q="payees vietnam", types=frozenset({"payee", "transaction"})))
    assert seen["payee"] == "vietnam"


def test_single_word_is_never_a_qualifier(monkeypatch):
    seen: dict[str, str] = {}
    monkeypatch.setitem(_reg.REGISTRY, "payee", _recording_provider("payee", seen))
    search(_query(q="payees", types=frozenset({"payee"})))
    assert seen == {"payee": "payees"}
