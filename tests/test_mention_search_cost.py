"""The @-mention picker's per-keystroke cost (2026-10-29: "@herbal" spun for over a minute).

- a search whose client has gone stops at the next provider instead of finishing the fan-out
- the data each provider searches is loaded once per data version, not once per keystroke
- payee rows outrank transaction rows of the same rank tier
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import threading

import backend.services.mentions.registry as _reg
from backend.api.mentionables import _set_when_disconnected
from backend.services.mentions import _corpus
from backend.services.mentions.descriptor import EntityDescriptor
from backend.services.mentions.generic import make_search
from backend.services.mentions.search import search
from backend.services.mentions.types import MentionProvider, SearchQuery


def _query(q: str = "", types=None) -> SearchQuery:
    return SearchQuery(q=q, types=types, surfaces=frozenset({"entity"}), limit=20, cursor=None)


def _provider(type_: str, rows: list[dict], calls: list[str] | None = None) -> MentionProvider:
    def _search(_q):
        if calls is not None:
            calls.append(type_)
        return rows

    return MentionProvider(
        type=type_, surfaces=frozenset({"entity"}), icon="◆", resolve=lambda r: None, search=_search
    )


def _row(type_: str, label: str) -> dict:
    return {"type": type_, "id": label, "label": label, "sublabel": "", "value": label}


# ── 1. an abandoned search stops ─────────────────────────────────────────────


def test_search_stops_at_the_next_provider_once_the_client_is_gone(monkeypatch):
    calls: list[str] = []
    for t in ("a", "b", "c"):
        monkeypatch.setitem(_reg.REGISTRY, t, _provider(t, [_row(t, t)], calls))

    gone = threading.Event()

    def _stop() -> bool:
        if calls:  # the client hangs up while the first provider runs
            gone.set()
        return gone.is_set()

    assert search(_query(types=frozenset("abc")), should_stop=_stop) == {
        "items": [],
        "next_cursor": None,
    }
    assert calls == ["a"]


def test_search_without_a_stop_check_runs_every_provider(monkeypatch):
    calls: list[str] = []
    for t in ("a", "b"):
        monkeypatch.setitem(_reg.REGISTRY, t, _provider(t, [_row(t, t)], calls))

    assert len(search(_query(types=frozenset("ab")))["items"]) == 2
    assert calls == ["a", "b"]


def test_disconnect_watcher_sets_the_flag_when_the_client_hangs_up():
    class _Request:
        def __init__(self):
            self.messages = iter(
                [{"type": "http.request", "body": b""}, {"type": "http.disconnect"}]
            )

        async def receive(self) -> dict:
            return next(self.messages)

    gone = threading.Event()
    asyncio.run(asyncio.wait_for(_set_when_disconnected(_Request(), gone), timeout=2))
    assert gone.is_set()


# ── 2. the corpus is reused while the data is unchanged ──────────────────────


def _counting(value):
    calls = []

    def load(*args):
        calls.append(args)
        return value

    return load, calls


def test_corpus_is_loaded_once_per_epoch(monkeypatch):
    epoch = [(1, 0, "2026-10-29")]
    monkeypatch.setattr(_corpus.read_cache, "current_epoch", lambda: epoch[0])
    load, calls = _counting([{"name": "Herbal Tea"}])

    assert _corpus.corpus(load) == [{"name": "Herbal Tea"}]
    assert _corpus.corpus(load) == [{"name": "Herbal Tea"}]
    assert len(calls) == 1

    epoch[0] = (2, 0, "2026-10-29")  # a write landed
    _corpus.corpus(load)
    assert len(calls) == 2


def test_corpus_expires_after_the_ttl(monkeypatch):
    monkeypatch.setattr(_corpus.read_cache, "current_epoch", lambda: (1, 0, "d"))
    now = [1000.0]
    monkeypatch.setattr(_corpus.time, "monotonic", lambda: now[0])
    load, calls = _counting([])

    _corpus.corpus(load)
    now[0] += _corpus._TTL_S - 1
    _corpus.corpus(load)
    assert len(calls) == 1
    now[0] += 2  # live prices move without a write; the TTL bounds that drift
    _corpus.corpus(load)
    assert len(calls) == 2


def test_corpus_keys_on_arguments_and_hands_out_copies(monkeypatch):
    monkeypatch.setattr(_corpus.read_cache, "current_epoch", lambda: (1, 0, "d"))
    load, calls = _counting({"groups": []})

    first = _corpus.corpus(load, "2026-10")
    first["groups"].append("mutated")
    assert _corpus.corpus(load, "2026-10") == {"groups": []}
    _corpus.corpus(load, "2026-09")
    assert calls == [("2026-10",), ("2026-09",)]


def test_corpus_is_bypassed_without_a_change_signal(monkeypatch):
    monkeypatch.setattr(_corpus.read_cache, "current_epoch", lambda: None)  # non-SQLite
    load, calls = _counting([])
    _corpus.corpus(load)
    _corpus.corpus(load)
    assert len(calls) == 2


def test_generic_search_loads_rows_once_across_keystrokes(monkeypatch):
    monkeypatch.setattr(_corpus.read_cache, "current_epoch", lambda: (1, 0, "d"))
    loads: list[str] = []

    def rows(query):
        loads.append(query.q)
        return [{"id": "p1", "name": "Herbal Tea"}, {"id": "p2", "name": "Hardware"}]

    d = EntityDescriptor(type="fake", label="Fake", icon="◆", order=99, rows=rows)
    found = [make_search(d)(_query(q)) for q in ("h", "he", "her", "herbal")]

    assert loads == [""]
    assert [len(r) for r in found] == [2, 1, 1, 1]


def test_no_generic_rows_function_reads_the_query():
    """Generic rows are memoised as the type's whole corpus, called with an empty query — a
    ``rows`` that filtered on ``query.q`` would serve the first keystroke's subset to every
    later one. A type that needs a query pushdown uses ``search_override``."""
    readers = []
    for type_, d in _reg.DESCRIPTORS.items():
        if d.search_override is not None or d.rows is None:
            continue
        fn = ast.parse(textwrap.dedent(inspect.getsource(d.rows))).body[0]
        param = fn.args.args[0].arg
        if any(isinstance(n, ast.Name) and n.id == param for n in ast.walk(fn) if n is not fn):
            readers.append(type_)
    assert readers == []


# ── 3. payees before transactions ────────────────────────────────────────────


def test_payee_outranks_a_transaction_in_the_same_tier(monkeypatch):
    monkeypatch.setitem(
        _reg.REGISTRY, "payee", _provider("payee", [_row("payee", "Herbal Tea Shop")])
    )
    monkeypatch.setitem(
        _reg.REGISTRY,
        "transaction",
        _provider("transaction", [_row("transaction", "Herbal Tea Shop 14 Oct card 1234")]),
    )

    items = search(_query("herbal", types=frozenset({"payee", "transaction"})))["items"]
    assert [i["type"] for i in items] == ["payee", "transaction"]
