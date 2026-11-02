"""Auto-generated tool discovery catalog (Lane A — tool-discovery RFC).

``build_tool_catalog`` renders every registered tool as a BARE NAME in ONE flat,
alphabetical, comma-separated list.  The output is embedded in the static
system-prompt block so the model always knows the full tool surface — zero
hand-maintenance.

Flat + names-only is deliberate (matching how Claude Code lists its own deferred
tools).  The catalog's job is EXISTENCE, not documentation or taxonomy: the model
does not need to read a description or a domain header to find a tool, it needs to
know the tool is there.  Descriptions live in the registry, where
``_groups._search_tools`` scores them server-side for ``find_tools(query=…)``, and
in the real schema that the dispatch loop auto-loads the instant the model calls an
un-loaded name.  Domain headers existed to make the (now removed) ``groups=[…]``
load path discoverable; with groups gone they were pure overhead.

NOTE: the EXTERNAL-MCP section below is deliberately NEITHER flat NOR names-only —
an external tool's name is often opaque and its server IS meaningful grouping, so a
small server still renders a (sanitized, untrusted) one-line summary per tool.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Deprecated/back-compat shims that must never be advertised to the model, and tools
# for a feature that is deliberately hidden while still live in code. Empty again since
# the tax feature was REMOVED outright (2026-08-25) rather than hidden — kept as the
# seam for the next one.
_CATALOG_HIDDEN: frozenset[str] = frozenset()


def hidden_tools() -> frozenset[str]:
    """Every tool kept out of what the model is offered: the permanent hides above plus any tool a
    feature flag has retired (``ai/search/switch.retired_tools``, empty while its flag is off).
    The flag is read once per turn (``assistant.run`` pins it in ``_skill_scope``); a caller
    outside a turn resolves it here."""
    from backend.ai.tools._skill_scope import turn_retired

    retired = turn_retired()
    if retired is None:
        from backend.ai.search.switch import retired_tools

        retired = retired_tools()
    return _CATALOG_HIDDEN | retired


# Summaries survive ONLY on the external-MCP path (see the module docstring): an
# external tool name is opaque and ungrouped, so a small server still gets one line each.
_MAX_SUMMARY_CHARS = 90

# Leading instruction line of the rendered catalog. Tells the model that names are all
# it gets here and where the schemas come from, so it never treats a bare name as an
# incomplete/unusable entry. Deliberately prose (parenthesized, no trailing ':') so it
# can never be mistaken for a tool name by a reader of the block.
_CATALOG_PREAMBLE = (
    "(Tool names only — no descriptions, and no schemas. Load a name with "
    'find_tools(names=[…]) before calling it, or find_tools(query="…") to search '
    "descriptions when the right name is not obvious.)"
)
# Adaptive external-MCP rendering: a small server is listed tool-by-tool; a larger one
# collapses to ONE roster line (name + tool count + a find_tools hint) so the 1h-cached
# system prompt stays flat no matter how many tools a connected server advertises — you
# cannot pre-group external tools, so search (find_tools query) is their discovery path
# at scale (cost/exhaustion control — an untrusted server could expose hundreds of tools).
_EXTERNAL_LIST_THRESHOLD = 8  # list individually up to this many tools per server
_MAX_EXTERNAL_TOOLS_LISTED = 40  # global cap on individually-listed external tool lines
_MAX_EXTERNAL_SERVERS = 20  # cap roster lines so a hub with hundreds of servers can't bloat
_MAX_EXTERNAL_NAME_CHARS = 80  # length-cap an (attacker-controlled) external tool name


def _sanitize_external(text: str) -> str:
    """Neutralize prompt-control characters in untrusted external-MCP text before it is
    embedded in the cached system prompt: drop control chars, collapse whitespace, and
    strip leading markdown header/fence markers so a description can't forge structure."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lstrip("#=`*->| ").strip()


def _first_sentence(description: str) -> str:
    """Return the first sentence of a description, trimmed to ~90 chars.

    External-MCP path only — the internal catalog is names-only.
    """
    # Split on '. ' or first newline, whichever comes first.
    newline_pos = description.find("\n")
    period_pos = description.find(". ")
    candidates = [p for p in (newline_pos, period_pos) if p >= 0]
    if candidates:
        cut = min(candidates)
        sentence = description[: cut + 1].strip()
    else:
        sentence = description.strip()
    if len(sentence) > _MAX_SUMMARY_CHARS:
        sentence = sentence[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return sentence


def _internal_names() -> list[str]:
    """Every registered internal tool name, sorted alphabetically.

    Excludes ``_CATALOG_HIDDEN`` (the permanent hides — empty today) AND
    ``_connector_gate.hidden_connector_tools()`` — a ``connector=``-tagged tool whose
    connector isn't enabled (RFC ``.claude/rfcs/2026-08-10-connectors-architecture.md``
    §B2). An untagged (internal) tool is never affected by the second check.
    """
    from backend.ai.tools._connector_gate import hidden_connector_tools
    from backend.ai.tools.registry import iter_registered_tools

    hidden = hidden_tools() | hidden_connector_tools()
    return sorted(
        name for name, _description, _module in iter_registered_tools() if name not in hidden
    )


def _external_sections() -> list[str]:
    """Render external-MCP tools as catalog lines, adaptively (best-effort, never raises).

    A server with <= ``_EXTERNAL_LIST_THRESHOLD`` tools (and within the global
    ``_MAX_EXTERNAL_TOOLS_LISTED`` budget) is listed tool-by-tool so the model can call
    them directly. A larger server collapses to a single roster line carrying its tool
    count and a ``find_tools(query=…)`` hint — the model searches that server on demand
    instead of paying for hundreds of tool lines in the 1h-cached prompt. You cannot
    pre-group external tools you have not connected to, so search is their discovery
    path at scale. Server-supplied text is untrusted, so every summary is sanitized.
    """
    try:
        from backend.core import mcp_external

        dispatcher = mcp_external.get_external_dispatcher()
        if dispatcher is None:
            return []
        by_server: dict[str, list[tuple[str, str]]] = {}
        for schema in dispatcher.schemas():
            name = schema.get("name", "")
            if not name:
                continue
            # Derive server label from the mcp__<server>__ prefix.
            match = re.match(r"mcp__([^_][^_]*)__", name)
            server = match.group(1) if match else "external"
            description = schema.get("description", "")
            # Do NOT fall back to the raw name (attacker-controlled, unsanitized) — an empty
            # description yields an empty summary; the name is rendered, sanitized, below.
            summary = _sanitize_external(_first_sentence(description)) if description else ""
            by_server.setdefault(server, []).append((name, summary))
        if not by_server:
            return []

        lines: list[str] = []
        listed = 0
        servers = sorted(by_server)
        # Cap the number of roster lines: a misconfigured/hostile hub could advertise
        # hundreds of servers, and even one short line each would bloat the 1h-cached
        # prompt. Past the cap, collapse the remainder into a single summary line.
        if len(servers) > _MAX_EXTERNAL_SERVERS:
            shown, overflow = servers[:_MAX_EXTERNAL_SERVERS], servers[_MAX_EXTERNAL_SERVERS:]
            overflow_tools = sum(len(by_server[s]) for s in overflow)
        else:
            shown, overflow, overflow_tools = servers, [], 0
        for server in shown:
            label = _sanitize_external(server) or "external"
            tools = sorted(by_server[server], key=lambda t: t[0])
            count = len(tools)
            if count <= _EXTERNAL_LIST_THRESHOLD and listed + count <= _MAX_EXTERNAL_TOOLS_LISTED:
                lines.append(f"External ({label}):")
                for name, summary in tools:
                    # The tool name carries an attacker-controlled suffix (the server's
                    # tools/list entry) — sanitize + length-cap before it enters the cached
                    # prompt so a crafted name can't inject newlines or forge catalog headers.
                    safe_name = _sanitize_external(name)[:_MAX_EXTERNAL_NAME_CHARS]
                    lines.append(f"  {safe_name} — {summary}" if summary else f"  {safe_name}")
                listed += count
            else:
                lines.append(
                    f'External ({label}): {count} tools — call find_tools(query="…") '
                    "to search this server"
                )
        if overflow:
            lines.append(
                f"External: +{len(overflow)} more servers ({overflow_tools} tools) — "
                'call find_tools(query="…") to search them'
            )
        return lines
    except Exception:
        return []


def build_tool_catalog(
    include_external: bool = False, *, names: Optional[Iterable[str]] = None
) -> str:
    """Compact discovery catalog: bare tool NAMES, comma-separated, one flat list.

    Generated from the registry — zero hand-maintenance. Returns a string ready to embed
    in the system prompt. Never raises (best-effort; returns '' or partial on error).

    Flat and alphabetical: domain grouping was what made the removed ``find_tools(groups=…)``
    load path discoverable, and with only keyword search left it bought nothing. Descriptions
    are NOT rendered — see the module docstring.

    ``names``, when given, restricts the internal list to exactly those names (still
    alphabetical) — the scoped variant an explicit-offer (agent) system prompt uses
    (``ai/prompts._fmt_agent_tool_catalog``) so a bounded agent's catalog names only ITS
    OWN not-yet-inlined tools, never the whole registry. ``None`` (the default) is the
    interactive behavior: every registered tool.
    """
    try:
        internal = _internal_names()
        if names is not None:
            allowed = set(names)
            internal = [n for n in internal if n in allowed]
        lines: list[str] = [_CATALOG_PREAMBLE, ", ".join(internal)]

        # External-MCP tools render after the internal list, adaptively (a large server
        # collapses to a roster line) so the cached prompt stays flat regardless of MCP scale.
        if include_external:
            lines.extend(_external_sections())
        return "\n".join(lines)
    except Exception:
        return ""
