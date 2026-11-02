"""Email comms-channel tools — the generic RECEIVE + SEND blocks.

Email is a bidirectional connector, not a hardcoded ingestion pipeline (RFC
docs/rfc/email-as-generic-source.md). It exposes generic blocks Lucius/agents compose:

  * ``email_search_messages`` + ``email_get_messages`` — the RECEIVE pair (ADR
    2026-08-12-email-search-hydrate-split, which also retired the earlier single fused
    ``fetch_emails`` tool this pair replaced): ``email_search_messages`` returns sender/subject/date
    for every candidate in a date window WITHOUT downloading any body — a full RFC822 fetch +
    MIME parse (the expensive part) previously happened for every matched email whether or not
    it was ever read. ``email_get_messages`` then fetches the FULL body for just the ids actually
    selected, batched in one IMAP connection. Each search result carries TWO dates that are
    NOT the same clock: ``date`` is parsed from the email's own ``Date:`` header (the sender's
    local timezone, never normalized), while ``received_date`` is the server's INTERNALDATE —
    the field ``since``/``before`` actually filter on. Added so a caller can tell the two apart
    instead of assuming a message's displayed date matches the window it was found in.
  * ``email_send_message``   — SEND: compose + send via the connector's SMTP config (an ACTION →
    confirm-first, OFF the headless ceiling).

Both are STATELESS — nothing is remembered between calls. Pass ``since``/``before``; there is
no cursor (a per-consumer incremental cursor was tried and retired 2026-08-10: it silently
advanced past unlogged mail on two separate incidents). Overlap is deliberate — re-searching
or re-fetching an already-booked email is a no-op at the ledger choke via its ``external_refs``
pair, so a window wider than your schedule costs a few duplicate reads while a cursor that
advanced past unprocessed mail lost it permanently.

SEARCH FILTERS
--------------
``from_address`` / ``subject_contains`` / ``unseen_only`` / ``header`` map to the RFC 3501
§6.4.4 SEARCH keys FROM / SUBJECT / UNSEEN / HEADER.  They are ADDITIONAL CRITERIA that AND
onto the date window, not a replacement for it.

Every filter value is free text arriving from the MODEL, which may have just read an
attacker-controlled email — so all of them go through the single guard
``imap_client.build_search_filters`` → ``imap_quote_search_value``, which REFUSES (never
strips) CR/LF/NUL and quotes as an IMAP quoted-string.  See that module for the full
threat model; the short version is that ``imaplib`` concatenates arguments onto the wire
RAW, so an unrefused newline is a second IMAP command. ``body``/``TEXT`` search is
deliberately NOT offered: it is a server-side full-text scan that is brutally slow on a
large mailbox, and the four header-level filters cover the real use case.

Spending-logging is no longer code here — it is a SEEDED, editable Agent + Skill +
Automation (the ``email-transaction-logger`` built-ins) that USES ``email_search_messages`` +
``email_get_messages`` + ``add_transactions``. The IMAP/SMTP passwords are secrets — human-only to
set.

Idempotency guarantee
---------------------
``add_transaction``/``add_transactions`` accept a provenance list (ADR 2026-07-21): the seeded
logger agent passes
``external_refs=[{"source": "email", "ref": <the message_id shown by email_search_messages/email_get_messages>}]``
(the ref copied verbatim — no prefix, no colon, no brackets to add). ``create_transaction`` does
a ``transaction_store.has_external_ref(source, ref)`` check — an existing pair is a safe no-op
(returns the existing row, writes nothing). So if the agent is re-run for any reason over
an email it already logged, the second call is a silent no-op. This is a LEDGER guarantee,
not a prompt guarantee.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    explicit_null,
    schema_of,
)
from backend.core.email_ids import internal_email_id
from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)


class HeaderFilter(NestedInput):
    """One ``HEADER <field> <value>`` SEARCH criterion — the generic escape hatch."""

    field: str = Field(
        description=(
            "Header name, a bare token with no spaces or colon (e.g. 'X-Mailer', "
            "'List-Id', 'Return-Path')."
        ),
    )
    value: str = Field(
        description=(
            "SUBSTRING to look for in that header's value, matched case-insensitively. "
            "Not an exact match."
        ),
    )


def _exc_text(exc: BaseException) -> str:
    """``str(exc)`` for an IMAP failure, whose args can be the server's raw response BYTES
    (``imaplib`` exceptions carry the literal wire bytes) — a bare ``str(exc)`` on a
    single-bytes-arg exception renders Python's bytes repr (``"b'Incorrect username, password
    or access token.'"``), which is exactly the mangled text a user saw on screen instead of
    the message underneath. Decodes any bytes/bytearray arg first, then falls back to the
    same single-arg / multi-arg shape ``Exception.__str__`` itself uses.
    """
    args = tuple(
        a.decode("utf-8", "replace") if isinstance(a, (bytes, bytearray)) else a for a in exc.args
    )
    if not args:
        return str(exc)
    if len(args) == 1:
        return str(args[0])
    return str(args)


def _search_arg(raw) -> "str | None":
    """One search argument, passed to the guard UNSTRIPPED.

    Two defects this closes, both from stripping before validating (security review
    2026-07-20):

    SEC-2 — `.strip()` first silently removed a CR/LF at either END and let the call
    proceed: `from_address='bank\\r\\n'` was ACCEPTED. The residue is quoted and not itself
    exploitable, but that is not the point — the guard's job is to be OBSERVABLE when an
    injected value arrives, and trailing CRLF is exactly the shape an injected instruction
    produces. A silently-cleaned attack is an attack nobody saw.

    BUG-1 — `'   '.strip() or None` collapsed a whitespace-only value to None, so NO filter
    was built while the caller believed the scan was narrowed to one sender. The call was
    then treated as unfiltered, which ALSO advances the incremental cursor past mail nobody
    read. Silent scope-widening and data loss from one blank value.

    So: hand the raw value to the guard (which refuses CR/LF/NUL loudly), and treat only a
    genuinely EMPTY string as "not supplied". A value that is present but blank is a caller
    error worth refusing, not a filter worth dropping.
    """
    from backend.core.errors import ValidationError  # local: module keeps its imports lazy

    if raw is None:
        return None
    text = str(raw)
    if text == "":
        return None
    if not text.strip():
        raise ValidationError(
            "search filter: value is blank. Omit the parameter entirely rather than "
            "passing whitespace — a blank filter would silently widen the scan."
        )
    return text


class SearchMessagesInput(ToolInput):
    """Schema-of-record for ``email_search_messages`` — same filters as ``fetch_emails``, minus
    nothing: the point of the split is the RESPONSE shape (headers only), not a narrower
    request."""

    folder: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description="Mailbox folder to scan (defaults to the configured folder, e.g. INBOX).",
    )
    offset: Optional[int] = Field(
        default=None,
        description=(
            "Skip this many matches in the window before returning any — how you reach the "
            "rest when more=true. Same since/before, offset += limit each time."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        description="Max messages to return, oldest first. Default 50; more=true when the window holds more.",
    )
    since: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description=(
            "Include messages received on or after this ISO date, YYYY-MM-DD. Pass this or "
            "'before' — a call with neither is refused, because nothing is remembered "
            "between calls and this is how you say what to search. Matches on INTERNALDATE "
            "— when the server RECEIVED the message, not the Date: header. Each result's "
            "'received_date' field is this same INTERNALDATE, so you can tell whether a "
            "message actually falls inside the window you asked for — its 'date' field is "
            "a different clock (see that field's note) and can legitimately land on the "
            "adjacent day."
        ),
    )
    before: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description=(
            "Include messages received BEFORE this ISO date (YYYY-MM-DD, exclusive). "
            "Optional — omit for 'since this date until now'."
        ),
    )
    from_address: Optional[str] = Field(
        default=None,
        description=(
            "FILTER: keep only messages whose From: header CONTAINS this text — a SUBSTRING "
            "match, CASE-INSENSITIVE, not an exact address. ASCII only, max 256 chars."
        ),
    )
    subject_contains: Optional[str] = Field(
        default=None,
        description=(
            "FILTER: keep only messages whose Subject CONTAINS this text — a SUBSTRING "
            "match, CASE-INSENSITIVE. ASCII only, max 256 chars."
        ),
    )
    unseen_only: Optional[bool] = Field(
        default=None,
        description="FILTER: when true, keep only UNREAD messages. Never marks anything as read.",
    )
    header: Optional[HeaderFilter] = Field(
        default=None,
        description=(
            "FILTER (escape hatch): keep only messages whose given header CONTAINS the given "
            "value — SUBSTRING, CASE-INSENSITIVE."
        ),
    )


@tool(
    "email_search_messages",
    description=(
        "Search the connected mailbox and return sender/subject/date for each match — NO "
        "body text, so it is cheap even over a wide date window. Use this to find "
        "candidates, then call email_get_messages with the message_id values you actually need "
        "to read to fetch their full body. Pass a date window; nothing is remembered "
        "between calls. Requires IMAP credentials (Connectors → Email)."
    ),
    input_schema=schema_of(SearchMessagesInput, tool_name="email_search_messages"),
    # Read-SHAPED but writes: stashes a lightweight (body-less) provenance snapshot per
    # match, same as fetch_emails, so email_get_messages can later locate it by message_id.
    mutates=("config",),
    # Scoped to the Email connector's IMAP leg — the JOIN onto tool_permissions (a
    # `blocked`/`always_allow` set via the Email connector's own ladder now actually
    # governs dispatch instead of silently landing under the unrelated `core` bucket) and
    # onto Remove's `clear_connector_permissions("email")`. Its default LEVEL is unchanged
    # by this tag (still `always_allow`, never asks) — `GATE_EXEMPT_READ_SHAPED` is
    # consulted by `effective_level` regardless of owner precisely so a tag that only
    # changes WHO a decision is filed under can't also start carding this read; see that
    # function's docstring.
    connector="email",
)
async def email_search_messages(input_data: dict):
    import asyncio

    task = asyncio.current_task()
    stop_flag = {"stop": False}

    def _should_stop() -> bool:
        return stop_flag["stop"] or bool(task is not None and task.cancelled())

    try:
        return await asyncio.to_thread(_email_search_messages_sync, input_data, _should_stop)
    except asyncio.CancelledError:
        stop_flag["stop"] = True
        raise


def _index_headers_best_effort(headers: list[dict]) -> None:
    """Persist a lightweight (body-less) snapshot per searched message so email_get_messages can
    later locate it by message_id without the model ever seeing the raw Message-ID header
    (core.email_ids' identity ADR). Best-effort, same swallow-and-log contract as
    ``_stash_snapshots_best_effort``."""
    from backend.services import email_snapshots

    try:
        email_snapshots.stash_snapshots(headers)
    except Exception:
        logger.warning("email_search_messages: header index stash failed", exc_info=True)


def _email_search_messages_sync(input_data: dict, should_stop=None):
    if not (input_data.get("since") or "").strip() and not (input_data.get("before") or "").strip():
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "email_search_messages needs a window: pass 'since' (YYYY-MM-DD) and "
                        "optionally 'before'. There is no stored position to resume from. "
                        "Overlap is safe — a re-search just re-shows the same message_id — "
                        "so choose a window a little WIDER than your schedule rather than "
                        "risk a gap."
                    ),
                }
            ),
            None,
        )
    import re as _re

    from backend.core.errors import ValidationError
    from backend.db import EmailConfigStore
    from backend.ingest import imap_client

    store = EmailConfigStore()
    creds = store.credentials()
    if creds is None:
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "Email IMAP is not configured. Set the mailbox credentials in "
                        "Connectors → Email."
                    ),
                }
            ),
            None,
        )

    folder = (input_data.get("folder") or "").strip()
    if folder:
        creds = {**creds, "folder": folder}
    try:
        limit = int(input_data.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(200, limit))
    try:
        offset = max(0, int(input_data.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    _DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
    since_raw = (input_data.get("since") or "").strip() or None
    before_raw = (input_data.get("before") or "").strip() or None
    for param_name, param_val in (("since", since_raw), ("before", before_raw)):
        if param_val is not None and not _DATE_RE.match(param_val):
            return (
                json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"Invalid {param_name!r} date {param_val!r} — expected YYYY-MM-DD."
                        ),
                    }
                ),
                None,
            )

    header_raw = input_data.get("header") or {}
    if not isinstance(header_raw, dict):
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": "Invalid 'header' — expected an object like "
                    "{'field': 'List-Id', 'value': 'statements'}.",
                }
            ),
            None,
        )
    try:
        filter_criteria = imap_client.build_search_filters(
            from_address=_search_arg(input_data.get("from_address")),
            subject_contains=_search_arg(input_data.get("subject_contains")),
            unseen_only=bool(input_data.get("unseen_only")),
            header_field=_search_arg(header_raw.get("field")),
            header_value=_search_arg(header_raw.get("value")),
        )
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": str(exc)}), None

    try:
        result = imap_client.search_headers(
            creds,
            date_since=since_raw,
            date_before=before_raw,
            filter_criteria=filter_criteria,
            limit=limit,
            offset=offset,
            should_stop=should_stop,
        )
    except Exception as exc:
        text = _exc_text(exc)
        status = "auth_error" if "authentication" in text.lower() else "connect_error"
        store.record_status(status, text)
        logger.warning("email_search_messages failed: %s", text)
        return json.dumps({"ok": False, "error": text}), None

    store.record_status("ok")
    headers = result["messages"]
    logger.info(
        "email-search since=%s before=%s candidates=%d returned=%d more=%s",
        since_raw,
        before_raw,
        result["candidate_count"],
        len(headers),
        result["more"],
    )
    _index_headers_best_effort(headers)
    rendered = [
        {
            "message_id": internal_email_id(m.get("message_id", "")),
            "from": sanitize_untrusted(m.get("from", "")),
            "subject": sanitize_untrusted(m.get("subject", "")),
            "date": m.get("date", ""),
            "received_date": m.get("received_date") or "",
        }
        for m in headers
    ]
    return (
        json.dumps(
            {
                "ok": True,
                "since": since_raw,
                "before": before_raw,
                "offset": offset,
                "showing": len(rendered),
                "total": result["candidate_count"],
                "more": result["more"],
                "messages": rendered,
                "note": (
                    "Message bodies are not included — call email_get_messages with the "
                    "message_id values you need to read in full."
                ),
                **(
                    {
                        "filtered": True,
                        "filter_note": (
                            "Counts are AFTER filtering — non-matching messages exist in "
                            "this window but were excluded, so no matches is not an empty "
                            "inbox."
                        ),
                    }
                    if filter_criteria
                    else {}
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


_MAX_GET_MESSAGES_BATCH = 25


class GetMessagesInput(ToolInput):
    """Schema-of-record for ``email_get_messages``."""

    message_ids: List[str] = Field(
        min_length=1,
        description=(
            "The message_id values shown by email_search_messages for the emails you want to "
            f"read in full, copied verbatim. Up to {_MAX_GET_MESSAGES_BATCH} per call — "
            "batch every id you need rather than calling this repeatedly."
        ),
    )


@tool(
    "email_get_messages",
    description=(
        "Read one or more emails IN FULL (sender, subject, date, body) by their "
        "message_id — the ids shown by email_search_messages. Batches in ONE mailbox "
        f"connection, up to {_MAX_GET_MESSAGES_BATCH} ids per call — pass every id you "
        "need in one call rather than calling this repeatedly. Requires IMAP credentials "
        "(Connectors → Email)."
    ),
    input_schema=schema_of(GetMessagesInput, tool_name="email_get_messages"),
    # Read-SHAPED but writes: stashes/completes a provenance snapshot per fetched message.
    mutates=("config",),
    # See email_search_messages' identical comment — same connector, same default-level guarantee.
    connector="email",
)
async def email_get_messages(input_data: dict):
    import asyncio

    task = asyncio.current_task()
    stop_flag = {"stop": False}

    def _should_stop() -> bool:
        return stop_flag["stop"] or bool(task is not None and task.cancelled())

    try:
        return await asyncio.to_thread(_email_get_messages_sync, input_data, _should_stop)
    except asyncio.CancelledError:
        stop_flag["stop"] = True
        raise


def _email_get_messages_sync(input_data: dict, should_stop=None):
    from backend.db import EmailConfigStore
    from backend.ingest import imap_client
    from backend.services import email_snapshots

    raw_ids = input_data.get("message_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return (
            json.dumps(
                {"ok": False, "error": "email_get_messages needs a non-empty 'message_ids' array."}
            ),
            None,
        )
    message_ids = [str(x).strip() for x in raw_ids if str(x or "").strip()]
    if not message_ids:
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": "email_get_messages needs at least one non-empty message_id.",
                }
            ),
            None,
        )
    if len(message_ids) > _MAX_GET_MESSAGES_BATCH:
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"email_get_messages accepts at most {_MAX_GET_MESSAGES_BATCH} message_ids "
                        f"per call; got {len(message_ids)}. Split into smaller batches."
                    ),
                }
            ),
            None,
        )

    store = EmailConfigStore()
    creds = store.credentials()
    if creds is None:
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "Email IMAP is not configured. Set the mailbox credentials in "
                        "Connectors → Email."
                    ),
                }
            ),
            None,
        )

    results_by_id: dict[str, dict] = {}
    requests = []
    for mid in message_ids:
        snap = email_snapshots.locate(mid)
        raw_message_id = (snap or {}).get("raw_message_id")
        if not raw_message_id:
            results_by_id[mid] = {
                "message_id": mid,
                "ok": False,
                "error": "Unknown message_id — call email_search_messages again to refresh it.",
            }
            continue
        requests.append({"message_id": mid, "raw_message_id": raw_message_id})

    if requests:
        try:
            fetch_result = imap_client.fetch_by_message_ids(
                creds, requests, should_stop=should_stop
            )
        except Exception as exc:
            text = _exc_text(exc)
            status = "auth_error" if "authentication" in text.lower() else "connect_error"
            store.record_status(status, text)
            logger.warning("email_get_messages failed: %s", text)
            return json.dumps({"ok": False, "error": text}), None
        store.record_status("ok")

        for r in fetch_result["results"]:
            mid = r["message_id"]
            if not r.get("found"):
                results_by_id[mid] = {
                    "message_id": mid,
                    "ok": False,
                    "error": (
                        "Message not found on the server — it may have been deleted or "
                        "moved. Call email_search_messages again to refresh it."
                    ),
                }
                continue
            # Defense in depth: the id this result claims to satisfy must actually hash
            # from the raw Message-ID this fetch confirmed — a mismatch would mean the
            # locator was corrupted or reused, and serving it under the WRONG id could
            # attach one email's content to another's booked transaction.
            confirmed_internal = internal_email_id(r.get("raw_message_id", ""))
            if confirmed_internal != mid:
                logger.warning(
                    "email_get_messages: locator mismatch for %s (resolved to %s) — refusing",
                    mid,
                    confirmed_internal,
                )
                results_by_id[mid] = {
                    "message_id": mid,
                    "ok": False,
                    "error": (
                        "Locator mismatch — refusing to serve possibly-wrong content. "
                        "Call email_search_messages again to refresh it."
                    ),
                }
                continue
            # Best-effort: a snapshot-store failure (e.g. a corrupt store) must never fail
            # the fetch itself — the model still gets the body it asked for, matching the
            # existing contract every other stash site in this module observes.
            try:
                email_snapshots.stash_or_update(
                    mid,
                    {
                        "raw_message_id": r["raw_message_id"][:512],
                        "subject": sanitize_untrusted(r.get("subject") or ""),
                        "from_addr": sanitize_untrusted(r.get("from") or ""),
                        "received_at": r.get("date_time") or r.get("date") or "",
                        "body_text": sanitize_untrusted(r.get("body_text") or ""),
                    },
                )
            except Exception:
                logger.warning(
                    "email_get_messages: snapshot hydrate failed for %s", mid, exc_info=True
                )
            results_by_id[mid] = {
                "message_id": mid,
                "ok": True,
                "from": sanitize_untrusted(r.get("from") or ""),
                "subject": sanitize_untrusted(r.get("subject") or ""),
                "date": r.get("date") or "",
                "body": sanitize_untrusted(r.get("body_text") or ""),
            }

    ordered = [results_by_id[mid] for mid in message_ids]
    return (
        json.dumps({"ok": True, "showing": len(ordered), "messages": ordered}, ensure_ascii=False),
        None,
    )


class SendEmailInput(ToolInput):
    """Schema-of-record for ``email_send_message`` (see ``schema_from_model``)."""

    to: str = Field(description="Recipient email address.")
    subject: str = Field(description="Email subject line.")
    body: str = Field(description="Plain-text email body.")
    cc: Optional[str] = Field(default=None, description="Optional CC recipient(s).")


@tool(
    "email_send_message",
    description=(
        "SEND block — compose and send a plaintext email via the Email connector's SMTP "
        "configuration. Use to reply to someone, send a summary/report, or notify a person by "
        "email. This SENDS an outbound message, so confirm the recipient + content with the "
        "user before sending. Requires the Email connector's SMTP credentials to be configured "
        "(Connectors → Email → send)."
    ),
    input_schema=schema_of(SendEmailInput, tool_name="email_send_message"),
    # Scoped to the Email connector's SMTP leg — the JOIN onto tool_permissions, so a level
    # set through the Email connector's own ladder (or cleared by its own Remove) governs
    # this tool instead of landing in the unrelated `core` bucket every other unscoped
    # write shares. Previously untagged: a decision made from Connectors -> Email's own
    # "Tool permissions" section was stored under connector_id="email" (the route takes the
    # id from the URL) but dispatch resolved the owner as "core" — so it was silently NEVER
    # consulted. This tag makes the write and read paths agree.
    connector="email",
    external_effect=True,
)
async def email_send_message(input_data: dict):
    import asyncio

    from backend.connectors.email_out.channel import send_email as _send

    result = await asyncio.to_thread(
        _send,
        input_data.get("to", ""),
        input_data.get("subject", ""),
        input_data.get("body", ""),
        input_data.get("cc"),
    )
    return json.dumps(result, ensure_ascii=False), None
