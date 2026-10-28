"""run_sql_query / describe_db_schema — direct, read-only reads of the app's own database.

Spec: ``docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md``.
ADR: ``docs/adr/2026-08-23-lucius-reads-the-database-directly.md``.

## Why these exist

The ~200-tool surface is broad, but each tool is a FIXED projection over a FIXED filter. There
is no tool that answers "which payees did I pay in both March and April", or "how many
transactions carry a tag no category uses", or "what's the distribution of `source` on rows
imported last month". Each is one line of SQL and zero tools, and the failure mode without
them is not an empty result — it is Lucius assembling a partial answer from three bounded
reads and stating it confidently.

## Why there are TWO of them

``describe_db_schema`` is not a convenience. Most stores in this app are a single ``payload``
JSON column (``payload_table()`` / ``book_payload_table()``), so the obvious
``SELECT quantity FROM positions`` errors and the real query is
``json_extract(payload, '$.quantity')``. Without a live schema map, every query the model
writes is a guess. Introspecting beats putting a static schema in the tool description: it
cannot go stale, and it costs nothing on turns that never need it.

## Safety

Enforcement is NOT in this module and NOT in the model's instructions. ``db/sql_console.py``
runs every statement through a connection the database itself has restricted (SQLite: an
allowlist authorizer, applied post-parse, so a credential table reached via a CTE inside a
UNION arm of a view is still caught; Postgres: a read-only transaction plus a fail-closed
identifier scan). ``services/sql_query.py`` owns the kill switch, the caveats and the audit
log. Read those two before changing anything here.

## Classification

Both are ``READ_ONLY_TOOLS``. ``describe_db_schema`` touches no store at all.
``run_sql_query`` appends one ``sql_query_log`` row per call, which is TELEMETRY — explicitly
out of scope for ``mutates=`` per ``tests/test_tool_domains.py``'s own docstring, and no
different in kind from the ``tool_call_log`` row the dispatcher already writes on behalf of
every other read tool. It is deliberately NOT ``NO_DOMAIN_WRITE_TOOLS``: that set feeds
``EXTERNAL_EFFECT_TOOLS`` / the discovery gate's ``_GATE_NEVER_AUTO_EXECUTE``, whose members
are outbound SMTP and subprocess execution. A bounded, read-only, logged SELECT does not belong
there, and putting it there would stop the discovery gate auto-executing a pure read for no
safety gain.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import sql_query as sql_service


def _origin_and_session() -> tuple[str, Optional[str]]:
    """Best-effort attribution for the audit row. Never raises — a missing turn context must
    not stop a query the policy already allowed."""
    try:
        from backend.ai.tools.call_log import _resolve_origin, _resolve_session_id

        return _resolve_origin(), _resolve_session_id()
    except Exception:  # noqa: BLE001 — attribution is nice to have, never load-bearing
        return "unknown", None


class RunSqlQueryInput(ToolInput):
    sql: str = Field(description="ONE read-only SELECT (or WITH … SELECT) statement.")
    limit: Optional[int] = Field(
        default=None,
        description="Max rows to return (default 200, hard max 1000). Aggregate instead of raising this.",
    )


@tool(
    "run_sql_query",
    description=(
        "Run ONE read-only SQL SELECT against this app's own database, for questions the other "
        "tools cannot express (joins, distributions, 'which X appears in both A and B'). "
        "Tables holding credentials are not readable. For earlier conversations use "
        "search_past_conversations and read_past_conversation instead.\n"
        "CALL describe_db_schema FIRST — most tables keep their real fields inside a `payload` "
        "JSON column, so `SELECT quantity FROM positions` fails and "
        "`json_extract(payload,'$.quantity')` is what you want.\n"
        "Capped at 200 rows by default; `showing`/`truncated` are honest, and when truncated "
        "the true total is UNKNOWN — re-run with COUNT(*) rather than guessing.\n"
        "NOT a source for figures you report: read the `caveats` on every result and confirm "
        "any number with get_transactions or net_worth first."
    ),
    input_schema=schema_of(RunSqlQueryInput, tool_name="run_sql_query"),
)
async def run_sql_query(input_data: dict):
    statement = (input_data.get("sql") or "").strip()
    origin, session_id = _origin_and_session()
    try:
        result = await asyncio.to_thread(
            sql_service.run_query,
            statement,
            input_data.get("limit"),
            origin=origin,
            session_id=session_id,
        )
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}, ensure_ascii=False), None
    return json.dumps(result, ensure_ascii=False, default=str), None


class DescribeDbSchemaInput(ToolInput):
    table: Optional[str] = Field(
        default=None,
        description=(
            "One table's name for its columns, indexes and payload keys. Omit to list every "
            "readable table with its row count."
        ),
    )


@tool(
    "describe_db_schema",
    description=(
        "The database's own shape, for writing run_sql_query statements. No argument: every "
        "readable table with its row count. With `table`: that table's columns and indexes, "
        "plus the top-level keys observed inside its `payload` JSON column — the part you "
        "cannot guess, and where most stores actually keep their fields. Column notes flag the "
        "traps (money is exact decimal TEXT, never CAST it to a float; `transactions` retains "
        "soft-deleted rows). This describes STORAGE; for what a WRITE accepts, use `describe`."
    ),
    input_schema=schema_of(DescribeDbSchemaInput, tool_name="describe_db_schema"),
)
async def describe_db_schema(input_data: dict):
    table = (input_data.get("table") or "").strip()
    try:
        if table:
            result = await asyncio.to_thread(sql_service.schema_for_table, table)
        else:
            result = await asyncio.to_thread(sql_service.schema_overview)
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}, ensure_ascii=False), None
    return json.dumps(result, ensure_ascii=False, default=str), None
