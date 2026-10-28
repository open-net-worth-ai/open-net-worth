"""Pure PREVIEWS of ledger writes — what the row will actually look like once committed.

The confirm gate's whole value is that the card shows the RESOLVED outcome, not the model's
input echoed back. Category precedence (explicit > rule > payee-default > None) and the
spend-rules engine run silently inside ``create_transaction``, so an input echo teaches the
user nothing about the exact thing that goes wrong (measured: 17% of ``update_transaction``
calls are a redo of an edit the user rejected).

Every function here is PURE — it resolves through ``ledger.resolve_transaction_row(...,
mint_payee=False)``, which contains no store write at all, and reads label/name lookups.
Nothing in this module may create, append or upsert anything; that is ADR 2026-07-18
(`previews-must-not-write`), pinned by ``tests/test_ledger_confirm_gate.py``.

The dicts returned here are the ``proposed_change`` payloads the chat card renders. They are
self-describing (labels, not ids) because ``ChangePreview`` is deliberately type-agnostic.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.services import ledger

_log = logging.getLogger(__name__)

# How many rows of a bulk import the card renders. One card for N rows is the requirement —
# a bulk write that fans into N confirmations has just moved the round-trips from the API to
# the human — but the payload still has to stay a card, so the sample is capped and the
# totals below are computed over ALL rows regardless.
_BULK_SAMPLE_ROWS = 12


def _category_label(category_id: Optional[str], rows: Optional[list[dict]] = None) -> str:
    """Human label for a resolved category id. 'Uncategorized' is a REAL state, not a blank —
    a user who expected a category needs to see that none was applied."""
    if not category_id:
        return "Uncategorized"
    if category_id == "split":
        return "Split"
    from backend.services import categories as categories_service

    rows = rows if rows is not None else categories_service.all_category_rows()
    for row in rows:
        if row.get("id") == category_id:
            return (row.get("label") or "").strip() or category_id
    return category_id


def _account_name(account_id: Optional[str], accounts: Optional[list[dict]] = None) -> str:
    if not account_id:
        return ""
    accounts = accounts if accounts is not None else ledger.accounts.all()
    for acc in accounts:
        if acc.get("id") == account_id:
            return (acc.get("name") or "").strip() or account_id
    # An unresolved id should be impossible here (_validate_legs refuses one at resolve time
    # for transfers), but a non-transfer leg is not existence-checked, so show the raw id
    # rather than an empty cell — a wrong-looking id on the card is the point.
    return account_id


def _find_payee(payee_id: Optional[str]) -> Optional[dict]:
    """Look a payee up by id, tolerating a stale/dangling link. Goes through the registry's
    OWN store instance so the preview and the write always read the same book."""
    if not payee_id:
        return None
    from backend.services import payee_registry

    try:
        return payee_registry.require_payee(payee_id)
    except Exception:  # noqa: BLE001 — a dangling id renders as the raw id, never a crash
        return None


def _payee_block(row: dict, meta: dict) -> Optional[dict]:
    pid = row.get("payee_id")
    if pid:
        found = _find_payee(pid)
        name = (found or {}).get("name") or pid
        return {"id": pid, "name": name, "is_new": False}
    if meta.get("payee_is_new"):
        # Resolved read-only: no payee matched and the write WOULD mint one. Report the
        # intent instead of minting it (ADR 2026-07-18), the same shape the position card's
        # `account_to_is_new` uses.
        name = (row.get("payee") or row.get("description") or "").strip()
        return {"id": None, "name": name, "is_new": True} if name else None
    return None


def _leg(prefix: str, row: dict, accounts: list[dict]) -> Optional[dict]:
    account_id = row.get(f"{prefix}_account_id")
    amount = row.get(f"{prefix}_amount")
    if not account_id and amount is None:
        return None
    return {
        "account_id": account_id,
        "account_name": _account_name(account_id, accounts),
        "amount": amount,
        "currency": row.get(f"{prefix}_currency"),
    }


def _base_block(row: dict) -> Optional[dict]:
    if row.get("base_amount") is None:
        return None
    return {
        "amount": row.get("base_amount"),
        "currency": row.get("base_currency"),
        "fx_rate": row.get("fx_rate"),
    }


def _splits_block(row: dict, category_rows: list[dict]) -> Optional[list[dict]]:
    splits = row.get("splits")
    if not isinstance(splits, list) or not splits:
        return None
    return [
        {
            "category_id": s.get("category"),
            "category_label": _category_label(s.get("category"), category_rows),
            "amount": s.get("amount"),
            "memo": s.get("memo"),
        }
        for s in splits
        if isinstance(s, dict)
    ]


def _warnings_for(row: dict, accounts: list[dict]) -> list[str]:
    """Tracking-start honesty parity: the same flags the post-write result surfaces, shown
    BEFORE the write instead of after. Best-effort — a warning lookup must never sink a
    preview (the card is still correct without it)."""
    try:
        from backend.services import tracking_start
        from backend.services.ledger import transactions as _txns

        return list(tracking_start.flags_for_txn(row, accounts, _txns.all()) or [])
    except Exception:  # noqa: BLE001
        _log.debug("ledger_preview: tracking-start flags unavailable", exc_info=True)
        return []


def _resolved_card(row: dict, meta: dict, accounts: list[dict], category_rows: list[dict]) -> dict:
    """The shared body every ledger card carries: what this row RESOLVES to."""
    return {
        "txn_kind": row.get("kind"),
        "date": row.get("date"),
        "description": row.get("description") or "",
        "memo": row.get("memo"),
        "category": {
            "id": row.get("category"),
            "label": _category_label(row.get("category"), category_rows),
            "source": meta.get("category_source", "none"),
        },
        "automation": (
            {
                "id": meta["automation_hint"].automation_id,
                "name": meta["automation_hint"].automation_name,
            }
            if meta.get("automation_hint")
            else None
        ),
        "payee": _payee_block(row, meta),
        "from": _leg("from", row, accounts),
        "to": _leg("to", row, accounts),
        "base": _base_block(row),
        "splits": _splits_block(row, category_rows),
        "tags": row.get("tags") or [],
        "warnings": _warnings_for(row, accounts),
    }


def preview_create(data: dict, *, force_transfer_rate: bool = False) -> dict:
    """Preview ONE ``create_transaction``. Raises the same ValidationError/ValueError the
    write would — a draft that cannot commit must not produce a confirm card."""
    from backend.services import categories as categories_service

    row, meta = ledger.resolve_transaction_row(
        data, mint_payee=False, force_transfer_rate=force_transfer_rate
    )
    accounts = ledger.accounts.all()
    category_rows = categories_service.all_category_rows()
    return {"type": "txn_create", **_resolved_card(row, meta, accounts, category_rows)}


# Fields whose before/after the update card renders, with the label the card shows. Ordered
# most-consequential-first: the money, then what it is, then how it is described.
_DIFF_FIELDS = (
    ("date", "Date"),
    ("kind", "Kind"),
    ("category", "Category"),
    ("from_account_id", "From account"),
    ("from_amount", "From amount"),
    ("from_currency", "From currency"),
    ("to_account_id", "To account"),
    ("to_amount", "To amount"),
    ("to_currency", "To currency"),
    ("description", "Description"),
    ("memo", "Memo"),
    ("payee_id", "Payee"),
    ("tags", "Tags"),
    ("base_amount", "Value in base"),
)


def _display_value(field: str, value, accounts: list[dict], category_rows: list[dict]):
    """Render a stored value as the card should show it — a resolved id becomes its NAME.
    An id-vs-id diff is unreadable, and unreadable is how a wrong edit gets confirmed."""
    if value is None or value == "":
        return None
    if field == "category":
        return _category_label(value, category_rows)
    if field in ("from_account_id", "to_account_id"):
        return _account_name(value, accounts)
    if field == "payee_id":
        found = _find_payee(value)
        return (found or {}).get("name") or value
    return value


def preview_update(txn_id: str, changes: dict) -> dict:
    """Preview ONE ``update_transaction`` as a resolved before → after diff.

    Mirrors ``ledger.update_transaction``'s own rebuild — same merge, same
    ``category_explicit`` flag, same base-amount preserve rule — so the diff shown is the
    diff that lands. Returns a dict with ``error`` when the row is missing/refused, so the
    caller reports it instead of staging an unconfirmable plan.

    **Coupling to watch:** the real ``update_transaction`` takes ``preserve_base``, and this
    mirror hardcodes the ``preserve_base=False`` branch. That is correct only because the one
    caller that passes ``True`` is ``merge_transactions``, which is not gated and never
    reaches this preview. Gating a merge, or threading ``preserve_base`` through the AI tool,
    must add the parameter here too — otherwise the card would show a repriced base amount
    that the write then preserves. Pinned by
    ``test_ledger_confirm_gate.test_preview_update_mirrors_the_real_edit``."""
    from backend.services import categories as categories_service

    existing = ledger.transactions.find_by_id(txn_id)
    if existing is None:
        return {"error": f"transaction '{txn_id}' not found"}
    # The two refusals the real edit raises BEFORE its rebuild. Mirrored here so they surface
    # on the card instead of after the ✓ — the tool description advertises the Starting-Balance
    # refusal as up-front, and a card for an edit that can never apply is worse than an error.
    if ledger._is_opening_txn(existing):
        return {
            "error": (
                "the Starting Balance transaction can't be edited directly — "
                "change the account's opening balance/date instead (update_accounts)"
            )
        }
    if changes.get("payee_id"):
        from backend.services import payee_registry

        try:
            payee_registry.require_payee(changes["payee_id"])
        except Exception as exc:  # noqa: BLE001 — surface the service's own message verbatim
            return {"error": str(exc)}

    merged = {**existing, **changes}
    # The edit path treats a settled category as explicit so the rules engine can't
    # re-categorize it — preview under the SAME flag or the card would show a rule firing
    # that the real edit will not fire.
    if merged.get("category"):
        merged["category_explicit"] = True
    _leg_changed = bool({"amount", "currency"} & set(changes)) or any(
        merged.get(k) != existing.get(k)
        for k in ("from_amount", "from_currency", "to_amount", "to_currency")
    )
    if "base_amount" not in changes and _leg_changed:
        merged.pop("base_amount", None)
        merged.pop("fx_rate", None)
        merged.pop("base_currency", None)
    if "external_refs" in changes:
        merged["external_refs"] = ledger.resolve_external_refs_patch(
            txn_id, existing, changes["external_refs"]
        )
    else:
        merged.pop("external_refs", None)

    row, meta = ledger.resolve_transaction_row(merged, mint_payee=False, skip_payee_resolve=True)
    if "base_amount" not in changes and not _leg_changed:
        row["base_amount"] = existing.get("base_amount")
        row["base_currency"] = existing.get("base_currency")
        row["fx_rate"] = existing.get("fx_rate")

    accounts = ledger.accounts.all()
    category_rows = categories_service.all_category_rows()
    fields = []
    for field, label in _DIFF_FIELDS:
        before = _display_value(field, existing.get(field), accounts, category_rows)
        after = _display_value(field, row.get(field), accounts, category_rows)
        if before != after:
            fields.append({"field": field, "label": label, "before": before, "after": after})
    if "external_refs" in changes:
        before = existing.get("external_refs") or []
        after = merged.get("external_refs") or []
        if before != after:
            fields.append(
                {
                    "field": "external_refs",
                    "label": "External references",
                    "before": before,
                    "after": after,
                }
            )
    return {
        "type": "txn_update",
        "transaction_id": txn_id,
        "fields": fields,
        **_resolved_card(row, meta, accounts, category_rows),
    }


def preview_bulk(data_list: list[dict], *, import_description: str | None = None) -> dict:
    """Preview a MULTI-ROW add as ONE card — a compact resolved sample plus totals over
    every row. One card for N rows is the contract: fanning a bulk write into N
    confirmations just moves the round-trips from the API to the human.

    Raises whatever the first invalid row raises, matching the write's all-or-nothing
    validation (``bulk_create_transactions`` validates every row before creating any)."""
    from backend.core.fx import _load_rates
    from backend.services import categories as categories_service
    from backend.services.automations.transaction_events import active_transaction_transforms

    accounts = ledger.accounts.all()
    category_rows = categories_service.all_category_rows()
    # Preload every store the resolver would otherwise re-read PER ROW — the same one-read
    # optimisation bulk_create_transactions does inline. Without it a 500-row preview costs
    # thousands of JSON reads, synchronously on the event loop, to preview a write that reads
    # each store once. Found by security review.
    automations = active_transaction_transforms()
    # core.fx._load_rates, NOT services.fx_cache.load_fx_cache — they are only incidentally
    # the same table. _load_rates goes through the loader seam that to_base(rates=None) uses;
    # load_fx_cache reads the cache file directly and bypasses it. A card that priced its
    # base_amount off a different table than the write would either promise a number the
    # write does not store, or trip the confirm gate's own outcome-mismatch refusal on a
    # book that never actually changed. bulk_create_transactions preloads from this same
    # function, so preview and write are the same computation by construction.
    rates = _load_rates()
    base = ledger.base_currency()

    rows: list[dict] = []
    metas: list[dict] = []
    for data in data_list:
        row, meta = ledger.resolve_transaction_row(
            data,
            mint_payee=False,
            automations=automations,
            category_rows=category_rows,
            rates=rates,
            account_rows=accounts,
            base=base,
        )
        rows.append(row)
        metas.append(meta)

    inflow = outflow = 0.0
    automation_counts: dict[str, dict] = {}
    warnings: list[str] = []
    for row, meta in zip(rows, metas):
        base_amount = row.get("base_amount")
        if base_amount is not None:
            # Classify by KIND, not by which leg is populated. A transfer between the user's
            # own accounts is neither an inflow nor an outflow — counting it as spending (the
            # naive "has a from-leg" test) would overstate the batch's outflow, which is
            # exactly the kind of wrong number this card exists to prevent. An adjustment is
            # a balance correction, likewise neither.
            kind = row.get("kind")
            if kind in ("transfer", "adjustment"):
                pass
            elif kind == "income" or (row.get("to_account_id") and not row.get("from_account_id")):
                inflow += abs(float(base_amount))
            else:
                outflow += abs(float(base_amount))
        hint = meta.get("automation_hint")
        if hint and hint.automation_id:
            entry = automation_counts.setdefault(
                hint.automation_id,
                {"id": hint.automation_id, "name": hint.automation_name, "count": 0},
            )
            entry["count"] += 1
        for warning in _warnings_for(row, accounts):
            if warning not in warnings:
                warnings.append(warning)

    # Over EVERY row, not the sample: the card names the account once when the whole import
    # is one account, and the capped sample can't prove that.
    account_ids = {row.get("from_account_id") or row.get("to_account_id") for row in rows}
    account_names = sorted({_account_name(aid, accounts) for aid in account_ids} - {""})

    sample = []
    for row, meta in list(zip(rows, metas))[:_BULK_SAMPLE_ROWS]:
        amount = row.get("from_amount")
        currency = row.get("from_currency")
        account_id = row.get("from_account_id")
        if amount is None:
            amount, currency, account_id = (
                row.get("to_amount"),
                row.get("to_currency"),
                row.get("to_account_id"),
            )
        sample.append(
            {
                "date": row.get("date"),
                "description": row.get("description") or "",
                "category_label": _category_label(row.get("category"), category_rows),
                "category_source": meta.get("category_source", "none"),
                "amount": amount,
                "currency": currency,
                "base_amount": row.get("base_amount"),
                "account_name": _account_name(account_id, accounts),
            }
        )

    return {
        "type": "txn_bulk",
        "count": len(rows),
        "import_description": import_description,
        "rows": sample,
        "truncated": len(sample) < len(rows),
        "totals": {
            "base_currency": base,
            "inflow_base": round(inflow, 2),
            "outflow_base": round(outflow, 2),
        },
        "automations_applied": sorted(automation_counts.values(), key=lambda row: -row["count"]),
        "account_names": account_names,
        "warnings": warnings,
    }
