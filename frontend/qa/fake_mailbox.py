"""An in-memory mailbox for the email-logging journey (frontend/qa/email-logging.mjs).

Loaded by the disposable backend's launcher, it replaces the two IMAP calls the email tools
make — ``imap_client.search_headers`` and ``imap_client.fetch_by_message_ids`` — and nothing
else: the tools, the skills, Lucius and the ledger all run for real. The mailbox is the JSON
file named by ``ONW_FAKE_MAILBOX`` (written by the test, so dates follow the instance's day):
``[{"message_id", "from", "subject", "date", "body"}]``. ``search_headers`` honours the date
window (on ``date``, which stands in for INTERNALDATE), simple FROM/SUBJECT/BODY/TEXT
criteria, and limit/offset paging, as the real server does.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date

from backend.ingest import imap_client

_CRITERION = re.compile(r'\b(FROM|SUBJECT|BODY|TEXT)\s+"((?:[^"\\]|\\.)*)"', re.I)


def _mailbox() -> list[dict]:
    with open(os.environ["ONW_FAKE_MAILBOX"], encoding="utf-8") as fh:
        return json.load(fh)


def _matches(mail: dict, criteria: list[str] | None) -> bool:
    for criterion in criteria or []:
        for field, value in _CRITERION.findall(criterion):
            needle = value.replace('\\"', '"').lower()
            field = field.upper()
            haystack = {
                "FROM": mail["from"],
                "SUBJECT": mail["subject"],
                "BODY": mail["body"],
                "TEXT": f"{mail['from']} {mail['subject']} {mail['body']}",
            }[field].lower()
            if needle not in haystack:
                return False
    return True


def _in_window(mail: dict, since: str | None, before: str | None) -> bool:
    day = date.fromisoformat(mail["date"][:10])
    if since and day < date.fromisoformat(str(since)[:10]):
        return False
    return not (before and day >= date.fromisoformat(str(before)[:10]))


def search_headers(
    creds,
    *,
    date_since=None,
    date_before=None,
    filter_criteria=None,
    limit=None,
    offset=0,
    should_stop=None,
):
    mails = [
        m
        for m in _mailbox()
        if _in_window(m, date_since, date_before) and _matches(m, filter_criteria)
    ]
    window = mails[offset:] if limit is None else mails[offset : offset + limit]
    return {
        "messages": [
            {
                "uid": i + 1 + offset,
                "message_id": m["message_id"],
                "from": m["from"],
                "subject": m["subject"],
                "date": m["date"][:10],
                "date_time": f"{m['date'][:10]}T09:00:00+00:00",
                "received_date": m["date"][:10],
                "received_at": f"{m['date'][:10]}T09:00:00+00:00",
            }
            for i, m in enumerate(window)
        ],
        "uidvalidity": 1,
        "folder": "INBOX",
        "candidate_count": len(mails),
        "more": offset + len(window) < len(mails),
    }


def fetch_by_message_ids(creds, requests, *, should_stop=None):
    by_id = {m["message_id"]: (i, m) for i, m in enumerate(_mailbox())}
    results = []
    for request in requests:
        hit = by_id.get(request.get("raw_message_id") or "")
        if hit is None:
            results.append({"message_id": request["message_id"], "found": False})
            continue
        uid, mail = hit
        results.append(
            {
                "message_id": request["message_id"],
                "found": True,
                "raw_message_id": mail["message_id"],
                "folder": "INBOX",
                "uid": uid + 1,
                "from": mail["from"],
                "subject": mail["subject"],
                "date": mail["date"][:10],
                "date_time": f"{mail['date'][:10]}T09:00:00+00:00",
                "body_text": mail["body"],
            }
        )
    return {"results": results}


def test_connection(creds):
    return True, "Connected (fake mailbox)"


imap_client.search_headers = search_headers
imap_client.fetch_by_message_ids = fetch_by_message_ids
imap_client.test_connection = test_connection
