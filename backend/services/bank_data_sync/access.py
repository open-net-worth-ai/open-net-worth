"""Each bank's access under a connector — when it ends, and whether it already has (ADR
2026-11-13). A European bank grants an app access for a set time (often 90 or 180 days); a sync
that silently stops reading on that day is the failure the page's per-bank line exists to head
off, so the date is read from what the connector already stores, never asked of the bank.

``banks(p)`` → ``[{name, ends_at, ended, renew_soon, account_ids}]`` with ``account_ids`` the provider's
account ids. A provider that grants no expiring access (Plaid) has none to report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.services.aggregation.providers import Provider


def _parse(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Warn this far ahead: a bank emails nobody, and renewing takes a minute at the bank's own site.
RENEW_WITHIN = timedelta(days=14)


def _row(name: str, ends: object, ended: bool, account_ids: object) -> dict:
    at = _parse(ends)
    now = datetime.now(timezone.utc)
    return {
        "name": name,
        "ends_at": at.isoformat() if at else None,
        "ended": bool(ended) or (at is not None and at <= now),
        "renew_soon": at is not None and now < at <= now + RENEW_WITHIN,
        "account_ids": [str(a) for a in (account_ids or [])],
    }


def banks(p: Provider) -> list[dict]:
    try:
        stored = p.store().banks()
    except Exception:  # noqa: BLE001 — a store that can't be read reports no dates, never fails
        return []
    if p.key == "enable_banking":
        return [
            _row(
                b.get("aspsp") or "",
                b.get("valid_until"),
                b.get("access_ended"),
                b.get("account_ids"),
            )
            for b in stored
        ]
    if p.key == "gocardless":
        from backend.db.gocardless_config_store import ENDED

        return [
            _row(
                b.get("institution_name") or "",
                b.get("consent_expires_at"),
                b.get("status") in ENDED,
                b.get("account_ids"),
            )
            for b in stored
        ]
    return []


def by_account(p: Provider) -> dict[str, dict]:
    """Provider account id → its bank's access row."""
    return {acc: b for b in banks(p) for acc in b["account_ids"]}
