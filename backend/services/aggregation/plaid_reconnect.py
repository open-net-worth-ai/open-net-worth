"""Lucius's half of reconnecting a linked Plaid bank — a sign-in link, never the sign-in.

A bank whose login expired (``ITEM_LOGIN_REQUIRED``, an expiring consent) is renewed with
Plaid **update mode**: the same Item and access token, so every account and every link to it
survives. ``start_reconnect`` mints that update-mode Hosted Link for ONE already-linked bank
and returns its URL for the user to open; the user signs in at their bank on Plaid's own page.
Nothing here takes or sees a bank credential, and nothing changes keys, banks, or account
links — linking a NEW bank stays a human action on the Plaid page (ADR
``2026-11-03-lucius-may-hand-the-user-a-plaid-reconnect-link``).

Completion needs no step of its own: once the user has signed in, the bank answers again, and
the next fetch clears its "needs reconnect" mark (``plaid.fetch_transactions_get``).
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.db.plaid_config_store import PlaidConfigStore, bank_key, bank_name
from backend.services.aggregation import plaid


def _label(item: dict) -> str:
    return bank_name(item) or "an unnamed bank"


def resolve_bank(store: PlaidConfigStore, ref: str) -> dict:
    """The linked bank ``ref`` names — its key, the user's name for it, or Plaid's (case-
    insensitive). With no ``ref``, the ONE bank that needs reconnecting, or the only bank.
    An unknown or ambiguous ref RAISES, naming every candidate."""
    items = store.items()
    if not items:
        raise ValidationError("No bank is linked through Plaid yet — link one on the Plaid page")
    wanted = (ref or "").strip()
    if not wanted:
        flagged = set(store.get().get("reconnect_needed") or [])
        pool = [i for i in items if bank_key(i.get("item_id") or "") in flagged] or items
        if len(pool) == 1:
            return pool[0]
        names = ", ".join(_label(i) for i in pool)
        raise ValidationError(f"Which bank? Name one of: {names}")
    folded = wanted.casefold()
    hits = [
        i
        for i in items
        if bank_key(i.get("item_id") or "") == wanted
        or folded in {bank_name(i).casefold(), str(i.get("institution_name") or "").casefold()}
    ]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(_label(i) for i in (hits or items))
    if hits:
        raise ValidationError(f"More than one linked bank is called {wanted!r}: {names}")
    raise ValidationError(f"No linked bank is called {wanted!r}. Linked banks: {names}")


def start_reconnect(ref: str = "") -> dict:
    """A Plaid update-mode sign-in link for ONE linked bank. Returns ``{bank, link,
    expiration, needed_reconnect}``. Raises ``ValidationError`` for an unknown bank or when
    Plaid refuses (bad keys, unreachable)."""
    store = PlaidConfigStore()
    item = resolve_bank(store, ref)
    key = bank_key(item.get("item_id") or "")
    flagged = key in set(store.get().get("reconnect_needed") or [])
    started = plaid.create_link_token(bank=key)
    return {
        "bank": _label(item),
        "link": started["link"],
        "expiration": started.get("expiration"),
        "needed_reconnect": flagged,
    }
