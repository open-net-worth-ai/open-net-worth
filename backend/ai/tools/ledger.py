"""Ledger / transaction tools."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import uuid
from collections import Counter
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import _txn_schema
from backend.ai.tools._shared import _txn_data_from_ai, _validate_account_id, svc
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_no_required
from backend.core.errors import (
    DuplicateTransactionError,
    OpenNetWorthError,
    PreviouslyDeletedError,
)
from backend.core.user_time import user_today
from backend.services import labels, ledger_preview, review_approval

logger = logging.getLogger(__name__)


# ── Unattended-write guard — the ONE call site every ledger write goes through ──────────────
#
# ``_gate.enforce_unattended_write`` (backend/ai/tools/_confirm_gate.py) is an INERT stub that
# always returns ``None`` — the hardline block and the per-agent envelope it once composed are
# gone, so calling it unconditionally here never changes behavior on any surface.
#
# The after-the-fact notice this comment used to describe (``report_unattended_write``) is
# GONE — it was left with zero call sites when the unattended concept was removed
# (2026-08-10) and has been deleted. ``ai._confirm_gate.surface_unattended_write`` beneath it
# survives, still tested and still governed by its app-config flag, but nothing in production
# calls it either: rewiring or retiring that is an open product decision, not dead-code
# cleanup.
#
# Wired at every real write in this module: the single-row paths (``_add_one``/
# ``_update_one``), the true multi-row bulk paths (``add_transactions``' statement-import
# ``to_create`` write, and ``update_transactions``' N>1 ``updates`` loop — evaluated PER ROW,
# so a batch with some rows inside the envelope and some outside books the former and refuses
# the latter, rather than an all-or-nothing abort), and the hardline check on
# ``delete_transactions_where`` (unconditional refusal, no envelope math — see that function).
# KNOWN GAP, deliberately NOT wired: the alias-only ``_update_legacy_bulk`` path (see its own
# docstring for why).


def _envelope_fields(
    txn: dict,
) -> tuple[Optional[str], Optional[str], Optional[float], Optional[str]]:
    """(account_id, kind, amount, category_id) the envelope evaluates a ledger row against,
    read off a two-leg transaction dict (a draft about to be created, or an existing row
    merged with an update's patch). Prefers the FROM leg (money leaving — the expense/source
    side of a transfer) and falls back to the TO leg (income has no from-leg at all); an
    envelope's ``kinds``/``account_ids`` dimensions are what actually discriminate expense vs
    income vs transfer for a caller like the built-in email-transaction-logger (kinds=
    ["expense","income"]), so checking a transfer against its from-leg only is not a gap in
    practice for that boundary."""
    account_id = txn.get("from_account_id") or txn.get("to_account_id")
    from_amount = txn.get("from_amount")
    amount = from_amount if from_amount is not None else txn.get("to_amount")
    return account_id, txn.get("kind"), amount, txn.get("category")


async def _check_unattended_envelope(tool_name: str, txn: dict) -> Optional[tuple[str, None]]:
    """Evaluate ``txn`` against the running agent's unattended-write envelope, returning a
    tool-result tuple to return VERBATIM on refusal, or ``None`` to proceed with the write.
    Thin wrapper around ``_gate.enforce_unattended_write`` — see the module note above for why
    calling it unconditionally is safe."""
    account_id, kind, amount, category_id = _envelope_fields(txn)
    return await _gate.enforce_unattended_write(
        tool_name, account_id=account_id, kind=kind, amount=amount, category_id=category_id
    )


def _unattended_add_summary(new: dict) -> str:
    """After-the-fact notice text for a headless ``add_transactions`` write that landed
    inside its agent's envelope."""
    amount = new.get("from_amount") if new.get("from_amount") is not None else new.get("to_amount")
    currency = new.get("from_currency") or new.get("to_currency") or ""
    category = new.get("category") or "Uncategorized"
    desc = new.get("description") or ""
    head = (
        f"Booked {currency} {amount:,.2f}".strip() if amount is not None else "Booked a transaction"
    )
    parts = [head, category] + ([desc] if desc else [])
    return " • ".join(parts)


def _unattended_add_undo_hint(new: dict) -> str:
    txn_id = new.get("id")
    return f'Undo via delete_transactions(transaction_ids=["{txn_id}"]).' if txn_id else ""


def _unattended_update_summary(txn_id: str, updated: dict, changed_fields: list[str]) -> str:
    """After-the-fact notice text for a headless ``update_transactions`` write."""
    amount = (
        updated.get("from_amount")
        if updated.get("from_amount") is not None
        else updated.get("to_amount")
    )
    currency = updated.get("from_currency") or updated.get("to_currency") or ""
    category = updated.get("category") or "Uncategorized"
    head = (
        f"Updated {currency} {amount:,.2f} • {category}"
        if amount is not None
        else f"Updated • {category}"
    )
    return f"{head} (changed: {', '.join(changed_fields)}) — transaction {txn_id}"


# Hard cap on one add_transactions (multi-row) call (security review 2026-07-16, FIX 5): the tool's
# own pre-flight runs matching.match_batch over the whole submitted list (O(rows × pool)), and
# the description already advises 20-25 rows per call for batching a large statement. 500 is a
# generous ceiling that still refuses a runaway/hostile payload loudly, BEFORE any validation
# or write work, instead of the model quietly paying for (or timing out on) a huge single call.
_MAX_BULK_ADD_ROWS = 500


# Batch/import-only parameters of add_transactions — everything else at the top level is a
# transaction field, which is how a legacy bare `add_transaction` payload is recognised.
def _lone_row_matches_the_ledger(item: dict, today_iso: str) -> bool:
    """True when a ONE-row call collides with something already booked.

    Such a row is handed to the BULK path, whose pre-flight aborts and returns the duplicate
    pair. The single-add path created the row first and merely attached a ``duplicate_review``
    note — created-then-flagged, the exact middle state that put a second 7-Eleven charge in
    the owner's ledger (2026-08-10). Fails OPEN like the bulk pre-flight it defers to: a broken
    matcher must never block a legitimate write.
    """
    try:
        from backend.services import matching

        data = _txn_data_from_ai(item, default_date=today_iso)
        # An L0 (source, ref) hit is IDEMPOTENCE, not a judgement call: re-presenting the same
        # email must stay the silent no-op that returns the existing row, so it never reaches
        # the abort. Checked on the refs directly — `match_batch` reports such a row as
        # NEAR_DUPLICATE (the content is identical too), so the match CLASS cannot tell the
        # two apart. Only an INFERRED match is worth a round trip.
        for source, ref in matching.ext_ref_pairs(data):
            if svc.transactions.by_external_ref(source, ref) is not None:
                return False
        row_match = matching.match_batch([data], context=matching.BatchContext(complete=False))[0]
        return row_match.band in ("strong", "review") and row_match.assigned is not None
    except Exception:  # noqa: BLE001 — see the docstring's fail-open contract
        logger.exception("add_transactions: lone-row pre-flight failed; taking the single path")
        return False


_BATCH_ONLY_KEYS = frozenset(
    {"decisions", "import_description", "import_session_id", "statement_ref_id"}
)

# Per-item transaction schema — built from the shared field table in ``_txn_schema``, which
# is the ONE statement of the transaction shape for this tool, update_transaction and
# get_transactions alike. See that module's docstring for the enforcement-vs-teaching split
# that governs the field prose.
_TXN_ITEM_SCHEMA = {
    "type": "object",
    "properties": _txn_schema.create_item_properties(),
    "required": ["description", "amount", "currency", "account_id"],
}


def _payee_minted_note(n: int) -> str:
    """What to DO about a payee this write just invented from a raw bank string.

    Phrased as the durable fix, not "go clean it up": a re-link alone leaves the junk entity in
    the book and the NEXT row with the same merchant mints another one, because every minted
    name embeds that row's own reference number and can never match again (19 such payees
    accumulated live before this signal existed). Teaching the payee to match this wording is
    what makes it resolve by itself next run, and a default category is what stops the row
    booking Uncategorized — the payee-default tier of explicit > rule > payee-default > None is empty
    by construction on a fresh mint.

    Deliberately names the FLOW, not tools or parameters. Tool names and signatures drift (a
    rename only has to keep an alias resolvable, and prose is not covered by
    ``tool_inventory.py --lint``), so a call signature baked in here rots silently and then
    actively misleads — the model can already find the tools for a clearly-stated goal.
    """
    rows, it = ("row", "it") if n == 1 else ("rows", "each")
    return (
        f"{n} {rows} had NO payee match, so the payee shown was INVENTED from the raw bank "
        "description — reference number and amount included — and carries no default category, "
        f"so {it} also books Uncategorized. That invented name embeds this row's own reference, so it "
        "can never match another transaction: left alone, the same merchant mints a fresh junk "
        "payee on every future run. For each flagged row: work out the real merchant or person, "
        "search the payee book for it, and link the row to that payee — creating the payee first "
        "if it does not exist yet (you CAN create payees here, including unattended). Then make "
        "it durable: give that payee a default category, and teach it to recognise this bank "
        "wording by itself. A raw-substring match is checked BEFORE normalization, which makes "
        "it the only thing that works on reference-laden strings — key it on the stable "
        "merchant/person part, never on the reference number. Finally retire the invented payee "
        "once nothing points at it. The raw string always stays in `description`."
    )


def _currency_conversion_warning(t: dict) -> dict | None:
    """`original_amount`/`original_currency` are stamped on a leg ONLY when its stated
    currency differed from its account's own currency — `ledger._normalise_leg` then
    silently FX-converted it. That conversion is easy for a terse "Done" reply to bury —
    live incident: a VND refund got booked with currency stated as "SGD 7.63", silently
    converted to the wrong VND total, and reported done without ever mentioning a
    conversion happened at all. Surface it as an explicit warning instead — dict-shaped
    like tracking_start.flags_for_txn's, since both land in the same `warnings` list and
    a caller iterating it expects every entry to be a {"type", "message", ...} dict."""
    orig_amt, orig_cur = t.get("original_amount"), t.get("original_currency")
    if orig_amt is None or not orig_cur:
        return None
    if t.get("kind") == "income":
        native_amt, native_cur = t.get("to_amount"), t.get("to_currency")
        acct_name = t.get("to_account_name")
    else:
        native_amt, native_cur = t.get("from_amount"), t.get("from_currency")
        acct_name = t.get("from_account_name")
        if native_amt is None:
            native_amt, native_cur = t.get("to_amount"), t.get("to_currency")
            acct_name = t.get("to_account_name")
    if native_amt is None or not native_cur:
        return None
    return {
        "type": "currency_converted",
        "original_amount": orig_amt,
        "original_currency": orig_cur,
        "booked_amount": native_amt,
        "booked_currency": native_cur,
        "message": (
            f"CURRENCY WAS CONVERTED: you stated {orig_cur} {orig_amt:,.2f}, but "
            f"{acct_name or 'the account'} is in {native_cur} — this booked as "
            f"{native_cur} {native_amt:,.2f} at today's FX rate. Tell the user this "
            "conversion happened. If the stated currency was wrong — e.g. you meant this "
            "as the account's own native amount, not a foreign-currency figure — undo and "
            "re-add with the correct currency."
        ),
    }


def _derived_leg_warning(derived: dict | None) -> dict | None:
    """The destination leg of a cross-currency transfer was COMPUTED, not stated.

    Sibling of ``_currency_conversion_warning`` above, for the case that one structurally
    cannot see: it reads ``original_amount``, which a transfer never carries (``ledger.
    _resolve_original`` returns ``(None, None)`` for the kind by design — a transfer has no
    single "original" charge). So a transfer whose other leg was filled in by FX had no
    surface at all.

    Worth saying because the derived figure is today's rate, not the rate that actually
    posted. On a receivable being paid off that difference is the entire point: two tranches
    at today's rate do not sum to the amount actually owed, and the account settles at "$2.40
    still owed" — wrong in a way nobody goes looking for. Same dict shape as its sibling, and
    it lands in the same ``warnings`` list."""
    if not derived:
        return None
    src_amt = derived.get("source_amount")
    src_cur = derived.get("source_currency")
    amt = derived.get("derived_amount")
    cur = derived.get("derived_currency")
    if src_amt is None or amt is None or not src_cur or not cur:
        return None
    rate = (amt / src_amt) if src_amt else None
    rate_text = f" (1 {src_cur} = {rate:,.6g} {cur})" if rate else ""
    return {
        "type": "leg_amount_derived",
        "source_amount": src_amt,
        "source_currency": src_cur,
        "derived_amount": amt,
        "derived_currency": cur,
        "message": (
            f"DESTINATION AMOUNT WAS DERIVED: you gave only {src_cur} {src_amt:,.2f}, so the "
            f"receiving leg was computed at today's FX rate as {cur} {amt:,.2f}{rate_text}. "
            "That is today's rate, not necessarily the one that actually posted. If you know "
            "the figure that landed in the destination account, pass `to_amount` to pin it "
            "exactly — it matters when the transfer is meant to settle a balance to zero."
        ),
    }


_PREVIOUSLY_DELETED_NOTE = (
    "NOT BOOKED — the user deleted this transaction earlier, and a deleted identity is "
    "remembered: the same source event is refused on re-delivery, allow_duplicate or not. Do "
    "NOT count it as logged and do NOT retry as-is. Re-submit with decisions "
    "[{index, action: 'create'}] ONLY if the user explicitly wants it back; otherwise leave it."
)


def _previously_deleted_entry(index: int, record: dict) -> dict:
    return {
        "index": index,
        "deleted_id": record.get("txn_id"),
        "deleted_on": (record.get("rejected_at") or "")[:10] or None,
        "reason": "the user deleted this transaction; its source ref is remembered",
    }


def _previously_deleted_single(e: PreviouslyDeletedError) -> dict:
    record = {
        "txn_id": e.context.get("deleted_txn_id"),
        "rejected_at": e.context.get("rejected_at"),
    }
    return {
        "summary": "1 submitted → 1 previously_deleted",
        "ok": True,
        "created": 0,
        "previously_deleted": [_previously_deleted_entry(0, record)],
        "results": [{"index": 0, "status": "previously_deleted", "id": None}],
        "note": _PREVIOUSLY_DELETED_NOTE,
    }


def _unverified_equal_amount_warning(txn: dict) -> dict | None:
    """A transfer's two legs landed on the exact same NUMBER in different currencies, and
    there is no known exchange rate for the pair to judge it against.

    ``services.ledger._assert_transfer_amount_converted`` refuses this shape outright when a
    known, non-1:1 rate exists — but it deliberately declines to judge an UNKNOWN pair (a
    currency your book has never held before, most often a crypto pair): refusing on
    ignorance would be permanently wrong for a real 1:1 transfer (USD<->USDC), and there is no
    rate to name in the message anyway. That leaves exactly the failure mode this guard was
    built for — the source amount carried across the boundary unconverted — silently possible
    on any pair that simply has not entered the FX cache yet, which a NEW currency always
    hasn't. This is the non-blocking half: surfaced so a model that meant to convert (and
    forgot) gets a chance to catch it itself, instead of it sitting silent until the pair
    happens to become known and the hard guard starts firing after the fact.
    """
    if txn.get("kind") != "transfer":
        return None
    famt, tamt = txn.get("from_amount"), txn.get("to_amount")
    fcur, tcur = txn.get("from_currency"), txn.get("to_currency")
    if not famt or not tamt or not fcur or not tcur or fcur == tcur:
        return None
    if round(abs(float(famt)), 2) != round(abs(float(tamt)), 2):
        return None
    from backend.core.fx import known_rate

    if known_rate(fcur, tcur) is not None:
        return None  # a known pair already went through the hard guard above
    return {
        "type": "unverified_equal_amount_transfer",
        "message": (
            f"{fcur} {abs(float(famt)):,.2f} and {tcur} {abs(float(tamt)):,.2f} are the exact "
            f"same number, and there is no known exchange rate for {fcur}/{tcur} yet to check "
            "it against, so this booked as stated. If this was meant to be a real currency "
            "conversion, double-check you didn't just copy the source amount instead of "
            "converting it — if so, fix to_amount now with update_transaction. If it "
            "genuinely settled at parity (e.g. a stablecoin peg), no action needed."
        ),
    }


def _names_the_tool_flag(message: str | None) -> str | None:
    """The choke's duplicate refusal names the SERVICE kwarg (``force=true`` — also the REST
    body flag); this tool's parameter is ``allow_duplicate``. Rewritten at the boundary so the
    advice the model reads is advice it can act on (ADR 2026-09-02 follow-up)."""
    if not message:
        return message
    return message.replace("force=true", "allow_duplicate=true")


async def _add_one(input_data: dict, *, gated: bool = False, promised: dict | None = None):
    """Book exactly ONE transaction — the single-row path of ``add_transactions``.

    ``gated=True`` means preview-and-stage instead of committing (the confirm gate); the
    caller decides, because only it knows whether this call already redeemed a token.
    ``promised`` is the outcome a redeemed card showed the user, re-verified before the write.

    Deliberately NOT the bulk path with a length-1 list: a single manual booking must not
    mint an import-batch record, and it keeps the transfer-duplicate confirmation prompt
    that a statement import (which has its own batch-aware pre-flight) does not want.
    """

    derived_leg: dict = {}
    try:
        data = _txn_data_from_ai(
            input_data, default_date=user_today().isoformat(), derived_out=derived_leg
        )
    except (ValueError, KeyError) as e:
        return json.dumps(
            {"ok": False, "error": str(e), "results": [{"index": 0, "error": str(e)}]},
            ensure_ascii=False,
        ), None
    force = bool(input_data.get("allow_duplicate", False))
    force_transfer_rate = bool(input_data.get("force_transfer_rate", False))
    # A duplicate transfer is the MATCH ENGINE's call (F14d: same route, same amount, near
    # date), reached through this tool's router — a lone row the engine matches is handed to
    # the bulk pre-flight, which aborts and returns the pair. The bespoke exact-key check that
    # used to sit here pre-dated F14d and, once the router existed, could only fire on rows the
    # engine had DELIBERATELY cleared (e.g. F03 vetoing two same-source writes as distinct):
    # it contradicted the engine and resolved the contradiction toward "re-call with
    # force=true" — the one piece of advice that put nine duplicate rows in the ledger.
    # Confirm gate: on an interactive turn a single booking PREVIEWS first (resolved category
    # + WHY, resolved accounts, base amount) and commits only when re-called with the token
    # staged here. Placed after validation and the duplicate check so an unbookable draft
    # still errors immediately instead of staging a plan the user could never confirm.
    # `input_data` is itself a valid bare `add_transactions` payload, so redeeming the token
    # replays this exact call through the normal path — no second write path.
    if gated or promised is not None:
        try:
            change = ledger_preview.preview_create(data, force_transfer_rate=force_transfer_rate)
        except (ValueError, OpenNetWorthError) as e:
            return _gate.preview_failed_result(str(e))
        if gated:
            return _gate.stage("add_transactions", input_data, change)
        # Confirming: the args are the staged ones, but they are re-RESOLVED here, so verify
        # they still resolve to what the card promised before committing.
        if _gate.outcome_of(change) != promised:
            return _gate.outcome_mismatch_result("add_transactions")

    # Unattended-write guard: self-guards on is_unattended(), so this is a no-op on every
    # interactive/connector/skill turn (gated/promised above already covers those).
    guard = await _check_unattended_envelope("add_transactions", data)
    if guard is not None:
        return guard

    write_meta: dict = {}
    try:
        new = svc.create_transaction(
            data, force=force, force_transfer_rate=force_transfer_rate, meta_out=write_meta
        )
    except PreviouslyDeletedError as e:
        return json.dumps(_previously_deleted_single(e), ensure_ascii=False), None
    except DuplicateTransactionError as e:
        # Its own branch ABOVE the generic one: the message already carries the recovery path,
        # but the model should not have to parse an id back out of prose. `existing` is the row
        # it collided with, so the model can show it to the user without a second call.
        return json.dumps(
            {
                "ok": False,
                "refused_duplicates": 1,
                "error": _names_the_tool_flag(str(e)),
                "duplicate_of": e.context.get("existing_id"),
                "existing": e.context.get("existing"),
                "results": [
                    {
                        "index": 0,
                        "error": _names_the_tool_flag(str(e)),
                        "duplicate_of": e.context.get("existing_id"),
                    }
                ],
            },
            ensure_ascii=False,
        ), None
    except (ValueError, OpenNetWorthError) as e:
        # OpenNetWorthError covers split validation (sum≠leg, unknown/invalid split category) so a
        # bad split is reported to Lucius, not crashed — mirroring the REST 422 path (P3-SP).
        return json.dumps(
            {"ok": False, "error": str(e), "results": [{"index": 0, "error": str(e)}]},
            ensure_ascii=False,
        ), None
    # Dedup / match engine (RFC transaction-dedup-engine): surface a cross-source duplicate or
    # transfer pair against the existing ledger — created-then-flagged into the one review queue,
    # same as imports/connectors (this unifies the manual/AI/email add surface with the engine).
    # Labels beside the opaque ids (services.labels) — a bare payee_id cannot be judged clean
    # or dirty, which is exactly what made the post-create payee check skippable.
    labelled = labels.label_txn(new, labels.label_maps(categories=True, payees=True, accounts=True))
    if write_meta.get("idempotent_existing_id"):
        return json.dumps(
            {
                "summary": "1 submitted → 1 already_recorded",
                "ok": True,
                "created": 0,
                "already_recorded": [
                    {
                        "index": 0,
                        "existing_id": new.get("id"),
                        "reason": "an external ref on this row is already on that transaction",
                    }
                ],
                "results": [{"index": 0, "status": "already_recorded", "id": new.get("id")}],
                "note": (
                    "NOTHING WAS WRITTEN — this row was already in the ledger under the same "
                    "external ref, so it was returned instead of booked. Do NOT count it as "
                    "newly logged, and do NOT retry: re-sending produces this same answer."
                ),
            },
            ensure_ascii=False,
        ), None
    out: dict = {
        "summary": "1 submitted → 1 created",
        "ok": True,
        "transaction": labelled,
        "results": [{"index": 0, "status": "created", "id": new.get("id")}],
    }
    conversion_warning = _currency_conversion_warning(labelled)
    if conversion_warning:
        out.setdefault("warnings", []).append(conversion_warning)
    derived_warning = _derived_leg_warning(derived_leg)
    if derived_warning:
        out.setdefault("warnings", []).append(derived_warning)
    equal_amount_warning = _unverified_equal_amount_warning(labelled)
    if equal_amount_warning:
        out.setdefault("warnings", []).append(equal_amount_warning)
    if write_meta.get("payee_minted_txn_ids"):
        out["payee_minted"] = True
        out["payee_minted_note"] = _payee_minted_note(1)
    try:
        from backend.services import match_review

        review = match_review.review_created_rows([new])
        flagged = review.get("queued", 0) + review.get("already_pending", 0)
        if flagged:
            # already_pending counts too: a re-add of a row the engine ALREADY flagged is still
            # a live duplicate the caller must be told about, and reporting only fresh enqueues
            # made the second telling of the same duplicate look like an all-clear.
            out["duplicate_review"] = (
                f"{flagged} possible duplicate/transfer-pair flagged for review "
                "(see the duplicate review queue or list_match_reviews)."
            )
    except Exception:  # noqa: BLE001 — surfacing must never break a completed add
        # Logged, never raised: without this a failure here is indistinguishable from
        # "no duplicate found" — a silent money-correctness gap, not merely a missing log.
        logger.exception(
            "tool add_transactions: dedup review pass failed for txn %s", new.get("id")
        )
    # Tracking-start honesty parity: a leg landing before its account's start (or a
    # straddling transfer) is flagged in the result, never silently dropped. Interactive
    # Lucius can resolve via update_accounts; headless can only flag + notify.
    try:
        from backend.services import tracking_start

        flags = tracking_start.flags_for_txn(
            new, svc.accounts.query_accounts(), svc.transactions.all()
        )
        if flags:
            out.setdefault("warnings", []).extend(flags)
    except Exception:  # noqa: BLE001
        pass
    return json.dumps(out, ensure_ascii=False), None


class SimulateCashflowInput(ToolInput):
    """Schema-of-record for ``simulate_cashflow`` (see ``schema_from_model``)."""

    income_override: Optional[float] = Field(
        default=None,
        description=(
            "Monthly income to use in the projection (base currency). Omit to use the "
            "actual 3-month average."
        ),
    )
    expense_override: Optional[float] = Field(
        default=None,
        description=(
            "Monthly expenses to use in the projection (base currency). Omit to use the "
            "actual 3-month average."
        ),
    )
    months: Optional[int] = Field(
        default=None, description="How many months to project. Default 12."
    )
    include_fixed_deposits: bool = Field(
        default=False,
        description=(
            "Add fixed-deposit balances to the runway's starting balance. Default false "
            "(cash-only, conservative)."
        ),
    )
    include_investments: bool = Field(
        default=False,
        description=(
            "Add investment positions + settlement cash to the runway's starting balance. "
            "Default false (cash-only, conservative)."
        ),
    )
    expense_growth_pct: Optional[float] = Field(
        default=None,
        description=(
            "Annual expense growth rate as a percentage (0–20). "
            "E.g. 3.0 means expenses compound at 3% per year across the projection. "
            "Omit to use 0% (flat expenses)."
        ),
    )
    income_growth_pct: Optional[float] = Field(
        default=None,
        description=(
            "Annual income growth rate as a percentage (0–20). "
            "E.g. 5.0 means income compounds at 5% per year across the projection. "
            "Omit to use 0% (flat income)."
        ),
    )
    include_commitments: Optional[bool] = Field(
        default=None,
        description=(
            "Whether to overlay committed installment outflows and funding draws onto the projection. "
            "Defaults to true. Set to false to see a pure income/expense baseline without scheduled obligations."
        ),
    )


@tool(
    "simulate_cashflow",
    description=(
        "Run a forward cash flow projection with hypothetical income and/or expense values. "
        "Use when the user asks 'what if my income drops to X?', 'what if I cut expenses to Y?', "
        "'how long could I last without a job?', or any what-if scenario about cash runway. "
        "Returns the same projection as the Cash Flow tab but with the overrides applied. "
        "Omit an override to keep the actual average for that field. "
        "Returns: liquid_balance_base, monthly figures used, income_runway_months, and "
        "a month-by-month summary (first 12 months). Starting balance is CASH ON HAND — "
        "Σ cash accounts only. It never nets margin or settlement cash; those live in "
        "investments_base and enter only with include_investments. Say 'cash on hand' "
        "for cash_balance_base."
    ),
    input_schema=schema_of(SimulateCashflowInput, tool_name="simulate_cashflow"),
)
async def simulate_cashflow(input_data: dict):
    from backend.services.cashflow import build_projection

    months_n = int(input_data.get("months") or 12)
    income_override = input_data.get("income_override")
    expense_override = input_data.get("expense_override")
    include_fixed_deposits = bool(input_data.get("include_fixed_deposits") or False)
    include_investments = bool(input_data.get("include_investments") or False)

    raw_expense_growth = input_data.get("expense_growth_pct")
    raw_income_growth = input_data.get("income_growth_pct")
    expense_growth_pct = (
        max(0.0, min(float(raw_expense_growth), 20.0)) if raw_expense_growth is not None else 0.0
    )
    income_growth_pct = (
        max(0.0, min(float(raw_income_growth), 20.0)) if raw_income_growth is not None else 0.0
    )

    include_commitments = input_data.get("include_commitments", True)
    extra_events = None
    if include_commitments:
        try:
            from backend.services import commitments as commitments_service

            extra_events = commitments_service.projected_events()
        except Exception:  # noqa: BLE001 — best-effort, never break the projection
            extra_events = None

    proj = build_projection(
        months=max(1, min(months_n, 60)),
        income_override=float(income_override) if income_override is not None else None,
        expense_override=float(expense_override) if expense_override is not None else None,
        include_fixed_deposits=include_fixed_deposits,
        include_investments=include_investments,
        expense_growth_pct=expense_growth_pct,
        income_growth_pct=income_growth_pct,
        extra_events=extra_events,
    )
    # Return a compact summary: top-level metrics + first 12 months (skip events detail)
    compact_months = [{k: v for k, v in m.items() if k != "events"} for m in proj["months"][:12]]
    # The projection already carries all three rungs, folded over the ONE book load it made.
    # Recomputing them re-read and re-folded the whole ledger three more times, and left the
    # model holding rungs that could disagree with the months it was shown beside them.
    return json.dumps(
        {
            "cash_balance_base": proj["cash_balance_base"],
            "fixed_deposit_base": proj["fixed_deposit_base"],
            "investments_base": proj["investments_base"],
            "liquid_balance_base": proj["liquid_balance_base"],
            "include": proj["include"],
            "actual_income_avg": proj["actual_income_avg"],
            "actual_expenses_avg": proj["actual_expenses_avg"],
            "monthly_income_used": proj["monthly_income_avg"],
            "monthly_expenses_used": proj["monthly_expenses_avg"],
            "income_runway_months": proj["income_runway_months"],
            "simulation_active": proj["simulation_active"],
            "baseline_source": proj["baseline_source"],
            "months": compact_months,
        },
        ensure_ascii=False,
    ), None


class GetLedgerSummaryInput(ToolInput):
    """Schema-of-record for ``get_ledger_summary`` (see ``schema_from_model``)."""

    month: Optional[int] = Field(default=None, description="1–12. Defaults to current month.")
    year: Optional[int] = Field(default=None, description="4-digit year. Defaults to current year.")


@tool(
    "get_ledger_summary",
    description=(
        "Get a full ledger summary for a month: income, expenses, investments, taxes, transfers — "
        "with credits, debits, and net for each type, plus overall total in/out/net. "
        "Use this when the user asks about cash flow, net savings, or a full financial picture. "
        "Use get_spending_summary for expense-only budget tracking."
    ),
    input_schema=schema_of(GetLedgerSummaryInput, tool_name="get_ledger_summary"),
)
async def get_ledger_summary(input_data: dict):

    from backend.services.ledger import get_ledger_summary as _ledger_svc

    today = user_today()
    m = int(input_data.get("month") or today.month)
    y = int(input_data.get("year") or today.year)
    result = _ledger_svc(month=m, year=y)
    return json.dumps(result, ensure_ascii=False), None


class GetSpendingSummaryInput(ToolInput):
    """Schema-of-record for ``get_spending_summary`` (see ``schema_from_model``)."""

    month: Optional[int] = Field(default=None, description="1–12. Defaults to current month.")
    year: Optional[int] = Field(default=None, description="4-digit year. Defaults to current year.")


@tool(
    "get_spending_summary",
    description=(
        "Get an expense-only spending summary: total spent vs budget, burn rate, "
        "category breakdown, and by-source breakdown. "
        "Use for 'how am I doing on spending?' or budget-vs-actual questions. "
        "For the full cash flow picture (income + expenses + investments), use get_ledger_summary."
    ),
    input_schema=schema_of(GetSpendingSummaryInput, tool_name="get_spending_summary"),
)
async def get_spending_summary(input_data: dict):
    # Sourced from the book's HOME budget lens (`budget_lens.home_spending_summary`), the same
    # function `reports.py` and the daily briefing use — RFC 2026-08-22-pluggable-budget-engines
    # §8.1 (docs/review/money-math-audit.md#math-24). This used to read the retired
    # `services.budgets.spending_summary` (an unrelated per-category-LIMIT engine), so on a
    # non-default budget engine Lucius could disagree with the Overview/Reports for the same
    # month. Same output shape as before, so no reshaping is needed here.

    from backend.services.budget_lens import home_spending_summary as _summary

    today = user_today()
    m = int(input_data.get("month") or today.month)
    y = int(input_data.get("year") or today.year)
    result = _summary(month=m, year=y)
    return json.dumps(result, ensure_ascii=False), None


class GetUncategorizedSummaryInput(ToolInput):
    """Schema-of-record for ``get_uncategorized_summary`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(
        default=None, description="ISO date lower bound (YYYY-MM-DD). Optional."
    )
    date_to: Optional[str] = Field(
        default=None, description="ISO date upper bound (YYYY-MM-DD). Optional."
    )
    limit: Optional[int] = Field(
        default=None,
        description="Max recent rows to include in the peek list (default 5; 0 to omit).",
    )


@tool(
    "get_uncategorized_summary",
    description=(
        "Return a count and base-currency total of expense/income transactions that have "
        "no category (None/blank) — i.e. not yet assigned a meaningful label. These appear "
        "as 'Uncategorized' in the budget. Split transactions are excluded (they carry the split "
        "sentinel top-level). Use this to answer 'how many transactions need categorizing?', "
        "'what's the total unsorted spend?', or before advising the user to label them. "
        "Optionally filter by date range; returns a small recent peek by default."
    ),
    input_schema=schema_of(GetUncategorizedSummaryInput, tool_name="get_uncategorized_summary"),
)
async def get_uncategorized_summary(input_data: dict):
    from backend.services.ledger import uncategorized_summary as _summary

    result = _summary(
        date_from=input_data.get("date_from"),
        date_to=input_data.get("date_to"),
        limit=int(input_data.get("limit") or 5) or None,
    )
    return json.dumps(result, ensure_ascii=False), None


@tool(
    "get_transactions",
    description=(
        "List ledger entries — every money movement (expense, income, transfer, investment, "
        "tax). Use date_from/date_to for ranges, month/year for a single month.\n\n"
        "FILTER SERVER-SIDE (account_id / payee_ids / categories / min_amount) rather than "
        "fetching everything and narrowing it yourself — 'my credit card spending' means "
        "account_id (from list_accounts).\n\n"
        "FILTER ONCE, NOT N TIMES: categories, transaction_types, tags, payee_ids and "
        "account_ids each accept a LIST as well as a single value (OR'd within a field). "
        "'What did I spend across my three cards' is ONE call with account_ids=[a,b,c] — "
        "never three calls.\n\n"
        "The response carries a `summary` block (total_expenses_base, total_income_base, "
        "net_base) already FX-converted to base currency and computed over the FULL filtered "
        "set, not just the returned page. Report those figures — summing the returned rows "
        "yourself gets FX conversion, two-leg selection AND pagination wrong.\n\n"
        "Returns 25 rows by default. When the question is 'how much' rather than 'which "
        "rows', pass summary_only=true — the totals are identical and it costs a fraction "
        "of the context. A partial page always says so in summary.page_note; never report a "
        "total derived from a page that does."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": _txn_schema.filter_properties(),
        "required": [],
    },
)
async def get_transactions(input_data: dict):

    from backend.services.ledger import get_transaction as _get_txn
    from backend.services.ledger import get_transactions as _get_txns

    txn_id = input_data.get("transaction_id") or None
    if txn_id:
        txn = _get_txn(txn_id)
        if txn is None:
            return json.dumps({"error": f"Transaction {txn_id} not found"}), None
        return json.dumps({"transaction": txn}, ensure_ascii=False), None
    today = user_today()
    date_from = input_data.get("date_from") or None
    date_to = input_data.get("date_to") or None
    # An account filter given by NAME is resolved like every other account ref (the model names
    # accounts the way the user does). Matched raw, a name hit nothing and came back as "0
    # transactions" — no error — so a card with 60 rows read as empty (QA 2026-10-13). An
    # unknown or ambiguous name is an error naming the candidates, never a silent zero.
    try:
        account_id = (
            _validate_account_id(input_data["account_id"]) if input_data.get("account_id") else None
        )
        account_ids = _as_filter_list(input_data.get("account_ids"))
        if account_ids is not None:
            refs = [account_ids] if isinstance(account_ids, str) else account_ids
            resolved = [_validate_account_id(ref, "account_ids") for ref in refs]
            account_ids = resolved[0] if len(resolved) == 1 else resolved
    except ValueError as exc:
        return json.dumps({"error": str(exc)}), None
    payee_id = _dual_filter(input_data, "payee_ids", "payee_id")
    q = (input_data.get("q") or "").strip() or None
    q_field = (input_data.get("q_field") or "any").strip() or "any"
    tag = _dual_filter(input_data, "tags", "tag")
    # Auto-map: if the model passed a kind name as category, treat it as transaction_type.
    # This handles the common model mistake of passing category='transfer'/'income'/etc.
    # after the old category namespace was removed.
    _ALL_KINDS = {"expense", "income", "transfer", "adjustment", "investment"}
    _raw_category = _dual_filter(input_data, "categories", "category")
    _raw_txn_type = _dual_filter(input_data, "transaction_types", "transaction_type")
    # The kind-passed-as-category auto-map applies to the single-value form only; a caller
    # that already sent a list is being deliberate about the field it chose.
    if isinstance(_raw_category, str) and _raw_category in _ALL_KINDS and not _raw_txn_type:
        _raw_txn_type = _raw_category
        _raw_category = None
    has_amount_filter = (
        input_data.get("min_amount") is not None or input_data.get("max_amount") is not None
    )
    has_search_filter = bool(q) or bool(tag)
    has_account_filter = bool(account_id) or bool(account_ids)
    raw_review = input_data.get("needs_review")
    needs_review = (
        None
        if raw_review is None
        else raw_review
        if isinstance(raw_review, bool)
        else str(raw_review).strip().lower() in ("true", "1", "yes")
    )
    # Don't default month/year when fetching by account_id (all-time), when an
    # explicit date range is given, or when an amount/text/tag/review filter is set without
    # an explicit month/year ("find big transactions" / "every uber charge" / "what's in my
    # review queue" is all-time unless dated) — let ledger.py's skip_date_filter kick in.
    if (
        date_from
        or has_account_filter
        or payee_id
        or (
            (has_amount_filter or has_search_filter or needs_review is not None)
            and not input_data.get("month")
            and not input_data.get("year")
        )
    ):
        m, y = None, None
    else:
        m = int(input_data["month"]) if input_data.get("month") else today.month
        y = int(input_data["year"]) if input_data.get("year") else today.year
    min_amount = input_data.get("min_amount")
    max_amount = input_data.get("max_amount")
    summary_only = bool(input_data.get("summary_only"))
    if summary_only:
        # Run the SAME query, then drop the rows. The service computes its money totals
        # over the full filtered set BEFORE paging (_txn_base_totals), so summary_only is
        # exact, not an approximation. We ask for 1 row rather than 0 because a falsy
        # limit takes ledger.py's unpaginated branch, which omits the totals entirely and
        # would silently fall back to the tool's page-sum path.
        limit, offset = 1, 0
    else:
        raw_limit = input_data.get("limit")
        limit = int(raw_limit) if raw_limit is not None else _b.DEFAULT_TXN_LIMIT
        offset = int(input_data["offset"]) if input_data.get("offset") is not None else 0
    logger.info(
        "tool get_transactions: month=%s year=%s kind=%s category=%s account_id=%s account_ids=%s payee_id=%s date_from=%s date_to=%s min_amount=%s max_amount=%s q=%s q_field=%s tag=%s limit=%s offset=%s",
        m,
        y,
        _raw_txn_type,
        _raw_category,
        account_id,
        account_ids,
        payee_id,
        date_from,
        date_to,
        min_amount,
        max_amount,
        q,
        q_field,
        tag,
        limit,
        offset,
    )
    # OFF THE LOOP. This is an async tool, but the service call under it is fully
    # synchronous — a whole-ledger scan through _apply_q_filter plus label_maps — so calling
    # it directly held the event loop for its entire duration. During an Automation run that
    # froze the whole app: /runs, /live, /signals and /portfolio all sat pending at once
    # while one tool ran (captured by core.loop_watch on 2026-08-11, and visible in the
    # browser as requests with no response).
    txns = await asyncio.to_thread(
        functools.partial(
            _get_txns,
            month=m,
            year=y,
            kind=_raw_txn_type,
            category=_raw_category,
            account_id=account_id,
            account_ids=account_ids,
            payee_id=payee_id,
            date_from=date_from,
            date_to=date_to,
            min_amount=min_amount,
            max_amount=max_amount,
            q=q,
            q_field=q_field,
            tag=tag,
            needs_review=needs_review,
            limit=limit,
            offset=offset,
        )
    )
    txn_list = txns.get("transactions", [])
    total = txns.get("total", len(txn_list))
    has_more = txns.get("has_more", False)
    logger.info("tool get_transactions: returned %s/%s transactions", len(txn_list), total)

    # Money totals come from the service, computed over the FULL filtered set
    # before pagination — a page slice must never change reported money math.
    # Page-sum fallback covers only the unpaginated response shape (no limit).
    base_cur = svc.base_currency()
    if "total_income_base" in txns:
        total_in = txns["total_income_base"]
        total_out = txns["total_expenses_base"]
    else:
        total_in = sum(
            abs(float(t["base_amount"]))
            for t in txn_list
            if t.get("kind") == "income" and t.get("base_amount") is not None
        )
        total_out = sum(
            abs(float(t["base_amount"]))
            for t in txn_list
            if t.get("kind") == "expense" and t.get("base_amount") is not None
        )
    end = offset + len(txn_list)
    page_note = (
        # Naming the narrowing filters matters: a model that only learns "there are more"
        # tends to page blindly (or worse, total the page it has). Point it at the filters
        # that make the read small AND at the totals that are already exact.
        #
        # The second sentence is scoped HARD to "how much" on purpose. It used to end "the
        # summary figures below already cover all N", full stop — which is true of totals and
        # of nothing else, but reads as general reassurance. Asked to "check my last 5 days and
        # flag anything", a model took one 25-of-40 page, never paged, and answered "nothing
        # duplicated" — over 15 rows it had not read, while two duplicate pairs sat astride the
        # page boundary (owner, 2026-08-16). A guardrail against over-totalling had become
        # permission to under-read, so the row-level case now gets its own refusal.
        f"Showing rows {offset + 1}–{end} of {total}. This page is PARTIAL. "
        f"For a 'how much' question, never total these rows — the summary figures below are "
        f"already exact over all {total}. For ANY question about the rows themselves "
        f"(duplicates, anomalies, 'check these', 'flag anything', 'is there a X'), the summary "
        f"tells you NOTHING and you have not seen {total - end} of the {total} matching rows: "
        f"page with offset until has_more is false before you answer, and never state that "
        f"something is absent from a set you have only partly read. To keep the read small, "
        f"narrow with date_from/date_to, account_id, categories or q."
        if has_more
        else None
    )
    summary = {
        "count": total,
        "showing": 0 if summary_only else len(txn_list),
        "offset": offset,
        "has_more": has_more,
        "total_expenses_base": round(total_out, 2),
        "total_income_base": round(total_in, 2),
        "net_base": round(total_in - total_out, 2),
        "base_currency": base_cur,
        "note": "Totals use base_amount (FX-converted). Transfers and adjustments are excluded from totals.",
    }
    if summary_only:
        summary["page_note"] = (
            f"summary_only: no rows returned. These totals cover all {total} matching "
            "transactions and are exact. Re-call without summary_only to see rows."
        )
        return json.dumps({"summary": summary}, ensure_ascii=False), None
    if page_note:
        summary["page_note"] = page_note
    # Built ONCE for the whole page (never a per-row store lookup) and passed down. An empty
    # page skips the three store reads entirely — there is nothing to label.
    maps = labels.label_maps(categories=True, payees=True, accounts=True) if txn_list else None
    projected = [_project_txn(t, maps) for t in txn_list]
    labels.attach_traded(projected)
    result = {"summary": summary, "transactions": projected}
    return json.dumps(result, ensure_ascii=False), None


def _as_filter_list(value):
    """Normalise a filter that may arrive as a bare string OR a list.

    Both shapes are kept deliberately. Stored skills and agent playbooks reference the
    singular parameter names, and a silently-dropped filter is far worse than no filter —
    it returns plausible rows for the wrong question. A blank/empty value becomes None so
    the downstream "was a filter supplied" checks stay simple.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if not cleaned:
            return None
        # Collapse a one-element list to the scalar form so downstream logging and the
        # kind-as-category auto-map see the shape they already understood.
        return cleaned[0] if len(cleaned) == 1 else cleaned
    return value


def _dual_filter(input_data: dict, new_key: str, old_key: str):
    """Read a ``get_transactions`` filter under its current PLURAL name, falling back to
    the pre-2026-07-19b singular name (``category``/``transaction_type``/``payee_id``/
    ``tag``, each renamed to ``categories``/``transaction_types``/``payee_ids``/``tags`` —
    a singular name inviting a list is the exact trap that got ``delete_account``'s
    ``account_id`` stringified).

    A param rename needs no alias-table entry the way a TOOL rename does — just this
    read-side tolerance, so a stored skill/agent playbook or an in-flight session still on
    the old key keeps working (this tool is ~34% of sessions in the corpus; the highest-
    traffic reason to keep the old key alive of anything in this pass).
    """
    raw = input_data.get(new_key)
    if raw is None:
        raw = input_data.get(old_key)
    return _as_filter_list(raw)


def _project_txn(t: dict, maps: labels.MapSet | None = None) -> dict:
    """Return the full stored transaction row. A prior curated allowlist here silently hid
    ticker/quantity/needs_review/taxable/source/etc. from every agent that reads the ledger
    (root-caused 2026-07-09) — return everything and let callers ignore what they don't need.

    ``maps`` is the server-built id->label table (``services.labels.label_maps``); when
    supplied, the category/payee/account LABELS are attached beside their opaque ids. That
    is purely ADDITIVE — it selects nothing away, so the 2026-07-09 scar above stands: this
    must never grow a caller-chosen fields/projection parameter."""
    return labels.label_txn(t, maps)


# Per-item PATCH schema for update_transactions — one row's changes, keyed by
# transaction_id. Built from the same shared field table every ledger tool uses
# (_txn_schema.update_properties() already includes transaction_id), so the single-object
# and array forms below describe an identical row shape — no separate prose to drift.
_TXN_UPDATE_ITEM_SCHEMA = {
    "type": "object",
    "properties": _txn_schema.update_properties(),
    "required": ["transaction_id"],
}

# The pre-merge `bulk_update_transactions` shape's shared-change fields — recognized (NOT
# advertised on the new schema) when a legacy caller sends a bare `transaction_ids` list plus
# these at the top level instead of the new `updates` shape, so a stored playbook calling the
# old name keeps working. Routed straight through the unchanged services.ledger.
# bulk_update_transactions choke (see _update_legacy_bulk) rather than decomposed into
# per-row generic edits, because that function's additive-tags-union, kind-aware
# skip-with-reason (adjustment/category, transfer/payee) and auto-move `to_account_id`
# sugar (re-points whichever leg applies by kind) have no equivalent on the generic
# per-row path and must not silently change behavior for an existing caller.
_LEGACY_BULK_UPDATE_FIELDS = (
    "category",
    "tags",
    "cleared",
    "payee_id",
    "needs_review",
    "to_account_id",
    "description",
    "memo",
)

# How many rows of a bulk update the confirm card renders — mirrors
# ledger_preview._BULK_SAMPLE_ROWS: one card for N rows, sample capped, totals over all N.
_BULK_UPDATE_SAMPLE_ROWS = 12

# The per-row patch fields update_transactions passes straight through to
# services.ledger.update_transaction. `cleared` is deliberately excluded — it never flows
# through that generic rebuild choke, it has its own (services.ledger.set_transaction_cleared,
# with a narrower Starting-Balance guard: cleared=true is allowed, cleared=false is not).
_UPDATE_FIELD_KEYS = (
    "date",
    "kind",
    "category",
    "description",
    "memo",
    "from_account_id",
    "from_amount",
    "from_currency",
    "to_account_id",
    "to_amount",
    "to_currency",
    "original_amount",
    "original_currency",
    "tags",
    "splits",
    "payee_id",
    "needs_review",
    "external_refs",
)


def _split_update_item(item: dict) -> tuple[Optional[str], dict, Optional[bool]]:
    """One ``updates`` item -> (transaction_id, non-cleared fields, cleared value-or-None)."""
    txn_id = item.get("transaction_id")
    fields = {k: item[k] for k in _UPDATE_FIELD_KEYS if k in item}
    cleared_value = bool(item["cleared"]) if "cleared" in item else None
    return txn_id, fields, cleared_value


_APPROVE_VIA_UPDATE_REFUSAL = (
    "Refused: update_transactions does not approve. Approving is its own call — use "
    "approve_transactions with the transaction id(s) (load it with find_tools names="
    '["approve_transactions"] if you do not have it); the user confirms it. NOTHING in this '
    "call was applied to the row. If the rest of your edit is still wanted, send it again "
    "WITHOUT needs_review. needs_review=true (re-flag) is still accepted here."
)


def _approval_via_update_refusal(fields: dict) -> Optional[str]:
    """The message for a patch that tries to CLEAR the review flag through the generic edit.

    Inside an Automation the reason is different (nobody can approve there — see
    ``review_approval``), and that refusal fires at the choke, so this steps aside rather than
    send the agent to a tool that would refuse it too."""
    if "needs_review" not in fields or fields["needs_review"]:
        return None
    if review_approval.acting_as_automation():
        return None
    return _APPROVE_VIA_UPDATE_REFUSAL


def _preview_one_update(txn_id: str, fields: dict, cleared_value: Optional[bool]) -> dict:
    """PURE preview of one row's patch — extends ``ledger_preview.preview_update`` with the
    two fields it cannot resolve. ``cleared``/``needs_review`` never flow through
    ``create_transaction``'s rebuild (cleared has its own choke entirely; needs_review flows
    through but ``preview_update``'s ``_DIFF_FIELDS`` walk doesn't enumerate it), so neither
    shows up in the card's diff unless added here. Not resolved inside ``ledger_preview``
    itself — out of this lane's edit scope, and a hand-computed before/after for two booleans
    needs nothing that module's category/account/payee resolution offers.

    Refuses what the write itself would refuse for an Automation (clearing the review flag), so a
    manual-approval run is never shown a card for an edit that can only fail once tapped."""
    existing = svc.transactions.find_by_id(txn_id)
    if existing is not None:
        try:
            review_approval.refuse_automation_approval(txn_id, existing, fields)
        except OpenNetWorthError as e:
            return {"error": e.message}
    refusal = _approval_via_update_refusal(fields)
    if refusal:
        return {"error": refusal}
    change = ledger_preview.preview_update(txn_id, fields)
    if change.get("error"):
        return change
    extra: list[dict] = []
    if existing is not None:
        if cleared_value is not None:
            before, after = bool(existing.get("cleared")), bool(cleared_value)
            if before != after:
                extra.append(
                    {"field": "cleared", "label": "Cleared", "before": before, "after": after}
                )
        if "needs_review" in fields:
            before = bool(existing.get("needs_review"))
            after = bool(fields["needs_review"])
            if before != after:
                extra.append(
                    {
                        "field": "needs_review",
                        "label": "Needs review",
                        "before": before,
                        "after": after,
                    }
                )
    if extra:
        change = {**change, "fields": [*change.get("fields", []), *extra]}
    return change


def _preview_bulk_update(items: list[dict]) -> dict:
    """Preview MANY row patches as ONE card — mirrors ``ledger_preview.preview_bulk``'s
    shape for creates: a resolved sample capped at ``_BULK_UPDATE_SAMPLE_ROWS`` plus an
    aggregate tally of which fields changed on how many rows. Loops the same PURE
    ``_preview_one_update`` each row would get individually — no store write (ADR 2026-07-18
    previews-must-not-write).

    A row carrying no field that could change it (only ``transaction_id``) is not previewed
    at all — there is nothing to resolve or diff — and is reported separately in ``no_ops``
    rather than counted into ``count``/shown in the sample with an empty diff. That is what
    lets ``update_transactions`` decide whether there is anything left to card: ``count``
    reflects only rows that would actually change something (see that function's own
    handling of ``count == 0``)."""
    sample: list[dict] = []
    errors: list[dict] = []
    no_ops: list[str] = []
    field_labels: dict[str, str] = {}
    field_counts: dict[str, int] = {}
    ok_count = 0
    for item in items:
        txn_id, fields, cleared_value = _split_update_item(item)
        if not txn_id:
            errors.append({"transaction_id": None, "error": "transaction_id is required"})
            continue
        if not fields and cleared_value is None:
            no_ops.append(txn_id)
            continue
        try:
            change = _preview_one_update(txn_id, fields, cleared_value)
        except (ValueError, OpenNetWorthError) as e:
            # Mirrors _update_one's own preview guard: resolution can raise (an unknown
            # category on this row), and one bad row must report as a row error here —
            # never escape uncaught and crash the whole batch's preview.
            errors.append({"transaction_id": txn_id, "error": str(e)})
            continue
        if change.get("error"):
            errors.append({"transaction_id": txn_id, "error": change["error"]})
            continue
        ok_count += 1
        for f in change.get("fields", []):
            field_labels[f["field"]] = f["label"]
            field_counts[f["field"]] = field_counts.get(f["field"], 0) + 1
        if len(sample) < _BULK_UPDATE_SAMPLE_ROWS:
            sample.append(
                {
                    "transaction_id": txn_id,
                    "date": change.get("date"),
                    "description": change.get("description") or "",
                    "fields": change.get("fields", []),
                }
            )
    return {
        "type": "txn_bulk_update",
        "count": ok_count,
        "rows": sample,
        "truncated": len(sample) < ok_count,
        "field_changes": [
            {"field": f, "label": field_labels[f], "count": c}
            for f, c in sorted(field_counts.items(), key=lambda kv: -kv[1])
        ],
        "errors": errors,
        "no_ops": no_ops,
    }


def _bulk_update_preview_error_result(change: dict) -> tuple[str, None]:
    """A batch where one or more rows can't be previewed aborts wholesale — same atomicity
    contract add_transactions' Phase-1 validation gives creates: fix everything, then
    re-submit the full list, rather than staging a card for a batch the model hasn't
    actually validated yet."""
    errs = change.get("errors", [])
    return (
        json.dumps(
            {
                "ok": False,
                "errors": errs,
                "error_note": (
                    f"{len(errs)} row(s) failed to preview — nothing was changed. Fix the "
                    "transaction_id/field and re-submit the full batch."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


def _txn_vanished_result(txn_id: str) -> tuple[str, None]:
    """The service returns None for a row that no longer exists. Reporting ok=True with a
    null transaction (the pre-confirm-gate behavior) told the model an edit had landed when
    nothing had — the gate widens the window in which a row can disappear between preview
    and ✓, so the honest answer matters more now."""
    logger.warning("tool update_transactions: txn_id=%s no longer exists", txn_id)
    return (
        json.dumps(
            {
                "ok": False,
                "error": (
                    f"transaction '{txn_id}' no longer exists (deleted or merged away) — "
                    "nothing was changed. Call get_transactions to find the current row."
                ),
            },
            ensure_ascii=False,
        ),
        None,
    )


def _no_op_update_result(existing: dict) -> dict:
    """The honest result for a per-row patch carrying NO field that could change the row —
    only ``transaction_id``, nothing else (no ``cleared``, no ``needs_review``, no other
    patch key). Reporting plain ``ok: true`` here (the pre-fix behavior) is byte-
    indistinguishable from a write that actually landed — the live incident this fixes: the
    owner asked Lucius to "approve the remaining one" (clear a review flag), the model called
    this tool with only the id, got ``ok:true`` back, and reported "Done" for a write that
    changed nothing, so the owner had to ask again. ``ok`` stays true (a well-formed call, not
    an error) but ``no_op: true`` plus the message is what stops that narration.

    Deliberately STRUCTURAL (presence of a patch key), not value-equality (a patch whose
    fields already match the stored row) — the latter is cheap to add here too but was left
    out: it would need its own before/after comparison per field (splits/tags are lists, not
    scalars) with no test coverage forcing it, for a case this incident never actually hit.
    The structurally-empty case is the reported bug and the priority."""
    return {
        "ok": True,
        "no_op": True,
        "transaction": existing,
        "message": (
            "No changeable fields were provided, so this transaction was NOT modified — an "
            "update needs at least one field to set besides transaction_id. If you meant to "
            "change something, re-send with the field(s) you intended to set."
        ),
    }


async def _update_one(input_data: dict, *, gated: bool = False, promised: dict | None = None):
    """Apply ONE patch — the single-row path of ``update_transactions``.

    Deliberately mirrors ``add_transactions``' ``_add_one``: a lone item never touches the
    array/bulk machinery below, so a single edit stays a ``txn_update``-type card (the full
    resolved before/after this tool has always shown) instead of a generic
    ``txn_bulk_update`` card with ``count=1``.
    """
    txn_id, fields, cleared_value = _split_update_item(input_data)
    if not txn_id:
        return json.dumps({"ok": False, "error": "transaction_id is required"}), None
    refusal = _approval_via_update_refusal(fields)
    if refusal:
        return json.dumps({"ok": False, "error": refusal}), None
    has_cleared = "cleared" in input_data

    # A structurally-empty patch — only transaction_id, no `cleared`/`needs_review`/other
    # field — cannot change the row no matter what runs next. Short-circuit BEFORE the confirm
    # gate would stage a card for it: the pre-fix gate built a `txn_update` card whose
    # "Resulting transaction" was just the row's unchanged current state — a confirmation
    # card for a write that alters nothing. Report honestly instead of either a fake-success
    # write (the reported incident) or a no-op card.
    if not fields and not has_cleared:
        existing = svc.transactions.find_by_id(txn_id)
        if existing is None:
            return _txn_vanished_result(txn_id)
        return json.dumps(_no_op_update_result(existing), ensure_ascii=False), None

    # Confirm gate, step 1 — the resolved before → after diff. This is the tool the forensics
    # singled out (39 redos in 231 calls): an edit whose category is silently re-decided by a
    # rule or a payee default is exactly what the user was rejecting after the fact.
    if gated or promised is not None:
        try:
            change = _preview_one_update(txn_id, fields, cleared_value)
        except (ValueError, OpenNetWorthError) as e:
            return _gate.preview_failed_result(str(e))
        if change.get("error"):
            return _gate.preview_failed_result(change["error"])
        if gated:
            return _gate.stage("update_transactions", input_data, change)
        if _gate.outcome_of(change) != promised:
            return _gate.outcome_mismatch_result("update_transactions")

    # Unattended-write guard: evaluate the RESULTING row (existing merged with this patch)
    # against the running agent's envelope. Self-guards on is_unattended() — a no-op on every
    # interactive/connector/skill turn. (`fields or has_cleared` always holds here — the
    # structurally-empty case above has already returned.)
    existing_for_guard = svc.transactions.find_by_id(txn_id)
    if existing_for_guard is not None:
        guard = await _check_unattended_envelope(
            "update_transactions", {**existing_for_guard, **fields}
        )
        if guard is not None:
            return guard

    try:
        updated = None
        if fields:
            updated = svc.update_transaction(txn_id, fields)
            if updated is None:
                return _txn_vanished_result(txn_id)
        if has_cleared:
            updated = svc.set_transaction_cleared(txn_id, cleared_value)
            if updated is None:
                return _txn_vanished_result(txn_id)
        logger.info(
            "tool update_transactions: txn_id=%s fields=%s cleared=%s",
            txn_id,
            list(fields.keys()),
            cleared_value,
        )
        # Labelled for the same reason as the create path: the commonest unattended update IS a
        # payee re-link, and a bare payee_id back cannot confirm the row now shows the clean
        # name — so the fix could not be verified without another read.
        labelled_update = labels.label_txn(
            updated, labels.label_maps(categories=True, payees=True, accounts=True)
        )
        update_out: dict = {"ok": True, "transaction": labelled_update}
        conversion_warning = _currency_conversion_warning(labelled_update)
        if conversion_warning:
            update_out["warnings"] = [conversion_warning]
        return (
            json.dumps(update_out, ensure_ascii=False),
            None,
        )
    except Exception as e:
        logger.warning("tool update_transactions: error txn_id=%s: %s", txn_id, e)
        return json.dumps({"ok": False, "error": str(e)}), None


async def _update_legacy_bulk(
    input_data: dict, ids: list, *, gated: bool, promised: dict | None
) -> tuple[str, dict | None]:
    """The pre-merge ``bulk_update_transactions`` shape: a bare ``transaction_ids`` list plus
    ONE shared change-set. Kept reachable via the alias (never advertised on the new schema)
    so a stored skill/agent calling it by the old name+shape is unaffected by the merge — see
    ``_LEGACY_BULK_UPDATE_FIELDS`` for why the write routes through the untouched service
    function rather than the new per-row loop.

    KNOWN GAP, deliberately left open: this path is NOT wired to the unattended-write
    envelope. It is alias-only (never advertised on the current schema) and writes via one
    atomic ``svc.bulk_update_transactions(ids, changes)`` call rather than a per-row loop, so
    per-row envelope filtering would need its own id-splitting logic rather than reusing the
    ``_check_unattended_envelope`` call already threaded through ``_update_one`` and the
    per-row ``update_transactions`` loop above. Flagged, not fixed, in this change."""
    ids = [tid for tid in ids if tid]
    changes = {k: input_data[k] for k in _LEGACY_BULK_UPDATE_FIELDS if k in input_data}
    if not changes:
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": "Supply at least one of: " + ", ".join(_LEGACY_BULK_UPDATE_FIELDS),
                }
            ),
            None,
        )
    refusal = _approval_via_update_refusal(changes)
    if refusal:
        return json.dumps({"ok": False, "error": refusal}), None

    if gated or promised is not None:
        items = [{"transaction_id": tid, **changes} for tid in ids]
        change = _preview_bulk_update(items)
        if change.get("errors"):
            return _bulk_update_preview_error_result(change)
        if gated:
            return _gate.stage("update_transactions", input_data, change)
        if _gate.outcome_of(change) != promised:
            return _gate.outcome_mismatch_result("update_transactions")

    try:
        result = svc.bulk_update_transactions(ids, changes)
    except Exception as e:  # noqa: BLE001 — report to the model, not a stack trace
        logger.warning("tool update_transactions: legacy bulk error: %s", e)
        return json.dumps({"ok": False, "error": str(e)}), None
    logger.info(
        "tool update_transactions: legacy bulk updated=%d not_found=%d skipped=%d",
        result["count"],
        len(result["not_found"]),
        len(result["skipped"]),
    )
    return json.dumps({"ok": True, **result}, ensure_ascii=False), None


@tool(
    "update_transactions",
    description=(
        "Update fields on one or many existing ledger entries. Pass ONE item in `updates` "
        "for a single edit (the common case), or many — one per row, each with its OWN "
        "changes — to edit several rows in one call: repeat the same fields in every item "
        "for the SAME change on every row, or vary them for a different change per row. "
        "Call get_transactions first to find the transaction id(s). Only the fields supplied "
        "on a row change — everything else on it is left as-is. An item with ONLY "
        "transaction_id and no other field changes NOTHING — reported as a no-op, never "
        "silent success. To APPROVE flagged rows use approve_transactions, not this tool "
        "(needs_review=true here re-flags a row; false is refused).\n\n"
        "Interactive turns PREVIEW first: ONE confirmation card (single edit or the whole "
        "batch) showing the resolved before/after per field — including a category a spend "
        "rule or payee default would silently change — and write nothing until confirmed.\n\n"
        "REFUSES an account's Starting Balance row (source='opening') for any field except "
        "`cleared=true` — change its opening balance/date via update_accounts instead."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "updates": {
                "type": "array",
                "minItems": 1,
                "description": "The edits to apply. One item is the common case.",
                "items": _TXN_UPDATE_ITEM_SCHEMA,
            },
        },
        "required": ["updates"],
    },
    mutates=("ledger",),
    # Former names, kept resolvable for stored agent/skill allowlists and in-flight
    # transcripts — the same convention add_transactions' own merge used. Zero prompt cost;
    # aliases never appear in build_tool_schemas().
    self_stages=True,
)
async def update_transactions(input_data: dict):
    # Confirm gate, step 2 — the ✓ path. Stored args win over anything re-sent on this call.
    gated = False
    promised: dict | None = None
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "update_transactions")
        if entry is None:
            return _gate.unknown_token_result("update_transactions")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = _gate.should_gate("update_transactions")

    legacy_ids = input_data.get("transaction_ids")
    if isinstance(legacy_ids, list) and legacy_ids:
        return await _update_legacy_bulk(input_data, legacy_ids, gated=gated, promised=promised)

    raw_updates = input_data.get("updates")
    if raw_updates is not None:
        items = raw_updates if isinstance(raw_updates, list) else [raw_updates]
    else:
        # Bare legacy `update_transaction` shape: fields directly at the top level, no
        # wrapper — the pre-merge single-row tool's only shape, and still how a caller
        # updating one row is expected to call this tool (trivial, no array ceremony).
        bare = {k: v for k, v in input_data.items() if k != _gate.CONFIRM_TOKEN_KEY}
        items = [bare] if bare.get("transaction_id") else []

    if not items:
        return json.dumps({"ok": False, "error": "updates (or transaction_id) is required"}), None

    # A lone item takes the single-edit path — full resolved diff, not a generic bulk card.
    if len(items) == 1:
        return await _update_one(items[0], gated=gated, promised=promised)

    # Confirm gate (multi-row): ONE card for the whole batch — never one per row. A row
    # carrying no field that could change it doesn't count toward `count` (see
    # `_preview_bulk_update`); if NONE of the submitted rows would change anything, there is
    # nothing to confirm — fall through to the execution loop below, which reports each such
    # row honestly as a no-op rather than staging a card for a write that alters nothing.
    if gated or promised is not None:
        change = _preview_bulk_update(items)
        if change.get("errors"):
            return _bulk_update_preview_error_result(change)
        if promised is not None and _gate.outcome_of(change) != promised:
            return _gate.outcome_mismatch_result("update_transactions")
        if gated and change.get("count", 0) > 0:
            return _gate.stage("update_transactions", input_data, change)

    results: list[dict] = []
    updated_count = 0
    no_op_count = 0
    for item in items:
        txn_id, fields, cleared_value = _split_update_item(item)
        if not txn_id:
            results.append({"transaction_id": None, "error": "transaction_id is required"})
            continue
        refusal = _approval_via_update_refusal(fields)
        if refusal:
            results.append({"transaction_id": txn_id, "error": refusal})
            continue
        has_cleared = "cleared" in item
        is_no_op = not fields and not has_cleared
        # Unattended-write guard (PER ROW): mirrors the bulk-add loop above — a row outside
        # the envelope is excluded, the rest of the batch still books. Self-guards on
        # is_unattended(), so this is a no-op on every interactive/connector/skill turn.
        # Skipped for a structurally-empty row — there is nothing it could write.
        if not is_no_op:
            existing_for_guard = svc.transactions.find_by_id(txn_id)
            if existing_for_guard is not None:
                guard = await _check_unattended_envelope(
                    "update_transactions", {**existing_for_guard, **fields}
                )
                if guard is not None:
                    results.append(
                        {"transaction_id": txn_id, "error": json.loads(guard[0])["error"]}
                    )
                    continue
        try:
            updated = (
                svc.update_transaction(txn_id, fields)
                if fields
                else svc.transactions.find_by_id(txn_id)
            )
            if has_cleared:
                updated = svc.set_transaction_cleared(txn_id, cleared_value)
            if updated is None:
                results.append(
                    {
                        "transaction_id": txn_id,
                        "error": f"transaction '{txn_id}' no longer exists",
                    }
                )
                continue
            if is_no_op:
                # No field on this row could have changed anything — report it honestly
                # rather than the silent `ok: true` that made this the reported incident:
                # only `transaction_id` was submitted, so nothing was written.
                results.append({"transaction_id": txn_id, "ok": True, "no_op": True})
                no_op_count += 1
            else:
                results.append({"transaction_id": txn_id, "ok": True})
                updated_count += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the rest of the batch
            logger.warning("tool update_transactions: error txn_id=%s: %s", txn_id, e)
            results.append({"transaction_id": txn_id, "error": str(e)})

    logger.info(
        "tool update_transactions: submitted=%d updated=%d no_op=%d",
        len(items),
        updated_count,
        no_op_count,
    )
    result_body: dict = {
        "ok": True,
        "updated": updated_count,
        "total_submitted": len(items),
        "results": results,
    }
    if no_op_count:
        result_body["no_op_count"] = no_op_count
        result_body["note"] = (
            f"{no_op_count} of {len(items)} row(s) carried no field that could change "
            "anything and were left as-is — see each row's own `no_op` flag in results."
        )
    return json.dumps(result_body, ensure_ascii=False), None


class DeleteTransactionsInput(ToolInput):
    """Schema-of-record for ``delete_transactions`` (see ``schema_from_model``)."""

    transaction_ids: List[str] = Field(min_length=1, description="One or more UUIDs to delete")
    reason: Optional[str] = Field(default=None, description="Why they're being deleted")


@tool(
    "delete_transactions",
    description=(
        "Soft-delete one or more ledger entries by ID. "
        "Call get_transactions first to find the IDs. "
        "Use for duplicate entries, rows logged in error, or bulk cleanup. "
        "REFUSES the whole batch if any id is an account's Starting Balance row "
        "(source='opening') — change the opening balance via update_accounts instead."
    ),
    input_schema=schema_of(DeleteTransactionsInput, tool_name="delete_transactions"),
    mutates=("ledger",),
)
async def delete_transactions(input_data: dict):
    ids = [
        tid
        for tid in (input_data.get("transaction_ids") or [input_data.get("transaction_id")])
        if tid
    ]
    logger.info("tool delete_transactions: requested ids=%d", len(ids))
    # Route through the ledger service, never TransactionStore.bulk_soft_delete: the service
    # is the choke point that refuses a Starting Balance row and emits the batched "delete"
    # audit event. The raw store method does neither.
    try:
        result = svc.bulk_delete_transactions(ids)
    except OpenNetWorthError as e:
        logger.warning("tool delete_transactions: refused: %s", e)
        return json.dumps({"ok": False, "error": e.message or str(e)}, ensure_ascii=False), None
    deleted, not_found = result["deleted"], result["not_found"]
    logger.info("tool delete_transactions: deleted=%d not_found=%d", len(deleted), len(not_found))
    if not_found:
        logger.warning("tool delete_transactions: not_found=%s", not_found)
    return json.dumps(
        {"ok": True, "deleted": len(deleted), "deleted_ids": deleted, "not_found": not_found}
    ), None


class DeleteTransactionsWhereInput(ToolInput):
    """Schema-of-record for ``delete_transactions_where`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(
        default=None, description="Delete transactions on or after this date (YYYY-MM-DD)."
    )
    date_to: Optional[str] = Field(
        default=None, description="Delete transactions on or before this date (YYYY-MM-DD)."
    )
    account_id: Optional[str] = Field(
        default=None, description="Only delete transactions involving this account (either leg)."
    )
    kind: Optional[Literal["expense", "income", "transfer", "adjustment", "investment"]] = Field(
        default=None
    )
    category: Optional[str] = Field(default=None)
    batch_id: Optional[str] = Field(
        default=None, description="Only delete transactions from this import batch."
    )
    reason: Optional[str] = Field(
        default=None, description="Why these transactions are being deleted."
    )
    preview: Optional[bool] = Field(
        default=None,
        description=(
            "Pass true to see what WOULD be deleted — how many rows match, and a sample — "
            "without deleting anything. Useful when you are unsure a filter is right."
        ),
    )


@tool(
    "delete_transactions_where",
    description=(
        "Bulk soft-delete transactions matching a set of filters in one call — no need to fetch IDs first. "
        "Use for: wiping a date range, removing all transactions for an account, cleaning up by kind/category. "
        "At least one filter must be supplied. All supplied filters are ANDed together."
    ),
    input_schema=schema_of_no_required(
        DeleteTransactionsWhereInput, tool_name="delete_transactions_where"
    ),
    mutates=("ledger",),
)
async def delete_transactions_where(input_data: dict):
    filters = {
        k: input_data.get(k) or None
        for k in ("date_from", "date_to", "account_id", "kind", "category", "batch_id")
    }
    guard = await _gate.enforce_unattended_write(
        "delete_transactions_where",
        account_id=filters.get("account_id"),
        kind=filters.get("kind"),
        amount=None,
        category_id=filters.get("category"),
    )
    if guard is not None:
        return guard
    # OPT-IN PREVIEW. This is the one ledger write whose SELECTION the caller cannot verify
    # from the arguments: they name a FILTER, not rows — which is why the Auto judge is told
    # to ask on a bulk delete through a filter whose reach it cannot see.
    #
    # It used to be a `confirm=true` the MODEL set to authorise its own call, with the
    # preview as the un-confirmed branch. That was the second confirm mechanism, deleted
    # everywhere else by ADR 2026-08-25-one-confirm-mechanism-for-every-tool and missed here
    # because it was spelled `confirm` rather than a minted token (owner, 2026-08-27). Asking
    # the owner is the HARNESS's job — `should_gate` stages this call and raises a card, and
    # nothing the model passes can skip it. What survives is the genuinely useful half: an
    # opt-in dry run for when a filter's reach is unclear.
    # The preview runs the SAME guard, category resolution and row predicate as the delete
    # (services.preview_delete_transactions_where → store.select_where), never a parallel
    # query, so it cannot report a set the delete would not touch.
    if input_data.get("preview"):
        try:
            rows = svc.preview_delete_transactions_where(**filters)
        except OpenNetWorthError as exc:
            return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
        sample = [
            {
                "id": r.get("id"),
                "date": r.get("date"),
                "description": r.get("description"),
                "base_amount": r.get("base_amount"),
            }
            for r in rows[:10]
        ]
        return (
            json.dumps(
                {
                    "ok": True,
                    "deleted": 0,
                    "would_delete": len(rows),
                    "showing": len(sample),
                    "sample": sample,
                    "note": (
                        f"NOTHING WAS DELETED. {len(rows)} transaction(s) match these filters. "
                        "Show the user the count and the sample, get explicit approval, then "
                        "call again with confirm=true."
                    ),
                }
            ),
            None,
        )
    # Shared resolving choke point (services.ledger.delete_transactions_where, CATF2): enforces
    # "at least one filter" itself (a bare call would otherwise wipe the whole ledger) and
    # resolves a caller-supplied category label to its stored opaque id, REFUSING an
    # unknown/ambiguous one rather than silently deleting nothing.
    try:
        deleted = svc.delete_transactions_where(**filters)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool delete_transactions_where: deleted=%d filters=%s", deleted, filters)
    return json.dumps({"ok": True, "deleted": deleted}), None


def _resolve_live_txn(existing_id: str | None) -> dict | None:
    """A transaction is resolvable for a `link` decision only if it exists and hasn't been
    deleted or merged away underneath us — mirrors match_review._is_live."""
    if not existing_id:
        return None
    t = svc.transactions.find_by_id(existing_id)
    if t is None or t.get("deleted") or t.get("merged_into"):
        return None
    return t


@tool(
    "add_transactions",
    description=(
        "Book any money movement — expense, income, inter-account transfer, investment "
        "contribution, tax, refund. Pass ONE item in `transactions` for a single entry (the "
        "common case), many to import a statement. Investment cash legs (buys/sells/dividends) "
        "go to the currency wallet (e.g. 'IBKR USD'), not the securities account ('IBKR').\n\n"
        "Returns per-item `results` — {index, ok, id} or {index, error}. Never re-submit a row "
        "that reported an id.\n\n"
        "Interactive turns PREVIEW first: ONE confirmation card (single entry or whole batch) "
        "showing the RESOLVED outcome — the category and WHICH tier set it, the resolved "
        "accounts, the base-currency value. Nothing is written until the user confirms.\n\n"
        "STATEMENT IMPORT: do NOT hand-parse statements into this tool. `import_transactions` "
        "is the single entrance for EVERY format and books through this same choke point. "
        "Use `add_transactions` for a statement ONLY as a fallback for rows the parser "
        "genuinely could not read — and then:\n"
        "• Submit every row and let the duplicate pre-flight decide — never skip or dedupe "
        "rows yourself.\n"
        "• >30 rows → split into 20–25-row calls sharing one import_session_id and "
        "import_description.\n\n"
        "MULTI-ROW CALLS ARE ATOMIC: all rows validate before any is created, and a duplicate "
        "pre-flight can abort the whole call — nothing booked. Both refusals name the fix.\n\n"
        "That pre-flight aborts only on a near-certain match. For rows from a source that can "
        "repeat itself (alerts, forwarded receipts), call check_duplicates first."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "transactions": {
                "type": "array",
                "minItems": 1,
                "description": "The entries to book. One item is the common case.",
                "items": _TXN_ITEM_SCHEMA,
            },
            "allow_duplicate": {
                "type": "boolean",
                "description": (
                    "Book every row even where it looks like a duplicate of one already in the "
                    "ledger. Set this ONLY when the user has confirmed these are genuinely "
                    "separate charges — it does not make a re-run safe, it books the rows twice. "
                    "Duplicates are still reported back to you either way."
                ),
            },
            "force_transfer_rate": {
                "type": "boolean",
                "description": (
                    "A transfer whose two legs carry the EXACT SAME NUMBER in different "
                    "currencies is refused when a known exchange rate says they should not "
                    "match — this is almost always the source amount copied into the "
                    "destination leg without actually applying the rate (compute "
                    "to_amount = amount x rate yourself and re-send first). Set this ONLY when "
                    "the transfer genuinely cleared at that exact number in both currencies "
                    "(a stale quote, a peg, a fee-inclusive settlement) — it does not change "
                    "the amounts, it just books the row as stated."
                ),
            },
            "decisions": {
                "type": "array",
                "description": (
                    "Per-row resolutions when RE-SUBMITTING a batch that aborted with "
                    "already_recorded, or reported a row previously_deleted (index into THIS SAME "
                    "transactions list). Omit on a fresh call."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index into `transactions`.",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["create", "link", "skip"],
                            "description": (
                                "create = book as new (for a previously_deleted row: re-book it "
                                "ONLY because the user explicitly asked for it back). link = fold "
                                "into existing_id instead of duplicating (never changes its "
                                "date/amount). skip = book nothing."
                            ),
                        },
                        "existing_id": {
                            "type": "string",
                            "description": (
                                "Required for action='link' — the existing_id the "
                                "already_recorded entry proposed for THIS row."
                            ),
                        },
                    },
                    "required": ["index", "action"],
                },
            },
            "import_description": {
                "type": "string",
                "description": "Short human-readable label for this import batch, e.g. 'Checking Jan–Mar' or 'Card Apr'. Used to identify the batch for rollback.",
            },
            "import_session_id": {
                "type": "string",
                "description": (
                    "Ties all batches of one split statement together. Omit on the first batch "
                    "— the result returns the id to pass back on every following batch."
                ),
            },
            "statement_ref_id": {
                "type": "string",
                "description": (
                    "statement_id of the uploaded file (returned by the upload endpoint and by "
                    "diff_statement), linking this batch to that statement record."
                ),
            },
        },
        "required": ["transactions"],
    },
    mutates=("ledger",),
    # Former names, kept resolvable for stored agent/skill allowlists and in-flight
    # transcripts (see the alias note in registry.py). Zero prompt cost — aliases never
    # appear in build_tool_schemas().
    self_stages=True,
)
async def add_transactions(input_data: dict):

    # Confirm gate, step 2 — a token means the user tapped ✓ on a preview card. Pop the
    # STORED args and execute THOSE: everything else the model sent on this call is
    # discarded, so the numbers that commit are exactly the numbers the card showed.
    gated = False
    promised: dict | None = None
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "add_transactions")
        if entry is None:
            return _gate.unknown_token_result("add_transactions")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = _gate.should_gate("add_transactions")

    today_iso = user_today().isoformat()
    # Shape leniency, mirroring the handler-leniency convention: a legacy `add_transaction`
    # call (bare single-transaction fields, no wrapper) arrives here through the alias and
    # is normalised into a one-row list.
    raw = input_data.get("transactions")
    if raw is None:
        bare = {
            k: v for k, v in input_data.items() if k not in _BATCH_ONLY_KEYS and k != "transactions"
        }
        raw = [bare] if bare else []
    if isinstance(raw, dict):  # a single item passed unwrapped inside `transactions`
        raw = [raw]
    allow_duplicate = bool(input_data.get("allow_duplicate", False))
    force_transfer_rate = bool(input_data.get("force_transfer_rate", False))

    # A lone row takes the single-add path: it must not mint an import-batch record, and it
    # keeps the transfer-duplicate confirmation prompt. Any batch/import parameter means the
    # caller genuinely wants the batch machinery, so honour that even for one row.
    if (
        len(raw) == 1
        and not any(input_data.get(k) for k in _BATCH_ONLY_KEYS)
        and (allow_duplicate or not _lone_row_matches_the_ledger(raw[0], today_iso))
    ):
        single = dict(raw[0])
        if allow_duplicate:
            single["allow_duplicate"] = True
        if force_transfer_rate:
            single["force_transfer_rate"] = True
        return await _add_one(single, gated=gated, promised=promised)

    logger.info(
        "tool add_transactions: received %d transactions (allow_duplicate=%s)",
        len(raw),
        allow_duplicate,
    )

    if len(raw) > _MAX_BULK_ADD_ROWS:
        logger.warning(
            "tool add_transactions: rejected — %d rows exceeds the %d-row cap",
            len(raw),
            _MAX_BULK_ADD_ROWS,
        )
        return (
            json.dumps(
                {
                    "ok": False,
                    "created": 0,
                    "total_submitted": len(raw),
                    "error_note": (
                        f"{len(raw)} rows exceeds the {_MAX_BULK_ADD_ROWS}-row cap for one call "
                        "— nothing was validated or written. Split into smaller calls (20-25 "
                        "rows each) sharing one import_session_id."
                    ),
                },
                ensure_ascii=False,
            ),
            None,
        )

    # Phase 1: pre-validate ALL items before creating anything (atomicity guarantee).
    # If any item fails normalisation the whole call is aborted — no partial batches.
    normalised = []
    errors = []
    for i, item in enumerate(raw):
        try:
            normalised.append(_txn_data_from_ai(item, default_date=today_iso))
        except (ValueError, KeyError) as e:
            _desc = item.get("description") or ""
            logger.warning(
                "tool add_transactions: index=%d description=%r error=%s",
                i,
                _desc,
                e,
            )
            errors.append({"index": i, "description": _desc, "error": str(e)})

    if errors:
        # At least one item is invalid — abort without writing anything.
        n_errors = len(errors)
        logger.warning(
            "tool add_transactions: aborted — %d/%d items failed pre-validation, created 0",
            n_errors,
            len(raw),
        )
        return (
            json.dumps(
                {
                    "ok": False,
                    "created": 0,
                    "total_submitted": len(raw),
                    "errors": n_errors,
                    "errors_detail": errors,
                    "results": [{"index": e["index"], "error": e["error"]} for e in errors],
                    "error_note": (
                        f"{n_errors} row(s) failed validation. No transactions were created. "
                        "Fix the errors and re-submit the full batch."
                    ),
                },
                ensure_ascii=False,
            ),
            None,
        )

    # Confirm gate (multi-row): ONE card for the whole batch — never one per row, which would
    # just move the round-trips from the API to the human. Placed after Phase 1 so an invalid
    # batch still aborts on validation, and before the duplicate pre-flight and every write.
    # NOTE the pre-flight therefore runs on the CONFIRMING call, so an `already_recorded`
    # abort surfaces after the ✓ rather than on the card; the batch is still never written.
    if gated or promised is not None:
        try:
            change = ledger_preview.preview_bulk(
                normalised, import_description=input_data.get("import_description")
            )
        except (ValueError, OpenNetWorthError) as e:
            return _gate.preview_failed_result(str(e))
        if gated:
            return _gate.stage("add_transactions", input_data, change)
        if _gate.outcome_of(change) != promised:
            return _gate.outcome_mismatch_result("add_transactions")

    # Phase 1.5 — duplicate pre-flight (RFC batch-aware-dedup, Phase 3): classify the WHOLE
    # batch against the ledger AND itself in one pass, BEFORE anything is booked, so a
    # near-certain duplicate is caught before insert instead of flagged after the fact.
    # force=True skips this entirely — today's escape hatch, unchanged. A pasted/dictated
    # batch claims no completeness (BatchContext(complete=False)), so books-close promotion
    # never fires here — only pair-level evidence (shared reference, exact wording) can band
    # strong.
    to_create = normalised
    link_plan: list[tuple[int, str, dict]] = []  # (index, existing_id, draft)
    skip_count = 0
    # Submitted-index bookkeeping for the per-item `results` array. Default (no decisions):
    # every row is created, in order.
    create_indices: list[int] = list(range(len(normalised)))
    skip_indices: set[int] = set()
    # The pre-flight runs on EVERY call, including an `allow_duplicate` one. Only its ACTIONS
    # are gated: the flag means "do not BLOCK me", never "do not TELL me". Welding those two
    # together is why three forced runs booked the same Apple receipt (`email:aaaa1111bbbb`)
    # three times over three days, S$31.96 each, with nothing recorded anywhere — the owner
    # found it by eye. What the flag skipped is now reported back as `forced_past`.
    ref_noop_by_index: dict[int, object] = {}
    forced_past: list[dict] = []
    from backend.services import matching

    try:
        _preflight = matching.match_batch(normalised, context=matching.BatchContext(complete=False))
    except Exception:  # noqa: BLE001 — a broken pre-flight must never block a valid write
        logger.exception("add_transactions: pre-flight match_batch failed; booking as submitted")
        _preflight = None

    if _preflight is not None:
        # L0 is IDENTITY, not resemblance, and is enforced even under `allow_duplicate`: one
        # email is one charge, so a row may never claim a `(source, ref)` another live row
        # already holds. A genuine second identical purchase arrives as a SECOND email with a
        # DIFFERENT ref and books normally — so this costs nothing real, while the alternative
        # is a ledger that believes one receipt produced three charges.
        ref_noop_by_index = {
            i: rm.assigned
            for i, rm in enumerate(_preflight)
            if rm.assigned is not None and rm.assigned.klass is matching.MatchClass.EXACT
        }
        if allow_duplicate:
            forced_past = [
                {
                    "index": i,
                    "description": normalised[i].get("description", ""),
                    "existing_id": rm.assigned.id,
                    "score": rm.assigned.score,
                    "reason": "; ".join(rm.assigned.reasons),
                }
                for i, rm in enumerate(_preflight)
                if rm.assigned is not None
                and rm.band in ("strong", "review")
                and i not in ref_noop_by_index
            ]

    from backend.services import rejected_identities as _rejected_identities

    previously_deleted_by_index: dict[int, dict] = {}
    try:
        previously_deleted_by_index = {
            i: rec
            for i, rec in _rejected_identities.rejections_for(normalised).items()
            if i not in ref_noop_by_index
        }
    except Exception:  # noqa: BLE001 — the write choke enforces it; this read only reports
        logger.exception("add_transactions: rejected-identity pre-flight failed; choke decides")
    resurrect_indices = {
        d["index"]
        for d in input_data.get("decisions") or []
        if isinstance(d, dict)
        and d.get("action") == "create"
        and isinstance(d.get("index"), int)
        and not isinstance(d.get("index"), bool)
        and d["index"] in previously_deleted_by_index
    }

    if not allow_duplicate:
        row_matches = _preflight

        if row_matches is not None:
            # Parse + validate any re-submit `decisions` before touching anything — an unknown
            # index/action, or a link with no live existing_id, aborts the whole call exactly
            # like a Phase-1 validation error (atomicity preserved: nothing is booked yet).
            decision_errors = []
            decisions_by_index: dict[int, dict] = {}
            for d in input_data.get("decisions") or []:
                if not isinstance(d, dict):
                    decision_errors.append(
                        {
                            "index": None,
                            "description": "",
                            "error": f"decisions: expected an object, got {d!r}",
                        }
                    )
                    continue
                idx = d.get("index")
                action = d.get("action")
                valid_idx = (
                    isinstance(idx, int)
                    and not isinstance(idx, bool)
                    and 0 <= idx < len(normalised)
                )
                note = normalised[idx].get("description", "") if valid_idx else ""
                if not valid_idx:
                    decision_errors.append(
                        {
                            "index": idx,
                            "description": note,
                            "error": (
                                f"decisions: index {idx!r} is not a valid row index for a "
                                f"{len(normalised)}-row batch"
                            ),
                        }
                    )
                    continue
                if action not in ("create", "link", "skip"):
                    decision_errors.append(
                        {
                            "index": idx,
                            "description": note,
                            "error": f"decisions: unknown action {action!r} (use create/link/skip)",
                        }
                    )
                    continue
                existing_id = d.get("existing_id")
                if action == "link":
                    if _resolve_live_txn(existing_id) is None:
                        decision_errors.append(
                            {
                                "index": idx,
                                "description": note,
                                "error": (
                                    f"decisions: link at index {idx} needs a resolvable, live "
                                    f"existing_id (got {existing_id!r})"
                                ),
                            }
                        )
                        continue
                    # FIX 3 (security review 2026-07-16): existing_id must be THIS row's own
                    # pre-flight verdict, not any live id the caller happens to supply — a live
                    # but never-proposed id would silently fold this row's facts into an
                    # unrelated transaction (the same trust gap FIX 2 closes at decision_commit).
                    proposed = row_matches[idx].assigned
                    if proposed is None or proposed.id != existing_id:
                        decision_errors.append(
                            {
                                "index": idx,
                                "description": note,
                                "error": (
                                    f"decisions: link at index {idx} existing_id {existing_id!r} "
                                    "was not proposed by the duplicate pre-flight for this row — "
                                    "re-submit without decisions to see the current match, or use "
                                    "action='create'"
                                ),
                            }
                        )
                        continue
                decisions_by_index[idx] = {"action": action, "existing_id": existing_id}

            if decision_errors:
                n_errors = len(decision_errors)
                logger.warning(
                    "tool add_transactions: aborted — %d decision(s) failed validation, created 0",
                    n_errors,
                )
                return (
                    json.dumps(
                        {
                            "ok": False,
                            "created": 0,
                            "total_submitted": len(raw),
                            "errors": n_errors,
                            "errors_detail": decision_errors,
                            "error_note": (
                                f"{n_errors} decision(s) failed validation. No transactions were "
                                "created. Fix the errors and re-submit the full batch."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    None,
                )

            # ANY matched row this call didn't get a decision for aborts the whole batch — book
            # nothing, hand back the duplicate PAIR, and let the caller decide on the re-submit.
            #
            # This used to abort on the ``strong`` band alone, so a ``review``-band match was
            # WRITTEN and merely stamped ``needs_review``. There is no such middle state: a
            # write either happens or it doesn't (owner, 2026-08-10 — "THERE IS NO FUCKING
            # NEEDS REVIEW CONCEPT ... WE HAVE TO ABORTS, AND RETURN THE DUPPLICATED PAIR").
            # The scar: a 7-Eleven charge captured by an iOS shortcut ("7-Eleven") and the
            # bank's own alert for it ("7-ELEVEN-EXAMPLEPLACE PLA") — same account, same S$21.70,
            # same day — scored 0.75 because two shared tokens miss ``desc_similar``'s 3-word
            # floor. Correctly identified, correctly banded review, and booked anyway. Deferring
            # it to a queue is not a safeguard here: ledger.py's own refusal comment says it —
            # "created-then-flagged only protects a book whose queue is READ", and an unattended
            # 3am automation is exactly a path with no reader.
            undecided_matches = [
                i
                for i, rm in enumerate(row_matches)
                if rm.band in ("strong", "review")
                and rm.assigned is not None
                and i not in decisions_by_index
                and i not in ref_noop_by_index
                and i not in previously_deleted_by_index
            ]
            if undecided_matches:
                already_recorded = []
                for i in undecided_matches:
                    cand = row_matches[i].assigned
                    already_recorded.append(
                        {
                            "index": i,
                            "description": normalised[i].get("description", ""),
                            "existing_id": cand.id,
                            "existing": _project_txn(cand.txn),
                            "score": cand.score,
                            "reason": "; ".join(
                                [f"{cand.klass.value} (score {cand.score})", *cand.reasons]
                            ),
                        }
                    )
                n = len(already_recorded)
                # Counted and NAMED separately from `n`. A ref no-op is already in the ledger
                # too, but by IDENTITY rather than resemblance: it needs no decision and is not
                # what the abort is waiting on. Folding it into `n` made the note claim more
                # rows than it listed; leaving it out of the sentence entirely made the note
                # claim fewer rows than were actually duplicates — a 6-row batch reported
                # "1 of 6 rows match" while `already_recorded_by_ref` listed 2 more, and the
                # reader could not reconcile the two (owner, 2026-08-23). Say both numbers.
                n_ref = len(ref_noop_by_index)
                logger.info(
                    "tool add_transactions: aborted — %d row(s) need a decision, "
                    "%d already recorded by ref, created 0",
                    n,
                    n_ref,
                )
                return (
                    json.dumps(
                        {
                            "ok": False,
                            "created": 0,
                            "total_submitted": len(raw),
                            "already_recorded": already_recorded,
                            # Name the rows that did NOT match. Listing only the offenders left
                            # the caller unable to account for the rest: a 7-row batch reported 6
                            # matches and said nothing about index 6, so the model reasoned
                            # "either it wasn't in the batch, or it failed differently, or the
                            # response was truncated" — and had to guess (live 2026-08-11). An
                            # abort is all-or-nothing, so the honest statement is that these were
                            # fine and are waiting on a decision about the others.
                            "blocked_but_unmatched": [
                                i
                                for i in range(len(raw))
                                if i not in {r["index"] for r in already_recorded}
                                and i not in ref_noop_by_index
                                and i not in previously_deleted_by_index
                            ],
                            "already_recorded_by_ref": sorted(ref_noop_by_index),
                            "previously_deleted": [
                                _previously_deleted_entry(i, rec)
                                for i, rec in sorted(previously_deleted_by_index.items())
                            ],
                            "error_note": (
                                f"NOTHING WAS WRITTEN. {n} of {len(raw)} row(s) NEED A DECISION "
                                "— each resembles a transaction already in the ledger and is "
                                "listed under `already_recorded` with the row it matched, so "
                                "you can compare them. Decide per row and re-submit the SAME full "
                                "batch with a `decisions` entry per listed index ({index, action: "
                                "'create'|'link'|'skip', existing_id if linking})."
                                + (
                                    f" A FURTHER {n_ref} row(s) — listed in "
                                    "`already_recorded_by_ref` — carry an external ref that is "
                                    "already recorded. They are duplicates too, but by identity "
                                    "rather than resemblance: they are NOT part of the count "
                                    "above, need no decision, and will not be written on the "
                                    "re-submit either."
                                    if n_ref
                                    else ""
                                )
                                + (
                                    f" A FURTHER {len(previously_deleted_by_index)} row(s) — "
                                    "listed in `previously_deleted` — are ones the user DELETED "
                                    "earlier; they need no decision and will not be written on "
                                    "the re-submit unless you add a `create` decision for them."
                                    if previously_deleted_by_index
                                    else ""
                                )
                                + " Any index in "
                                "`blocked_but_unmatched` had NO problem — it was not written only "
                                "because this abort is all-or-nothing, and it will be created "
                                "when you re-submit. Deciding later "
                                "is fine — nothing is lost by aborting. Do NOT use force to get "
                                "past this: it books every row blind, duplicates included."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    None,
                )

            # Every MATCHED row now has a decision — split the batch: skip books nothing, link
            # merges into the existing transaction, everything else (an explicit create, or a row
            # the engine matched to nothing at all) is booked as new.
            to_create = []
            create_indices = []
            for i, draft in enumerate(normalised):
                decision = decisions_by_index.get(i)
                action = decision["action"] if decision else "create"
                if action == "skip":
                    skip_count += 1
                    skip_indices.add(i)
                elif action == "link":
                    link_plan.append((i, decision["existing_id"], draft))
                else:
                    to_create.append(draft)
                    create_indices.append(i)

    # Applied out here, not inside the pre-flight, so it holds on the fail-open path too — a
    # match_batch that threw must still not send a known re-present to the write choke.
    if ref_noop_by_index:
        kept_drafts, kept_indices = [], []
        for idx, draft in zip(create_indices, to_create):
            if idx in ref_noop_by_index:
                continue
            kept_drafts.append(draft)
            kept_indices.append(idx)
        to_create, create_indices = kept_drafts, kept_indices

    resurrect_refs: set[tuple[str | None, str]] = set()
    if previously_deleted_by_index:
        kept_drafts, kept_indices = [], []
        for idx, draft in zip(create_indices, to_create):
            if idx in previously_deleted_by_index:
                if idx not in resurrect_indices:
                    continue
                resurrect_refs |= matching.ext_ref_pairs(draft)
            kept_drafts.append(draft)
            kept_indices.append(idx)
        to_create, create_indices = kept_drafts, kept_indices

    # Unattended-write guard (PER ROW, not per call): a headless batch is the one place an
    # envelope could otherwise be trivially bypassed by submitting many rows in one call. Each
    # row is checked independently and a refusal excludes ONLY that row — book what's inside
    # the envelope, report the rest, rather than aborting a whole batch for one over-limit row
    # (self-guards on is_unattended(), so this is a no-op loop on every interactive/connector/
    # skill turn: to_create/create_indices pass through unchanged).
    envelope_refused: dict[int, str] = {}
    if to_create:
        kept_drafts: list[dict] = []
        kept_indices: list[int] = []
        for idx, draft in zip(create_indices, to_create):
            guard = await _check_unattended_envelope("add_transactions", draft)
            if guard is not None:
                envelope_refused[idx] = json.loads(guard[0])["error"]
                continue
            kept_drafts.append(draft)
            kept_indices.append(idx)
        to_create, create_indices = kept_drafts, kept_indices

    # Phase 2: create the batch record and insert whatever the pre-flight left to create.
    statement_ref_id = input_data.get("statement_ref_id") or None
    # add_transactions is called for ANY bulk AI booking, not just statement imports (e.g. the
    # email-transaction-logger agent batching several emails) — only claim "Statement import"
    # in the default label when the batch is actually backed by an uploaded statement, else the
    # drawer's Activity note lies about where the transaction came from.
    default_description = (
        f"Statement import ({user_today().isoformat()})"
        if statement_ref_id
        else f"Bulk add ({user_today().isoformat()})"
    )
    import_description = input_data.get("import_description") or default_description
    # Minting the session id server-side (rather than instructing the model to generate a
    # uuid4 before its first batch) makes reuse a matter of echoing back what the previous
    # result handed over, instead of remembering a procedure across calls. A caller-supplied
    # id always wins, so an in-flight multi-batch import is unaffected.
    import_session_id = input_data.get("import_session_id") or f"imp_{uuid.uuid4().hex[:12]}"
    batch = svc.import_batches.create_batch(
        description=import_description,
        transaction_count=len(to_create),
        import_session_id=import_session_id,
        statement_ref_id=statement_ref_id,
    )

    # Link batch back to statement record
    if statement_ref_id:
        try:
            svc.statements.add_batch_id(statement_ref_id, batch["id"])
        except Exception:
            pass
    batch_id = batch["id"]

    bulk_meta: dict = {}
    try:
        created, refused = (
            svc.bulk_create_transactions(
                to_create,
                batch_id=batch_id,
                force=allow_duplicate,
                meta_out=bulk_meta,
                force_transfer_rate=force_transfer_rate,
                resurrect_refs=resurrect_refs or None,
            )
            if to_create
            else ([], [])
        )
    except (ValueError, OpenNetWorthError) as e:
        # Unexpected validation error from the service layer (e.g. split mismatch).
        # bulk_create_transactions is a single bulk_append so nothing was committed;
        # mark the batch as rolled_back so it doesn't show as a ghost import.
        svc.import_batches.set_status(batch_id, "rolled_back")
        logger.error(
            "add_transactions: creation failed after pre-validation, batch=%s: %s", batch_id, e
        )
        return (
            json.dumps(
                {
                    "ok": False,
                    "created": 0,
                    "rolled_back": True,
                    "error": str(e),
                    "error_note": "Creation failed unexpectedly; the batch was rolled back (0 transactions committed).",
                },
                ensure_ascii=False,
            ),
            None,
        )

    # Apply any `link` decisions: fold the draft into its existing transaction instead of
    # creating a duplicate. facts="reference_only" (RFC batch-aware-dedup §Merge semantics,
    # open question #2 — a bulk-add batch has no completeness claim over the ledger, unlike a
    # bank statement): the canonical KEEPS its own date and amount; only the reference
    # external_refs pairs and cleared are absorbed. Any date difference is reported as date_skew,
    # never silently written. Best-effort per link — existing_id was already validated live
    # above, so a failure here is exceptional and must never sink the batch already committed.
    linked_detail: list[dict] = []
    link_errors: list[dict] = []
    for idx, existing_id, draft in link_plan:
        try:
            merged_row = svc.apply_import_merge(existing_id, draft, facts="reference_only")
            linked_detail.append(
                {
                    "index": idx,
                    "existing_id": existing_id,
                    "date_skew": f"{merged_row.get('date')} vs {draft.get('date')}",
                }
            )
        except Exception as e:  # noqa: BLE001 — a broken link must never sink a committed batch
            logger.exception(
                "add_transactions: link failed for index=%d existing_id=%s", idx, existing_id
            )
            link_errors.append({"index": idx, "existing_id": existing_id, "error": str(e)})

    # Dedup / match engine (RFC transaction-dedup-engine): surface cross-source duplicates and
    # transfer pairs against the existing ledger via the ONE review queue — created-then-flagged,
    # the same engine + queue as imports/connectors. Replaces the old bespoke
    # (account, amount, date, direction) post-insert grouping: the engine is smarter (fuzzy /
    # cross-currency / transfer pairs) and the four invariants protect against false flags (two
    # real same-day charges, distinct authoritative ids, recurring subscriptions).
    review = {"auto_merged": 0, "queued": 0}
    # Runs under `allow_duplicate` too. Gating it on the flag meant the one call most likely to
    # create a duplicate was also the only one that recorded nothing — the queue that exists to
    # catch this was switched off by the same boolean that caused it.
    if created:
        try:
            from backend.services import match_review

            review = match_review.review_created_rows(created, batch_id=batch_id)
        except Exception:  # noqa: BLE001 — surfacing must never break a completed bulk add
            logger.exception("add_transactions: dedup review pass failed")

    svc.import_batches.set_status(batch_id, "active")

    n_queued = review.get("queued", 0)
    # Rows the engine had ALREADY flagged (a re-add, a retried batch) are still live duplicates
    # the caller must hear about — reporting only fresh enqueues turns the second telling of the
    # same duplicate into an all-clear.
    n_already = review.get("already_pending", 0)
    n_created = len(created)
    n_submitted = len(normalised)
    logger.info(
        "tool add_transactions: batch=%s submitted=%d created=%d linked=%d skipped=%d "
        "review_queued=%d already_flagged=%d",
        batch_id,
        n_submitted,
        n_created,
        len(linked_detail),
        skip_count,
        n_queued,
        n_already,
    )
    # Per-item outcome, submitted-index keyed: which row became which transaction, was folded
    # into an existing one, or was skipped. Without it a partially-resolved batch (some rows
    # linked/skipped via `decisions`) reports only aggregate counts, leaving the model to guess
    # the mapping — and a model that guesses wrong re-submits and double-books.
    linked_by_index = {d["index"]: d["existing_id"] for d in linked_detail}
    link_error_by_index = {d["index"]: d["error"] for d in link_errors}
    # bulk_create_transactions preserves submitted order, so the i-th created row is
    # create_indices[i]. A short `created` list (a row the service dropped) leaves the
    # remaining indices unreported rather than mis-attributing an id to the wrong row.
    # A refusal REMOVES its row from `created`, so "the i-th created row is create_indices[i]"
    # no longer holds — without excluding the refused positions first, every row after a refusal
    # is handed its neighbour's id. `_batch_index` is the submitted position the service refused.
    refused_by_index = {
        create_indices[r["_batch_index"]]: r
        for r in refused
        if r.get("_batch_index") is not None and r["_batch_index"] < len(create_indices)
    }
    _refused_positions = {r.get("_batch_index") for r in refused}
    _kept = [ci for j, ci in enumerate(create_indices) if j not in _refused_positions]
    created_by_index = {_kept[i]: t.get("id") for i, t in enumerate(created) if i < len(_kept)}
    # Per-row payee name + "this one was invented" flag. Batch-level counts alone would tell the
    # model N rows need attention without saying WHICH, so it would have to re-read every row.
    minted_ids = set(bulk_meta.get("payee_minted_txn_ids") or [])
    payee_name_by_txn_id: dict[str, str] = {}
    if created:
        payee_labels = labels.label_maps(payees=True).get("payees") or {}
        for t in created:
            name = payee_labels.get(t.get("payee_id") or "")
            if name:
                payee_name_by_txn_id[t["id"]] = name
    # ONE mutually-exclusive verdict per row, never a bag of booleans. The previous shape gave a
    # row that was NOT written `{"ok": true, "id": "txn_…", "already_recorded": true}` — the exact
    # shape of a successful create, with the correction third and expected to override the two
    # fields before it. It did not: a 9-row batch that wrote 1 row and reported 8 already-recorded
    # was read as 9 writes, twice in a row, by a model that had the right answer in front of it
    # (run_95f4ea913df0). `status` cannot be half-read.
    results: list[dict] = []
    for i in range(n_submitted):
        if i in envelope_refused:
            results.append({"index": i, "status": "error", "error": envelope_refused[i]})
        elif i in link_error_by_index:
            results.append({"index": i, "status": "error", "error": link_error_by_index[i]})
        elif i in linked_by_index:
            results.append({"index": i, "status": "linked", "id": linked_by_index[i]})
        elif i in refused_by_index and refused_by_index[i].get("_previously_deleted"):
            results.append(
                {
                    "index": i,
                    "status": "previously_deleted",
                    "id": None,
                    "deleted_id": refused_by_index[i].get("_duplicate_of"),
                    # Same "status cannot be half-read" reasoning as the sibling "refused"
                    # branch below: the choke already computed a readable message
                    # (_rejected_identities.previously_deleted_error) — carry it onto THIS
                    # row instead of leaving it only in the aggregate `previously_deleted`
                    # list a caller reading `results` row-by-row would never look at.
                    "reason": refused_by_index[i].get("_duplicate_reason"),
                }
            )
        elif i in refused_by_index:
            r = refused_by_index[i]
            results.append(
                {
                    "index": i,
                    "status": "refused",
                    "error": _names_the_tool_flag(r.get("_duplicate_reason")),
                    "duplicate_of": r.get("_duplicate_of"),
                }
            )
        elif i in previously_deleted_by_index and i not in resurrect_indices:
            rec = previously_deleted_by_index[i]
            results.append(
                {
                    "index": i,
                    "status": "previously_deleted",
                    "id": None,
                    "deleted_id": rec.get("txn_id"),
                    "deleted_on": (rec.get("rejected_at") or "")[:10] or None,
                    # Same reason as the sibling refused_by_index branch above: carry the
                    # readable "why" onto THIS row, not only the aggregate
                    # `previously_deleted` list a row-by-row reader of `results` never sees.
                    "reason": _rejected_identities.previously_deleted_error(rec).message,
                }
            )
        elif i in ref_noop_by_index:
            results.append(
                {
                    "index": i,
                    "status": "already_recorded",
                    "id": ref_noop_by_index[i].txn.get("id"),
                }
            )
        elif i in skip_indices:
            results.append({"index": i, "status": "skipped", "id": None})
        elif i in created_by_index:
            txn_id = created_by_index[i]
            entry: dict = {"index": i, "status": "created", "id": txn_id}
            if txn_id in payee_name_by_txn_id:
                entry["payee_name"] = payee_name_by_txn_id[txn_id]
            if txn_id in minted_ids:
                entry["payee_minted"] = True
            results.append(entry)
        else:
            results.append(
                {
                    "index": i,
                    "status": "error",
                    "error": "not created (no id returned by the ledger)",
                }
            )

    tally = Counter(r["status"] for r in results)
    # FIRST key, and the only one that has to be read. `created: 1` next to `total_submitted: 9`
    # is two bytes of truth between an 18-byte batch_id and an 8KB block — technically present,
    # reliably missed.
    summary = f"{n_submitted} submitted → " + ", ".join(
        f"{tally[k]} {k}"
        for k in (
            "created",
            "already_recorded",
            "previously_deleted",
            "linked",
            "skipped",
            "refused",
            "error",
        )
        if tally[k]
    )
    result: dict = {
        "summary": summary,
        "ok": True,
        "batch_id": batch_id,
        "import_session_id": import_session_id,
        "total_submitted": n_submitted,
        "created": n_created,
        "errors": 0,
        "results": results,
    }
    if linked_detail:
        result["linked"] = len(linked_detail)
        result["linked_detail"] = linked_detail
    if link_errors:
        result["link_errors"] = link_errors
    if skip_count:
        result["skipped"] = skip_count
    if forced_past:
        result["forced_past"] = forced_past
        result["forced_past_note"] = (
            f"allow_duplicate was set, so {len(forced_past)} row(s) were booked DESPITE matching "
            "a transaction already in the ledger — each is listed above with the row it matched. "
            "They are now in the book twice unless these really are separate charges. Check them "
            "and merge or delete the extras if not; they are also queued for duplicate review."
        )
    if ref_noop_by_index:
        # Deliberately NO `existing` projection here, unlike the inferred-match abort above.
        # There the caller must COMPARE two rows to decide; here the note itself says no decision
        # is needed, so a full row per entry is a detailed comparison for a verdict that requires
        # none — and it dominated the payload (6,669 of 8,642 bytes, 77%, on a 9-row call) while
        # the actual counts went unread.
        result["already_recorded"] = [
            {
                "index": i,
                "description": normalised[i].get("description", ""),
                "existing_id": cand.txn.get("id"),
                "reason": "; ".join(cand.reasons),
            }
            for i, cand in sorted(ref_noop_by_index.items())
        ]
        # `existing_id` is null for the intra-batch case — two SUBMITTED rows carrying one ref,
        # where the survivor is a sibling of this same call rather than a stored row.
        result["already_recorded_note"] = (
            f"{len(ref_noop_by_index)} of {n_submitted} row(s) were NOT written — each carries "
            "an external ref that is already recorded (see each entry's `reason`: already in "
            "the ledger, or repeated by an earlier row of this same batch). Do NOT count these "
            "as newly logged, and do NOT re-send them: re-sending produces this same answer. "
            "This is not an error and needs no decision."
        )
    previously_deleted_rows = [r for r in results if r["status"] == "previously_deleted"]
    if previously_deleted_rows:
        result["previously_deleted"] = [
            {
                "index": r["index"],
                "description": normalised[r["index"]].get("description", ""),
                "deleted_id": r.get("deleted_id"),
                "deleted_on": r.get("deleted_on"),
            }
            for r in previously_deleted_rows
        ]
        result["previously_deleted_note"] = (
            f"{len(previously_deleted_rows)} of {n_submitted} row(s) were NOT written — "
            + _PREVIOUSLY_DELETED_NOTE
        )
    if resurrect_indices:
        result["resurrected"] = sorted(i for i in resurrect_indices if i in created_by_index)
    refused = [r for r in refused if not r.get("_previously_deleted")]
    if refused:
        # A refusal is NOT an "ok" outcome the model may skip past: the row it was asked to book
        # is not in the ledger. Counted in `errors` so the existing error handling sees it, and
        # given its own instructable note — a refusal the model cannot act on just becomes a
        # silent data loss with extra steps.
        result["refused_duplicates"] = len(refused)
        result["errors"] = result["errors"] + len(refused)
        result["refused_note"] = (
            f"{len(refused)} row(s) were NOT written — each matches a transaction already in the "
            "ledger (see results[].error and results[].duplicate_of). Show the user the existing "
            "row before doing anything else. If they confirm it is a genuinely separate charge, "
            "re-send ONLY that row with allow_duplicate=true. Do NOT re-send unchanged, and do NOT "
            "delete "
            "the existing row to make room."
        )
    if minted_ids:
        result["payee_minted"] = len(minted_ids)
        result["payee_minted_note"] = _payee_minted_note(len(minted_ids))
    if n_queued:
        result["review_queued"] = n_queued
        if n_already:
            result["already_flagged"] = n_already
        result["duplicate_note"] = (
            f"{n_queued + n_already} inserted transaction(s) look like a duplicate or transfer pair of an "
            "existing one and were flagged in the duplicate review queue. Use list_match_reviews "
            "to show the user each pair, then resolve_match_review (accept = merge / dismiss = keep "
            "both). Do NOT silently delete anything."
        )
    if envelope_refused:
        result["envelope_refused"] = len(envelope_refused)
        result["envelope_refused_note"] = (
            f"{len(envelope_refused)} row(s) were outside this agent's unattended-write envelope "
            "and were NOT booked — see the per-row errors in `results`."
        )
    # Tracking-start honesty, same as the single-add path — a leg landing before its account's
    # start (or a straddling transfer) is flagged, never silently dropped. Absent here until
    # 2026-08-12, which had it backwards: a batch import is exactly where out-of-range dates
    # arrive, and the one-row manual add is where they least do.
    if created:
        try:
            from backend.services import tracking_start

            accounts_now = svc.accounts.query_accounts()
            all_txns = svc.transactions.all()
            warnings: list[dict] = []
            for txn in created:
                for flag in tracking_start.flags_for_txn(txn, accounts_now, all_txns) or []:
                    warnings.append({"txn_id": txn.get("id"), **flag})
                equal_amount_warning = _unverified_equal_amount_warning(txn)
                if equal_amount_warning:
                    warnings.append({"txn_id": txn.get("id"), **equal_amount_warning})
            if warnings:
                result["warnings"] = warnings
        except Exception:  # noqa: BLE001 — surfacing must never break a completed batch
            logger.exception(
                "tool add_transactions: tracking-start pass failed for batch %s", batch_id
            )
    return json.dumps(result, ensure_ascii=False), None
