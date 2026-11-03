"""Restoring to before a message undoes that message AND every later turn of the conversation.

The Restore dialog promises it "undoes this and any later changes", and the docs say the data
goes "back as it was before that message". Restore used to replay only the chosen message's own
checkpoint: the later turns were marked superseded — their Restore buttons vanished — while
their writes stayed in the book (owner report, 2026-11-03: three transfers booked by a later
message survived a restore of an earlier one).
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select, update

from backend.ai import assistant
from backend.ai import checkpoint as cp
from backend.core.io import fire_db_write_hook
from backend.db import engine as eng
from backend.db import tables as t

SESSION = "s-later-turns"


def _history(turns: int) -> list[dict]:
    out: list[dict] = []
    for n in range(1, turns + 1):
        out.append({"role": "user", "content": f"message {n}"})
        out.append({"role": "assistant", "content": [{"type": "text", "text": f"done {n}"}]})
    return out


def _book(book_id: str, name: str) -> dict:
    return {"id": book_id, "name": name, "base_currency": "", "created_at": ""}


def _add(book_id: str):
    def write():
        fire_db_write_hook("books", [(book_id, None)])
        with eng.get_engine().begin() as c:
            c.execute(insert(t.books).values(**_book(book_id, book_id)))

    return write


def _rename(book_id: str, old: str, new: str):
    def write():
        fire_db_write_hook("books", [(book_id, _book(book_id, old))])
        with eng.get_engine().begin() as c:
            c.execute(update(t.books).where(t.books.c.id == book_id).values(name=new))

    return write


def _turn(anchor: int, write, session: str = SESSION) -> str:
    ctx, token = cp.begin_turn(session, anchor, f"message at {anchor}")
    write()
    return cp.end_turn(ctx, token)["id"]


def _books() -> dict[str, str]:
    with eng.get_engine().connect() as c:
        rows = c.execute(select(t.books.c.id, t.books.c.name)).all()
    return {i: n for i, n in rows if i in {"A", "B", "C", "D"}}


def _users() -> list[dict]:
    return [m for m in assistant.rehydrate_messages(SESSION) or [] if m["role"] == "user"]


@pytest.fixture
def three_turns(db_isolated, monkeypatch):
    """Message 1 adds A, message 2 adds B, message 3 renames A."""
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history(3))
    ids = [_turn(0, _add("A")), _turn(2, _add("B")), _turn(4, _rename("A", "A", "A3"))]
    assert _books() == {"A": "A3", "B": "B"}
    return ids


def test_restoring_message_one_undoes_every_later_turn(three_turns):
    c1, _, _ = three_turns
    cp.restore(c1, mode="data")
    assert _books() == {}


def test_restoring_a_middle_message_keeps_the_earlier_one(three_turns):
    _, c2, _ = three_turns
    cp.restore(c2, mode="data")
    assert _books() == {"A": "A"}, "message 1's row stays; 2 and 3 are undone"


def test_undo_puts_every_later_turn_back(three_turns):
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    cp.restore(result["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A3", "B": "B"}
    assert [m["checkpointId"] for m in _users()] == [c1, c2, c3]


def test_preview_counts_the_later_turns(three_turns):
    c1, _, _ = three_turns
    rows = {r["file"]: r for r in cp.preview(c1)["changes"]}
    assert rows["db:books"]["delta"] == -2


def test_another_conversations_writes_are_not_undone(three_turns):
    c1, _, _ = three_turns
    _turn(0, _add("C"), session="s-elsewhere")
    cp.restore(c1, mode="data")
    assert _books() == {"C": "C"}


@pytest.fixture
def one_message_two_checkpoints(db_isolated, monkeypatch):
    """Message 1's turn adds A and pauses on a card; the approved call adds B in a checkpoint
    of its own on the SAME message (the live client holds that later id); message 2 adds C."""
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history(2))
    head = _turn(0, _add("A"))
    approved = _turn(0, _add("B"))
    later = _turn(2, _add("C"))
    return head, approved, later


def test_any_checkpoint_of_a_message_restores_the_whole_message(one_message_two_checkpoints):
    head, approved, later = one_message_two_checkpoints
    result = cp.restore(approved, mode="data")
    assert _books() == {}
    assert sorted(result["superseded_checkpoint_ids"]) == sorted([approved, later])

    first = _users()[0]
    assert first["checkpointId"] == head and first.get("restored"), "a reload shows it restored"

    cp.restore(result["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A", "B": "B", "C": "C"}
    assert not _users()[0].get("restored")


def test_a_rewound_message_is_never_grouped_with_the_new_one(db_isolated, monkeypatch):
    """Rewinding the chat reuses the message index. The new message at that index must not
    pull the rewound message's turn into its own restore — and the rewound turn's data, which
    a chat-only rewind keeps, is still undone by restoring an earlier message."""
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history(2))
    monkeypatch.setattr(cp, "_truncate_session_history", lambda sid, n: n)
    first = _turn(0, _add("A"))
    rewound = _turn(2, _add("B"))
    cp.restore(rewound, mode="conversation")
    assert _books() == {"A": "A", "B": "B"}, "a chat-only rewind keeps the data"

    replacement = _turn(2, _add("C"))
    assert [m.get("checkpointId") for m in _users()] == [first, replacement]
    cp.restore(replacement, mode="data")
    assert _books() == {"A": "A", "B": "B"}

    cp.restore(first, mode="data")
    assert _books() == {}
