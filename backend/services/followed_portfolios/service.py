"""Followed portfolios — the views every surface reads and the one writer. Information only.

``apply_followed_portfolio_change`` is the ONE writer (REST, Lucius tools and the daily refresh
job). A filing is fetched BEFORE the lock is taken — a slow source must not hold the table —
and the row is re-read inside it, so a concurrent unfollow or settings change is never undone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.core.errors import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    OpenNetWorthError,
    ValidationError,
)
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.followed_portfolio_store import KINDS, MAX_FOLLOWS, FollowedPortfolioStore
from backend.db.tables import followed_portfolios
from backend.services.followed_portfolios import edgar_13f, house_ptr, signals
from backend.services.followed_portfolios.model import DEFAULT_TOP_N, build_model, status_of

logger = logging.getLogger(__name__)

_SOURCES = {"fund_13f": edgar_13f, "house_member": house_ptr}
_NORMALIZE = {"fund_13f": edgar_13f.normalize_cik, "house_member": house_ptr.normalize_ref}
_SNAPSHOT_FIELDS = ("current", "previous", "activity", "reports", "cusip_map")
_ACTIVITY_FIELDS = (
    "date",
    "filed_at",
    "symbol",
    "name",
    "side",
    "amount_low",
    "amount_high",
    "asset_type",
    "counted",
)
_SOURCE_FAILED = "The source could not be read."
_UNREADABLE = "The newest filing could not be read. Showing the last one that could."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source(kind: str):
    if kind not in KINDS:
        raise ValidationError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    return _SOURCES[kind]


def view(row: dict, top_n: int = DEFAULT_TOP_N) -> dict:
    """The follow as every surface reads it — the model derived, the caches left out."""
    model = build_model(row, user_today(), top_n)
    return {
        "id": row["id"],
        "kind": row["kind"],
        "source_ref": row["source_ref"],
        "name": row["name"],
        "subtitle": row.get("subtitle") or "",
        "status": status_of(row, model),
        "error": row.get("last_error"),
        "last_checked_at": row.get("last_checked_at"),
        "model": model,
        "activity": [{k: t.get(k) for k in _ACTIVITY_FIELDS} for t in row.get("activity") or []],
    }


def list_follows(top_n: int = DEFAULT_TOP_N) -> dict:
    return {"follows": [view(r, top_n) for r in FollowedPortfolioStore().all()]}


def _row(follow_id: str, *, conn=None) -> dict:
    row = FollowedPortfolioStore().find_by_id(str(follow_id or "").strip(), conn=conn)
    if row is None:
        raise NotFoundError("That followed portfolio no longer exists.")
    return row


def resolve(ref: str) -> dict:
    """A follow by id or by its exact name (case-insensitive) — for the Lucius tools."""
    wanted = str(ref or "").strip()
    rows = FollowedPortfolioStore().all()
    hits = [r for r in rows if r["id"] == wanted] or [
        r for r in rows if r["name"].lower() == wanted.lower()
    ]
    if len(hits) != 1:
        names = ", ".join(sorted(r["name"] for r in rows)) or "none yet"
        raise NotFoundError(f"No followed portfolio '{wanted}'. Followed: {names}.")
    return hits[0]


def directory(query: str = "") -> dict:
    """Featured portfolios for an empty query, else both sources searched by name. A source
    that is down contributes nothing rather than failing the other."""
    query = " ".join(str(query or "").split())[:80]
    featured = edgar_13f.featured() + house_ptr.featured()
    if len(query) < 2:
        results = [{**r, "featured": True} for r in featured]
    else:
        by_key = {
            (r["kind"], r["source_ref"]): {**r, "featured": True}
            for r in featured
            if query.lower() in f"{r['name']} {r['subtitle']}".lower()
        }
        for source in (edgar_13f, house_ptr):
            try:
                found = source.search(query)
            except ExternalServiceError:
                continue
            for r in found:
                by_key.setdefault((r["kind"], r["source_ref"]), {**r, "featured": False})
        results = list(by_key.values())
    followed = {(r["kind"], r["source_ref"]) for r in FollowedPortfolioStore().all()}
    return {
        "results": [{**r, "followed": (r["kind"], r["source_ref"]) in followed} for r in results]
    }


def _featured_name(kind: str, source_ref: str) -> dict:
    for r in edgar_13f.featured() + house_ptr.featured():
        if r["kind"] == kind and r["source_ref"] == source_ref:
            return {"name": r["name"], "subtitle": r["subtitle"]}
    return {}


def _has_positions(snapshot: dict | None) -> bool:
    return any(p.get("symbol") for p in (snapshot or {}).get("positions") or [])


def _follow(change: dict) -> dict:
    kind = str(change.get("kind") or "")
    source = _source(kind)
    source_ref = _NORMALIZE[kind](change.get("source_ref"))
    store = FollowedPortfolioStore()
    if store.find_by_source(kind, source_ref):
        raise ConflictError("You already follow that portfolio.")
    if len(store.all()) >= MAX_FOLLOWS:
        raise ConflictError(f"You can follow up to {MAX_FOLLOWS} portfolios. Unfollow one first.")
    fetched = source.fetch(source_ref)
    if not _has_positions(fetched.get("current")):
        raise ValidationError("That portfolio has no holdings that could be read.")
    now = _now()
    row = {
        "id": new_id("fol"),
        "kind": kind,
        "source_ref": source_ref,
        "name": fetched.get("name") or source_ref,
        "subtitle": fetched.get("subtitle") or ("13F filer" if kind == "fund_13f" else "House"),
        **_featured_name(kind, source_ref),
        **{k: fetched.get(k) for k in _SNAPSHOT_FIELDS if k in fetched},
        "last_checked_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    with locked_unit_of_work(followed_portfolios) as conn:
        if store.find_by_source(kind, source_ref, conn=conn):
            raise ConflictError("You already follow that portfolio.")
        return store.insert(row, conn=conn)


def _unfollow(change: dict) -> dict:
    store = FollowedPortfolioStore()
    with locked_unit_of_work(followed_portfolios) as conn:
        row = _row(change.get("id"), conn=conn)
        store.delete(row["id"], conn=conn)
        return row


def _refresh(change: dict) -> tuple[dict, bool]:
    """Returns (row, new_filing). A source failure is recorded on the row, not raised: the
    previous model stays readable and the screen shows why it is not newer."""
    store = FollowedPortfolioStore()
    before = _row(change.get("id"))
    patch: dict = {"last_checked_at": _now(), "last_error": None}
    new_filing = False
    try:
        fetched = _source(before["kind"]).fetch(before["source_ref"], before)
        if fetched.get("unchanged"):
            pass
        elif not _has_positions(fetched.get("current")):
            patch["last_error"] = _UNREADABLE
        else:
            patch.update({k: fetched[k] for k in _SNAPSHOT_FIELDS if k in fetched})
            old_id = (before.get("current") or {}).get("filing_id")
            new_filing = fetched["current"].get("filing_id") != old_id
    except OpenNetWorthError as exc:
        patch["last_error"] = exc.message or _SOURCE_FAILED
    except Exception:  # a source that changed shape must not take the follow down with it
        logger.exception("followed portfolios: refresh of %s failed", before["id"])
        patch["last_error"] = _SOURCE_FAILED
    with locked_unit_of_work(followed_portfolios) as conn:
        row = _row(before["id"], conn=conn)
        patch["updated_at"] = _now()
        return store.update_by_id(row["id"], patch, conn=conn), new_filing


def apply_followed_portfolio_change(change: dict) -> dict:
    """The one writer. ``op``:

    * ``follow`` — ``kind``, ``source_ref``: fetch the newest filing and store the follow.
    * ``refresh`` — ``id``: re-read the source; a new filing raises one Intel story.
    * ``unfollow`` — ``id``.

    Returns ``{"follow": view, "new_filing": bool}``."""
    op = change.get("op")
    new_filing = False
    if op == "follow":
        row = _follow(change)
    elif op == "refresh":
        row, new_filing = _refresh(change)
        if new_filing:
            signals.emit_new_filing_story(row, build_model(row, user_today()))
    elif op == "unfollow":
        row = _unfollow(change)
    else:
        raise ValidationError(f"Unknown followed-portfolio change '{op}'.")
    return {"follow": view(row), "new_filing": new_filing}


def refresh_all() -> dict:
    """The daily job: refresh every follow, one at a time. One failing never stops the rest."""
    checked = new = 0
    for row in FollowedPortfolioStore().all():
        try:
            result = apply_followed_portfolio_change({"op": "refresh", "id": row["id"]})
        except Exception:
            logger.exception("followed portfolios: refresh failed for %s", row.get("id"))
            continue
        checked += 1
        new += 1 if result["new_filing"] else 0
    return {"checked": checked, "new_filings": new}
