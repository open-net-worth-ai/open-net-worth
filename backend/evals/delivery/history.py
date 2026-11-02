"""Rebuild what the user was actually pushed, from a QA clone of their book — read-only.

Three traces carry it, each written where the push happens today:

* a pushed analysis stamps the story's ``push_log`` (``services.alert_feed`` — last 5 per story,
  so a busy story is under-counted: the replay is a LOWER bound on the old volume);
* a fired price alert or crash-watch IS a ``price_alert`` story (a re-fire replaces it, so only
  the latest fire per alert survives — another lower bound);
* an explained watch alert stamps the story's ``explained_log``.

Each trace becomes a candidate shaped exactly like the live senders' (``monitoring.delivery``),
with the timeline entry written at that moment for its sources. Nothing here writes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ENTRY_MATCH = timedelta(minutes=5)


def _ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _entries_by_story(con: sqlite3.Connection, since: datetime) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    rows = con.execute(
        "select signal_id, date, title, summary, sources from signal_timeline_entries "
        "where date >= ? order by date",
        (since.isoformat()[:10],),
    )
    for sid, date, title, summary, sources in rows:
        out.setdefault(sid, []).append(
            {"at": _ts(date), "title": title, "summary": summary, "sources": json.loads(sources)}
        )
    return out


def _nearest(entries: list[dict], at: datetime) -> dict | None:
    timed = [e for e in entries if e["at"] and abs(e["at"] - at) <= _ENTRY_MATCH]
    return min(timed, key=lambda e: abs(e["at"] - at)) if timed else None


def _candidate(
    story: dict,
    *,
    cid: str,
    at: datetime,
    source: str,
    headline: str,
    why: str,
    sources: list[dict],
) -> dict:
    user_set = source in ("price_alert", "crash_watch")
    return {
        "id": cid,
        "source": source,
        "route_key": "alerts"
        if user_set
        else ("watch_alerts" if source == "watch_alert" else "news"),
        "story_id": story["id"],
        "subject": story.get("type") or "",
        "story_type": story.get("type") or "",
        "ticker": story.get("ticker") or "",
        "label": story.get("label") or "",
        "headline": headline,
        "why": why,
        "impact": story.get("portfolio_impact") or "",
        "actions": [],
        "sources": sources[:4],
        "materiality": "",
        "user_set": user_set,
        "legacy_message": f"{story.get('label') or story.get('ticker')}: {headline}",
        "created_at": at.isoformat(),
    }


def _analysis_pushes(story: dict, entries: list[dict], since: datetime) -> list[dict]:
    out = []
    for push in story.get("push_log") or []:
        at = _ts(push.get("at"))
        if not at or at < since:
            continue
        entry = _nearest(entries, at)
        out.append(
            _candidate(
                story,
                cid=f"an-{push.get('id') or at.timestamp()}",
                at=at,
                source="analysis",
                headline=(entry or {}).get("title") or push.get("title") or "",
                why=push.get("summary") or (entry or {}).get("summary") or "",
                sources=(entry or {}).get("sources") or [],
            )
        )
    return out


def _alert_fire(story: dict, since: datetime) -> list[dict]:
    at = _ts(story.get("created_at"))
    if story.get("type") != "price_alert" or not at or at < since:
        return []
    source = "crash_watch" if story["id"].startswith("crash-watch-") else "price_alert"
    return [
        _candidate(
            story,
            cid=f"pa-{story['id']}",
            at=at,
            source=source,
            headline=story.get("label") or "",
            why=story.get("current_summary") or "",
            sources=[],
        )
    ]


def _watch_alerts(story: dict, entries: list[dict], since: datetime) -> list[dict]:
    out = []
    for raw in story.get("explained_log") or []:
        at = _ts(raw)
        if not at or at < since:
            continue
        entry = _nearest(entries, at) or {}
        out.append(
            _candidate(
                story,
                cid=f"wa-{story['id']}-{int(at.timestamp())}",
                at=at,
                source="watch_alert",
                headline=entry.get("title") or "",
                why=entry.get("summary") or "",
                sources=entry.get("sources") or [],
            )
        )
    return out


def pushed_stream(db: Path, *, days: int, now: datetime | None = None) -> list[dict]:
    """Every push the book can still prove, oldest first, as candidates."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    con = _connect(db)
    try:
        entries = _entries_by_story(con, since)
        stream: list[dict] = []
        for (payload,) in con.execute("select payload from intel_stories"):
            story = json.loads(payload)
            mine = entries.get(story["id"], [])
            stream += _analysis_pushes(story, mine, since)
            stream += _alert_fire(story, since)
            stream += _watch_alerts(story, mine, since)
    finally:
        con.close()
    return sorted(stream, key=lambda c: c["created_at"])
