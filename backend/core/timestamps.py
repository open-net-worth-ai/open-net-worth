"""Sortable-timestamp normalization for populations that mix naive-local and aware-UTC
ISO strings (e.g. rows `intel_agent.py` stamped naive local before 2026-11-01, beside the
Analyst's tz-aware UTC ones — see I6). Comparing the raw strings lexicographically
misorders them: a naive "14:00" local can sort after an aware "10:00+00:00" that actually
happened later.

`sort_key()` gives every caller a genuinely comparable, always-aware `datetime` — the
one normalization rule (naive → treated as local time), applied once, instead of each
sort site re-deriving it ad hoc. `to_utc_iso()` applies the SAME rule to serialise a
legacy naive value unambiguously: a browser reads a zoneless ISO string as ITS OWN local
time, so a server in one zone and a browser in another disagree by the offset.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Sentinel for a missing/empty/unparseable timestamp — sorts as the OLDEST possible
# instant, so a blank stays at the bottom of a recency-descending list.
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def sort_key(ts: str | None) -> datetime:
    """Parse an ISO timestamp into an aware `datetime` safe to compare against any other
    result of this function. A naive value (no tzinfo) is treated as LOCAL time and
    converted via `.astimezone()` — mirrors the normalization `intel_agent._archive_stale_stories`
    established for this exact mixed-population problem. A missing/empty/unparseable value
    returns `_OLDEST` so it sorts last in a recency-descending list."""
    if not ts:
        return _OLDEST
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return _OLDEST
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def to_utc_iso(ts: str | None) -> str | None:
    """`ts` as an aware-UTC ISO string, by `sort_key`'s rule (naive → server-local time).
    A missing/empty/unparseable value comes back unchanged — never an invented instant."""
    if not ts:
        return ts
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return ts
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc).isoformat()
