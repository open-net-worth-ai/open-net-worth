"""An account the bank gave a new id, followed rather than lost (ADR 2026-11-13, Amendment 17).

When a bank re-consents, some give every account a new id (Plaid: Chase, PNC, US Bank re-issue
``account_id``). The run read the old id, the bank no longer listed it, and the account stopped
syncing until you unlinked it and linked the new one ("the bank no longer lists this account").
Plaid also sends ``persistent_account_id``, which stays the same across that change (Sure
follows it the same way).

Every listing that carries one (``persistent_id`` on the common account shape) is remembered
per provider account id (``budget`` state ``persistent``). When the linked id is no longer
listed and EXACTLY ONE listed account carries the same persistent id — not the old id, and not
already linked to another book account — the book account's link moves to it (its
``external_ids`` entry keeps its name and mask) and it is read under the new id in the same run.
That read is a re-link read (``relink.is_relinked``), so the history already booked is matched,
not booked again. Anything else is still "the bank no longer lists this account"."""

from __future__ import annotations

import logging

from backend.services.bank_data_sync import budget

logger = logging.getLogger(__name__)


def remember(provider: str, accounts: list[dict]) -> None:
    pairs = {
        str(a.get("provider_account_id")): str(a.get("persistent_id"))
        for a in accounts
        if a.get("provider_account_id") and a.get("persistent_id")
    }
    if pairs:
        budget.mutate(
            provider, lambda e: e.update(persistent={**(e.get("persistent") or {}), **pairs})
        )


def successor(provider: str, old_id: str, accounts: list[dict]) -> str | None:
    """The one listed account that is ``old_id`` under a new id, or None."""
    from backend.services import accounts as accounts_service

    persistent = (budget.state(provider).get("persistent") or {}).get(old_id)
    if not persistent:
        return None
    linked = {
        str(a.get("provider_account_id"))
        for a in accounts_service.linked_accounts_for_source(provider)
    }
    hits = {
        str(a["provider_account_id"])
        for a in accounts
        if a.get("persistent_id") == persistent
        and a.get("provider_account_id")
        and str(a["provider_account_id"]) not in linked | {old_id}
    }
    return hits.pop() if len(hits) == 1 else None


def move_link(provider: str, app_id: str, old_id: str, new_id: str) -> bool:
    """Point the book account's link at ``new_id``. False if it couldn't be saved."""
    from backend.services import accounts as accounts_service

    row = next((a for a in accounts_service.all_account_rows() if a.get("id") == app_id), None)
    if row is None:
        return False
    ids = [
        {**e, "ref": new_id}
        if isinstance(e, dict) and e.get("source") == provider and e.get("ref") == old_id
        else e
        for e in row.get("external_ids") or []
    ]
    try:
        accounts_service.update_account(app_id, {"external_ids": ids})
    except Exception:  # noqa: BLE001 — still "no longer listed"; the user can link it by hand
        logger.exception("bank_data_sync: %s could not follow %s to its new id", provider, app_id)
        return False
    logger.info("bank_data_sync: %s account %s followed to its new bank id", provider, app_id)
    return True
