"""Display metadata for a tool call — the ONE place a tool call becomes a human sentence.

The chat UI used to answer "what do I call this?" itself, from a 15-entry label table
against a 290-tool registry plus an unbounded set of external MCP tools; everything else
rendered its raw name (``mcp__playwright__browser_navigate``). Deriving it in the frontend
also meant joining three unrelated client-side sources — the tool name, the connectors
read, and the brand catalog — to draw one row.

So the SERVER answers it instead, once, and ships the answer with the call. This module is
that answer. It is consumed in exactly two places, and both matter:

  * ``assistant.py``'s live ``tool_use_start`` forward — a call as it happens;
  * ``assistant.py``'s ``rehydrate_messages`` — a call read back from a stored transcript.

The second is why this lives server-side rather than in the client: a transcript stores the
tool NAME, not its label, so labeling on read means a conversation recorded before this
module existed renders exactly like one recorded after it. There is no legacy path to keep
alive in the frontend and no second copy of the vocabulary to drift (the scar behind
``services.connector_copy.SHORT`` being read through by the frontend catalog rather than
re-authored there — 2026-08-14).

WHAT THIS IS NOT: a narration. Every field is DERIVED from the tool name, its declared
input, and the connector registry — never written by the model. ``ai/turn_activity``'s
rotating word makes the same argument for the working indicator: inventing plausible step
names states something the reader cannot check. A label here is a restatement of the call,
so it can always be checked against the Request the row expands to show.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core import mcp_external
from backend.core.untrusted import sanitize_untrusted
from backend.models.mcp_client import mcp_connector_id

# Caps are deliberately tight. A label is a ROW in a chat transcript, not a paragraph, and
# an external server chooses its own tool names — an un-capped one is a layout attack even
# when it is not a forgery attempt.
LABEL_CAP = 80
DETAIL_CAP = 48
OWNER_CAP = 48  # a real connector name ("Web search • Google Programmable Search") is 39

# The reserved owner for a tool with no ``connector=`` tag, mirroring
# ``services.tool_permissions.CORE_CONNECTOR_ID`` (imported lazily at call time — this
# module is imported from ``ai`` and must not drag the service layer in at import time).
CORE_OWNER = "core"


# --- icon hints -----------------------------------------------------------------------
#
# Leading token -> icon class. HINTS, not a contract: a prefix missing from this map costs
# a slightly less specific icon and NOTHING else. No label depends on it, so a new tool
# never needs an entry here and nobody has to remember to add one.
#
# The label itself is derived from the name with no table at all (see `_derive`), which is
# what makes the whole pipeline zero-maintenance. An earlier version mapped each prefix to a
# PAST-TENSE word ("get" -> "Read"), and that table WAS a contract: a verb missing from it
# fell through to "Used <noun>". Past tense was the only thing requiring the lookup, and it
# was wrong twice over — it also claimed a still-running call had finished.
_ICON_HINTS: dict[str, str] = {
    "get": "read",
    "list": "read",
    "read": "read",
    "describe": "read",
    "show": "read",
    "preview": "read",
    "explain": "read",
    "check": "read",
    "diff": "read",
    "search": "search",
    "find": "search",
    "query": "search",
    "lookup": "search",
    "match": "search",
    "trace": "search",
    "fetch": "browse",
    "navigate": "browse",
    "open": "browse",
    "browse": "browse",
    "visit": "browse",
    # The update group. Dropped by accident in the 2026-08-23 rewrite, which sent
    # `update_run_task` to its SECOND token — "run" — and drew a trade-shaped terminal glyph
    # on a plain edit (owner report). A missing hint is normally harmless; a missing hint on
    # a name whose second token happens to be another verb is not.
    "update": "update",
    "set": "update",
    "rename": "update",
    "move": "update",
    "reorder": "update",
    "apply": "update",
    "assign": "update",
    "merge": "update",
    "edit": "update",
    "mark": "update",
    "enable": "update",
    "disable": "update",
    "sync": "update",
    "refresh": "update",
    "save": "update",
    "resolve": "update",
    "confirm": "update",
    "link": "update",
    "attach": "update",
    "close": "update",
    "restore": "update",
    "swap": "update",
    "create": "create",
    "add": "create",
    "new": "create",
    "import": "create",
    "record": "create",
    "declare": "create",
    "acquire": "create",
    "propose": "create",
    "delete": "delete",
    "remove": "delete",
    "archive": "delete",
    "clear": "delete",
    "undo": "delete",
    "forget": "delete",
    "unlink": "delete",
    "cancel": "delete",
    "dismiss": "delete",
    "dispose": "delete",
    "run": "run",
    "execute": "run",
    "compute": "run",
    "calculate": "run",
    "analyze": "run",
    "generate": "run",
    "build": "run",
    "rebalance": "run",
    "simulate": "run",
    "screen": "run",
    "scan": "run",
    "verify": "run",
    "reconcile": "run",
    "take": "capture",
    "capture": "capture",
    "screenshot": "capture",
    "send": "send",
    "notify": "send",
    "post": "send",
    "reply": "send",
    "schedule": "send",
    "authenticate": "auth",
    "connect": "auth",
    "login": "auth",
    "authorize": "auth",
}

# Icon classes that assert the call only LOOKED at something. A tool that writes may never
# wear one — see `_icon_class`.
_READ_SHAPED_ICONS = frozenset({"read", "search", "browse"})

# Nouns that read badly under the mechanical pluralize/singularize rules, or whose tool
# name says something the reader does not share vocabulary for.
_NOUN_OVERRIDES: dict[str, str] = {
    "txn": "transaction",
    "txns": "transactions",
    "fx": "exchange rates",
    "pnl": "profit and loss",
    "nav": "net asset value",
    "rta": "ready to assign",
    "ytd": "year to date",
    "ohlc": "price history",
    "csv": "CSV",
    "pdf": "PDF",
    "url": "URL",
    "api": "API",
    "id": "id",
    "ids": "ids",
    "mcp": "MCP",
    "ai": "AI",
    "ui": "UI",
}

# OPTIONAL polish, never a requirement. Every tool labels itself from its own name; these
# are the handful whose name genuinely misleads or reads badly, and the table can be empty
# without anything breaking. Nothing needs adding here when a tool is added.
_OVERRIDES: dict[str, tuple[str, str, str]] = {
    # name: (icon class, label, grouping noun)
    "find_tools": ("search", "Search the tool catalog", "tools"),
    "activate_skill": ("read", "Load a skill", "skills"),
    "web_search": ("search", "Search the web", "searches"),
    "fetch_page": ("browse", "Read a page", "pages"),
    "run_code": ("run", "Run code", "scripts"),
    "get_screen_detail": ("read", "Read on-screen detail", "screens"),
    "show_guide": ("read", "Show a guide", "guides"),
    # `net_worth` has no verb at all in the name; `connector_status` is the pre-2026-10-10
    # name stored transcripts still carry (display only — it no longer dispatches).
    "net_worth": ("read", "Read net worth", "net worth"),
    "get_connector_status": ("read", "Check connector status", "connectors"),
    "connector_status": ("read", "Check connector status", "connectors"),
}

# One tool runs every budget method's own operations, so its name says nothing about the call
# ("Apply budget verb" — owner, 2026-09-26). Its row is labelled from the operation it ran.
# Transcripts from before the rename still carry `apply_budget_verb` + `verb_id`; they read the
# same (display only — the old name does not dispatch).
_BUDGET_OPERATION_TOOLS = frozenset({"apply_budget_operation", "apply_budget_verb"})
_BUDGET_METHOD_NAMES = {"proportional": "50/30/20", "zero_based": "Zero-Based"}
_BUDGET_OPERATION_LABELS = {
    "configure": "Set up",
    "set_config": "Set the split",
    "set_targets": "Change the split",
    "set_bucket": "Move an envelope",
    "set_cap": "Set a cap",
    "set_caps": "Set caps",
    "copy_targets_to_caps": "Copy targets to caps",
    "set_order": "Reorder envelopes",
    "set_income_categories": "Choose income categories",
    "set_group_targets": "Set group shares",
    "set_group_excluded": "Count a group in or out",
    "set_envelope_excluded": "Count an envelope in or out",
}


def _budget_operation_label(tool_input: Any) -> str:
    """ "Set caps (50/30/20)" — the operation, in words, and the method it ran on."""
    args = tool_input if isinstance(tool_input, dict) else {}
    op = str(args.get("operation") or args.get("verb_id") or "")
    words = _BUDGET_OPERATION_LABELS.get(op) or (
        op.replace("_", " ").capitalize() if op else "Change the budget"
    )
    method = _BUDGET_METHOD_NAMES.get(str(args.get("engine_id") or ""), "")
    return f"{words} ({method})" if method else words


# Input fields worth showing after the label, most specific first. Only ONE is ever used —
# a row is a sentence, not a record dump, and a money-bearing read must never be talked
# into projecting more of itself into a place the model can read back.
_DETAIL_FIELDS = (
    "url",
    "query",
    "q",
    "search",
    "symbol",
    "ticker",
    "path",
    "file_path",
    "name",
    "title",
    "account",
    "account_id",
    "category",
    "payee",
    "month",
    "period",
)


# Tokens that are never the thing acted on. ``sync_now`` is a sync, not a sync of "a
# now"; ``get_all_accounts`` reads accounts, not "all accounts".
_FILLER = frozenset(
    {
        "now",
        "all",
        "the",
        "a",
        "an",
        "my",
        "current",
        "latest",
        "again",
        "here",
        "for",
        "to",
        "from",
        "by",
        "with",
        "of",
        "in",
        "on",
    }
)

DEFAULT_NOUNS: dict[str, str] = {
    "read": "records",
    "search": "searches",
    "browse": "pages",
    "create": "records",
    "update": "records",
    "delete": "records",
    "run": "runs",
    "capture": "screenshots",
    "send": "messages",
    "auth": "connections",
    "unknown": "calls",
}


def _titlecase_noun(tokens: list[str]) -> str:
    """``["envelope", "targets"]`` → ``envelope targets``, with the abbreviation table applied."""
    return " ".join(_NOUN_OVERRIDES.get(t, t) for t in tokens if t)


def _owner_tokens(owner_id: str) -> set[str]:
    """The words the breadcrumb already carries, which the label must not repeat.

    `tavily_search` owned by `web_search:tavily` would otherwise read "Tavily search" beside
    a breadcrumb that already says "Web search • Tavily" — the same words, twice, in one row.
    """
    if not owner_id or owner_id == CORE_OWNER:
        return set()
    raw = owner_id.replace(":", "_").replace("-", "_")
    return {t for t in raw.split("_") if t}


def _trim_filler(tokens: list[str]) -> list[str]:
    """Drop filler at the EDGES only. `sync_now` is a sync of nothing, but
    `apply_payee_rules_to_payees` needs its "to" — dropping filler wherever it appeared
    turned that into "Apply payee rules payees"."""
    out = list(tokens)
    while out and out[0] in _FILLER:
        out.pop(0)
    while out and out[-1] in _FILLER:
        out.pop()
    return out


def _icon_class(name: str, tokens: list[str], mutates: tuple[str, ...]) -> str:
    """The icon class, taken from what the tool DOES before what it is called.

    A name proves nothing: `open_option` is a trade writing options + portfolio + ledger, and
    trusting its `open_` prefix rendered a money-moving call with the globe glyph a fetched
    web page gets. So a tool that writes may not wear a read-shaped icon — a RULE rather than
    a list of exceptions, so a future write tool named `get_…` is handled the day it is
    added.

    The fact it trusts is `mutates=` alone: a read that only makes a screen refresh
    (`refreshes=` — a bank fetch, an email search) is a read, and
    drawing a pencil beside "Fetch transactions" tells a person their data was edited when
    nothing they care about changed (owner report, 2026-08-22, on mobile). The same fact
    decides whether the tool asks by default, so the icon and the approval card cannot
    disagree about what counts as a change.
    """
    hint = ""
    for token in tokens[:2]:
        if token in _ICON_HINTS:
            hint = _ICON_HINTS[token]
            break
    if not mutates:
        return hint or "read"
    if not hint or hint in _READ_SHAPED_ICONS:
        return "update"
    return hint


def _strip_owner_prefix(tokens: list[str], skip: set[str]) -> list[str]:
    """Drop the owner's name off the FRONT of a tool name.

    `lunch_flow_get_status` and `google_pse_fetch_page` otherwise read as "Lunch flow get
    status" — the breadcrumb beside them already says whose tool it is.

    Only a LEADING run is stripped, never every occurrence: `web_search:google_pse` owns the
    word "search", and removing it wherever it appeared would eat the verb out of
    `google_pse_search`. Never strips the last token, so something always remains.
    """
    i = 0
    while i < len(tokens) - 1 and tokens[i] in skip:
        i += 1
    return tokens[i:]


def _derive(
    name: str, tokens: list[str], owner_id: str, mutates: tuple[str, ...]
) -> tuple[str, str, str]:
    """``(icon class, label, noun)`` from a tool name — with NO word list involved.

    The label is the name itself, de-underscored and sentence-cased: `export_tax_report` ->
    "Export tax report", `flag_suspicious_transactions` -> "Flag suspicious transactions".
    Any name this repo's `<verb>_<noun>` convention produces reads correctly the day it is
    added, and a name using a verb nobody anticipated reads exactly as well as one that was.

    That is the whole point. The previous version converted the verb to PAST TENSE, which
    required a lookup table — and a table is a thing to maintain and to forget: an
    unrecognised verb fell through to "Used <noun>". Present tense needs no table, and it is
    also the more honest tense: a row is drawn while its call is still running, where "Read
    live prices" claims something that has not happened yet.
    """
    if not tokens:
        return ("update" if mutates else "read"), "Run a tool", DEFAULT_NOUNS["unknown"]
    skip = _owner_tokens(owner_id)
    tokens = _strip_owner_prefix(tokens, skip)
    icon = _icon_class(name, tokens, mutates)
    words = _trim_filler([t for t in tokens if t not in skip]) or tokens
    label = _titlecase_noun(words)
    label = label[:1].upper() + label[1:]
    noun = _titlecase_noun(words[1:]) or DEFAULT_NOUNS.get(icon, "calls")
    return icon, label, noun


def _detail_from_input(tool_input: Any) -> Optional[str]:
    """The single most-informative input value, shortened for a row."""
    if not isinstance(tool_input, dict):
        return None
    for field in _DETAIL_FIELDS:
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if field == "url":
                text = _hostname(text)
            elif field in ("path", "file_path"):
                text = text.rsplit("/", 1)[-1] or text
            return _shorten(text)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _shorten(text: str) -> str:
    """Cap a detail — and SAY so when it was capped.

    A bare slice cut a 49-character query to "...retirement simulatio", printed inside
    quotes as if that were what was searched for (owner report, 2026-08-25). A truncation
    the reader cannot see is a value silently altered; the ellipsis is what makes the row
    checkable against the Request it expands to show.

    Sanitizes to one character PAST the cap first, because sanitizing can also shorten
    (confusables are stripped) — so the input length is not a reliable truncation signal,
    but the cleaned length is.

    Three dots rather than "…": `sanitize_untrusted` NFKC-normalizes, which expands the
    ellipsis character back into three dots — and the folded label is sanitized again on the
    way out while a standalone detail is not, so the two would disagree on the marker for
    the same truncation.
    """
    clean = sanitize_untrusted(text, DETAIL_CAP + 1)
    if len(clean) <= DETAIL_CAP:
        return clean
    return clean[: DETAIL_CAP - 3].rstrip() + "..."


def _hostname(url: str) -> str:
    """Host only — a full URL is never a row's worth of text."""
    stripped = url.split("://", 1)[-1]
    return stripped.split("/", 1)[0] or url


def _mcp_title(server_id: str, tool: str) -> Optional[str]:
    """The server's OWN title for this tool, when it reported one at handshake.

    Preferred over anything derived — "Search email threads" is what the server calls it,
    and beats a mechanical "Searched threads". It is read from ``discovered_tools``, which
    ``record_discovery`` alone writes from an actual handshake (ADR 2026-08-14: a tool cache
    is discovered, never supplied), so it cannot be set by a request body.

    Still SERVER-CHOSEN text, so the caller sanitizes and caps it: a title is drawn in the
    operator's UI, and a server that titles itself with a fence marker must not get to forge
    the marking that means the operator is speaking (ADR 2026-08-13).
    """
    try:
        from backend.db.mcp_client_store import McpClientStore

        server = McpClientStore().find(server_id)
        for entry in getattr(server, "discovered_tools", None) or []:
            if isinstance(entry, dict) and entry.get("name") == tool:
                title = entry.get("title") or entry.get("annotations", {}).get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()
        return None
    except Exception:
        return None


def describe_tool_call(name: str, tool_input: Any = None) -> dict[str, Any]:
    """The display block for one tool call: what the row says, and what it groups with.

    Shape (every field a plain string — this crosses SSE, a stored transcript and a React
    prop, so it stays JSON-trivial):

        label      the human sentence, capped and sanitized
        verb       closed-set class, keys the icon and the grouping
        noun       what was acted on, keys the grouping and the summary aggregate
        owner_id   ``plaid`` / ``web_search:tavily`` / ``mcp:<server>`` / ``core``
        owner_name display name for the breadcrumb, sanitized ("" when it adds nothing)
        detail     one input value worth showing, or ""

    Never raises. A label is decoration on a call that already happened; failing to compute
    one must never be able to take the call — or the turn — down with it.
    """
    try:
        return _describe(name, tool_input)
    except Exception:  # noqa: BLE001 — see docstring
        safe = sanitize_untrusted(str(name or "tool"), LABEL_CAP)
        return {
            "label": safe,
            "verb": "unknown",
            "noun": safe,
            "owner_id": CORE_OWNER,
            "owner_name": "",
            "detail": "",
        }


def _describe(name: str, tool_input: Any) -> dict[str, Any]:
    raw = str(name or "")
    detail = _detail_from_input(tool_input) or ""

    split = mcp_external.split_external(raw)
    if split is not None:
        server_id, tool = split
        owner_id = mcp_connector_id(server_id)
        tokens = [t for t in tool.replace("-", "_").split("_") if t]
        # An external server's tools are dispatched outside the registry, so nothing declares
        # `mutates` for them; treat them as writes for icon purposes only when the name says
        # so, never by assumption.
        verb_class, derived_label, noun = _derive(tool, tokens, owner_id, ())
        title = _mcp_title(server_id, tool)
        label = sanitize_untrusted(title, LABEL_CAP) if title else derived_label
        label, detail = _with_detail(label, detail, verb_class)
        return {
            "label": sanitize_untrusted(label, LABEL_CAP),
            "verb": verb_class,
            "noun": noun,
            "owner_id": owner_id,
            "owner_name": _owner_name(owner_id),
            "detail": detail,
        }

    from backend.ai.tools._connector_gate import permission_owner

    canonical = raw or raw
    owner_id = permission_owner(canonical)

    override = _OVERRIDES.get(canonical)
    if canonical in _BUDGET_OPERATION_TOOLS:
        verb_class, label, noun = "update", _budget_operation_label(tool_input), "budget changes"
    elif override is not None:
        verb_class, label, noun = override
    else:
        tokens = [t for t in canonical.split("_") if t]
        verb_class, label, noun = _derive(canonical, tokens, owner_id, _mutates_of(canonical))

    label, detail = _with_detail(label, detail, verb_class)
    return {
        "label": sanitize_untrusted(label, LABEL_CAP),
        "verb": verb_class,
        "noun": noun,
        "owner_id": owner_id,
        "owner_name": _owner_name(owner_id),
        "detail": detail,
    }


def _with_detail(label: str, detail: str, verb_class: str) -> tuple[str, str]:
    """Fold the detail into the sentence where it reads as one, else leave it to the row.

    Returns ``(label, detail)``, and a detail that was folded in comes back EMPTY. Both
    fields ship, and the row renders both — so a detail that is inside the sentence AND
    returned on its own is printed twice, once in the label and once in the muted span
    beside it: `Search docs for "planned purchase..."  planned purchase...` (owner report,
    2026-08-25). A value belongs in exactly one place on a row, and this is the function
    that decides which place.

    Only the classes whose detail IS the object get the inline treatment. For everything
    else the row renders the detail as its own muted span, because "Updated transactions
    groceries" is not a sentence.
    """
    if not detail:
        return label, detail
    if verb_class == "browse":
        return f"Opened {detail}", ""
    if verb_class == "search":
        return f'{label} for "{detail}"', ""
    return label, detail


def _mutates_of(name: str) -> tuple[str, ...]:
    """What this tool changes, per the registry — the fact `_icon_class` trusts over the name.
    Empty for an unknown tool, which is the safe read-shaped default."""
    try:
        from backend.ai.tools.registry import written_domains

        return tuple(written_domains(name) or ())
    except Exception:
        return ()


def _owner_name(owner_id: str) -> str:
    """Breadcrumb for a connector-owned tool. ``core`` gets none — an unscoped tool is just
    the app itself, and prefixing every row with the product name is noise.

    Sanitized and capped on the way out because an MCP server's name resolves through this
    same helper and is operator/server-chosen text, not ours.
    """
    if not owner_id or owner_id == CORE_OWNER:
        return ""
    try:
        from backend.services.connectors import display_name_for

        return sanitize_untrusted(display_name_for(owner_id), OWNER_CAP)
    except Exception:
        return ""
