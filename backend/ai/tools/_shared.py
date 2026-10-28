"""Shared helpers + module-level imports for the tool group modules.

This module owns the names that the old flat ``tools.py`` bound at module top
and that the characterization tests monkeypatch (``read_rows``, ``_NAMES_CACHE``,
``read_actions``, ``write_actions``, …). The package ``__init__`` re-exports them
so ``monkeypatch.setattr(backend.ai.tools, "read_rows", …)`` keeps working — and
handlers read them **through this module at call time** (``_shared.read_rows()``)
so the patch is observed.
"""

from __future__ import annotations

import math

from backend.core.errors import ValidationError, recovery_hint
from backend.services import ledger as svc
from backend.services.actions import read_actions, write_actions
from backend.services.categories import kind_for as _kind_for
from backend.services.positions import hydrated as read_rows
from backend.services.prices import (
    _NAMES_CACHE,
    _resolve_coingecko_coin,
    fetch_coingecko_quotes,
    fetch_history_result,
    fetch_live,
    fetch_yahoo_quotes,
)
from backend.services.profile import read_profile, write_profile

__all__ = [
    "read_actions",
    "write_actions",
    "read_rows",
    "_NAMES_CACHE",
    "fetch_live",
    "fetch_yahoo_quotes",
    "fetch_coingecko_quotes",
    "fetch_history_result",
    "_resolve_coingecko_coin",
    "read_profile",
    "write_profile",
    "svc",
    "_validate_account_id",
    "_account_currency",
    "_txn_data_from_ai",
    "NO_OP_WRITE_MESSAGE",
    "no_op_write_result",
]


# The honest result for an update call that carried NO changeable field. An id-only patch used
# to return a plain success shape echoing the unchanged row — byte-indistinguishable from a real
# edit, so the model reports "Done" for a write that changed nothing (the update_transactions
# incident, 2026-07-21; an audit found the same trap in 16 more update tools). Shared so the
# wording is defined once. `ok` stays true (the call was WELL-FORMED, not an error); `no_op:
# true` + the message is what stops the "Done" narration and prompts a rethink of which field
# was meant. Deliberately NO "no further action required" — that hands the model a false
# all-clear (owner, 2026-07-21).
NO_OP_WRITE_MESSAGE = (
    "No changeable fields were provided, so nothing was modified — a write needs at least one "
    "field to set besides the id. If you meant to change something, re-send with the field(s) "
    "you intended to set."
)


def no_op_write_result(echo: dict | None = None, message: str | None = None) -> dict:
    """``{"ok": True, "no_op": True, "message": …}`` for an empty patch, plus any ``echo`` keys.

    ``echo`` carries whatever the tool's ORIGINAL success shape put alongside ``ok`` — the
    unchanged entity under its own key (``{"account": row}``), a bare id (``{"position_id":
    pid}``), a batch ``results`` list, etc. — so a reader that ignores ``no_op`` sees the same
    shape as before. The caller serializes per its own convention (a bare dict,
    ``json.dumps(...)``, or a ``(json_str, None)`` dispatch tuple) — this fixes the SHAPE only,
    never the transport.
    """
    out: dict = {"ok": True, "no_op": True, "message": message or NO_OP_WRITE_MESSAGE}
    if echo:
        out.update(echo)
    return out


def _validate_account_id(account_id: str | None, field: str = "account_id") -> str:
    """Resolve an account REF — an id or a NAME — and return the id. Raises ValueError with
    an actionable message when it's missing or matches nothing.

    Resolution itself lives in ``services.accounts.resolve_account_ref`` (the same function
    the ledger create choke calls) — this is not a second implementation. It has to run here
    too because ``_txn_data_from_ai`` derives each leg's CURRENCY from the resolved account
    before the choke ever sees the row; a name left unresolved until then would pick up the
    wrong leg currency on the way. An ambiguous name raises out of the resolver, naming the
    candidates, rather than resolving to whichever account happens to sort first."""
    from backend.services import accounts as accounts_service

    if not account_id:
        raise ValueError(
            f"{field} is required — pass the account's name (e.g. 'DBS Savings') or its id. "
            "Call list_accounts only if you don't already know the name."
        )
    # Id fast path through THIS module's store handle, which callers and tests monkeypatch by
    # convention (see the module docstring). Keeping it here means a known id never depends on
    # the resolver's own store read, so the two can't disagree about what exists.
    if svc.accounts.find_by_id(account_id):
        return account_id
    try:
        resolved = accounts_service.resolve_account_ref(account_id, rows=svc.accounts.all())
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    if resolved is None:
        names = [(a.get("name") or "").strip() for a in svc.accounts.all()]
        raise ValueError(
            # "not found" is retained verbatim — tests/test_char_tool_dispatch.py pins it.
            f"{field} '{account_id}' not found — it matched no account by id or by name."
            + recovery_hint(account_id, names, tool_hint="list_accounts")
        )
    return resolved


def _account_currency(account_id: str | None) -> str:
    """Return the currency of an account, falling back to the base currency if not set.
    Accepts a name or an id — callers pass whichever the model supplied."""
    if not account_id:
        return svc.base_currency()
    acc = svc.accounts.find_by_id(account_id)
    if acc is None:
        from backend.services import accounts as accounts_service

        try:
            resolved = accounts_service.resolve_account_ref(account_id, rows=svc.accounts.all())
        except ValidationError:
            # A currency lookup is not where an ambiguous ref should surface — every caller
            # here has already been through _validate_account_id, which reports it properly.
            resolved = None
        acc = svc.accounts.find_by_id(resolved) if resolved else None
    return (acc.get("currency") if acc else None) or svc.base_currency()


def _foreign_leg(
    prefix: str, account_id: str, amount: float, currency: str, settled, acct_cur: str
) -> dict:
    """Build a single-leg fragment for an expense/income. When a FOREIGN charge (stated currency
    differs from the account's own) has a known ``settled`` posted amount in the account currency,
    the leg is that settled amount in the account currency and the stated amount/currency become
    the original foreign charge (display/audit). Otherwise the leg is the stated amount/currency —
    a foreign currency is converted and the original auto-captured at the create choke point."""
    if settled is not None and currency != acct_cur:
        return {
            f"{prefix}_account_id": account_id,
            f"{prefix}_amount": _unsigned_amount(settled, "settled_amount"),
            f"{prefix}_currency": acct_cur,
            "original_amount": amount,
            "original_currency": currency,
        }
    return {
        f"{prefix}_account_id": account_id,
        f"{prefix}_amount": amount,
        f"{prefix}_currency": currency,
    }


def _unsigned_amount(value, field: str) -> float:
    """Validate caller-provided money as a magnitude, never a signed direction."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(
            f"{field} must be zero or greater; use direction and the account leg to represent money in or out"
        )
    return amount


def _counterpart_amount(amount: float, currency: str, to_currency: str) -> float:
    """A transfer's destination amount when the caller stated only one figure.

    Same currency on both legs → the same number (the case this default was written for:
    exact, no FX call, no drift). Different → converted at today's rate.

    NEVER the raw number carried across a currency boundary and relabelled. That booked a
    ₫300,000,000 loan repayment as S$300,000,000 on an SGD receivable (2026-08-23) — the
    source leg's stated VND unit had been assigned to the OTHER leg, so the amount arrived
    at ``_normalise_leg`` already wearing its account's own currency, FX was skipped as a
    no-op, no ``original_amount`` was stamped, and ``_currency_conversion_warning`` (which
    keys off exactly that) stayed silent. A 20,600x error with no guard left standing."""
    if currency == to_currency:
        return amount
    return svc.to_base(amount, currency, to_currency)


def _txn_data_from_ai(item: dict, default_date: str, *, derived_out: dict | None = None) -> dict:
    """Map an AI tool transaction input into the two-leg create_transaction schema.
    Expense → from-leg only; income → to-leg only; internal transfer → both legs.

    For debit transfers: account_id is source, to_account_id is destination.
    For credit transfers: to_account_id is source, account_id is destination.
    If from_account_id is explicitly provided, it is used directly as the source leg and
    account_id becomes the destination (overrides direction-based inference for transfers/income).
    The REST-style symmetric pair (from_account_id + to_account_id, NO account_id) is also
    accepted — the anchor is inferred as the source leg and the pair wins over direction.
    External transfers (no to_account_id / from_account_id) → recorded as expense so money leaves the system.

    Currency resolution: 'currency' is REQUIRED — a missing/blank value raises ValueError
    instead of silently defaulting. A silent default once stored a USD card charge as SGD
    (the LLM parsed "USD 10.94" but omitted the field), understating the amount by the FX
    factor.

    On a transfer, 'amount'/'currency' are the SOURCE leg and 'to_amount'/'to_currency' the
    DESTINATION leg, in every anchor form — the anchor picks which account is on which side,
    never how the caller's figure and its unit are paired. 'to_currency' defaults to the
    destination account's own currency (account metadata, not parsed from a fallible source);
    an omitted 'to_amount' is derived by _counterpart_amount, which converts across a currency
    boundary rather than relabelling the number. So the credit form's SOURCE leg is FX-derived:
    to pin both legs exactly, use the debit form (account_id = source, to_* = destination).

    ``derived_out``, when given, is FILLED when the destination leg was derived ACROSS a
    currency boundary — the one case where the stored figure is the app's arithmetic rather
    than the caller's own number, and so the one worth reporting back. An out-param rather
    than a widened return because every existing call site would otherwise have to change to
    ignore a fact it does not use (the same shape ``create_transaction``'s ``meta_out`` uses).
    """
    anchor = item.get("account_id")
    direction = item.get("direction", "debit")
    explicit_from_id = item.get("from_account_id")
    other_account_id = item.get("to_account_id")
    # An anchor that IS the source makes from_account_id redundant — drop it so the
    # explicit-from branch below never builds a degenerate same-account transfer.
    if anchor and explicit_from_id == anchor:
        explicit_from_id = None
    # REST-style symmetric pair (from → to) with no anchor: the pair is authoritative —
    # anchor on the source leg and force debit so direction can never swap the legs.
    if not anchor and explicit_from_id and other_account_id:
        anchor = explicit_from_id
        explicit_from_id = None
        direction = "debit"
    if not anchor and (explicit_from_id or other_account_id):
        given, missing = (
            ("from_account_id", "destination") if explicit_from_id else ("to_account_id", "source")
        )
        raise ValueError(
            f"account_id is required. For a transfer, {given} alone is ambiguous — also pass "
            f"account_id as the {missing} account (or pass both from_account_id and "
            "to_account_id)."
        )
    account_id = _validate_account_id(anchor, "account_id")
    # Leave category unset when the user didn't name one, so a fill-only
    # (override_existing=false) spend rule can still categorize it inside
    # create_transaction; an expense/income with no category falls back to
    # "other" AFTER apply_rules runs (services.ledger.create_transaction).
    # Kind names (income/transfer/expense/adjustment) are NOT category values — clear them
    # here so the reserved-category guard in create_transaction never fires on AI input.
    _raw_cat = item.get("category") or None
    _KIND_NAMES = frozenset({"expense", "income", "transfer", "adjustment", "investment"})
    category = None if (_raw_cat in _KIND_NAMES) else _raw_cat
    # The matching/bank string is `description` (ADR 2026-07-17; the retired `note` field is
    # no longer accepted here — 2026-07-20). `memo` is personal, display-only — never feeds
    # payee resolution.
    text = item.get("description") or ""
    # Treat as transfer if a counterparty account is given, regardless of direction
    if explicit_from_id or other_account_id:
        kind = "transfer"
    elif direction == "credit":
        kind = "income"
    else:
        kind = _kind_for(category)
    # Keystone (ADR 0010): a payee's pinned default beats an AI-GUESSED category. The
    # model's category is a per-row guess; a pinned autocategorize default is the user's
    # durable choice for that entity. Drop the guess so create_transaction's fill-only
    # payee default applies. Human edits go through update_transaction (never this helper),
    # so a deliberate human category still wins. resolve uses create=False — the actual
    # mint/attach happens later in the choke point.
    if category and kind in ("expense", "income"):
        from backend.services import payee_registry

        _pid = (item.get("payee_id") or "").strip() or payee_registry.resolve_payee_id(
            text, create=False
        )
        if _pid and payee_registry.default_category_for(_pid):
            category = None
    amount = _unsigned_amount(item["amount"], "amount")
    # No silent currency default: an omitted 'currency' once stored a USD card charge
    # as SGD at fx_rate=1 (the model parsed "USD 10.94" but skipped the field). The
    # error text doubles as model guidance so a retry self-corrects.
    currency = str(item.get("currency") or "").strip().upper()
    if not currency:
        raise ValueError(
            "currency is required — pass the 3-letter code stated in the source "
            "(e.g. a card alert saying 'USD 10.94' → currency='USD'), even when it "
            f"differs from the account's own currency ({_account_currency(account_id)})."
        )
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(
            f"currency must be a 3-letter ISO 4217 code (e.g. SGD, USD, VND), got {currency!r}."
        )

    # Foreign-charge inputs (expense/income): when a settled (posted) account-currency amount is
    # given for a charge whose stated currency differs from the account's, the leg uses the settled
    # amount and the stated amount/currency become the original (see _foreign_leg).
    settled_amount = item.get("settled_amount")
    acct_currency = _account_currency(account_id)

    data = {
        "date": item.get("date") or default_date,
        "kind": kind,
        # adjustment never carries a category; every other kind (including transfer, since
        # the "transfers carry category=None" invariant was retired — owner decision
        # 2026-07-03, docs/adr/2026-07-03-categorized-transfers.md) passes the caller's
        # category through to the create_transaction choke point, which validates it.
        "category": None if kind == "adjustment" else category,
        "description": text,
        "memo": item.get("memo") or "",
        "tags": [str(t) for t in (item.get("tags") or []) if str(t).strip()],
        "source": "ai",
    }
    # Optional idempotency pairs (ADR 2026-07-21, list-only): each external_refs entry is a
    # {"source": <namespace>, "ref": <raw per-event id>} pair; create_transaction dedupes on
    # every (source, ref) pair so a re-presented source row never double-logs. The retired
    # scalar spelling is REFUSED loudly — silently ignoring it would silently drop dedup.
    if item.get("external_source") or item.get("external_ref"):
        raise ValidationError(
            "external_source/external_ref were retired (2026-07-21) — pass "
            'external_refs=[{"source": ..., "ref": ...}] instead'
        )
    external_refs = []
    for entry in item.get("external_refs") or []:
        if not isinstance(entry, dict):
            raise ValidationError('each external_refs entry must be {"source": ..., "ref": ...}')
        ref = str(entry.get("ref") or "").strip()
        if not ref:
            continue
        external_refs.append(
            {"source": (str(entry.get("source") or "").strip() or None), "ref": ref}
        )
    if external_refs:
        data["external_refs"] = external_refs
    if kind == "transfer" and (explicit_from_id or other_account_id):
        # ONE mapping for all three anchor forms. `amount`/`currency` are the SOURCE leg;
        # `to_amount`/`to_currency` are the DESTINATION leg (their documented meaning). The
        # anchor decides only WHICH account is which side — never how the caller's figure and
        # its unit are paired. Two of these forms used to split that pair across legs (the
        # number on one, the unit on the other); see _counterpart_amount for the scar.
        if explicit_from_id:  # from_account_id → account_id
            from_id = _validate_account_id(explicit_from_id, "from_account_id")
            to_id = account_id
        elif direction == "credit":  # counterparty → account_id
            from_id = _validate_account_id(other_account_id, "to_account_id")
            to_id = account_id
        else:  # account_id → counterparty
            from_id = account_id
            to_id = _validate_account_id(other_account_id, "to_account_id")
        to_currency = (item.get("to_currency") or _account_currency(to_id)).upper()
        to_amount = item.get("to_amount")
        if to_amount is not None:
            to_amt = _unsigned_amount(to_amount, "to_amount")
        else:
            to_amt = _counterpart_amount(amount, currency, to_currency)
            # Report the derivation ONLY when it crossed a currency boundary. The
            # same-currency default copies the caller's own number, which is not a
            # derivation and warning about it would be noise on every ordinary transfer.
            if derived_out is not None and currency != to_currency:
                derived_out.update(
                    {
                        "source_amount": amount,
                        "source_currency": currency,
                        "derived_amount": to_amt,
                        "derived_currency": to_currency,
                    }
                )
        data |= {
            "from_account_id": from_id,
            "from_amount": amount,
            "from_currency": currency,
            "to_account_id": to_id,
            "to_amount": to_amt,
            "to_currency": to_currency,
        }
    elif kind == "transfer":
        # Reached only via a category that MAPS to kind=transfer with no counterparty account
        # (today: 'investment' — a brokerage deposit / crypto buy). Money leaves the system, so
        # record an expense and keep the category label. (Kind names were already cleared at
        # the top, so category here is never 'transfer' itself.)
        data["kind"] = "expense"
        data["category"] = category
        data |= {"from_account_id": account_id, "from_amount": amount, "from_currency": currency}
    elif direction == "credit" or kind == "income":
        data |= _foreign_leg("to", account_id, amount, currency, settled_amount, acct_currency)
    else:
        data |= _foreign_leg("from", account_id, amount, currency, settled_amount, acct_currency)

    # Split allocation (P3-SP): pass the [{category, amount, note?}] list straight through to the
    # create_transaction choke point, which runs the SAME _validate_splits as the REST path
    # (kind gate, valid categories, Σ == leg amount ±0.01 REFUSED-not-rescaled, 1-line→unsplit).
    # Only attach for the final expense/income kind — a split is meaningless on a transfer and the
    # choke point would reject it anyway (the kind gate). Each amount is in the LEG's native
    # currency (= ``currency``), matching the single leg above.
    raw_splits = item.get("splits")
    if raw_splits and data["kind"] in ("expense", "income"):
        data["splits"] = [
            {
                "category": (s.get("category") or "").strip(),
                "amount": _unsigned_amount(s.get("amount"), "splits[].amount")
                if s.get("amount") is not None
                else 0.0,
                "memo": (s.get("memo") or ""),
            }
            for s in raw_splits
            if isinstance(s, dict)
        ]
    # Explicit original foreign charge (display/audit): forward to the create choke point,
    # where explicit input wins over auto-capture (services.ledger._resolve_original). Attached
    # only for the final expense/income kind — a transfer carries no single original (same
    # invariant as splits above). Wins over a settled-derived original from _foreign_leg.
    orig_amount = item.get("original_amount")
    orig_currency = item.get("original_currency")
    if data["kind"] in ("expense", "income"):
        if orig_amount is not None:
            data["original_amount"] = _unsigned_amount(orig_amount, "original_amount")
        if orig_currency:
            data["original_currency"] = str(orig_currency).strip().upper()
    # Explicit payee link (P4-PAYEE): attach only for the FINAL expense/income kind — a
    # transfer never carries a payee (same invariant as splits above). When omitted, the
    # create choke point auto-resolves the payee from the note.
    payee_id = (item.get("payee_id") or "").strip()
    if payee_id and data["kind"] in ("expense", "income"):
        data["payee_id"] = payee_id
    return data
