"""Lucius data-mention resolver — turn a structured entity reference into compact,
authoritative context for the chat turn.

A widget (net-worth hero, a holding row, a budget envelope, a transaction row, …) emits
a ``MentionRef = {type, ref, label}``. The frontend ships those refs as a structured
side-channel on ``/api/chat`` (NOT parsed out of free text). This module composes EXISTING
read services to resolve each ref into a small, bounded payload of CURRENT data, so the
model gets fresh, base-currency-correct numbers without the client ever shipping (possibly
stale, possibly amount-hidden) figures.

Design constraints (see ``docs/rfc/lucius-data-mentions.md``):
- Pure / compute-only — never a write path, no checkpoint, no FX-cache write.
- All monetary values come from the services (recomputed into base via ``to_base`` /
  ``txn_base_amount`` / ``net_worth``), so the block is base-currency-change-safe and immune
  to the UI's amount-hiding.
- NEVER raises on a bad ref: an unknown / malformed ref resolves to ``{error: 'not_found'}``
  for that one entry, leaving the rest intact.
- Bounded: ``MAX_MENTIONS`` caps the count; each payload is a handful of scalars / derived
  fields, never a full transaction list.
- Dependency rule: imports only ``db`` + other ``services`` + ``core``; NEVER ``api.*``
  (so it uses ``positions.hydrated()``, not ``api.portfolio.read_rows``).
"""

from __future__ import annotations

from contextvars import ContextVar

from backend.core.fx import base_currency, to_base, txn_base_amount
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.transaction_store import TransactionStore

# Hard cap on how many refs a single turn may carry. The frontend also caps, but the
# backend is the authority — a forged / oversized array is truncated here.
MAX_MENTIONS = 12

# ── Resolve-on-tap no-splice flag (RFC §8.3) ──────────────────────────────────
# Set to True by the POST /api/mentions/resolve endpoint so _brief_context and
# _analyst_context are NOT called on the tap path (they may be network-touching
# and belong to the chat-turn resolution path only).  The chat turn leaves this
# unset (None → falsy → splicing proceeds as normal).
_mention_detail_only: ContextVar[bool] = ContextVar("mention_detail_only", default=False)


def _round(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _this_month() -> str:
    t = user_today()
    return f"{t.year:04d}-{t.month:02d}"


# ── Per-type resolvers (each returns a compact dict, or None for "not found") ──────


def _resolve_net_worth(_ref: str) -> dict:
    from backend.services import ledger

    nw = ledger.net_worth()
    bd = nw.get("breakdown", {})
    account_classes = bd.get("account_classes", {})
    return {
        "base_currency": nw.get("base_currency"),
        "net_worth": nw.get("net_worth"),
        "assets": bd.get("accounts_asset"),
        "liabilities": bd.get("accounts_liability"),
        "market_positions": bd.get("market_positions"),
        # account_classes split (never lump non-cash accounts — e.g. a real-estate
        # account — into "assets" the way a caller might read as "cash"): mirrors the
        # AI system-prompt snapshot's breakdown (ai/prompts.py).
        "cash": account_classes.get("cash"),
        "settlement_cash": account_classes.get("settlement_cash"),
        "fixed_deposit": account_classes.get("fixed_deposit"),
        "retirement": bd.get("retirement_base") or None,
        "property": account_classes.get("property"),
        "receivable": account_classes.get("receivable"),
    }


def _brief_context(instrument_id: str | None) -> dict:
    """Attach the cached per-asset brief (news headlines, key questions, Lucius's take) so a
    chat turn about this asset answers from it WITHOUT re-running search/LLM. Empty if no brief."""
    if not instrument_id:
        return {}
    try:
        from backend.db.asset_brief_store import AssetBriefStore

        brief = AssetBriefStore().get(instrument_id)
    except Exception:
        brief = None
    if not brief:
        return {}
    out: dict = {}
    headlines = [(n.get("headline") or n.get("title")) for n in (brief.get("news") or [])[:5]]
    headlines = [h for h in headlines if h]
    if headlines:
        out["recent_news"] = " | ".join(headlines)
    issues = []
    for ki in (brief.get("key_issues") or [])[:4]:
        q = ki.get("question")
        if not q:
            continue
        bull = ((ki.get("bullish") or {}).get("text") or "").strip()
        bear = ((ki.get("bearish") or {}).get("text") or "").strip()
        seg = q
        if bull:
            seg += f" [bull: {bull}]"
        if bear:
            seg += f" [bear: {bear}]"
        issues.append(seg)
    if issues:
        out["key_issues"] = " || ".join(issues)
    take = brief.get("portfolio_narrative") or brief.get("portfolio_impact")
    if take:
        out["lucius_take"] = take
    call = _lucius_call(instrument_id, brief)
    if call:
        out["lucius_call"] = call
    if brief.get("synthesized_at"):
        out["brief_as_of"] = brief["synthesized_at"]
    return out


def _analyst_context(instrument_id: str | None) -> dict:
    """Attach the live analyst view (Yahoo, $0 — consensus rating, 52-week price targets, and
    recent per-firm rating actions) so a chat turn about this asset can speak to Wall Street's
    stance without re-fetching. Empty for assets with no coverage (crypto / commodities)."""
    if not instrument_id:
        return {}
    try:
        from backend.services.asset_detail import analyst_consensus

        payload = analyst_consensus(instrument_id)
    except Exception:
        return {}
    a = payload.get("analyst") or {}
    ratings = payload.get("ratings") or []
    if not a and not ratings:
        return {}
    cur = payload.get("currency") or ""
    out: dict = {}
    if a.get("rating_label"):
        cnt = a.get("analyst_count")
        out["analyst_rating"] = a["rating_label"] + (f" ({cnt} analysts)" if cnt else "")
    if a.get("target_mean") is not None:
        parts = [f"avg {cur} {a['target_mean']}".strip()]
        if a.get("target_low") is not None and a.get("target_high") is not None:
            parts.append(f"(low {a['target_low']} / high {a['target_high']})")
        if a.get("upside_pct") is not None:
            parts.append(f"{a['upside_pct']:+.1f}% upside")
        out["analyst_targets"] = " ".join(parts)
    if ratings:
        segs = []
        for r in ratings[:5]:
            seg = (r.get("firm") or "").strip()
            if not seg:
                continue
            if r.get("rating"):
                seg += f" {r['rating']}"
            if r.get("target") is not None:
                seg += f" {cur} {r['target']}".rstrip()
            segs.append(seg)
        if segs:
            out["analyst_ratings_recent"] = "; ".join(segs)
    return out


def _lucius_call(instrument_id: str, brief: dict) -> str:
    """The take's call (``services.position_verdict``) as one line, or "" — never raises."""
    try:
        from backend.db.instrument_store import InstrumentStore
        from backend.services import investment_theses, position_verdict

        inst = InstrumentStore().find_by_id(instrument_id) or {}
        return position_verdict.call_text(
            brief.get("position_verdict"),
            investment_theses.thesis_for(instrument_id),
            currency=inst.get("quote_currency") or "",
        )
    except Exception:
        return ""


# Mention types that carry a per-asset news brief (instrument-backed). Used to bind a
# conversation to an asset so its cached brief rides EVERY turn, not just the chip turn.
_BRIEFABLE_TYPES = {"instrument", "holding"}


def instrument_id_for_brief(mtype: str, ref: str) -> str | None:
    """Map a briefable mention to its instrument id (for session-level brief binding).

    An ``instrument`` ref IS the instrument id; a ``holding`` ref is a position id, resolved
    to its instrument via the positions service. Any other type → None. Never raises — a bad
    ref or store hiccup yields None so the chat turn is never wedged."""
    mtype = (mtype or "").strip()
    ref = (ref or "").strip()
    if not ref or mtype not in _BRIEFABLE_TYPES:
        return None
    if mtype == "instrument":
        return ref
    try:
        from backend.services import positions as positions_service

        row = positions_service.hydrated_one(ref)
    except Exception:
        return None
    iid = (row or {}).get("instrument_id")
    return str(iid) if iid else None


def session_brief_block(instrument_id: str, label: str = "") -> str:
    """Render the cached per-asset brief as a standalone context block for a conversation that
    is BOUND to this asset, so every follow-up turn answers from it without re-fetching.

    Returns ``""`` when no brief is cached yet (the caller then merges nothing, leaving the
    turn unchanged until a refresh has populated the brief). A pure $0 store read — no search,
    no LLM. Framed to steer the model AWAY from re-running search unless the user asks for
    newer news, since stale-but-cheap-cached beats an expensive redundant fetch."""
    ctx = _brief_context(instrument_id)
    analyst = _analyst_context(instrument_id)
    if not ctx and not analyst:
        return ""
    name = label or "this asset"
    as_of = ctx.get("brief_as_of")
    head = (
        f"[Cached brief for {name}"
        + (f" (news as of {as_of})" if as_of else "")
        + " — this conversation is about this asset. Answer its recent-news / outlook / "
        "analyst questions from this brief; only run a fresh web_search or scan_intel if the "
        "user explicitly asks for newer or additional news:]"
    )
    lines = [head]
    if ctx.get("recent_news"):
        lines.append(f"recent_news: {ctx['recent_news']}")
    if ctx.get("key_issues"):
        lines.append(f"key_issues: {ctx['key_issues']}")
    if ctx.get("lucius_take"):
        lines.append(f"lucius_take: {ctx['lucius_take']}")
    if ctx.get("lucius_call"):
        lines.append(f"lucius_call: {ctx['lucius_call']}")
    for k in ("analyst_rating", "analyst_targets", "analyst_ratings_recent"):
        if analyst.get(k):
            lines.append(f"{k}: {analyst[k]}")
    return "\n".join(lines)


def _resolve_holding(ref: str) -> dict | None:
    from backend.services import ledger
    from backend.services import positions as positions_service

    row = positions_service.hydrated_one(ref)
    if not row:
        return None
    base = base_currency()
    value = ledger.row_base_value(row, base)
    cost = row.get("cost_basis")
    cost_base = (
        to_base(float(cost) * float(row.get("quantity") or 0), row.get("currency", base), base)
        if cost
        else None
    )
    unrealized = _round(value - cost_base) if cost_base is not None else None
    data = {
        "symbol": row.get("symbol"),
        "name": row.get("label"),
        "account": row.get("account_name"),
        "asset_class": row.get("asset_class"),
        "quantity": row.get("quantity"),
        "currency": row.get("currency"),
        "price": row.get("price"),
        "value_base": value,
        "cost_base": cost_base,
        "unrealized_base": unrealized,
        "day_change_base": (
            _round(
                to_base(
                    row["day_change"],
                    row.get("quote_currency") or row.get("currency") or base,
                    base,
                )
            )
            if row.get("day_change") is not None
            else None
        ),
        "base_currency": base,
    }
    if not _mention_detail_only.get():
        data.update(_brief_context(row.get("instrument_id")))
        data.update(_analyst_context(row.get("instrument_id")))
    return data


def _resolve_instrument(ref: str) -> dict | None:
    inst = InstrumentStore().find_by_id(ref)
    if not inst:
        return None
    latest = PriceHistoryStore().latest_map().get(inst.get("price_source_id") or "")
    data = {
        "symbol": inst.get("symbol"),
        "name": inst.get("name"),
        "asset_class": inst.get("asset_class"),
        "asset_subclass": inst.get("asset_subclass"),
        "sector": inst.get("sector"),
        "quote_currency": inst.get("quote_currency"),
        "latest_price": latest,
        "dividend_rate_annual": inst.get("dividend_rate_annual"),
    }
    if not _mention_detail_only.get():
        data.update(_brief_context(ref))
        data.update(_analyst_context(ref))
    return data


def _resolve_account(ref: str) -> dict | None:
    from backend.services import ledger

    acct = AccountStore().find_by_id(ref)
    if not acct:
        return None
    hydrated = ledger.account_with_balance(acct)
    out = {
        "name": hydrated.get("name"),
        "kind": hydrated.get("kind"),
        "type": hydrated.get("type"),
        "currency": hydrated.get("currency"),
        "balance": hydrated.get("balance"),
        "balance_base": hydrated.get("balance_base"),
        "cleared_balance_base": hydrated.get("cleared_balance_base"),
        "base_currency": base_currency(),
    }
    custodian = hydrated.get("institution")
    if custodian:
        out["custodian"] = custodian
    return out


def _resolve_transaction(ref: str) -> dict | None:
    t = TransactionStore().find_by_id(ref)
    if not t or t.get("deleted"):
        return None
    out: dict = {
        "date": t.get("date"),
        "kind": t.get("kind"),
        "category": t.get("category"),
        "tags": t.get("tags") or [],
        "amount_base": _round(txn_base_amount(t)),
        "status": "Cleared" if t.get("cleared") else "Uncleared",
        "base_currency": base_currency(),
    }
    # Native leg amount/currency — DISTINCT from amount_base/base_currency above. Without
    # this a foreign-currency transaction (e.g. a VND charge on an SGD-base book) exposes
    # ONLY its base-currency-converted figure with no currency of its own attached, which a
    # model reading "the amount as stated in the source" (the add_transactions tool's own
    # instruction) will silently mislabel as the base currency — observed failure mode: a VND
    # rideshare refund referenced via mention got logged as "SGD 6.10" (the base-currency
    # figure) into a VND account, which the ledger then cross-currency-converted into a WRONG
    # VND amount, all because the mention never showed the real ₫125,000/VND figure at all.
    if t.get("kind") == "transfer":
        if t.get("from_amount") is not None:
            out["from_amount"] = _round(t["from_amount"])
            if t.get("from_currency"):
                out["from_currency"] = t["from_currency"]
        if t.get("to_amount") is not None:
            out["to_amount"] = _round(t["to_amount"])
            if t.get("to_currency"):
                out["to_currency"] = t["to_currency"]
    else:
        if t.get("kind") == "income":
            leg_amount, leg_currency = t.get("to_amount"), t.get("to_currency")
        elif t.get("from_amount") is not None:
            leg_amount, leg_currency = t.get("from_amount"), t.get("from_currency")
        else:
            leg_amount, leg_currency = t.get("to_amount"), t.get("to_currency")
        if leg_amount is not None:
            out["amount"] = _round(leg_amount)
        if leg_currency:
            out["currency"] = leg_currency
    # The charge BEFORE the bank converted it, when there was one. A card that settles in the
    # base currency leaves amount == amount_base, so without this the foreign side of the
    # purchase is invisible here and survives only in whatever the memo happens to say.
    if t.get("original_amount") is not None and t.get("original_currency"):
        out["original_amount"] = _round(t["original_amount"])
        out["original_currency"] = t["original_currency"]
    # Resolve foreign keys to NAMES. The model can't turn an opaque payee_id / account_id into a
    # name — without this it guesses (e.g. reads "VNTG" out of the description) or burns a tool
    # call that still returns the bare id. Names are exactly what a user asks about ("what's the
    # payee for this?"). The payee NAME is authoritative here even when it's an odd auto-created
    # one — Lucius reporting the real name beats it inventing a plausible wrong one.
    # `description` and `memo`, under their REAL names. This used to expose the description
    # as `note` — the field name retired by the 2026-07-17 note→description+memo split — and
    # never exposed `memo` at all.
    #
    # Observed consequence: asked to "delete the memo", the model had no memo in context and
    # a field labelled `note` holding the description. Its own reasoning went in circles —
    # "note=CORNER BAKERY… but that's the description field, not the memo… it's possible the
    # memo was indeed CORNER BAKERY" — it cleared the memo, and then could not restore it
    # because it had never seen it.
    #
    # The two fields are deliberately distinct (description = bank-origin, read by dedup and
    # rules; memo = the user's own annotation, read by nothing) so collapsing them into one
    # mislabelled key destroys exactly the distinction the split created.
    description = (t.get("description") or "").strip()
    if description:
        out["description"] = description
    memo = (t.get("memo") or "").strip()
    if memo:
        out["memo"] = memo
    pid = t.get("payee_id")
    if pid:
        from backend.db.payee_store import PayeeStore

        p = PayeeStore().find_by_id(pid)
        if p:
            out["payee"] = p.get("name")
    accts = AccountStore()
    from_acct = accts.find_by_id(t["from_account_id"]) if t.get("from_account_id") else None
    if from_acct:
        out["from_account"] = from_acct.get("name")
    to_acct = accts.find_by_id(t["to_account_id"]) if t.get("to_account_id") else None
    if to_acct:
        out["to_account"] = to_acct.get("name")
    # Single-leg "account" name (the side the row belongs to): the receiving leg for
    # income, the spending leg for expense, either side for an adjustment. Transfers
    # are two-legged and carry from_account/to_account instead.
    if t.get("kind") != "transfer":
        primary = to_acct if t.get("kind") == "income" else (from_acct or to_acct)
        if primary:
            out["account"] = primary.get("name")
    return out


def _target_amount(spec) -> float | None:
    """A budget row's target as a SCALAR amount — the mention payload never carries the raw
    target-spec dict (a dict renders as '[object Object]' in the client popover and as noisy
    repr text in the model fence)."""
    if isinstance(spec, dict):
        amt = spec.get("amount")
        return float(amt) if isinstance(amt, (int, float)) else None
    return float(spec) if isinstance(spec, (int, float)) else None


def _resolve_category(ref: str) -> dict | None:
    """A spending/envelope category, resolved from the CURRENT budget month's slice
    (assigned / activity / available / target / status) — bounded scalars, no txn list.

    ``ref`` is a REAL category key (the "category" mention type always references a category,
    never an envelope — docs/adr/2026-07-05-envelope-id-native.md: the row's own ``key`` is
    now the envelope id, which is a DIFFERENT entity). A mention for any member of a merged
    (N>1) envelope resolves to that row, echoing back the row's PRIMARY category
    (``category_ids[0]``) as the canonical "key" for this category mention.

    A category with NO budget row this month (income/transfer/investment kinds, or an
    unlinked expense category with no activity — everything the Manage-categories surface
    lists) falls back to its STORE identity (label/kind, ``budgeted=False``) instead of
    ``not_found`` (ADR 2026-07-11-envelope-first-class-mention-type)."""
    from backend.services import categories, planning

    month = _this_month()
    table = planning.budget_month(month)
    for grp in table.get("groups", []):
        for cat in grp.get("categories", []):
            cat_keys = cat.get("category_ids") or []
            if ref in cat_keys:
                return {
                    "key": cat_keys[0],
                    "label": cat.get("label"),
                    "group": grp.get("name"),
                    "month": month,
                    "assigned": cat.get("assigned"),
                    "activity": cat.get("activity"),
                    "available": cat.get("available"),
                    "target": _target_amount(cat.get("target")),
                    "status": cat.get("status"),
                    "base_currency": table.get("base_currency"),
                }
    # The structural "split" sentinel is a marker, not a real category — never resolvable
    # (mirrors search_category's store-leg exclusion).
    if ref in categories.STRUCTURAL_KEYS:
        return None
    row = next((r for r in categories.all_category_rows() if r.get("id") == ref), None)
    if not row:
        return None
    return {
        "key": row.get("id"),
        "label": row.get("label"),
        "kind": row.get("kind"),
        "budgeted": False,
    }


def _resolve_envelope(ref: str) -> dict | None:
    """A budget ENVELOPE — the Plan tab's row identity — resolved from the CURRENT
    month's slice by its own envelope id (``row.envelope_id``). Distinct from the
    "category" type (a transaction label's identity): a zero-category envelope has no
    category to mention, so the envelope itself is the mentionable entity. Payment
    envelopes additionally carry ``available_for_payment``/``underfunded``."""
    from backend.services import planning

    month = _this_month()
    table = planning.budget_month(month)
    for grp in table.get("groups", []):
        for row in grp.get("categories", []):
            if row.get("envelope_id") != ref:
                continue
            out = {
                "key": ref,
                "label": row.get("label"),
                "group": grp.get("name"),
                "month": month,
                "categories": len(row.get("category_ids") or []),
                "assigned": row.get("assigned"),
                "activity": row.get("activity"),
                "available": row.get("available"),
                "target": _target_amount(row.get("target")),
                "status": row.get("status"),
                "base_currency": table.get("base_currency"),
            }
            if row.get("is_payment_envelope"):
                out["available_for_payment"] = row.get("available_for_payment")
                out["underfunded"] = row.get("underfunded")
            return out
    return None


def _resolve_budget_month(ref: str) -> dict:
    """The Planning header for a month — RTA + totals, TRIMMED (no per-category rows)."""
    from backend.services import planning

    month = ref or _this_month()
    table = planning.budget_month(month)
    return {
        "month": table.get("month"),
        "ready_to_assign": table.get("ready_to_assign"),
        "on_budget_cash": table.get("on_budget_cash"),
        # EVERY term Ready to Assign is MADE of (ADR 2026-09-09 + 2026-09-10) — cash − baseline
        # − held in envelopes − held outside envelopes − uncovered card spend − the
        # future-assigned clamp — so a reader of this projection can close the identity without
        # a second fetch. The baseline and the clamp were missing from the first cut, which left
        # the arithmetic short on any book carrying either.
        "pre_budget_baseline": table.get("pre_budget_baseline"),
        "held_in_envelopes": table.get("held_in_envelopes"),
        "held_outside_envelopes": table.get("held_outside_envelopes"),
        "card_spend_not_covered": table.get("card_spend_not_covered"),
        "assigned_in_future_months": table.get("assigned_in_future_months"),
        "flow": table.get("flow"),
        "cash_overspending": table.get("cash_overspending"),
        "totals": table.get("totals"),
        "group_count": len(table.get("groups", [])),
        "base_currency": table.get("base_currency"),
    }


def _resolve_target(ref: str) -> dict | None:
    from backend.services import targets

    t = targets.get_hydrated(ref)
    if not t:
        return None
    return {
        "name": t.get("name"),
        "target_amount_base": t.get("target_amount_base"),
        "target_date": t.get("target_date"),
        "current_base": t.get("current_base"),
        "remaining_base": t.get("remaining_base"),
        "required_monthly_base": t.get("required_monthly_base"),
        "months_remaining": t.get("months_remaining"),
        "status": t.get("status"),
        "base_currency": t.get("base_currency"),
    }


def _resolve_allocation_slice(ref: str) -> dict | None:
    """A slice of the portfolio: ``asset_class:equity`` | ``currency:USD`` | ``account:<id>``.
    Folds ``positions.hydrated()`` into a value + count for that slice."""
    from backend.services import ledger
    from backend.services import positions as positions_service

    if ":" not in ref:
        return None
    axis, _, sel = ref.partition(":")
    axis = axis.strip().lower()
    if axis not in ("asset_class", "currency", "account"):
        return None
    sel_l = sel.strip().lower()
    base = base_currency()
    total = 0.0
    count = 0
    for row in positions_service.hydrated():
        if axis == "asset_class" and (row.get("asset_class") or "").lower() != sel_l:
            continue
        if axis == "currency" and (row.get("currency") or "").lower() != sel_l:
            continue
        if axis == "account" and (row.get("account_id") or "") != sel:
            continue
        total += ledger.row_base_value(row, base)
        count += 1
    return {
        "axis": axis,
        "selector": sel,
        "value_base": _round(total),
        "holdings": count,
        "base_currency": base,
    }


def _resolve_returns(ref: str) -> dict | None:
    from backend.services import returns

    period = (ref or "1Y").strip() or "1Y"
    result = returns.portfolio_returns(period)
    if not result:
        return None
    return {
        "period": result.get("period"),
        "twr": result.get("twr"),
        "mwr": result.get("mwr"),
        "period_gain_base": result.get("period_gain_base"),
        "start_value": result.get("start_value"),
        "end_value": result.get("end_value"),
        "base_currency": result.get("base_currency"),
    }


def _resolve_payee(ref: str) -> dict | None:
    from backend.db.payee_store import PayeeStore
    from backend.db.transaction_store import TransactionStore

    p = PayeeStore().find_by_id(ref)
    if not p:
        return None
    # LIVE txn count (matches Manage Payees / payee_registry.list_registry + the picker), NOT the
    # stored monotonic usage stat — so the popover agrees with both other surfaces.
    live_count = sum(
        1 for t in TransactionStore().all() if t.get("payee_id") == ref and not t.get("deleted")
    )
    return {
        "name": p.get("name"),
        "default_categories": dict(p.get("default_categories") or {}),
        "txn_count": live_count,
        "hidden": p.get("hidden", False),
    }


def _resolve_watchlist(ref: str) -> dict | None:
    from backend.db.watchlist_store import WatchlistStore

    wls = WatchlistStore().list_watchlists()
    wl = next((w for w in wls if w.get("id") == ref), None)
    if not wl:
        return None
    # item_count from the items list filtered to this watchlist
    try:
        store = WatchlistStore()
        item_count = store.count(watchlist_id=ref)
    except Exception:
        item_count = 0
    return {
        "name": wl.get("name"),
        "description": wl.get("description") or "",
        "item_count": item_count,
        "is_auto_sync": wl.get("is_auto_sync", False),
    }


def _resolve_automation(ref: str) -> dict | None:
    from backend.db.automation_store import AutomationStore
    from backend.services.automations.schedules import describe_trigger

    automation = AutomationStore().find_by_id(ref)
    if not automation:
        return None
    return {
        "name": automation.get("name"),
        "description": automation.get("description") or "",
        "instructions": str(automation.get("instructions") or "")[:2000],
        "trigger": describe_trigger(automation.get("trigger") or {"kind": "manual"}),
        "next_run_at": automation.get("next_run_at"),
        "status": automation.get("status"),
    }


def _resolve_skill(ref: str) -> dict | None:
    """A user skill, resolved to a bounded read-only summary for the chat turn.

    The ``ref`` is the skill id / slug — the same identifier ``activate_skill`` and
    ``resolve_slash_skills`` accept. Returns ``None`` for an unknown or missing skill.
    Never raises; a store hiccup returns None so the chat turn is not wedged.
    """
    try:
        from backend.db.skill_store import SkillStore

        s = SkillStore().find_by_id(ref)
    except Exception:
        return None
    if not s:
        return None
    return {
        "name": s.get("name"),
        "description": s.get("description") or "",
        "enabled": bool(s.get("enabled", True)),
    }
