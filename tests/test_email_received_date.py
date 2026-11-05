"""``received_date`` on ``email_search_messages`` — the field that reconciles the
`since`/`before` mismatch confirmed with real data: `since`/`before` match IMAP
INTERNALDATE (the server's receipt clock), but the existing `date` field is parsed from
the email's own `Date:` header (the SENDER's local timezone, never normalized). A search
with `since=2026-09-07` returned messages displaying `date: 2026-09-06` — same instant,
two different clocks under one field name.

`received_date` is ADDITIVE: `date` keeps its existing (header-derived) semantics
unchanged; `received_date` is the new INTERNALDATE-derived field, parsed straight off the
IMAP FETCH response's own tagged line (no separate literal block) via the same regex
approach `_FETCH_RESPONSE_UID_RE` already uses for the UID.

Reuses the fake-IMAP pattern from tests/test_search_messages.py, extended to carry a
per-message INTERNALDATE and echo it in the tagged FETCH response line.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.db.email_config_store import EmailConfigStore
from backend.ingest.imap_client import (
    _parse_internaldate,
    _parse_internaldate_instant,
    _parse_multi_fetch_response,
)

# ── fake IMAP: header fetch response now also carries INTERNALDATE ──────────────────────


class _FakeIMAP:
    """Answers UID SEARCH (matches everything — date filtering is covered elsewhere in
    tests/test_search_messages.py) and UID FETCH, echoing each message's own INTERNALDATE
    in the tagged line exactly as a real server would for ``(UID INTERNALDATE
    BODY.PEEK[HEADER.FIELDS (...)])``."""

    def __init__(self, messages: list[tuple[int, bytes, str]]):
        # messages: [(uid, raw_rfc822_bytes, internaldate_str)]
        self._messages = sorted(messages)
        self.fetch_items: list[tuple[int, str]] = []

    def login(self, user, password):
        return ("OK", [b"ok"])

    def select(self, folder, readonly=False):
        return ("OK", [str(len(self._messages)).encode()])

    def response(self, key):
        if key == "UIDVALIDITY":
            return (key, [b"1"])
        return (key, [None])

    def uid(self, command, *args):
        if command == "search":
            uids = [u for u, _raw, _idate in self._messages]
            return ("OK", [" ".join(str(u) for u in uids).encode()])
        if command == "fetch":
            uid_set = [int(u) for u in str(args[0]).split(",")]
            item = args[1] if len(args) > 1 else ""
            for uid in uid_set:
                self.fetch_items.append((uid, item))
            by_uid = {u: (raw, idate) for u, raw, idate in self._messages}
            if "HEADER.FIELDS" in item:
                out: list = []
                for uid in uid_set:
                    entry = by_uid.get(uid)
                    if entry is None:
                        continue
                    raw, idate = entry
                    header_bytes = _headers_only(raw)
                    tagged = (
                        '%d (UID %d INTERNALDATE "%s" BODY[HEADER.FIELDS] {%d}'
                        % (uid, uid, idate, len(header_bytes))
                    ).encode()
                    out.append((tagged, header_bytes))
                    out.append(b")")
                return ("OK", out) if out else ("OK", [None])
            raw, _idate = by_uid.get(uid_set[0], (None, None))
            if raw is not None:
                return ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
            return ("OK", [None])
        return ("NO", [b"unsupported"])

    def logout(self):
        return ("BYE", [b"bye"])


def _headers_only(raw: bytes) -> bytes:
    idx = raw.find(b"\r\n\r\n")
    return raw[: idx + 4] if idx != -1 else raw


def _raw(subject: str, message_id: str | None, date_header: str) -> bytes:
    header_line = f"Message-ID: {message_id}\r\n" if message_id else ""
    return (
        f"From: alerts@bank.example\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date_header}\r\n"
        f"{header_line}"
        f"\r\n"
        f"Amount charged.\r\n"
    ).encode()


@pytest.fixture()
def store(tmp_path):
    s = EmailConfigStore()
    s.update(
        {
            "enabled": True,
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "address": "u@example.com",
            "password": "secret",
            "imap_folder": "INBOX",
            "lookback_days": 7,
        }
    )
    return s


def _search(monkeypatch, store, messages, *, input_data: dict):
    import backend.db as db
    import backend.ingest.imap_client as imap_client
    from backend.ai.tools import email_ingest

    fake = _FakeIMAP(messages)
    monkeypatch.setattr(db, "EmailConfigStore", lambda: store, raising=True)
    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", lambda host, port, **kw: fake)

    out, _ = asyncio.run(email_ingest.email_search_messages(input_data))
    return json.loads(out), fake


# ── the real-world mismatch, reproduced ──────────────────────────────────────────────────


def test_date_and_received_date_can_land_on_different_calendar_days(monkeypatch, store):
    """The exact confirmed bug: a message received (INTERNALDATE) 2026-09-07 carries a
    Date: header stamped 2026-09-06 (sender's local clock, one day behind UTC receipt).
    `date` keeps reporting the header's day unchanged; `received_date` must report the day
    `since`/`before` actually filtered on."""
    messages = [
        (
            1,
            _raw("Grab charge", "<m-1@x>", "Sat, 06 Sep 2026 23:50:00 -0700"),
            "07-Sep-2026 05:32:28 +0000",
        )
    ]
    result, _fake = _search(monkeypatch, store, messages, input_data={"since": "2026-09-07"})
    assert result["ok"] is True
    assert result["showing"] == 1
    msg = result["messages"][0]
    assert msg["date"] == "2026-09-06"
    assert msg["received_date"] == "2026-09-07"


def test_received_date_matches_when_both_clocks_agree(monkeypatch, store):
    messages = [
        (
            2,
            _raw("Charge B", "<m-2@x>", "Mon, 07 Sep 2026 10:00:00 +0000"),
            "07-Sep-2026 10:00:05 +0000",
        )
    ]
    result, _fake = _search(monkeypatch, store, messages, input_data={"since": "2026-09-01"})
    msg = result["messages"][0]
    assert msg["date"] == msg["received_date"] == "2026-09-07"


def test_headerless_upgrade_path_still_carries_received_date(monkeypatch, store):
    """A message with no Message-ID header is upgraded to a full RFC822 fetch — the
    INTERNALDATE it needs was already captured off the earlier header peek, so it must
    not go missing across that upgrade."""
    messages = [
        (
            3,
            _raw("No id mail", None, "Mon, 07 Sep 2026 09:00:00 +0000"),
            "07-Sep-2026 09:00:00 +0000",
        )
    ]
    result, fake = _search(monkeypatch, store, messages, input_data={"since": "2026-09-01"})
    assert result["ok"] is True
    assert result["showing"] == 1
    assert any(item == "(RFC822)" for _uid, item in fake.fetch_items), "must upgrade to full fetch"
    assert result["messages"][0]["received_date"] == "2026-09-07"


def test_missing_internaldate_renders_as_empty_string_not_none(monkeypatch, store):
    """A server that (against the request) omits INTERNALDATE from the tagged line must
    not blow up the tool — `received_date` degrades to '' like every other optional
    string field this tool renders."""
    import backend.db as db
    import backend.ingest.imap_client as imap_client
    from backend.ai.tools import email_ingest

    class _NoInternaldateIMAP(_FakeIMAP):
        def uid(self, command, *args):
            if command == "fetch" and "HEADER.FIELDS" in (args[1] if len(args) > 1 else ""):
                uid_set = [int(u) for u in str(args[0]).split(",")]
                by_uid = {u: raw for u, raw, _idate in self._messages}
                out: list = []
                for uid in uid_set:
                    raw = by_uid.get(uid)
                    if raw is None:
                        continue
                    header_bytes = _headers_only(raw)
                    tagged = (
                        "%d (UID %d BODY[HEADER.FIELDS] {%d}" % (uid, uid, len(header_bytes))
                    ).encode()
                    out.append((tagged, header_bytes))
                    out.append(b")")
                return ("OK", out) if out else ("OK", [None])
            return super().uid(command, *args)

    fake = _NoInternaldateIMAP(
        [
            (
                4,
                _raw("Charge D", "<m-4@x>", "Mon, 07 Sep 2026 09:00:00 +0000"),
                "07-Sep-2026 09:00:00 +0000",
            )
        ]
    )
    monkeypatch.setattr(db, "EmailConfigStore", lambda: store, raising=True)
    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", lambda host, port, **kw: fake)
    out, _ = asyncio.run(email_ingest.email_search_messages({"since": "2026-09-01"}))
    result = json.loads(out)
    assert result["messages"][0]["received_date"] == ""


# ── unit coverage on the parsing helpers directly ────────────────────────────────────────


def test_parse_internaldate_extracts_iso_date():
    line = b'12 (UID 12 INTERNALDATE "07-Sep-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {10}'
    assert _parse_internaldate(line) == "2026-09-07"


def test_parse_internaldate_pads_single_digit_day():
    line = b'12 (UID 12 INTERNALDATE "7-Sep-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {10}'
    assert _parse_internaldate(line) == "2026-09-07"


def test_parse_internaldate_returns_none_when_absent():
    line = b"12 (UID 12 BODY[HEADER.FIELDS] {10}"
    assert _parse_internaldate(line) is None


def test_parse_multi_fetch_response_carries_both_uid_and_internaldate():
    data = [
        (
            b'5 (UID 5 INTERNALDATE "07-Sep-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {4}',
            b"a: b",
        ),
        b")",
    ]
    result = _parse_multi_fetch_response(data)
    assert result[5] == (b"a: b", "2026-09-07", "2026-09-07T05:32:28+00:00")


# ── received_at: the same clock as an instant, for "after my last run" ───────────────────


def test_received_at_is_the_internaldate_instant_in_utc():
    # The zone counts here, unlike received_date: this is compared to a run's own start.
    line = b'12 (UID 12 INTERNALDATE "07-Sep-2026 05:32:28 +0800" BODY[HEADER.FIELDS] {10}'
    assert _parse_internaldate_instant(line) == "2026-09-06T21:32:28+00:00"


def test_received_at_reads_the_rfc_space_padded_day():
    line = b'12 (UID 12 INTERNALDATE " 7-Sep-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {10}'
    assert _parse_internaldate_instant(line) == "2026-09-07T05:32:28+00:00"


@pytest.mark.parametrize(
    "line",
    [
        b"12 (UID 12 BODY[HEADER.FIELDS] {10}",
        b'12 (UID 12 INTERNALDATE "07-Sep-2026" BODY[HEADER.FIELDS] {10}',
        b'12 (UID 12 INTERNALDATE "07-Foo-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {10}',
        b'12 (UID 12 INTERNALDATE "32-Sep-2026 05:32:28 +0000" BODY[HEADER.FIELDS] {10}',
    ],
)
def test_received_at_is_none_without_a_valid_instant(line):
    assert _parse_internaldate_instant(line) is None


def test_search_results_carry_received_at(monkeypatch, store):
    messages = [
        (
            7,
            _raw("Payment issue", "<m-7@x>", "Mon, 07 Sep 2026 10:00:00 +0000"),
            "07-Sep-2026 10:00:05 +0000",
        )
    ]
    result, _fake = _search(monkeypatch, store, messages, input_data={"since": "2026-09-01"})
    assert result["messages"][0]["received_at"] == "2026-09-07T10:00:05+00:00"
