"""A credit-card payment is ONE transfer, not income on the card plus spending in checking
(ADR 2026-11-13, Amendment 13).

A bank reports the same movement twice — an outflow from the paying account and an inflow on
the card — and booked as read it inflates income AND spending by the payment (net worth, which
only sums balances, stays right). The pairing rule, applied to every booking of a run:

* the card side is an ``income`` into an account of type ``credit``; the paying side is an
  ``expense`` out of a ``cash`` account;
* same currency and the same amount to the cent, dates at most ``PAIR_GAP_DAYS`` (4) apart —
  a payment taken on a Friday can land on the card on Tuesday (Amendment 19);
* the wording of at least one side says it is a payment (``_PAYMENT``) and the card side doesn't
  say it is a refund (``_REFUND``) — an exact-amount coincidence between a merchant refund and an
  unrelated bill is not a payment;
* the match is UNIQUE both ways: a draft with two candidate counterparts, or a counterpart two
  drafts want, pairs with nothing — a wrong pairing is silent, an unpaired row is visible;
* neither leg was deleted by the user before (``rejected_identities``): their delete stands.

The counterpart is another draft of the same run (the two become one transfer draft) or a LIVE
row already in the book — from another connector, an earlier run or typed by hand — which is
promoted to the transfer by the ledger's own pair merge. So the answer doesn't depend on which
side arrived first. A pending leg is never paired; it pairs when it SETTLES (``pair_in_book``):
both rows are in the book by then, so the two are merged into one transfer
(``ledger.merge_transactions(become_transfer=True)``, undone with the run). Two rows that fit
every rule but the wording are not paired silently: they become a ``transfer_pair`` review
item ("Make it one transfer"), asked once (Amendment 19). A card payment with NO counterpart in the book (the
paying account isn't synced) stays what the bank says, income on the card: a transfer needs both
legs, and the other one, when it arrives, pairs with it.

Money moved between two of your own CASH accounts (checking to savings) is the same double count
— spending in one, income in the other — and pairs by the same rule (Amendment 17): an
``expense`` out of one ``cash`` account and an ``income`` into ANOTHER, the same amount and
currency, at most the same few days apart, unique both ways, and the wording of one side says it
is a transfer (``_TRANSFER``) while the receiving side doesn't say refund. Both accounts are on
the budget, so the transfer moves no budget money. A move to a loan, a deposit or an investment
account is not paired: it leaves the budget, and booked as spending it is still categorised."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from backend.services import ledger as ledger_service
from backend.services import rejected_identities
from backend.services.bank_data_sync import pending, reviews

logger = logging.getLogger(__name__)

PAIR_GAP_DAYS = 4
_GAP = timedelta(days=PAIR_GAP_DAYS)
_TOL = 0.005
_PAYMENT = re.compile(
    r"\b(payments?|pymts?|pmts?|auto-?pay|bill ?pay|thank you|card services|credit card|crd)\b",
    re.IGNORECASE,
)
_TRANSFER = re.compile(
    r"\b(transfers?|xfers?|trf|tfr|internal|own account|standing order|sweep|savings|round ?ups?)\b",
    re.IGNORECASE,
)
# The account types a paired inflow may land in: a card (its payment) or another cash account.
_INFLOW_TYPES = frozenset({"credit", "cash"})
_REFUND = re.compile(r"\b(refund|return|reversal|chargeback|cash-?back|rewards?)\b", re.IGNORECASE)


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("description", "payee", "memo"))


def _is_card(row: dict, types: dict) -> bool:
    """The receiving side: income into a card (its payment) or into a cash account."""
    return (
        row.get("kind") == "income"
        and types.get(row.get("to_account_id")) in _INFLOW_TYPES
        and not row.get("splits")
    )


def _is_cash(row: dict, types: dict) -> bool:
    return (
        row.get("kind") == "expense"
        and types.get(row.get("from_account_id")) == "cash"
        and not row.get("splits")
    )


def _pays(card: dict, cash: dict, types: dict) -> bool:
    """Do these two rows read as the two sides of one card payment, or of one move between two
    of your cash accounts?"""
    said = _PAYMENT if types.get(card.get("to_account_id")) == "credit" else _TRANSFER
    return bool(
        (said.search(_text(card)) or said.search(_text(cash))) and _could_pay(card, cash, types)
    )


def _could_pay(card: dict, cash: dict, types: dict) -> bool:
    """Every rule of ``_pays`` but the wording: what is ASKED about, never paired silently."""
    if card.get("to_currency") != cash.get("from_currency"):
        return False
    if card.get("to_account_id") == cash.get("from_account_id"):
        return False
    try:
        if abs(float(card["to_amount"]) - float(cash["from_amount"])) > _TOL:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    a, b = _day(card.get("date")), _day(cash.get("date"))
    if not a or not b or abs(a - b) > _GAP:
        return False
    return not _REFUND.search(_text(card))


def _unique_pairs(
    cards: list[dict], cashes: list[dict], types: dict, fits=None
) -> list[tuple[int, int]]:
    """Index pairs ``(card, cash)`` that match each other and nothing else."""
    fits = fits or _pays
    wants = {i: [j for j, b in enumerate(cashes) if fits(a, b, types)] for i, a in enumerate(cards)}
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
    for i, j in _unique_pairs([d for _, d in cards], [d for _, d in cashes], types):
        card, cash = cards[i], cashes[j]
        if _deleted_before(card[1], cash[1]):
            continue
        gone |= {id(card[1]), id(cash[1])}
        merged.append((cash[0], _transfer_draft(card[1], cash[1])))
    return [(r, d) for r, d in candidates if id(d) not in gone] + merged


def _book_pool(drafts: list[dict], provider: str) -> list[dict]:
    """Live single-leg rows that could be the other side of one of ``drafts``: cleared, or from a
    provider (they carry an external ref). A stray uncleared row typed by hand is never
    rewritten: promotion changes its date, category and cleared state."""
    days = [x for x in (_day(d.get("date")) for d in drafts) if x]
    if not days:
        return []
    # A booking still pending is never paired; one the bank has since settled is a row like any
    # other (its entry is kept a while, marked settled, only so the bank dropping its pending
    # ref isn't read as a withdrawal).
    tracked = {e.get("txn_id") for e in pending.tracked(provider).values() if not e.get("settled")}
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.query_txns(
            kind=["income", "expense"],
            date_from=(min(days) - _GAP).isoformat(),
            date_to=(max(days) + _GAP).isoformat(),
        )
        if not t.get("merged_into")
        and t["id"] not in tracked
        and t["id"] not in opening
        and (t.get("cleared") or t.get("external_refs"))
    ]


def promote_into_book(
    provider: str, candidates: list[tuple[dict, dict]], types: dict
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """Candidates left after each one that is the other side of a row ALREADY in the book is
    folded into it (``apply_import_transfer_pair_merge``: the row becomes the transfer and
    inherits the bank's refs). Also returns, for Undo, each promoted row's id, the bank refs it
    took and the fields the promotion changed (before and after). A
    promotion that fails leaves its candidate to book as it was read."""
    settled = [(r, d) for r, d in candidates if not r.get("pending") and d.get("cleared")]
    pool = _book_pool([d for _, d in settled], provider)
    kept = list(candidates)
    snapshots: list[dict] = []
    for want, other in ((_is_card, _is_cash), (_is_cash, _is_card)):
        drafts = [(r, d) for r, d in settled if want(d, types) and any(d is k[1] for k in kept)]
        rows = [t for t in pool if other(t, types)]
        pairs = (
            _unique_pairs([d for _, d in drafts], rows, types)
            if want is _is_card
            else [(j, i) for i, j in _unique_pairs(rows, [d for _, d in drafts], types)]
        )
        for i, j in pairs:
            row, draft = drafts[i]
            existing = rows[j]
            snapshot = dict(existing)
            if _deleted_before(draft):
                continue
            try:
                after = ledger_service.apply_import_transfer_pair_merge(existing["id"], draft) or {}
            except Exception:  # noqa: BLE001 — books as read; a later run pairs it
                logger.exception("bank_data_sync: %s card payment not paired", provider)
                continue
            snapshots.append(
                {
                    "txn_id": existing["id"],
                    "changed": {
                        k: {"before": snapshot.get(k), "after": after.get(k)}
                        for k in snapshot.keys() | after.keys()
                        if snapshot.get(k) != after.get(k)
                    },
                    "refs": [str(e.get("ref") or "") for e in draft.get("external_refs") or []],
                }
            )
            kept = [k for k in kept if k[1] is not draft]
            pool = [t for t in pool if t["id"] != existing["id"]]
    return kept, snapshots


def _live_single(txn_ids: set[str]) -> list[dict]:
    rows = (ledger_service.transactions.find_by_id(i) for i in txn_ids)
    return [
        t
        for t in rows
        if t and not t.get("deleted") and not t.get("merged_into") and t.get("cleared")
    ]


def pair_in_book(provider: str, txn_ids: set[str], types: dict, run_id: str | None) -> list[str]:
    """Rows this run left single-leg in the book — a pending booking that settled, a row booked
    with no counterpart — paired with ONE live row on the other side: merged into one transfer
    when every rule holds, offered as a ``transfer_pair`` review item when only the wording is
    missing. Returns the absorbed ids (Undo unmerges them)."""
    touched = _live_single(txn_ids)
    if not touched:
        return []
    by_id = {t["id"]: t for t in _book_pool(touched, provider)} | {t["id"]: t for t in touched}
    rows = list(by_id.values())
    cards = [t for t in rows if _is_card(t, types)]
    cashes = [t for t in rows if _is_cash(t, types)]
    mine = {t["id"] for t in touched}
    absorbed: list[str] = []
    paired: set[str] = set()
    for i, j in _unique_pairs(cards, cashes, types):
        card, cash = cards[i], cashes[j]
        if mine.isdisjoint({card["id"], cash["id"]}):
            continue
        try:
            ledger_service.merge_transactions(
                cash["id"], card["id"], reason=f"{provider} card payment", become_transfer=True
            )
        except Exception:  # noqa: BLE001 — left as two rows; the review list or a hand merge fixes it
            logger.exception("bank_data_sync: %s settled card payment not paired", provider)
            continue
        absorbed.append(card["id"])
        paired |= {card["id"], cash["id"]}
    _suggest(provider, cards, cashes, types, mine, paired, run_id)
    return absorbed


def _suggest(provider, cards, cashes, types, mine, paired, run_id) -> int:
    """Ask about each pair that fits every rule but the wording, once: a dismissed one stays
    dismissed (``reviews.seen``)."""
    asked = 0
    cards = [c for c in cards if c["id"] not in paired]
    cashes = [c for c in cashes if c["id"] not in paired]
    for i, j in _unique_pairs(cards, cashes, types, fits=_could_pay):
        card, cash = cards[i], cashes[j]
        if mine.isdisjoint({card["id"], cash["id"]}) or _pays(card, cash, types):
            continue
        ref = "pair:" + "|".join(sorted((card["id"], cash["id"])))
        if reviews.seen(provider, ref, "transfer_pair"):
            continue
        reviews.record(
            provider,
            "transfer_pair",
            ref,
            run_id=run_id,
            txn_id=card["id"],
            account_id=card.get("to_account_id"),
            detail={"pay_txn_id": cash["id"]},
        )
        asked += 1
    return asked
