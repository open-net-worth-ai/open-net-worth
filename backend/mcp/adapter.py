"""The MCP↔tool-registry seam.

The MCP server is NOT a chat loop — an MCP client *is* the model. So we do not route
through ``assistant.run`` (no system prompt, no LLM, no token cost). The adapter calls
``execute_tool(name, arguments)`` directly, exactly like a single tool dispatch.

**Authorization is AUTHENTICATION, here.** A caller that presents the instance's Bearer
token IS the operator (``server._require_mcp_bearer``); there is no second, finer-grained
permission layer behind it. The former tiering — a ``MCP_TOOL_NAMES`` allowlist, a
grant/capability envelope with per-run/day/amount caps, and a propose-instead-of-write
fallback queue — has been REMOVED. A ``tools/call`` dispatches its tool.

What survives, and why it is not authorization:

  * **Checkpoint + audit** (``call_tool``) — a MUTATING call runs inside an explicit
    ``CheckpointContext`` (so it is undoable; there is no ``assistant.run`` turn to wrap it)
    and appends a row to ``McpWriteLogStore``. Recoverability and observability, not a gate:
    nothing here can refuse a call.
  * **Annotations** (``_annotations_for``) — MCP ``readOnlyHint``/``destructiveHint``
    presentation so a client (Claude Desktop) can group tools. Derived from each tool's own
    ``mutates=(…)`` registry tag.
  * **Resources** (``_RESOURCE_CATALOG``) — a curated read-only data surface. The catalog is
    still a closed set (an unlisted uri is refused) because it is a *catalog*, not a
    permission list: there is nothing to address outside it.
"""

from __future__ import annotations

import json as _json
from typing import Optional, Tuple

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools.registry import is_rest_send, mutated_domains


def _annotations_for(name: str) -> dict:
    """The MCP ``annotations`` block for a tool — CLIENT PRESENTATION ONLY.

    Derived from the tool's own ``mutates=(…)`` registry tag, which is complete and
    test-enforced for every registered tool (``tests/test_tool_domains.py``): a tool that
    declares no mutated data domain writes nothing persistent, so it is read-only.

      * untagged (no ``mutates``) → ``readOnlyHint: true`` + ``idempotentHint: true``.
      * tagged → ``readOnlyHint: false``; ``destructiveHint`` for the delete/remove/reset
        verbs, which an MCP client renders with a stronger confirmation affordance.

    ``propose_*`` is checked FIRST: it returns a diff and writes nothing, but it is not
    idempotent in the client's sense (re-running surfaces the diff again). The tag is a
    data-REFRESH signal, so a tool whose side effect leaves no local data domain to refetch
    (outbound mail, sandboxed code execution) is untagged — see ``_SIDE_EFFECT_NO_DOMAIN``,
    which keeps those from being advertised read-only.
    """
    if name.startswith("propose_"):
        return {"readOnlyHint": True}
    if name in _SIDE_EFFECT_NO_DOMAIN or is_rest_send(name):
        return {"readOnlyHint": False}
    if not mutated_domains(name):
        return {"readOnlyHint": True, "idempotentHint": True}
    if name.startswith(_DESTRUCTIVE_PREFIXES) or name in _DESTRUCTIVE_TOOLS:
        return {"readOnlyHint": False, "destructiveHint": True}
    return {"readOnlyHint": False}


# Tools with a REAL side effect but no local data domain a view could refetch, so they carry
# no ``mutates`` tag: outbound mail, sandboxed execution, turn-ephemeral scope changes.
# Annotating them read-only would lie to the client. This MIRRORS the repo's own reviewed
# classification — ``NO_DOMAIN_WRITE_TOOLS`` in tests/test_tool_domains.py — and
# ``test_side_effect_set_matches_the_repo_classification`` pins the two together so this
# can't drift. (Deliberately NOT a guess: ``web_search``/``fetch_page`` are reviewed
# READ_ONLY_TOOLS there and stay read-only here; ``telegram_send_message``/
# ``webhook_send_message`` need no entry — both are tagged ``mutates=("notification_log",)``
# and fall through to the write branch on their own.)
_SIDE_EFFECT_NO_DOMAIN: frozenset[str] = frozenset(
    {
        "email_send_message",
        "find_tools",
        "activate_skill",
        "run_script",
        "run_code",
        "run_screen_command",
        # An Automation run's own task list — turn-ephemeral, like the scope changes above.
        "add_run_tasks",
        "update_run_task",
    }
)

# Verbs that REMOVE or overwrite data — surfaced to the client as ``destructiveHint``.
_DESTRUCTIVE_PREFIXES: tuple[str, ...] = (
    "delete_",
    "remove_",
    "cancel_",
    "discard_",
    "rollback_",
    "reset_",
    "bulk_delete_",
)
# Destructive without a destructive verb: a merge folds one row into another, and
# correct_asset_value overwrites a stored value in place, keeping no before-image.
_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {"merge_transactions", "merge_payees", "correct_asset_value"}
)


def mcp_tool_descriptors() -> list[dict]:
    """The MCP ``tools/list`` payload: ``{name, description, inputSchema, annotations}`` per tool.

    The offered set is the WHOLE registry — a Bearer-authenticated caller is the operator, and
    there is no narrower surface to filter to. Names / descriptions / schemas come straight
    from the registry (single source of truth); ``annotations`` are derived presentation hints.
    """
    descriptors = [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["input_schema"],
            "annotations": _annotations_for(t["name"]),
        }
        for t in TOOL_SCHEMAS
    ]
    descriptors.sort(key=lambda d: d["name"])
    return descriptors


async def call_tool(
    name: str, arguments: dict, *, surface: str = "api_token"
) -> Tuple[str, Optional[dict]]:
    """Dispatch an MCP ``tools/call`` to the tool registry.

    Returns the ``(text, proposed_change)`` tuple from ``execute_tool``. A read tool returns
    ``proposed_change=None``; a ``propose_*`` tool returns a diff the server surfaces to the
    client (it wrote nothing — the client applies it by calling the matching ``apply_*`` tool).

    A MUTATING call (one whose tool declares ``mutates=(…)``) is wrapped in an explicit
    ``CheckpointContext`` so it is undoable — MCP has no ``assistant.run`` turn to wrap it —
    is attributed ``actor=ai / surface=mcp:<caller>`` for transaction provenance, and appends
    an audit row. A read costs none of that. An unknown tool name returns the registry's own
    ``{"error": "unknown tool: …"}`` sentinel.
    """
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    if not mutated_domains(name):
        # A read costs no checkpoint and no audit row, but it IS still attributed: the
        # per-tool-call telemetry ring resolves its origin from this scope, and without it
        # every inbound-MCP read would be recorded as "unknown" — blinding the instrument
        # on exactly the surface it most needs to see. Set+cleared around the call only;
        # for the ledger this is inert (a read performs no write to stamp).
        read_token = set_audit_scope("ai", f"mcp:{surface}", note=f"mcp tool:{name}")
        try:
            return await execute_tool(name, arguments)
        finally:
            clear_audit_scope(read_token)

    from backend.ai import checkpoint as ckpt

    ctx, token = ckpt.begin_turn(
        session_id=f"mcp:{surface}",
        anchor_history_len=0,
        message_preview=f"MCP call: {name}",
        kind="mcp",
    )
    set_audit_scope("ai", f"mcp:{surface}", note=f"mcp tool:{name}")
    text = ""
    proposed: Optional[dict] = None
    result_ok = True
    try:
        text, proposed = await execute_tool(name, arguments)
        result_ok = not _result_failed(text)
    except Exception as exc:  # noqa: BLE001 — surface the tool error, still finalize + audit
        # json.dumps, not an f-string: an exception message containing a quote/backslash
        # would otherwise emit malformed JSON, which the caller's isError parse reads as
        # "not an error" — reporting a crashed write as a clean success.
        text = _json.dumps({"ok": False, "error": str(exc)})
        result_ok = False
    finally:
        clear_audit_scope()
        entry = ckpt.end_turn(ctx, token)
    checkpoint_id = (entry or {}).get("id") if isinstance(entry, dict) else ctx.id

    _audit_write(
        surface=surface,
        tool=name,
        arguments=arguments,
        checkpoint_id=checkpoint_id,
        result_ok=result_ok,
    )
    return text, proposed


def _audit_base_amount(tool: str, arguments: dict) -> Optional[float]:
    """Best-effort base magnitude of a money-writing tool's row(s), for the audit log only
    (never re-trusted for math). None when not a money tool or no parseable amount."""
    try:
        from backend.core.fx import base_currency, to_base

        base = base_currency().upper()
        rows = arguments.get("transactions") or [arguments]
        total = 0.0
        seen = False
        for row in rows:
            amt = row.get("amount") if isinstance(row, dict) else None
            if amt is None:
                continue
            total += abs(to_base(abs(float(amt)), (row.get("currency") or base).upper(), base))
            seen = True
        return total if seen else None
    except Exception:  # noqa: BLE001 — audit is informational; never block the write
        return None


def _audit_write(
    *, surface: str, tool: str, arguments: dict, checkpoint_id: Optional[str], result_ok: bool
) -> None:
    """Append the audit row for a MUTATING MCP call (best-effort, never blocks)."""
    try:
        from backend.db.mcp_write_log_store import McpWriteLogStore

        McpWriteLogStore().append(
            {
                "surface": surface,
                "tool": tool,
                "domains": list(mutated_domains(tool)),
                "args_summary": _args_summary(tool, arguments),
                "base_amount": _audit_base_amount(tool, arguments),
                "checkpoint_id": checkpoint_id,
                "result_ok": result_ok,
            }
        )
    except Exception:  # noqa: BLE001 — auditing is best-effort; never fail the call on it
        pass


def _args_summary(tool: str, arguments: dict) -> str:
    """A short, no-secret one-line summary of a write call for the audit log."""
    rows = arguments.get("transactions")
    if isinstance(rows, list):
        return f"{tool}: {len(rows)} row(s)"
    desc = arguments.get("description") or arguments.get("category") or ""
    amt = arguments.get("amount")
    parts = [tool]
    if amt is not None:
        parts.append(f"amount={amt}")
    if desc:
        parts.append(str(desc)[:60])
    return " • ".join(parts)


def _result_failed(text: str) -> bool:
    """Whether a tool result string reports failure (``ok:false`` / an ``error`` key)."""
    try:
        parsed = _json.loads(text)
    except (ValueError, TypeError):
        return False
    if isinstance(parsed, dict):
        return parsed.get("ok") is False or bool(parsed.get("error"))
    return False


# ── MCP resources — a curated read-only data surface ─────────────────────────
#
# MCP **resources** are read-only addressable data (distinct from tools, which are
# actions). The catalog below is the single source of truth for which resources exist +
# how each is fetched; every entry routes through the SAME ``execute_tool`` read path the
# tools use. There is NO money/business math here — the adapter only names a read tool +
# shapes the resource envelope.
#
# A uri not in this catalog is refused (``McpResourceNotFound``). That is a CATALOG bound,
# not a permission gate: a resource is an address, and there is nothing to address outside
# the catalog. ``_assert_resource_tools_are_read_only`` keeps the catalog from drifting onto
# a tool that writes.

MCP_RESOURCE_MIME = "application/json"

# uri → (tool name, human name, description). Each tool must be a READ tool.
_RESOURCE_CATALOG: dict[str, tuple[str, str, str]] = {
    "networth://summary": (
        "get_net_worth",
        "Net worth summary",
        "Current total net worth and its breakdown, in the base currency.",
    ),
    "portfolio://holdings": (
        "get_portfolio",
        "Portfolio holdings",
        "All market holdings (equity / crypto / commodity / …) with quantity, value, and P&L.",
    ),
    "accounts://list": (
        "list_accounts",
        "Accounts",
        "All cash, liability, and receivable accounts with their computed balances.",
    ),
    "spending://summary": (
        "get_spending_summary",
        "Spending summary",
        "Spending analytics by category over the recent period, in the base currency.",
    ),
    "returns://summary": (
        "get_returns",
        "Portfolio returns",
        "Time- and money-weighted returns (TWR / MWR) over 1M/3M/YTD/1Y/ALL windows.",
    ),
    "profile://summary": (
        "get_profile",
        "Profile",
        "User profile: base currency, income, FIRE settings, and target allocation.",
    ),
}


def _assert_resource_tools_are_read_only() -> None:
    """Fail fast at import if the catalog ever points a resource at a writing tool.

    Checked against the tool's OWN ``mutates=(…)`` registry tag — the same source of truth
    ``_annotations_for`` uses, and one that is complete + test-enforced for every registered
    tool. (This replaced a check against a now-removed allowlist plus a name-prefix scan; the
    prefix scan only ever caught write-shaped NAMES, so a genuinely-writing tool called
    ``get_``/``list_`` would have passed it.) A name that is not registered at all is also a
    bug — the resource would 404 at read time.
    """
    registered = {t["name"] for t in TOOL_SCHEMAS}
    for uri, (tool, _name, _desc) in _RESOURCE_CATALOG.items():
        # Resolve a former name first: a catalog entry written before a rename still points at
        # a live tool, and it must be judged on THAT tool's mutates tag. Reporting it as
        # "unregistered" would both mask a genuine write and crash the server on a rename that
        # the alias exists to absorb.
        canonical = tool
        if canonical not in registered:
            raise RuntimeError(f"MCP resource {uri} maps to unregistered tool {tool!r}")
        domains = mutated_domains(canonical)
        if domains:
            raise RuntimeError(
                f"MCP resource {uri} maps to tool {tool!r}, which mutates {list(domains)}"
            )


_assert_resource_tools_are_read_only()


def mcp_resource_descriptors() -> list[dict]:
    """The MCP ``resources/list`` payload: ``{uri, name, description, mimeType}`` per resource."""
    descriptors = [
        {
            "uri": uri,
            "name": name,
            "description": desc,
            "mimeType": MCP_RESOURCE_MIME,
        }
        for uri, (_tool, name, desc) in _RESOURCE_CATALOG.items()
    ]
    descriptors.sort(key=lambda d: d["uri"])
    return descriptors


def is_resource(uri: str) -> bool:
    """Whether ``uri`` is a curated MCP resource (the re-asserted catalog check)."""
    return uri in _RESOURCE_CATALOG


async def read_resource(uri: str) -> list[dict]:
    """Fetch one curated resource → the MCP ``contents`` list (a single JSON text block).

    Validates ``uri`` against the curated catalog (unknown → ``McpResourceNotFound``, which
    the server maps to a JSON-RPC error — never a 500/leak), then dispatches through the SAME
    ``execute_tool`` path the tools use. The mapped tool is asserted non-mutating at import,
    so this can never write.
    """
    if not is_resource(uri):
        raise McpResourceNotFound(uri)
    tool, _name, _desc = _RESOURCE_CATALOG[uri]
    text, _proposed = await call_tool(tool, {})
    return [{"uri": uri, "mimeType": MCP_RESOURCE_MIME, "text": text}]


class McpResourceNotFound(Exception):
    """Raised when an MCP client reads a uri outside the curated resource catalog."""

    def __init__(self, uri: str):
        super().__init__(f"resource not found: {uri}")
        self.uri = uri
