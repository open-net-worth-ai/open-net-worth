"""Keyword search over what was SAID in past conversations (``session_messages`` text).

The scan, the exclusions, the de-duplication and the LIMIT all run in SQL; only a window of text
around the first match of each of the (at most ``limit``) hits is ever read into Python.

A candidate is a plain-string message or a ``text`` block of a user/assistant message — never a
tool call, a tool result or an attachment block, a skill invocation row, or a text block that
``core.chat_context_markers`` names as model context, nor a USER turn of a capture session
(``meta.origin == 'capture'``: that turn is the app's own prompt template) (turn snapshot, screen or mention
dump, harness note, attachment contents). Matching is Unicode case-insensitive over NFC-normalised
text on both sides: SQLite's own ``lower()`` is ASCII-only, so it gets two small registered
functions; Postgres uses ``normalize(…, NFC)`` and ``lower()`` (which needs a UTF-8 database).
Extracting the blocks from JSON is the one dialect-specific part (``json_each`` vs
``json_array_elements``) — the rest is one shared statement.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Optional

from sqlalchemy import Connection, bindparam, text
from sqlalchemy.engine import Engine

from backend.core.chat_context_markers import (
    CONTEXT_ANYWHERE,
    CONTEXT_OPENINGS,
    ONBOARDING_INIT,
)

_SQLITE_UNITS = """
    SELECT m.id AS mid, m.session_id AS sid, m.created_at AS at, m.role AS role,
           onw_nfc(json_extract(m.content, '$')) AS txt
      FROM session_messages m
     WHERE m.role IN ('user', 'assistant') AND json_type(m.content) = 'text'
       AND json_extract(m.extra, '$._onw_skill') IS NULL
    UNION ALL
    SELECT m.id, m.session_id, m.created_at, m.role, onw_nfc(json_extract(b.value, '$.text'))
      FROM session_messages m,
           json_each(CASE WHEN json_type(m.content) = 'array' THEN m.content ELSE '[]' END) b
     WHERE m.role IN ('user', 'assistant') AND json_extract(m.extra, '$._onw_skill') IS NULL
       AND CASE WHEN b.type = 'object' THEN json_extract(b.value, '$.type') END = 'text'
"""

_POSTGRES_UNITS = """
    SELECT m.id AS mid, m.session_id AS sid, m.created_at AS at, m.role AS role,
           normalize(m.content #>> '{}', NFC) AS txt
      FROM session_messages m
     WHERE m.role IN ('user', 'assistant') AND json_typeof(m.content) = 'string'
       AND m.extra -> '_onw_skill' IS NULL
    UNION ALL
    SELECT m.id, m.session_id, m.created_at, m.role, normalize(b.value ->> 'text', NFC)
      FROM session_messages m
     CROSS JOIN LATERAL json_array_elements(
            CASE WHEN json_typeof(m.content) = 'array' THEN m.content ELSE '[]'::json END
          ) AS b(value)
     WHERE m.role IN ('user', 'assistant') AND m.extra -> '_onw_skill' IS NULL
       AND CASE WHEN json_typeof(b.value) = 'object' THEN b.value ->> 'type' END = 'text'
"""


def _register_sqlite_functions(conn: Connection) -> None:
    raw = conn.connection.driver_connection

    def nfc(value: Any) -> Optional[str]:
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else None

    def lower(value: Any) -> Optional[str]:
        return value.lower() if isinstance(value, str) else None

    raw.create_function("onw_nfc", 1, nfc, deterministic=True)
    raw.create_function("onw_lower", 1, lower, deterministic=True)


def fold(value: str) -> str:
    """The form both sides of a match are compared in: NFC, then lower case."""
    return unicodedata.normalize("NFC", value).lower()


def _statement(
    dialect: str, *, has_current: bool, hidden_prefixes: tuple[str, ...]
) -> tuple[str, dict[str, Any]]:
    lower, pos = ("onw_lower", "instr") if dialect == "sqlite" else ("lower", "strpos")
    units = _SQLITE_UNITS if dialect == "sqlite" else _POSTGRES_UNITS
    origin = "json_extract(s.meta, '$.origin')" if dialect == "sqlite" else "s.meta ->> 'origin'"
    params: dict[str, Any] = {"init": ONBOARDING_INIT}
    conds = [
        f"NOT (u.role = 'user' AND COALESCE({origin}, '') = 'capture')",
        "u.txt IS NOT NULL",
        "trim(u.txt) <> :init",
        f"{pos}({lower}(u.txt), :needle) > 0",
    ]
    for i, opening in enumerate(CONTEXT_OPENINGS):
        conds.append(f"substr(ltrim(u.txt), 1, {len(opening)}) <> :open{i}")
        params[f"open{i}"] = opening
    for i, marker in enumerate(CONTEXT_ANYWHERE):
        conds.append(f"{pos}(u.txt, :any{i}) = 0")
        params[f"any{i}"] = marker
    conds.append("u.sid NOT IN :hidden_ids")
    if has_current:
        conds.append("u.sid <> :current")
    for i, prefix in enumerate(hidden_prefixes):
        conds.append(f"substr(u.sid, 1, {len(prefix)}) <> :prefix{i}")
        params[f"prefix{i}"] = prefix
    sql = f"""
        WITH units AS ({units}),
        hits AS (
            SELECT u.sid, s.title, u.mid, u.at, u.txt, {pos}({lower}(u.txt), :needle) AS pos,
                   ROW_NUMBER() OVER (PARTITION BY u.sid ORDER BY u.mid DESC) AS rn
              FROM units u JOIN sessions s ON s.id = u.sid
             WHERE {" AND ".join(conds)}
        )
        SELECT h.sid, h.title, h.at, h.pos, length(h.txt) AS total, substr(h.txt, 1, 300) AS head,
               substr(h.txt, CASE WHEN h.pos > :ctx THEN h.pos - :ctx ELSE 1 END, :span) AS excerpt
          FROM hits h
         WHERE h.rn = 1
         ORDER BY h.mid DESC
         LIMIT :lim
    """
    return sql, params


def search_message_text(
    engine: Engine,
    needle: str,
    *,
    limit: int,
    exclude_session_id: Optional[str] = None,
    hidden_ids: tuple[str, ...] = (),
    hidden_prefixes: tuple[str, ...] = (),
    context: int = 200,
) -> list[dict]:
    """The newest message text containing ``needle``, one hit per conversation, newest first.

    Returns ``[{session_id, title, when, excerpt, head, cut_before, cut_after}]``; ``excerpt``
    holds up to ``context`` characters either side of the first match, ``head`` the first 300
    characters of the block (where an attachment header would sit), and ``cut_*`` say whether
    the message continues past the excerpt on that side.
    """
    sql, params = _statement(
        engine.dialect.name,
        has_current=exclude_session_id is not None,
        hidden_prefixes=hidden_prefixes,
    )
    wanted = fold(needle)
    span = len(wanted) + 2 * context
    params.update(
        needle=wanted,
        ctx=context,
        span=span,
        lim=limit,
        hidden_ids=list(hidden_ids) or [""],
    )
    if exclude_session_id is not None:
        params["current"] = exclude_session_id
    statement = text(sql).bindparams(bindparam("hidden_ids", expanding=True))
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            _register_sqlite_functions(conn)
        rows = conn.execute(statement, params).fetchall()
    out = []
    for r in rows:
        start = max(1, r.pos - context)
        out.append(
            {
                "session_id": r.sid,
                "title": r.title,
                "when": r.at,
                "excerpt": r.excerpt,
                "head": r.head,
                "cut_before": start > 1,
                "cut_after": r.total > start + span - 1,
            }
        )
    return out
