"""Which rows a bank-connector fetch must not miss — the default window and same-content refs.

Two ways a fetch silently lost transactions (found 2026-11-13, GoCardless e2e):

* **A first import.** An account linked but never imported into gets the whole history the bank
  serves (``Provider.history_days`` — 90 days for GoCardless), not two weeks of it.
* **The window.** The default was a fixed ``lookback_days`` back from TODAY. A pause longer than
  that — a holiday, an expired consent, a bank's daily limit, a paused Automation — left every
  row older than the window unfetched for good. The default now counts back from the NEWEST
  row already booked from this connector, so the next fetch after any pause picks up where the
  ledger stops. Derived from the ledger, never stored: a failed fetch, or one whose rows were
  never booked, cannot move it forward. Banks only serve ~90 days after consent (PSD2), so the
  catch-up stops there and says so.
* **Identical rows with no id.** A row with no provider id is keyed by its content
  (``providers.derived_ref``: account, date, amount, currency, description). Two genuinely
  separate identical rows — two £3.00 coffees at the same café on one day — got ONE key, and
  the second was refused as already booked. Each repeat within a window now gets an occurrence
  suffix (``#2``, ``#3``…), in the provider's own order, so the first keeps the key it always had.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from backend.core.user_time import user_today
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import Provider

#: How far back a catch-up may reach — what PSD2 banks serve once consent is older than a few
#: minutes. An explicit ``since`` is never capped.
MAX_CATCH_UP_DAYS = 90
_DERIVED_PREFIX = "derived:"


def default_window(
    provider: Provider, since: Optional[str], before: Optional[str]
) -> tuple[str, str, Optional[str]]:
    """``(since, before, warning)``. Without ``since``: ``lookback_days`` before the newest
    booked row from this provider (today when nothing is booked yet, never later than
    today), and never more than ``MAX_CATCH_UP_DAYS`` back — ``warning`` then says rows in
    the gap may be lost."""
    today = user_today()
    resolved_before = before or today.isoformat()
    if since:
        return since, resolved_before, None
    if _has_unimported_account(provider):
        # An account linked but never imported into gets everything the bank serves.
        first = today - timedelta(days=provider.history_days)
        return first.isoformat(), resolved_before, None
    newest = ledger_service.newest_booked_date(provider.key)
    anchor = min(newest, today) if newest else today
    start = anchor - timedelta(days=provider.lookback_days)
    floor = today - timedelta(days=MAX_CATCH_UP_DAYS)
    warning = None
    if start < floor:
        warning = (
            f"Nothing was booked from {provider.label} since {anchor.isoformat()} — longer than "
            f"the {MAX_CATCH_UP_DAYS} days banks serve. Rows before {floor.isoformat()} can't be "
            "fetched automatically; check the account against a statement."
        )
        start = floor
    return start.isoformat(), resolved_before, warning


def _has_unimported_account(provider: Provider) -> bool:
    """A provider that serves history (``history_days``) has a linked account nothing has been
    imported into yet — its first import must reach back that far, not one lookback."""
    if provider.history_days <= 0:
        return False
    from backend.services import accounts as accounts_service

    linked = {
        a["linked_account_id"]
        for a in accounts_service.linked_accounts_for_source(provider.key)
        if a.get("linked_account_id")
    }
    return bool(linked - ledger_service.accounts_booked_from(provider.key))


def number_repeated_refs(rows: list[dict]) -> None:
    """Give each repeat of a content-derived ref an occurrence suffix, in place. Pending and
    booked rows are numbered apart: a booked row's key must not depend on whether a pending
    twin is still listed, or it would change when that twin clears and be offered twice."""
    seen: dict[tuple[str, bool], int] = {}
    for row in rows:
        for entry in row.get("external_refs") or []:
            ref = entry.get("ref") if isinstance(entry, dict) else None
            if not isinstance(ref, str) or not ref.startswith(_DERIVED_PREFIX):
                continue
            key = (ref, bool(row.get("pending")))
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                entry["ref"] = f"{ref}#{seen[key]}"
