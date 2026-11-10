"""Owned-assets service — the Assets tab domain (ADR 2026-09-06-assets-tab-faithful-port).

ONE write choke, ``apply_owned_asset_change(change)``, dispatching on ``change["op"]``:
``create | update | add_mark | correct_mark | remove_mark | dispose | delete | set_photo |
add_doc | update_doc | remove_doc | create_category``. The router (``api/assets_api.py``) and
Lucius's tools validate shapes and call it. Every op is one function, living with the concern
it writes, and every read-modify-write runs in ONE ``locked_unit_of_work``:

    owned_assets_rules        every refusal — bounds, parsing, reference checks
    owned_assets_marks        the value history (add / correct / remove a valuation)
    owned_assets_disposal     dispose, and the proceeds-account picker it validates against
    owned_assets_attachments  the photo and document ops
    owned_assets_summary      the register's summary block (server money math)
    owned_assets_valuation    the value rule — the net-worth seam ``ledger.py`` imports

Builtin categories (``CATEGORIES``) are a module constant, never stored — only a
user-created category lives in ``OwnedAssetCategoriesStore``. ``categories()`` merges the
two, customs inserted before "Other" so "Other" is always the last choice in a picker.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from backend.core.errors import NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.owned_assets_store import (
    OwnedAssetCategoriesStore,
    OwnedAssetsStore,
)
from backend.db.tables import owned_assets as owned_assets_table
from backend.services import attachments as attachments_service
from backend.services import ledger as ledger_service
from backend.services import (
    owned_assets_attachments,
    owned_assets_disposal,
    owned_assets_marks,
    owned_assets_summary,
)
from backend.services import owned_assets_rules as rules
from backend.services import portfolio_analytics as portfolio_analytics_service
from backend.services.fx_cache import load_fx_cache
from backend.services.owned_assets_disposal import cash_accounts
from backend.services.owned_assets_rules import (  # noqa: F401 — re-export: the wire bounds api/ reads
    MAX_CATEGORY_LABEL,
    MAX_DESCRIPTION,
    MAX_FACT_FIELD,
    MAX_FACTS,
    MAX_MARK_NOTE,
    MAX_NAME,
    MAX_NOTES,
    MAX_SHARE_NOTE,
)
from backend.services.owned_assets_summary import LedgerSnapshot
from backend.services.owned_assets_valuation import series

_log = logging.getLogger(__name__)

# ── Builtin categories (never stored — services.owned_assets is the one source) ────────────

CATEGORIES: list[dict] = [
    {
        "key": "property",
        "label": "Property",
        "plural": "Property",
        "color": "var(--cat-property)",
        "custom": False,
    },
    {
        "key": "vehicle",
        "label": "Vehicle",
        "plural": "Vehicles",
        "color": "var(--cat-vehicle)",
        "custom": False,
    },
    {
        "key": "watch",
        "label": "Watch & jewellery",
        "plural": "Watches & jewellery",
        "color": "var(--cat-watch)",
        "custom": False,
    },
    {
        "key": "art",
        "label": "Art & collectibles",
        "plural": "Art & collectibles",
        "color": "var(--cat-art)",
        "custom": False,
    },
    {
        "key": "metal",
        "label": "Precious metals",
        "plural": "Precious metals",
        "color": "var(--cat-metal)",
        "custom": False,
        "hint": (
            "Physical bullion and coins you hold. Gold ETFs like GLD are market positions "
            "and already live under Investments."
        ),
    },
    {
        "key": "policy",
        "label": "Insurance & endowments",
        "plural": "Insurance & endowments",
        "color": "var(--cat-policy)",
        "custom": False,
        "hint": (
            "Only policies with a cash value — whole life, endowments, ILPs — valued at "
            "surrender value from the annual statement. Term, health and car cover have no "
            "value and belong in Plan."
        ),
    },
    {
        "key": "other",
        "label": "Other",
        "plural": "Other",
        "color": "var(--cat-other)",
        "custom": False,
    },
]

# The prototype's CUSTOM_COLORS ring — a fresh user-created category cycles through these.
CUSTOM_COLORS = (
    "var(--recv)",
    "var(--crypto)",
    "var(--contingent)",
    "var(--liquid)",
    "var(--ibkr)",
    "var(--warn)",
)


def _today() -> str:
    return user_today().isoformat()


# ── Categories ───────────────────────────────────────────────────────────────────────────


def categories() -> list[dict]:
    """Builtins, with any user-created category inserted before "Other" — "Other" stays
    the last choice in every picker."""
    customs = OwnedAssetCategoriesStore().all()
    builtins = [c for c in CATEGORIES if c["key"] != "other"]
    other = next(c for c in CATEGORIES if c["key"] == "other")
    return [*builtins, *customs, other]


def _category_by_key(key: str | None) -> dict | None:
    for cat in categories():
        if cat["key"] == key:
            return cat
    return None


def _require_category(key: str | None) -> dict:
    cat = _category_by_key(key)
    if cat is None:
        raise ValidationError(
            f"Unknown category '{key}' — pick one from the list.", field="category"
        )
    return cat


def create_category(label: str) -> dict:
    label = str(label or "").strip()
    if not label:
        raise ValidationError("Give the category a name (up to 60 characters).", field="label")
    label = rules.bounded_text(label, MAX_CATEGORY_LABEL, "label")
    # Idempotent against the WHOLE category set (builtins included) — re-adding "Property"
    # returns the builtin rather than shadowing it with a duplicate custom row.
    for cat in categories():
        if cat["label"].strip().lower() == label.lower():
            return cat
    store = OwnedAssetCategoriesStore()
    color = CUSTOM_COLORS[len(store.all()) % len(CUSTOM_COLORS)]
    return store.create(label, color=color)


# ── Loans (a projection over the ledger's own accounts) ──────────────────────────────────


def _ledger_snapshot(base: str, book: ledger_service.LedgerBook | None = None) -> LedgerSnapshot:
    return LedgerSnapshot(base, rates=load_fx_cache(), book=book)


def loans(rows: list[dict] | None = None, ledger: LedgerSnapshot | None = None) -> list[dict]:
    """Every non-archived liability account of a loan type, ledger-computed balance, and
    `held_by` — the live asset it secures, or None. `rows` (the stored assets) and `ledger`
    (the page's shared snapshot) are passed by a caller that already loaded them."""
    if ledger is None:
        ledger = _ledger_snapshot(ledger_service.base_currency())
    holders = rules.loan_holders(rows if rows is not None else OwnedAssetsStore().all())
    result = []
    for account in ledger.accounts:
        # Type alone, never `kind_of` as well: `loan` IS a liability, so the second test was
        # redundant — and it raised on a row this build cannot classify, turning a legacy
        # account anywhere in the book into a 500 on GET /api/assets.
        if account.get("archived") or account.get("type") not in rules.LOAN_TYPES:
            continue
        apr = account.get("apr")
        payoff = account.get("payoff_date")
        result.append(
            {
                "id": account["id"],
                "name": account.get("name") or "",
                "balance": abs(ledger.balance_base(account)),
                "rate_label": f"{apr:g}%" if apr else "",
                "term_label": f"to {payoff[:4]}" if payoff else "",
                "held_by": holders.get(account["id"]),
            }
        )
    return result


# ── Read projection ──────────────────────────────────────────────────────────────────────


def _live_attachments(rows: list[dict]) -> dict[str, dict]:
    """Which of these assets' photo/document refs still have an attachment, each with its
    filename, media type and size — one lookup."""
    refs = {asset.get("photo_ref") for asset in rows}
    refs |= {doc.get("ref_id") for asset in rows for doc in asset.get("docs") or []}
    return attachments_service.attachment_file_meta(refs - {None})


def _doc_view(doc: dict, meta: dict) -> dict:
    # What the file is — for its preview and thumbnail; a renamed doc's name no longer says.
    return {
        **doc,
        "filename": meta.get("filename"),
        "media_type": meta.get("media_type"),
        "size_bytes": meta.get("size_bytes"),
    }


def project(asset: dict, *, attachments: dict[str, dict] | None = None) -> dict:
    """The wire shape of one asset. `marks` is the value series — the stored valuations plus
    the derived purchase point, each with its `kind` — and `value`/`valued_on` come from its
    current point, always the newest valuation (``owned_assets_valuation``). `share_value` is
    the owner's share of that value in the asset's OWN currency: converting here could raise a
    missing-rate error after a write had already committed. None of these are ever stored.

    A photo or document whose attachment no longer exists is left out — attachments sit
    outside checkpoints, so an undo can restore a row whose file was deleted since, and the
    page must never show a broken link. `attachments` maps the refs that still exist,
    passed by a caller projecting many rows (each with its file's name, type and size, which
    every doc carries); omitted, it is looked up for this asset."""
    if attachments is None:
        attachments = _live_attachments([asset])
    docs = [
        _doc_view(doc, attachments[doc["ref_id"]])
        for doc in asset.get("docs") or []
        if doc.get("ref_id") in attachments
    ]
    photo = asset.get("photo_ref") if asset.get("photo_ref") in attachments else None
    marks = series(asset)
    current = marks[0] if marks else None
    value = current["value"] if current else None
    share = asset.get("share_pct") if asset.get("share_pct") is not None else 100
    return {
        **asset,
        "photo_ref": photo,
        "docs": docs,
        "marks": marks,
        "value": value,
        "valued_on": current["date"] if current else None,
        "share_value": None if value is None else round(value * share / 100, 2),
    }


def list_assets(include_archived: bool = False, category: str | None = None) -> list[dict]:
    rows = OwnedAssetsStore().query_assets(category=category, include_archived=include_archived)
    attachments = _live_attachments(rows)
    return [project(a, attachments=attachments) for a in rows]


def get_asset(asset_id: str) -> dict:
    return project(rules.require_asset(OwnedAssetsStore(), asset_id))


def summary(base: str | None = None) -> dict:
    """The register's summary block (``owned_assets_summary``), read on its own."""
    base = (base or ledger_service.base_currency()).upper()
    ledger = _ledger_snapshot(base)
    return owned_assets_summary.summary(
        OwnedAssetsStore().query_assets(include_archived=False),
        base,
        net_worth=ledger_service.net_worth(base),
        market=portfolio_analytics_service.market_book(base),
        ledger=ledger,
    )


def get_assets_page(include_archived: bool = False, base: str | None = None) -> dict:
    """The whole `GET /api/assets` payload — the ONE call the router makes. The book is read
    once: one asset read, one ledger snapshot every balance on the page folds (loans, the
    proceeds picker, the Cash card), ONE net-worth fold and one market projection."""
    started = time.perf_counter()
    base = (base or ledger_service.base_currency()).upper()
    book = ledger_service.read_book()
    ledger = _ledger_snapshot(base, book)
    rows = OwnedAssetsStore().all()
    live = [a for a in rows if not a.get("archived")]
    loan_rows, cash_rows = loans(rows, ledger), cash_accounts(ledger)
    balanced = time.perf_counter()
    net_worth = ledger_service.net_worth(base, book=book)
    folded = time.perf_counter()
    market = portfolio_analytics_service.market_book(base, book=book)
    priced = time.perf_counter()
    shown = rows if include_archived else live
    attachments = _live_attachments(shown)
    projected = [project(a, attachments=attachments) for a in shown]
    page = {
        "assets": [
            {**a, **owned_assets_summary.base_figures(a, base, ledger.rates)} for a in projected
        ],
        "categories": categories(),
        "loans": loan_rows,
        "cash_accounts": cash_rows,
        "summary": owned_assets_summary.summary(
            live, base, net_worth=net_worth, market=market, ledger=ledger
        ),
        "base_currency": base,
    }
    _log.info(
        "PERF assets_page net_worth=%.1fms book=%.1fms loans+cash=%.1fms "
        "assets+composition=%.1fms (assets=%d, accounts=%d, txns=%d)",
        (folded - balanced) * 1000,
        (priced - folded) * 1000,
        (balanced - started) * 1000,
        (time.perf_counter() - priced) * 1000,
        len(rows),
        len(ledger.accounts),
        len(ledger.txns),
    )
    return page


def discard_upload(ref_id: str) -> None:
    """A file an asset upload route saved for a write that then failed or was refused: deleted
    unless an owned asset references it after all — the write may have committed before the
    failure (security review 2026-09-26, S3)."""
    owned_assets_attachments.discard_unreferenced(OwnedAssetsStore(), ref_id)


# ── Identity ops (create / update / delete) ──────────────────────────────────────────────


def _create(payload: dict) -> dict:
    rules.refuse_retired(payload)
    share = payload.get("share_pct")
    # Field order is refusal order: a dict display evaluates left to right, so the first
    # invalid field in form order is the one reported.
    row = {
        "name": rules.asset_name(payload.get("name")),
        "category": _require_category(payload.get("category"))["key"],
        # Exactly ONE valuation, dated today — the purchase point is derived from
        # cost/cost_date on read, never written.
        "marks": [
            {
                "date": _today(),
                "value": rules.positive_value(payload.get("value")),
                "note": rules.bounded_text(payload.get("note") or "", MAX_MARK_NOTE, "note"),
                "by": "you",
            }
        ],
        "description": rules.bounded_text(
            payload.get("description", ""), MAX_DESCRIPTION, "description"
        ),
        "photo_ref": payload.get("photo_ref"),
        "facts": rules.bounded_facts(payload.get("facts")),
        "notes": rules.bounded_text(payload.get("notes", ""), MAX_NOTES, "notes"),
        "share_pct": 100.0 if share is None else rules.share_pct(share),
        "share_note": rules.bounded_text(
            payload.get("share_note", ""), MAX_SHARE_NOTE, "share_note"
        ),
        "loan_account_id": rules.validate_loan_account_id(payload.get("loan_account_id")),
        "cost": rules.cost(payload.get("cost")),
        "cost_date": rules.parse_day(
            payload.get("cost_date"), field="cost_date", future=rules.FUTURE_PURCHASE
        ),
        "insured": rules.insured_flag(payload.get("insured", False)),
        "maturity_date": rules.parse_day(payload.get("maturity_date"), field="maturity_date"),
        "currency": ledger_service.base_currency(),
    }
    store = OwnedAssetsStore()
    with locked_unit_of_work(owned_assets_table) as conn:
        if row["loan_account_id"]:
            rules.assert_loan_free(row["loan_account_id"], asset_id=None, rows=store.all(conn=conn))
        return store.create(row, conn=conn)


def _update(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    """Identity fields only — never a value. The purchase point follows cost/cost_date/
    category because it is derived; a CHANGED cost_date must pass neither the first value nor
    the sale."""
    changes = dict(change.get("changes") or {})
    rules.refuse_retired(changes)
    rules.refuse_uneditable(changes)
    clean = rules.identity_changes(changes)
    if "category" in clean:
        clean["category"] = _require_category(clean["category"])["key"]
    with locked_unit_of_work(owned_assets_table) as conn:
        asset = rules.require_asset(store, asset_id, conn=conn)
        if "cost_date" in clean and clean["cost_date"] != asset.get("cost_date"):
            rules.refuse_purchase_after_first_value(clean["cost_date"], asset)
            rules.refuse_purchase_after_sale(clean["cost_date"], asset)
        if "loan_account_id" in clean:
            clean["loan_account_id"] = rules.changed_loan_link(
                store, asset, clean["loan_account_id"], conn=conn
            )
        return store.update_asset(asset_id, clean, conn=conn)


def _delete(store: OwnedAssetsStore, asset_id: str) -> dict:
    with locked_unit_of_work(owned_assets_table) as conn:
        rules.require_asset(store, asset_id, conn=conn)
        store.delete(asset_id, conn=conn)
    return {"ok": True, "id": asset_id}


# ── The ONE write choke ─────────────────────────────────────────────────────────────────────

_ASSET_OPS: dict[str, Callable[[OwnedAssetsStore, str, dict], dict | None]] = {
    "update": _update,
    "add_mark": owned_assets_marks.add_mark,
    "correct_mark": owned_assets_marks.correct_mark,
    "remove_mark": owned_assets_marks.remove_mark,
    "dispose": owned_assets_disposal.dispose,
    "set_photo": owned_assets_attachments.set_photo,
    "add_doc": owned_assets_attachments.add_doc,
    "update_doc": owned_assets_attachments.update_doc,
    "remove_doc": owned_assets_attachments.remove_doc,
}


def apply_owned_asset_change(change: dict) -> dict:
    rules.refuse_retired(change)
    op = change.get("op")
    if op == "create":
        return project(_create(change.get("asset") or change))
    if op == "create_category":
        return create_category(change.get("label"))

    asset_id = change.get("id")
    if not asset_id:
        raise ValidationError("An asset id is required.")
    store = OwnedAssetsStore()
    if op == "delete":
        return _delete(store, asset_id)
    handler = _ASSET_OPS.get(op)
    if handler is None:
        raise ValidationError(f"Unknown asset change '{op}'.")
    written = handler(store, asset_id, change)
    if written is None:
        raise NotFoundError(f"That asset ('{asset_id}') no longer exists.")
    return project(written)
