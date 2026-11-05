"""
Read-only IMAP fetch — the RECEIVE plumbing behind ``search_messages``/``get_messages``
(docs/adr/2026-08-12-email-search-hydrate-split.md).

Connects read-only (never mutates the mailbox — no flag changes, no deletion). Returns
lightweight dicts; any reasoning over an email (e.g. logging spending) is the seeded
email-transaction-logger agent's job, not here. See docs/rfc/email-as-generic-source.md.

Stateless, date-range only — no incremental poll cursor anywhere in this module
(``search_headers`` below is explicit about this in its own docstring). Every call takes an
explicit ``date_since``/``date_before`` window from the caller; ``search_messages`` itself
refuses to run without one ("no stored position to resume from"). Re-running a call with an
overlapping window is safe and expected — a re-search just re-shows the same
``message_id``, and the ledger's ``external_refs`` dedup makes a repeat booking a no-op.
The configured folder (default ``INBOX``) is used as given, with no Gmail-specific
All Mail substitution — an incremental UID-cursor poller with that substitution used to
live here (removed 2026-08-18: it had zero live callers, since nothing in this app polls
email on a schedule anymore — ingestion is on-demand, either the model calling
``search_messages``/``get_messages`` directly or an Automation doing so on its own
schedule).

Socket timeout
--------------
``imaplib.IMAP4_SSL`` is constructed with ``timeout=_IMAP_TIMEOUT_S`` (default 30 s) so a
hostile or stalled server cannot block the worker thread indefinitely.
"""

from __future__ import annotations

import email
import html
import imaplib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from backend.core.email_ids import content_fallback_id
from backend.core.errors import ValidationError
from backend.core.user_time import user_today

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 8000  # truncate before sending to the LLM
_IMAP_TIMEOUT_S = 30  # LOW-2: explicit socket timeout so a stalled server can't block forever
# UIDs per batched FETCH command (search_headers). Matches search_messages' own hard cap
# on `limit` (see email_ingest.py) so a normal call is ONE round trip — a 200-UID comma
# set is ~1.5KB on the wire, nowhere near any real IMAP command-line limit, and the
# ~200-header response is a trivial payload. The real cost of a bigger chunk isn't the
# wire, it's cancellation granularity: `should_stop()` is only checked BETWEEN chunks, so
# raising this raises how long a cancelled turn can keep running before Stop takes effect.
_HEADER_FETCH_CHUNK = 200
# A batched UID FETCH response line carries its UID as an explicitly-requested attribute
# (`UID BODY.PEEK[...]`) since the untagged `* n FETCH (...)` response otherwise uses the
# message SEQUENCE number, not the UID, as its own identifier.
_FETCH_RESPONSE_UID_RE = re.compile(rb"UID\s+(\d+)")
# INTERNALDATE arrives as a quoted string literal directly in the same tagged line (never a
# separate literal block), e.g. `INTERNALDATE "07-Sep-2026 05:32:28 +0000"` — extracted
# alongside the UID above rather than via `imaplib.Internaldate2tuple` because that helper
# converts to the LOCAL system clock, which is not what we want: IMAP SINCE/BEFORE (RFC 3501
# §6.4.4) match on INTERNALDATE's calendar date "disregarding time and timezone", so the
# `received_date` we report must be that same zone-naive date, not a shifted one.
_FETCH_RESPONSE_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"(\d{1,2})-([A-Za-z]{3})-(\d{4})')
# The same literal in full, for `received_at`: an instant to compare against a run's own start,
# so its zone DOES count here. RFC 3501 pads a one-digit day with a space.
_FETCH_RESPONSE_INTERNALDATE_INSTANT_RE = re.compile(
    rb'INTERNALDATE\s+"\s?(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"'
)
_MONTH_ABBR_TO_NUM = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t\n]*")
_HTML_DOC_RE = re.compile(r"\A\s*(?:<!doctype\s+html|<html[\s>])|<body[\s>]", re.IGNORECASE)

# RFC 3501 §9 atom characters — any name containing chars outside this set requires quoting.
# In practice a space is the most common trigger (e.g. "[Gmail]/All Mail").
_IMAP_ATOM_SPECIALS_RE = re.compile(r'[ "\\(){%*\x00-\x1f\x7f]')


def _quote_mailbox(name: str) -> str:
    """
    Return the mailbox name as an IMAP quoted-string when it contains characters that would
    break the IMAP parser (spaces, backslashes, double-quotes, or control chars per RFC 3501
    §4.3).  Simple atom-safe names such as ``INBOX`` are returned verbatim so we don't
    gratuitously alter common paths.

    >>> _quote_mailbox("INBOX")
    'INBOX'
    >>> _quote_mailbox("[Gmail]/All Mail")
    '"[Gmail]/All Mail"'
    >>> _quote_mailbox('[Gmail]/My "Special" Folder')
    '"[Gmail]/My \\\\"Special\\\\" Folder"'
    """
    # SEC-1 (security review 2026-07-20) — REFUSE CR/LF/NUL before quoting.
    #
    # An IMAP quoted-string cannot legally carry CR/LF, so QUOTING DOES NOT NEUTRALISE THEM:
    # the bytes go out raw and split one command into two. `folder` is MODEL-SUPPLIED free
    # text (ai/tools/email_ingest.py takes it verbatim, .strip() only), so this was a live
    # prompt-injection -> IMAP-command-injection chain. Reproduced:
    #
    #     _quote_mailbox("INBOX\r\nA666 LOGOUT")
    #       wire:  A002 EXAMINE "INBOX
    #              A666 LOGOUT"          <- a second tagged command
    #
    # This PREDATES the SEARCH-filter guard below, whose own comment wrongly asserted that
    # every argument reaching this module was an int or a strptime-validated date. It was
    # not: this one was never validated at all.
    #
    # Refuse rather than strip — a stripped payload is an attack that proceeded quietly, and
    # the whole value of a guard here is that it is observable when it fires.
    for _ch, _label in (("\r", "CR"), ("\n", "LF"), ("\x00", "NUL")):
        if _ch in name:
            raise ValidationError(
                f"folder: contains a {_label} character, which would split the IMAP command. "
                "Pass a plain mailbox path such as INBOX or [Gmail]/All Mail."
            )
    if _IMAP_ATOM_SPECIALS_RE.search(name):
        # Escape embedded backslashes first, then double-quotes (order matters).
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return name


# ── SEARCH criterion guard (the ONE gate every free-text search value goes through) ─────────
#
# WHY THIS EXISTS, in one paragraph.  ``imaplib.IMAP4._command`` builds the wire command by
# RAW concatenation — ``data = data + b" " + arg`` — then ``self.send(data + CRLF)``.  There
# is NO quoting and NO escaping anywhere in the stdlib path.  That was NOT previously true of
# , which has always been model-supplied free text — see the SEC-1 note on
#  above, which refuses CR/LF/NUL for exactly this reason.  Every
# SEARCH FILTER is free text that arrives from the MODEL, which may have just read an
# attacker-controlled email — so an injected value is a prompt-injection → IMAP-command
# injection chain: a hostile email that says "search for
# ``X\r\nA001 STORE 1:* +FLAGS (\Deleted)``" and an obedient model would otherwise put that
# on the wire verbatim, and the second line executes as its own tagged command.  Note that
# this connection is read-only, which is a mitigation and NOT a defence: SELECT readonly is
# a server-side promise about THIS session's mailbox, not a bound on what an injected
# arbitrary command line can name.
#
# The rules, all enforced here and nowhere else:
#   * REJECT (never sanitize-and-continue) CR / LF / NUL.  Silently stripping them would hide
#     an attack in progress; refusing surfaces it to the caller and the log.
#   * ALWAYS quote as an IMAP quoted-string (RFC 3501 §4.3), escaping ``\`` then ``"``.  An
#     unquoted value containing a space does not merely fail to match — it becomes two
#     criteria and CHANGES THE COMMAND.  This is why this helper does not reuse
#     ``_quote_mailbox`` above: that one deliberately leaves atom-safe names bare, which is
#     right for a mailbox path and wrong for an attacker-influenced search term.
#   * REJECT non-ASCII rather than mangle it.  Correct non-ASCII SEARCH requires
#     ``SEARCH CHARSET UTF-8``, which not every server accepts; claiming support we have not
#     tested would be worse than refusing.
#   * CAP the length (256 chars is generous for FROM/SUBSTRING/HEADER).
#
# If you add a criterion, route its value through ``imap_quote_search_value`` — a second code
# path that formats a value itself is exactly how this regresses.
_MAX_SEARCH_VALUE_CHARS = 256

# RFC 5322 §3.6.8 field-name = 1*ftext; ftext = %d33-57 / %d59-126 (printable ASCII, no
# colon, no space).  A header FIELD name is a bare token, so it gets this on top of the
# shared value guard.
_HEADER_FIELD_RE = re.compile(r"^[!-9;-~]+$")

_FORBIDDEN_CONTROL_CHARS = {"\r": "CR", "\n": "LF", "\x00": "NUL"}


def imap_quote_search_value(value: str, *, field: str) -> str:
    """
    Validate and quote one free-text SEARCH value as an IMAP quoted-string.

    Raises ``ValidationError`` — never returns a sanitized-but-accepted string — on a value
    carrying CR/LF/NUL, exceeding ``_MAX_SEARCH_VALUE_CHARS``, or containing non-ASCII.
    ``field`` names the offending parameter in the error so the caller can fix the right one.

    >>> imap_quote_search_value("bank", field="from_address")
    '"bank"'
    >>> imap_quote_search_value('say "hi"', field="subject_contains")
    '"say \\\\"hi\\\\""'
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field}: must be a string")

    for char, name in _FORBIDDEN_CONTROL_CHARS.items():
        if char in value:
            raise ValidationError(
                f"{field}: contains a {name} character, which is refused outright — an IMAP "
                "command is newline-delimited, so this would inject a second command. "
                "The value is rejected, not stripped."
            )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValidationError(f"{field}: contains a control character, which is not allowed")
    if len(value) > _MAX_SEARCH_VALUE_CHARS:
        raise ValidationError(
            f"{field}: is {len(value)} characters, over the {_MAX_SEARCH_VALUE_CHARS} limit"
        )
    if not value.isascii():
        raise ValidationError(
            f"{field}: contains non-ASCII characters. Correct non-ASCII IMAP search needs "
            "SEARCH CHARSET UTF-8, which is not supported here — use an ASCII substring."
        )

    # Escape embedded backslashes FIRST, then double-quotes (order matters).
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_search_filters(
    *,
    from_address: str | None = None,
    subject_contains: str | None = None,
    unseen_only: bool = False,
    header_field: str | None = None,
    header_value: str | None = None,
) -> list[str]:
    """
    Build the additional RFC 3501 §6.4.4 SEARCH criteria for the optional filters.

    Returns a flat token list ready to splat into ``conn.uid("search", None, *base, *extra)``.
    IMAP ANDs all criteria, so these COMPOSE with whatever mode-specific criteria the caller
    already built (a UID range, SINCE/BEFORE, X-GM-RAW) rather than replacing them.

    Every string value goes through ``imap_quote_search_value`` — that is the single gate.
    """
    crit: list[str] = []

    if unseen_only:
        # A bare keyword, no caller-supplied text — nothing to quote.
        crit.append("UNSEEN")
    if from_address:
        crit += ["FROM", imap_quote_search_value(from_address, field="from_address")]
    if subject_contains:
        crit += ["SUBJECT", imap_quote_search_value(subject_contains, field="subject_contains")]

    if header_field or header_value:
        if not header_field or not header_value:
            raise ValidationError(
                "header: both 'field' and 'value' are required (e.g. "
                "{'field': 'X-Mailer', 'value': 'AcmeBank'})"
            )
        # The FIELD name needs the shared guard AND must be a bare RFC 5322 token — a space
        # in it would split into two criteria exactly like an unquoted value would.
        quoted_field = imap_quote_search_value(header_field, field="header.field")
        if not _HEADER_FIELD_RE.match(header_field):
            raise ValidationError(
                f"header.field: {header_field!r} is not a valid header name — it must be a "
                "bare token (printable ASCII, no spaces and no colon), e.g. 'X-Mailer'."
            )
        crit += [
            "HEADER",
            quoted_field,
            imap_quote_search_value(header_value, field="header.value"),
        ]

    return crit


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _normalize_whitespace(text: str) -> str:
    """
    Trim per-line padding and collapse runs of blank lines to at most one.

    Banking/template emails routinely ship a text/plain part padded with many blank
    lines and trailing spaces (e.g. table-cell-per-line layouts flattened to plain
    text) — `_html_to_text` above already collapses tag-driven whitespace for the
    HTML fallback, but the plain-text branch left it untouched, so that padding went
    straight into `body_text` (wasted LLM tokens, ugly rendering).

    Applied ONLY to the display/storage copy of the body (`_parse_full_message`'s
    `body_text`), never to the value fed into `content_fallback_id` — that hash is a
    stable cross-fetch identity for a headerless email (dedup key), and letting a
    future formatting tweak here reshuffle it would double-book already-seen emails.
    See `_extract_body`, whose return value is deliberately the RAW (unnormalized)
    body for exactly that reason.
    """
    lines = [line.strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _body_parts(msg: Message) -> tuple[str, str]:
    """The first non-attachment text/plain and text/html parts, decoded — either may be ""."""
    plain, html_body = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain" and not plain:
                plain = decoded
            elif ctype == "text/html" and not html_body:
                html_body = decoded
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            decoded = (msg.get_payload(decode=True) or b"").decode(charset, errors="replace")
        except Exception:
            decoded = ""
        if msg.get_content_type() == "text/html":
            html_body = decoded
        else:
            plain = decoded
    return plain, html_body


def _extract_body(msg: Message) -> str:
    """
    Prefer text/plain; fall back to stripped text/html.

    Returns the RAW extracted body — no whitespace normalization. This is
    deliberate: `_parse_full_message` feeds this value into `content_fallback_id` for a
    headerless email's stable dedup identity, so it must never change shape based on
    display formatting. Callers that want a normalized/trimmed body for storage or
    rendering should apply `_normalize_whitespace` themselves at the point they build
    that display copy, not here.
    """
    plain, html_body = _body_parts(msg)
    text = plain.strip() or _html_to_text(html_body)
    return text[:_MAX_BODY_CHARS]


def _display_body(msg: Message) -> str:
    """The body as stored and shown: like `_extract_body`, except a text/plain part that is
    really an HTML document (some receipt senders ship one) is stripped to text too — before
    the length cap, so the cap never spends itself on markup. Never feeds the dedup hash."""
    plain, html_body = _body_parts(msg)
    plain = plain.strip()
    if plain and _HTML_DOC_RE.search(plain):
        plain = _html_to_text(plain)
    text = plain or _html_to_text(html_body)
    return _normalize_whitespace(text[:_MAX_BODY_CHARS])


def _decode_envelope(msg: Message) -> tuple[str, str, str, str | None]:
    """subject, sender, iso_date, iso_date_time — the header-derived envelope fields
    every parse path needs, whether it came from a full RFC822 fetch or a
    HEADER.FIELDS-only peek (``search_headers``)."""
    subject = _decode(msg.get("Subject"))
    sender = _decode(msg.get("From"))
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        iso_date = dt.date().isoformat() if dt else user_today().isoformat()
    except Exception:
        dt = None
        iso_date = user_today().isoformat()
    iso_date_time = dt.isoformat() if dt else None
    return subject, sender, iso_date, iso_date_time


def _parse_full_message(uid: int, raw: bytes) -> dict:
    """Parse one RFC822 byte blob into the tool-facing message dict. Pure — no I/O.

    Shared by every full-RFC822-fetch call site (``fetch_by_message_ids``, the targeted
    Message-ID-header lookup behind ``get_messages``, and ``search_headers``' own
    no-Message-ID-header fallback) so each parses a full fetch IDENTICALLY — same
    message_id derivation, same body normalization — rather than a hand-maintained copy
    per caller that could drift.
    """
    msg = email.message_from_bytes(raw)
    subject, sender, iso_date, iso_date_time = _decode_envelope(msg)
    # `raw_body` (unnormalized) is what content_fallback_id hashes — see
    # `_extract_body`'s docstring for why it must stay byte-stable across any future
    # formatting-only change. `body_text` is the display/storage copy.
    raw_body = _extract_body(msg)
    body = _display_body(msg)
    return {
        "uid": uid,
        "message_id": (
            msg.get("Message-ID")
            or content_fallback_id(subject, sender, iso_date_time or iso_date, raw_body)
        ).strip(),
        "from": sender,
        "subject": subject,
        "date": iso_date,
        "date_time": iso_date_time,
        "body_text": body,
    }


def _chunked(items: list, size: int):
    """Yield ``items`` in fixed-size slices, preserving order."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_internaldate(header_line: bytes) -> str | None:
    """The zone-naive ISO date (``YYYY-MM-DD``) out of a tagged FETCH line's own
    ``INTERNALDATE "dd-Mon-yyyy hh:mm:ss +zzzz"`` literal, or ``None`` when the line carries
    none (a server that didn't honor the requested attribute)."""
    m = _FETCH_RESPONSE_INTERNALDATE_RE.search(header_line)
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2).decode("ascii").title(), m.group(3).decode("ascii")
    month_num = _MONTH_ABBR_TO_NUM.get(mon)
    if not month_num:
        return None
    return f"{year}-{month_num}-{int(day):02d}"


def _parse_internaldate_instant(header_line: bytes) -> str | None:
    """The UTC ISO instant out of the same ``INTERNALDATE`` literal, or ``None`` when the line
    carries no complete, valid one."""
    m = _FETCH_RESPONSE_INTERNALDATE_INSTANT_RE.search(header_line)
    if not m:
        return None
    day, mon, year, hour, minute, second, zone = (g.decode("ascii") for g in m.groups())
    month_num = _MONTH_ABBR_TO_NUM.get(mon.title())
    if not month_num:
        return None
    try:
        parsed = datetime.strptime(
            f"{year}-{month_num}-{int(day):02d} {hour}:{minute}:{second} {zone}",
            "%Y-%m-%d %H:%M:%S %z",
        )
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat()


def _parse_multi_fetch_response(data: list) -> dict[int, tuple[bytes, str | None, str | None]]:
    """Map UID -> (fetched header payload bytes, parsed INTERNALDATE as ISO date or None, the
    same INTERNALDATE as a UTC instant or None)
    from a raw imaplib response covering MULTIPLE UIDs in one
    ``UID FETCH <set> (UID INTERNALDATE ...)`` command.

    imaplib returns a flat list mixing ``(tagged_line, payload_bytes)`` tuples (one per
    message) with bare closing-paren byte strings (one per message, imaplib's own FETCH
    quirk) — never an association back to which UID a tuple belongs to, so the UID (and the
    INTERNALDATE, which rides in the same tagged line rather than its own literal block) must
    be pulled out of each tuple's OWN tagged line via the explicitly-requested fetch-atts,
    never assumed from response order (servers are not required to preserve the requested UID
    set's order)."""
    result: dict[int, tuple[bytes, str | None, str | None]] = {}
    for item in data:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        header_line, payload = item
        if not isinstance(header_line, (bytes, bytearray)) or not isinstance(
            payload, (bytes, bytearray)
        ):
            continue
        m = _FETCH_RESPONSE_UID_RE.search(header_line)
        if not m:
            continue
        result[int(m.group(1))] = (
            payload,
            _parse_internaldate(header_line),
            _parse_internaldate_instant(header_line),
        )
    return result


def test_connection(creds: dict) -> tuple[bool, str]:
    """Connect and authenticate only — no messages read, no cursor touched.
    Returns (True, "") on success or (False, error_message) on failure."""
    host = (creds.get("host") or "").strip()
    try:
        conn = imaplib.IMAP4_SSL(host, creds["port"], timeout=_IMAP_TIMEOUT_S)
        try:
            conn.login(creds["user"], creds["password"])
            conn.logout()
        except Exception:
            try:
                conn.logout()
            except Exception:
                pass
            raise
    except imaplib.IMAP4.error as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
    return True, ""


def search_headers(
    creds: dict,
    *,
    date_since: str | None = None,
    date_before: str | None = None,
    filter_criteria: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """
    Header-only search — the SEARCH half of the ``search_messages``/``get_messages``
    split (docs/adr/2026-08-12-email-search-hydrate-split.md). Never downloads a full
    RFC822 body: every candidate UID gets one ``BODY.PEEK[HEADER.FIELDS (...)]`` fetch
    instead of ``(RFC822)``, deferring the expensive MIME body fetch+parse to
    ``fetch_by_message_ids`` for whichever messages are actually selected afterward.

    Stateless date-range mode ONLY — no incremental cursor, no Gmail All Mail resolution:
    SINCE/BEFORE match INTERNALDATE, and the CONFIGURED folder is used UNCHANGED. Nothing
    here reads or writes any cursor (see this module's own docstring for why there is no
    poll cursor anywhere in it anymore).

    A message with no Message-ID header (rare, but real for some malformed/legacy mail)
    is upgraded to a full RFC822 fetch for JUST that one message, so its identity is
    still ``content_fallback_id`` computed from the SAME raw bytes ``_parse_full_message``/
    ``fetch_by_message_ids`` would see. Skipping this would let the same email hash to
    two different ids depending on which path first saw it, defeating the ledger's
    ``external_refs`` dedup — the exact failure class ``core.email_ids`` exists to
    prevent.

    Returns:
        {
            "messages": [{uid, message_id (RAW header value or content-fallback digest),
                          from, subject, date, date_time, received_date, received_at}],
                         # UID ascending, NO body_text. `date`/`date_time` are parsed from
                         # the email's OWN Date: header (the sender's clock, never
                         # normalized); `received_date` is the server's INTERNALDATE
                         # (what `since`/`before` actually filter on) as an ISO date —
                         # the two can legitimately land on different calendar days.
                         # `received_at` is that INTERNALDATE as a UTC instant.
            "uidvalidity": int,
            "folder": str,           # the folder actually SELECTed (for locator caching)
            "candidate_count": int,  # UIDs matched before limit/offset windowing
            "more": bool,
        }

    Raises on connection / auth errors.
    """
    configured_folder: str = (creds.get("folder") or "INBOX").strip() or "INBOX"
    host: str = (creds.get("host") or "").strip()

    logger.info(
        "imap: connecting to %s:%s as %s (mode=search-headers)",
        host,
        creds["port"],
        creds["user"],
    )
    conn = imaplib.IMAP4_SSL(host, creds["port"], timeout=_IMAP_TIMEOUT_S)
    try:
        conn.login(creds["user"], creds["password"])
        logger.info("imap: login OK")

        status, _data = conn.select(_quote_mailbox(configured_folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"could not select folder {configured_folder!r}")
        cur_validity = int(conn.response("UIDVALIDITY")[1][0] or 0)

        base_crit: list[str] = []
        if date_since:
            dt_since = datetime.strptime(date_since, "%Y-%m-%d").date()
            base_crit += ["SINCE", dt_since.strftime("%d-%b-%Y")]
        if date_before:
            dt_before = datetime.strptime(date_before, "%Y-%m-%d").date()
            base_crit += ["BEFORE", dt_before.strftime("%d-%b-%Y")]

        extra_crit = list(filter_criteria or [])
        crit = base_crit + extra_crit
        logger.info(
            "imap: search-headers scan — SEARCH %s (uidvalidity=%s, filters=[%s])",
            " ".join(base_crit),
            cur_validity,
            " ".join(t for t in extra_crit if not t.startswith('"')),
        )
        typ, search_data = conn.uid("search", None, *crit)
        if typ != "OK":
            raise RuntimeError("UID SEARCH failed")

        uids = sorted(int(u) for u in (search_data[0] or b"").split())
        candidate_count = len(uids)
        # A plain offset (not a shifted `since`) is the stable way to reach page 2 of a
        # deterministic, fixed window — without this, a caller had no way to reach the
        # rest of a "more" result set and improvised by sliding `since` forward instead,
        # re-reading the whole overlap every time and never draining the window.
        start = max(0, int(offset or 0))
        end = None if limit is None else start + limit
        more = candidate_count > (end if end is not None else start)
        uids = uids[start:end]

        # Batch the header FETCH across chunks of UIDs — ONE IMAP round trip per chunk
        # instead of one per message. Live 2026-08-12: a 50-message one-UID-at-a-time scan
        # took 37-57s against a real Gmail account and hit the 30s per-READ socket timeout
        # mid-scan — the cost here is IMAP round-trip LATENCY (one per command, regardless
        # of response size), not the header/body distinction this function otherwise
        # optimizes for. RFC 3501 §6.4.5 allows a UID SET (comma-separated) in one FETCH.
        headers_by_uid: dict[int, tuple[bytes, str | None, str | None]] = {}
        for chunk in _chunked(uids, _HEADER_FETCH_CHUNK):
            if should_stop is not None and should_stop():
                logger.info(
                    "imap: search-headers stopping early — caller cancelled (%d fetched)",
                    len(headers_by_uid),
                )
                break
            uid_set = ",".join(str(u) for u in chunk)
            typ, fetched = conn.uid(
                "fetch",
                uid_set,
                "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
            )
            if typ != "OK":
                logger.warning("imap: header fetch chunk %s failed", uid_set)
                continue
            headers_by_uid.update(_parse_multi_fetch_response(fetched))

        messages: list[dict] = []
        for uid in uids:
            if uid not in headers_by_uid:
                if should_stop is not None and should_stop():
                    break
                logger.warning("imap: header fetch UID %d missing from batch response", uid)
                continue
            header_bytes, received_date, received_at = headers_by_uid[uid]
            header_msg = email.message_from_bytes(header_bytes)
            raw_message_id = (header_msg.get("Message-ID") or "").strip()
            if not raw_message_id:
                # No Message-ID header on the peek — upgrade to a full fetch so the
                # identity is a content hash of the SAME bytes any other path would see.
                # Rare enough (malformed/legacy mail only) that a per-message round trip
                # here is not worth batching. `received_date` came off the header peek
                # above (which already requested INTERNALDATE), so it needs no re-fetch.
                typ2, full_fetched = conn.uid("fetch", str(uid), "(RFC822)")
                if typ2 != "OK" or not full_fetched or not full_fetched[0]:
                    logger.warning("imap: headerless-mail upgrade fetch UID %d failed", uid)
                    continue
                parsed = _parse_full_message(uid, full_fetched[0][1])
                logger.info(
                    "imap: header-fetched UID %d (%s) — upgraded to full fetch (no Message-ID)",
                    uid,
                    parsed["date"],
                )
                messages.append(
                    {
                        "uid": uid,
                        "message_id": parsed["message_id"],
                        "from": parsed["from"],
                        "subject": parsed["subject"],
                        "date": parsed["date"],
                        "date_time": parsed["date_time"],
                        "received_date": received_date,
                        "received_at": received_at,
                    }
                )
                continue
            subject, sender, iso_date, iso_date_time = _decode_envelope(header_msg)
            messages.append(
                {
                    "uid": uid,
                    "message_id": raw_message_id,
                    "from": sender,
                    "subject": subject,
                    "date": iso_date,
                    "date_time": iso_date_time,
                    "received_date": received_date,
                    "received_at": received_at,
                }
            )

        logger.info(
            "imap: search-headers done — %d candidate(s), %d header(s) fetched, folder=%r",
            candidate_count,
            len(messages),
            configured_folder,
        )
        return {
            "messages": messages,
            "uidvalidity": cur_validity,
            "folder": configured_folder,
            "candidate_count": candidate_count,
            "more": more,
        }
    finally:
        try:
            conn.logout()
            logger.debug("imap: logged out")
        except Exception:
            pass


def fetch_by_message_ids(
    creds: dict,
    requests: list[dict],
    *,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """
    Targeted full-body fetch for a bounded batch of previously-searched messages — the
    HYDRATE half of the ``search_messages``/``get_messages`` split. Opens ONE IMAP
    connection for the whole batch, never one per id: sibling tool calls in one model
    turn dispatch SEQUENTIALLY (backend/ai/assistant.py), so N singular fetches would
    cost N connection round trips where this costs one.

    ``requests``: ``[{"message_id": <internal short id, echoed back untouched>,
    "raw_message_id": <raw Message-ID header value, from a prior search_headers
    result>}]``. The raw value is never shown to the model (core/email_ids.py's identity
    ADR) — it travels server-side only, threaded through by the caller's own locator cache.

    Locates each message via ``UID SEARCH HEADER "Message-ID" <raw>`` in the configured
    folder (same "the configured folder is used unchanged" rule as ``search_headers`` —
    no Gmail All Mail resolution) and fetches the FIRST match. A request with an empty
    ``raw_message_id`` or no server match (deleted/
    moved/never existed) comes back ``found: False`` — this function never raises for a
    single missing id, so one gap in a batch doesn't fail its siblings.

    Returns: ``{"results": [{"message_id", "found": True, "raw_message_id", "folder",
    "uid", "from", "subject", "date", "date_time", "body_text"} |
    {"message_id", "found": False}]}``

    Raises on connection / auth errors (the whole batch fails together — the caller has
    already committed to reaching the mailbox once).
    """
    configured_folder: str = (creds.get("folder") or "INBOX").strip() or "INBOX"
    host: str = (creds.get("host") or "").strip()

    logger.info(
        "imap: connecting to %s:%s as %s (mode=fetch-by-message-id, batch=%d)",
        host,
        creds["port"],
        creds["user"],
        len(requests),
    )
    conn = imaplib.IMAP4_SSL(host, creds["port"], timeout=_IMAP_TIMEOUT_S)
    try:
        conn.login(creds["user"], creds["password"])
        logger.info("imap: login OK")

        status, _data = conn.select(_quote_mailbox(configured_folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"could not select folder {configured_folder!r}")

        results: list[dict] = []
        for req in requests:
            if should_stop is not None and should_stop():
                logger.info(
                    "imap: fetch-by-message-id stopping early — caller cancelled (%d/%d done)",
                    len(results),
                    len(requests),
                )
                break
            message_id = req.get("message_id")
            raw = (req.get("raw_message_id") or "").strip()
            if not raw:
                results.append({"message_id": message_id, "found": False})
                continue
            quoted = imap_quote_search_value(raw, field="message_id")
            typ, search_data = conn.uid("search", None, "HEADER", "Message-ID", quoted)
            uids = [int(u) for u in (search_data[0] or b"").split()] if typ == "OK" else []
            if not uids:
                logger.info("imap: fetch-by-message-id — no match for %s", message_id)
                results.append({"message_id": message_id, "found": False})
                continue
            uid = uids[0]
            typ, fetched = conn.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK" or not fetched or not fetched[0]:
                logger.warning(
                    "imap: fetch-by-message-id — fetch UID %d failed for %s", uid, message_id
                )
                results.append({"message_id": message_id, "found": False})
                continue
            parsed = _parse_full_message(uid, fetched[0][1])
            raw_message_id_confirmed = parsed.pop("message_id")
            logger.info("imap: fetch-by-message-id — fetched UID %d for %s", uid, message_id)
            results.append(
                {
                    "message_id": message_id,
                    "found": True,
                    "raw_message_id": raw_message_id_confirmed,
                    "folder": configured_folder,
                    **parsed,
                }
            )

        logger.info(
            "imap: fetch-by-message-id done — %d/%d found, folder=%r",
            sum(1 for r in results if r["found"]),
            len(requests),
            configured_folder,
        )
        return {"results": results}
    finally:
        try:
            conn.logout()
            logger.debug("imap: logged out")
        except Exception:
            pass
