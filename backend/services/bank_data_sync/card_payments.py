"""A credit-card payment is ONE transfer, not income on the card plus spending in checking
(ADR 2026-11-13, Amendment 13).

A bank reports the same movement twice — an outflow from the paying account and an inflow on
the card — and booked as read it inflates income AND spending by the payment (net worth, which
only sums balances, stays right). The pairing rule, applied to every booking of a run:

* the card side is an ``income`` into an account of type ``credit``; the paying side is an
  ``expense`` out of a ``cash`` account;
* same currency and the same amount to the cent, dates at most
  ``matching.TRANSFER_PAIR_MAX_GAP_DAYS`` apart (the ledger's own transfer-pair window);
* the wording of at least one side says it is a payment (``_PAYMENT``) and the card side doesn't
  say it is a refund (``_REFUND``) — an exact-amount coincidence between a merchant refund and an
  unrelated bill is not a payment;
* the match is UNIQUE both ways: a draft with two candidate counterparts, or a counterpart two
  drafts want, pairs with nothing — a wrong pairing is silent, an unpaired row is visible;
* neither leg was deleted by the user before (``rejected_identities``): their delete stands.

The counterpart is another draft of the same run (the two become one transfer draft) or a LIVE
row already in the book — from another connector, an earlier run or typed by hand — which is
promoted to the transfer by the ledger's own pair merge. So the answer doesn't depend on which
side arrived first. A pending or still-unsettled leg is never paired; it pairs when it settles
(its booking is a normal row until then). A card payment with NO counterpart in the book (the
paying account isn't synced) stays what the bank says, income on the card: a transfer needs both
legs, and the other one, when it arrives, pairs with it."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from backend.services import ledger as ledger_service
from backend.services import matching, rejected_identities
from backend.services.bank_data_sync import pending

logger = logging.getLogger(__name__)

_GAP = timedelta(days=matching.TRANSFER_PAIR_MAX_GAP_DAYS)
_TOL = 0.005
_PAYMENT = re.compile(
    r"\b(payments?|pymts?|pmts?|auto-?pay|bill ?pay|thank you|card services|credit card|crd)\b",
    re.IGNORECASE,
)
_REFUND = re.compile(r"\b(refund|return|reversal|chargeback|cash-?back|rewards?)\b", re.IGNORECASE)


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("description", "payee", "memo"))


def _is_card(row: dict, types: dict) -> bool:
    return (
        row.get("kind") == "income"
        and types.get(row.get("to_account_id")) == "credit"
        and not row.get("splits")
    )


def _is_cash(row: dict, types: dict) -> bool:
    return (
        row.get("kind") == "expense"
        and types.get(row.get("from_account_id")) == "cash"
        and not row.get("splits")
    )


def _pays(card: dict, cash: dict) -> bool:
    """Do these two rows read as the two sides of one card payment?"""
    if card.get("to_currency") != cash.get("from_currency"):
        return False
    try:
        if abs(float(card["to_amount"]) - float(cash["from_amount"])) > _TOL:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    a, b = _day(card.get("date")), _day(cash.get("date"))
    if not a or not b or abs(a - b) > _GAP:
        return False
    return bool(
        (_PAYMENT.search(_text(card)) or _PAYMENT.search(_text(cash)))
        and not _REFUND.search(_text(card))
    )


def _unique_pairs(cards: list[dict], cashes: list[dict]) -> list[tuple[int, int]]:
    """Index pairs ``(card, cash)`` that match each other and nothing else."""
    wants = {i: [j for j, b in enumerate(cashes) if _pays(a, b)] for i, a in enumerate(cards)}
    taken: dict[int, list[int]] = {}
    for i, js in wants.items():
        for j in js:
            taken.setdefault(j, []).append(i)
    return [(i, js[0]) for i, js in wants.items() if len(js) == 1 and len(taken[js[0]]) == 1]


def _deleted_before(*drafts: dict) -> bool:
    return bool(rejected_identities.rejections_for(list(drafts)))


def _transfer_draft(card: dict, cash: dict) -> dict:
    refs = [dict(e) for d in (cash, card) for e in d.get("external_refs") or []]
    draft = {
        "kind": "transfer",
        "date": min(str(cash["date"]), str(card["date"])),
        "description": cash.get("description") or card.get("description") or "",
        "external_refs": refs,
        "source": "feed",
        "cleared": bool(cash.get("cleared") and card.get("cleared")),
        "needs_review": True,
        "from_account_id": cash["from_account_id"],
        "from_amount": cash["from_amount"],
        "from_currency": cash["from_currency"],
        "to_account_id": card["to_account_id"],
        "to_amount": card["to_amount"],
        "to_currency": card["to_currency"],
    }
    memo = card.get("memo") or cash.get("memo")
    if memo:
        draft["memo"] = memo
    return draft


def pair_within(candidates: list[tuple[dict, dict]], types: dict) -> list[tuple[dict, dict]]:
    """``(row, draft)`` candidates of one run with each card payment's two drafts replaced by
    ONE transfer draft (keyed to the paying side's row)."""
    settled = [(r, d) for r, d in candidates if not r.get("pending") and d.get("cleared")]
    cards = [(r, d) for r, d in settled if _is_card(d, types)]
    cashes = [(r, d) for r, d in settled if _is_cash(d, types)]
    gone: set[int] = set()
    merged: list[tuple[dict, dict]] = []
    for i, j in _unique_pairs([d for _, d in cards], [d for _, d in cashes]):
        card, cash = cards[i], cashes[j]
        if _deleted_before(card[1], cash[1]):
            continue
        gone |= {id(card[1]), id(cash[1])}
        merged.append((cash[0], _transfer_draft(card[1], cash[1])))
    return [(r, d) for r, d in candidates if id(d) not in gone] + merged


def _book_pool(drafts: list[dict], provider: str) -> list[dict]:
    """Live single-leg rows that could be the other side of one of ``drafts``."""
    days = [x for x in (_day(d.get("date")) for d in drafts) if x]
    if not days:
        return []
    tracked = {e.get("txn_id") for e in pending.tracked(provider).values()}
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.query_txns(
            kind=["income", "expense"],
            date_from=(min(days) - _GAP).isoformat(),
            date_to=(max(days) + _GAP).isoformat(),
        )
        if not t.get("merged_into") and t["id"] not in tracked and t["id"] not in opening
    ]


def promote_into_book(
    provider: str, candidates: list[tuple[dict, dict]], types: dict
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """Candidates left after each one that is the other side of a row ALREADY in the book is
    folded into it (``apply_import_transfer_pair_merge``: the row becomes the transfer and
    inherits the bank's refs). Also returns each promoted row's before-image, for Undo. A
    promotion that fails leaves its candidate to book as it was read."""
    settled = [(r, d) for r, d in candidates if not r.get("pending") and d.get("cleared")]
    pool = _book_pool([d for _, d in settled], provider)
    kept = list(candidates)
    snapshots: list[dict] = []
    for want, other in ((_is_card, _is_cash), (_is_cash, _is_card)):
        drafts = [(r, d) for r, d in settled if want(d, types) and any(d is k[1] for k in kept)]
        rows = [t for t in pool if other(t, types)]
        pairs = (
            _unique_pairs([d for _, d in drafts], rows)
            if want is _is_card
            else [(j, i) for i, j in _unique_pairs(rows, [d for _, d in drafts])]
        )
        for i, j in pairs:
            row, draft = drafts[i]
            existing = rows[j]
            snapshot = dict(existing)
            if _deleted_before(draft):
                continue
            try:
                ledger_service.apply_import_transfer_pair_merge(existing["id"], draft)
            except Exception:  # noqa: BLE001 — books as read; a later run pairs it
                logger.exception("bank_data_sync: %s card payment not paired", provider)
                continue
            snapshots.append({"txn_id": existing["id"], "before": snapshot})
            kept = [k for k in kept if k[1] is not draft]
            pool = [t for t in pool if t["id"] != existing["id"]]
    return kept, snapshots
