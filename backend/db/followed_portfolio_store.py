"""
FollowedPortfolioStore — a public portfolio the user follows (a 13F fund or a House member).

One row per follow: which portfolio, and the last snapshots fetched for it. The snapshot is a
cache of a public filing, but a refresh that fails must leave the previous one readable, so it
is stored, not recomputed. Information only — nothing here knows about the user's accounts.

Row schema:
    id               : "fol_<12hex>"
    kind             : "fund_13f" | "house_member"
    source_ref       : fund → the SEC CIK as digits; member → "last|first" (lower case)
    name, subtitle   : display strings from the source
    current, previous: snapshot | None
        {filing_id, as_of, filed_at, total_value, unread_reports,
         positions: [{key, symbol | None, name, value, qty}]}
    activity         : member only — parsed trades, newest first
    reports          : member only — {doc_id: {year, filed_at, readable, trades}} already fetched
    cusip_map        : fund only — {cusip: symbol or ""} already looked up
    last_checked_at, last_error, created_at, updated_at

``services.followed_portfolios.service.apply_followed_portfolio_change`` is the one writer.
"""

from __future__ import annotations

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import followed_portfolios

KINDS = ("fund_13f", "house_member")
MAX_FOLLOWS = 20


class FollowedPortfolioStore(BookPayloadTableStore):
    def __init__(self) -> None:
        super().__init__(followed_portfolios, id_field="id")

    def find_by_source(self, kind: str, source_ref: str, *, conn=None) -> dict | None:
        for row in self.all(conn=conn):
            if row.get("kind") == kind and row.get("source_ref") == source_ref:
                return row
        return None
