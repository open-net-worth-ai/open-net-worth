"""The commit-msg guard: a message may not name the owner's own book (synthetic book only)."""

from __future__ import annotations

import json
import sqlite3

from backend.scripts import commit_msg_guard as g


def _book(tmp_path):
    db = tmp_path / "onw.db"
    con = sqlite3.connect(db)
    con.execute("create table instruments (seq int, id text, payload json)")
    con.execute("create table positions (book_id text, seq int, id text, payload json)")
    for iid, inst in {
        "inst-held": {"price_source_id": "ACME", "symbol": "ACME"},
        "inst-coin": {"price_source_id": "examplecoin", "symbol": "EXC"},
        "inst-seen": {"price_source_id": "ZZZQ", "symbol": "ZZZQ"},  # viewed, never held
    }.items():
        con.execute("insert into instruments values (1, ?, ?)", (iid, json.dumps(inst)))
    for iid in ("inst-held", "inst-coin"):
        payload = json.dumps({"instrument_id": iid, "quantity": 10})
        con.execute("insert into positions values ('b', 1, ?, ?)", (f"pos-{iid}", payload))
    con.commit()
    con.close()
    return db


def test_the_held_book_is_its_tickers_and_symbols_never_a_viewed_one(tmp_path):
    tickers, ids = g._held(_book(tmp_path))
    assert tickers == {"ACME", "EXC"}  # a crypto feed id ("examplecoin") is not a ticker
    assert ids == {"inst-held", "inst-coin"}


def test_a_held_ticker_is_caught_as_a_whole_case_sensitive_word():
    tickers, ids = {"ACME", "NOW"}, set()
    assert g.findings("On the live case (ACME, no alerts)", tickers, ids, []) == [
        "a ticker you hold: ACME"
    ]
    assert g.findings("Fixed the bug. Now it works for ACMEX holders.", tickers, ids, []) == []
    assert g.findings("ACME.DE and ACME-B", {"ACME.DE"}, ids, []) == ["a ticker you hold: ACME.DE"]


def test_an_instrument_id_or_a_denylist_entry_is_caught_without_echoing_it():
    found = g.findings(
        "fix for inst-held; ping Jane Doé", set(), {"inst-held"}, ["jane doe", "unused"]
    )
    assert found == ["denylist entry #1", "an instrument id from your book"]


def test_comment_lines_and_the_verbose_diff_are_not_the_message(tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(
        "fix: a synthetic change\n# ACME in a comment\n"
        "# ------------------------ >8 ------------------------\ndiff ACME\n"
    )
    assert g._message(msg) == "fix: a synthetic change"


def test_the_hook_blocks_a_message_naming_the_book_and_passes_a_clean_one(tmp_path, monkeypatch):
    db = _book(tmp_path)
    monkeypatch.setattr(g, "_book_db", lambda: db)
    monkeypatch.setattr(g, "_denylist", lambda: [])
    bad, good = tmp_path / "bad", tmp_path / "good"
    bad.write_text("fix: Lucius now guards ACME's stop\n")
    good.write_text("fix: Lucius now guards a holding's stop\n")
    assert g.main([str(bad)]) == 1
    assert g.main([str(good)]) == 0


def test_an_author_trailer_is_not_a_denylist_leak_but_still_cannot_carry_a_ticker():
    msg = "fix: a synthetic change\n\nCo-Authored-By: Jane Doe <jane@example.com>\n"
    assert g.findings(msg, set(), set(), ["jane doe"]) == []
    assert g.findings(msg + "Signed-off-by: ACME\n", {"ACME"}, set(), []) == [
        "a ticker you hold: ACME"
    ]
