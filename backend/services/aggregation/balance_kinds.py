"""A bank's balance types → the book's own terms (ADR 2026-11-13, Amendment 4).

PSD2 banks (GoCardless, Enable Banking) report several balance TYPES; the app speaks of an
account's CLEARED balance (settled only) and its working BALANCE (settled + pending). So:

  * booked — interim (up to now) or closing (end of the last business day) → ``cleared_balance``
  * expected — booked plus what's pending                                     → ``working_balance``
  * available — what can be spent now, overdraft or credit line included       → ``available_to_spend``

``available`` is never called a balance: it can hold an overdraft that isn't the user's money.
A type the bank didn't send is None — one is never filled in from another. The names come in both
the ISO 20022 codes (Enable Banking: ``ITBD``) and GoCardless's words (``interimBooked``)."""

from __future__ import annotations

from typing import Optional

# Most current first: interim booked is today's settled figure, closing booked yesterday's.
_CLEARED = ("interimbooked", "itbd", "closingbooked", "clbd")
_WORKING = ("expected", "xpcd")
_AVAILABLE = ("interimavailable", "itav", "closingavailable", "clav", "forwardavailable", "fwav")


def _first(by_type: dict[str, float], names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        if name in by_type:
            return by_type[name]
    return None


def split(by_type: dict[str, float]) -> dict:
    """``{cleared_balance, working_balance, available_to_spend}`` from ``{type: amount}``
    (type matched case-insensitively)."""
    lowered = {str(k).lower(): v for k, v in by_type.items()}
    return {
        "cleared_balance": _first(lowered, _CLEARED),
        "working_balance": _first(lowered, _WORKING),
        "available_to_spend": _first(lowered, _AVAILABLE),
    }


def book_terms(account: dict) -> dict:
    """An account as a listing shows it (Lucius's ``<bank>_list_accounts``, the connector page):
    ``cleared_balance``, ``balance`` (the WORKING balance — the book's own meaning of the word)
    and ``available_to_spend``. A provider that reports one undivided balance keeps it as
    ``balance``. The internal ``working_balance`` key isn't shown."""
    out = {k: v for k, v in account.items() if k != "working_balance"}
    if "working_balance" in account or "cleared_balance" in account:
        out["balance"] = account.get("working_balance")
    out.setdefault("cleared_balance", None)
    out.setdefault("available_to_spend", None)
    return out
