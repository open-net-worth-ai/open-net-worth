"""What was SAID in past conversations (``session_messages`` text): keyword search, and reading
one conversation back.

The scan, the exclusions, the ranking and the LIMIT all run in SQL; only a window of text around
the match of each of the (at most ``limit``) hits — or one bounded page of a conversation — is
ever read into Python.

A candidate is a plain-string message or a ``text`` block of a user/assistant message — never a
tool call, a tool result or an attachment block, a skill invocation row, or a text block that
``core.chat_context_markers`` names as model context, nor a USER turn of a capture session
(``meta.origin == 'capture'``: that turn is the app's own prompt template) (turn snapshot, screen or mention
dump, harness note, attachment contents). Matching is Unicode case-insensitive over NFC-normalised
text on both sides: SQLite's own ``lower()`` is ASCII-only, so it gets two small registered
functions; Postgres uses ``normalize(…, NFC)`` and ``lower()`` (which needs a UTF-8 database).
Extracting the blocks from JSON is the one dialect-specific part (``json_each`` vs
``json_array_elements``) — the rest is one shared statement.

Search matches each term on its own, anywhere in a conversation's said text or its title: the
words someone recalls are rarely a phrase that was typed, and the title is often the only place
the topic is named.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Optional, Sequence

from sqlalchemy import Connection, bindparam, text
from sqlalchemy.engine import Engine

from backend.core.chat_context_markers import (
    CONTEXT_ANYWHERE,
    CONTEXT_OPENINGS,
    ONBOARDING_INIT,
)

_SQLITE_UNITS = """
    SELECT m.id AS mid, m.session_id AS sid, m.created_at AS at, m.role AS role, 0 AS ord,
           onw_nfc(json_extract(m.content, '$')) AS txt
      FROM session_messages m
     WHERE m.role IN ('user', 'assistant') AND json_type(m.content) = 'text'
       AND json_extract(m.extra, '$._onw_skill') IS NULL
    UNION ALL
    SELECT m.id, m.session_id, m.created_at, m.role, CAST(b.key AS INTEGER),
           onw_nfc(json_extract(b.value, '$.text'))
      FROM session_messages m,
           json_each(CASE WHEN json_type(m.content) = 'array' THEN m.content ELSE '[]' END) b
     WHERE m.role IN ('user', 'assistant') AND json_extract(m.extra, '$._onw_skill') IS NULL
       AND CASE WHEN b.type = 'object' THEN json_extract(b.value, '$.type') END = 'text'
"""

_POSTGRES_UNITS = """
    SELECT m.id AS mid, m.session_id AS sid, m.created_at AS at, m.role AS role, 0::bigint AS ord,
           normalize(m.content #>> '{}', NFC) AS txt
      FROM session_messages m
     WHERE m.role IN ('user', 'assistant') AND json_typeof(m.content) = 'string'
       AND m.extra -> '_onw_skill' IS NULL
    UNION ALL
    SELECT m.id, m.session_id, m.created_at, m.role, b.ord, normalize(b.value ->> 'text', NFC)
      FROM session_messages m
     CROSS JOIN LATERAL json_array_elements(
            CASE WHEN json_typeof(m.content) = 'array' THEN m.content ELSE '[]'::json END
          ) WITH ORDINALITY AS b(value, ord)
     WHERE m.role IN ('user', 'assistant') AND m.extra -> '_onw_skill' IS NULL
       AND CASE WHEN json_typeof(b.value) = 'object' THEN b.value ->> 'type' END = 'text'
"""

# Larger than any text position, so "not in this message" never wins the earliest-match pick.
_FAR = 2_000_000_000


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


def _said_conditions(dialect: str, params: dict[str, Any]) -> list[str]:
    """What makes a unit something the person or Lucius SAID — the exclusions every reader shares."""
    origin = "json_extract(s.meta, '$.origin')" if dialect == "sqlite" else "s.meta ->> 'origin'"
    pos = "instr" if dialect == "sqlite" else "strpos"
    params["init"] = ONBOARDING_INIT
    conds = [
        f"NOT (u.role = 'user' AND COALESCE({origin}, '') = 'capture')",
        "u.txt IS NOT NULL",
        "trim(u.txt) <> :init",
    ]
    for i, opening in enumerate(CONTEXT_OPENINGS):
        conds.append(f"substr(ltrim(u.txt), 1, {len(opening)}) <> :open{i}")
        params[f"open{i}"] = opening
    for i, marker in enumerate(CONTEXT_ANYWHERE):
        conds.append(f"{pos}(u.txt, :any{i}) = 0")
        params[f"any{i}"] = marker
    return conds


def _visible_chat_conditions(
    params: dict[str, Any], *, has_current: bool, hidden_prefixes: tuple[str, ...]
) -> list[str]:
    conds = ["s.id NOT IN :hidden_ids"]
    if has_current:
        conds.append("s.id <> :current")
    for i, prefix in enumerate(hidden_prefixes):
        conds.append(f"substr(s.id, 1, {len(prefix)}) <> :prefix{i}")
        params[f"prefix{i}"] = prefix
    return conds


def _search_statement(
    dialect: str, n_terms: int, *, has_current: bool, hidden_prefixes: tuple[str, ...]
) -> tuple[str, dict[str, Any]]:
    sqlite = dialect == "sqlite"
    lower, pos = ("onw_lower", "instr") if sqlite else ("lower", "strpos")
    title = "onw_lower(onw_nfc(s.title))" if sqlite else "lower(normalize(s.title, NFC))"
    params: dict[str, Any] = {"far": _FAR}
    said = _said_conditions(dialect, params)
    visible = _visible_chat_conditions(
        params, has_current=has_current, hidden_prefixes=hidden_prefixes
    )
    terms = range(n_terms)
    hit_cols = ", ".join(f"{pos}(low, :t{i}) AS p{i}" for i in terms)
    any_hit = " OR ".join(f"{pos}(low, :t{i}) > 0" for i in terms)
    in_msg = " + ".join(f"(CASE WHEN p{i} > 0 THEN 1 ELSE 0 END)" for i in terms)
    chat_cols = ", ".join(
        f"CASE WHEN {pos}({title}, :t{i}) > 0 OR MAX(COALESCE(sc.p{i}, 0)) > 0 "
        f"THEN 1 ELSE 0 END AS c{i}"
        for i in terms
    )
    flags = ", ".join(f"r.c{i}" for i in terms)
    score = " + ".join(f"c{i}" for i in terms)
    firsts = [f"CASE WHEN p{i} > 0 THEN p{i} ELSE :far END" for i in terms]
    if n_terms == 1:
        first = firsts[0]
    else:
        first = f"{'min' if sqlite else 'LEAST'}({', '.join(firsts)})"
    sql = f"""
        WITH units AS ({_SQLITE_UNITS if sqlite else _POSTGRES_UNITS}),
        said AS (
            SELECT u.sid, u.mid, u.at, u.txt, {lower}(u.txt) AS low
              FROM units u JOIN sessions s ON s.id = u.sid
             WHERE {" AND ".join(said + visible)}
        ),
        scored AS (
            SELECT sid, mid, at, txt, {hit_cols} FROM said WHERE {any_hit}
        ),
        chats AS (
            SELECT s.id AS sid, s.title AS title, s.updated_at AS updated_at, {chat_cols}
              FROM sessions s LEFT JOIN scored sc ON sc.sid = s.id
             WHERE {" AND ".join(visible)}
             GROUP BY s.id, s.title, s.updated_at
        ),
        ranked AS (SELECT c.*, {score} AS score FROM chats c),
        best AS (
            SELECT sid, mid, at, txt, {first} AS pos,
                   ROW_NUMBER() OVER (PARTITION BY sid ORDER BY {in_msg} DESC, mid DESC) AS rn
              FROM scored
        )
        SELECT r.sid, r.title, r.updated_at, {flags}, b.mid, b.at, b.pos,
               length(b.txt) AS total, substr(b.txt, 1, 300) AS head,
               substr(b.txt, CASE WHEN b.pos > :ctx THEN b.pos - :ctx ELSE 1 END, :span) AS excerpt
          FROM ranked r LEFT JOIN best b ON b.sid = r.sid AND b.rn = 1
         WHERE r.score > 0 AND r.score = (SELECT MAX(score) FROM ranked)
         ORDER BY (CASE WHEN b.mid IS NULL THEN 1 ELSE 0 END), b.mid DESC, r.updated_at DESC
         LIMIT :lim
    """
    return sql, params


def search_message_text(
    engine: Engine,
    terms: Sequence[str],
    *,
    limit: int,
    exclude_session_id: Optional[str] = None,
    hidden_ids: tuple[str, ...] = (),
    hidden_prefixes: tuple[str, ...] = (),
    context: int = 200,
) -> list[dict]:
    """The conversations holding the most of ``terms`` — each matched on its own, in said text or
    the title — one hit per conversation, message hits newest first, then title-only hits.

    Returns ``[{session_id, title, when, matched, excerpt, head, cut_before, cut_after}]``.
    ``matched`` lists the terms that conversation holds; every hit holds the same number.
    ``excerpt`` is up to ``context`` characters either side of the earliest match in the message
    holding the most terms (newest on a tie), ``head`` that block's first 300 characters (where an
    attachment header would sit), ``cut_*`` whether it continues past the excerpt. A title-only
    hit has ``excerpt`` and ``head`` None.
    """
    if isinstance(terms, str):
        terms = [terms]
    wanted = list(dict.fromkeys(fold(t) for t in terms if t and t.strip()))
    if not wanted:
        return []
    sql, params = _search_statement(
        engine.dialect.name,
        len(wanted),
        has_current=exclude_session_id is not None,
        hidden_prefixes=hidden_prefixes,
    )
    span = max(len(t) for t in wanted) + 2 * context
    params.update({f"t{i}": t for i, t in enumerate(wanted)})
    params.update(ctx=context, span=span, lim=limit, hidden_ids=list(hidden_ids) or [""])
    if exclude_session_id is not None:
        params["current"] = exclude_session_id
    statement = text(sql).bindparams(bindparam("hidden_ids", expanding=True))
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            _register_sqlite_functions(conn)
        rows = conn.execute(statement, params).mappings().fetchall()
    out = []
    for r in rows:
        hit: dict[str, Any] = {
            "session_id": r["sid"],
            "title": r["title"],
            "when": r["at"] or r["updated_at"],
            "matched": [t for i, t in enumerate(wanted) if r[f"c{i}"]],
            "excerpt": None,
            "head": None,
            "cut_before": False,
            "cut_after": False,
        }
        if r["mid"] is not None:
            start = max(1, r["pos"] - context)
            hit.update(
                excerpt=r["excerpt"],
                head=r["head"],
                cut_before=start > 1,
                cut_after=r["total"] > start + span - 1,
            )
        out.append(hit)
    return out


def read_said_messages(
    engine: Engine, session_id: str, *, offset: int, limit: int, max_chars: int
) -> dict:
    """One page of what was said in a conversation, oldest first: ``{total, messages}``.

    ``total`` counts the conversation's said messages; each message is ``{role, at, blocks}``
    with every said text block in order, each cut to ``max_chars + 1`` characters so the caller
    can tell a cut one from one that fits exactly.
    """
    dialect = engine.dialect.name
    params: dict[str, Any] = {"sid": session_id, "off": offset, "lim": limit, "cap": max_chars + 1}
    said = " AND ".join(["u.sid = :sid", *_said_conditions(dialect, params)])
    units = _SQLITE_UNITS if dialect == "sqlite" else _POSTGRES_UNITS
    base = f"""
        WITH units AS ({units}),
        said AS (
            SELECT u.mid, u.ord, u.role, u.at, u.txt
              FROM units u JOIN sessions s ON s.id = u.sid
             WHERE {said}
        )
    """
    page_sql = (
        base
        + """,
        picked AS (SELECT DISTINCT mid FROM said ORDER BY mid LIMIT :lim OFFSET :off)
        SELECT d.mid, d.role, d.at, substr(d.txt, 1, :cap) AS txt
          FROM said d JOIN picked p ON p.mid = d.mid
         ORDER BY d.mid, d.ord
    """
    )
    count_sql = base + "SELECT COUNT(DISTINCT mid) FROM said"
    count_params = {k: v for k, v in params.items() if k not in ("off", "lim", "cap")}
    with engine.connect() as conn:
        if dialect == "sqlite":
            _register_sqlite_functions(conn)
        total = conn.execute(text(count_sql), count_params).scalar_one()
        rows = conn.execute(text(page_sql), params).fetchall()
    messages: list[dict] = []
    for r in rows:
        if not messages or messages[-1]["mid"] != r.mid:
            messages.append({"mid": r.mid, "role": r.role, "at": r.at, "blocks": []})
        messages[-1]["blocks"].append(r.txt)
    return {"total": total, "messages": messages}
