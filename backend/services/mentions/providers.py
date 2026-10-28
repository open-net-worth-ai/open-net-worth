"""Per-type mention search providers.

Each ``search_<type>`` function accepts a ``SearchQuery`` and returns a list of row dicts
with shape ``{type, id, label, sublabel, value, icon}`` where ``value`` equals the ref
the existing ``_resolve_<type>`` dereferences.

INVARIANT: value must equal the ref that the resolver expects, so a user picking a search
result can immediately be resolved by dispatch.resolve_mentions.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services.mentions._common import _ROW_CAP, _matches, _row, _type_synonym_match
from backend.services.mentions._corpus import corpus
from backend.services.mentions.types import SearchQuery

# ── transaction ───────────────────────────────────────────────────────────────


def _txn_signed_amount(t: dict) -> str:
    """Signed native-leg amount for a transaction mention sublabel: '-' for expense (cash out),
    '+' for income (cash in); transfers/adjustments carry no inflow/outflow sign."""
    kind = t.get("kind")
    if kind == "income":
        amount = t.get("to_amount") or t.get("from_amount") or 0
        currency = t.get("to_currency") or t.get("from_currency") or ""
        sign = "+"
    elif kind == "expense":
        amount = t.get("from_amount") or t.get("to_amount") or 0
        currency = t.get("from_currency") or t.get("to_currency") or ""
        sign = "-"
    else:  # transfer / adjustment — a movement between accounts, not an inflow/outflow
        amount = t.get("from_amount") or t.get("to_amount") or 0
        currency = t.get("from_currency") or t.get("to_currency") or ""
        sign = ""
    return f"{sign}{amount} {currency}".strip()


def _txn_payee(t: dict, payee_names: dict[str, str]) -> str:
    """The transaction's payee NAME, or '' when it has none. Shown even when it repeats the
    description (some imports mint a payee from the raw description, e.g. 'ORCL BUY 5 @ 170 - VNTG'):
    consistency — every row that HAS a payee shows it — beats de-duping, and the visible repetition
    surfaces the junk payee so it can be cleaned up rather than looking like missing data."""
    pid = t.get("payee_id")
    return payee_names.get(pid or "", "") if pid else ""


def _txn_account(t: dict, account_names: dict[str, str]) -> str:
    """The account the money moved through (from-leg, else to-leg) resolved to its NAME. Always
    shown in the sublabel — it's the one consistently-present 'where' for any transaction."""
    acct_id = t.get("from_account_id") or t.get("to_account_id")
    return account_names.get(acct_id or "", "")


def search_transaction(query: SearchQuery) -> list[dict]:
    from backend.db.account_store import AccountStore
    from backend.db.payee_store import PayeeStore
    from backend.db.transaction_store import TransactionStore

    q = (query.q or "").strip().lower()
    # Check synonym match BEFORE the name-match filter so "spending"/"activity" etc. surface all rows.
    synonym_hint = _type_synonym_match("transaction", q) if q else None
    store = TransactionStore()
    txns = (
        store.query_txns(q=q or None, include_deleted=False)
        if (q and not synonym_hint)
        else store.query_txns(include_deleted=False)
    )
    # Name maps for the sublabel "who". `.all()` includes archived accounts so an old txn that
    # references a since-archived account still resolves to a readable name.
    payee_names = {p["id"]: (p.get("name") or "") for p in PayeeStore().all()}
    account_names = {a["id"]: (a.get("name") or "") for a in AccountStore().all()}
    out: list[dict] = []
    for t in txns:
        if synonym_hint or not q or _matches(q, t.get("description"), t.get("category")):
            # sublabel: "{payee} • {account} • {date} • {signed amount}" — empty parts dropped.
            # Payee AND account are distinct fields, both shown when present, so a row never
            # silently swaps fields (the account is the consistently-present 'where').
            payee = _txn_payee(t, payee_names)
            acct = _txn_account(t, account_names)
            parts = [p for p in (payee, acct, t.get("date", ""), _txn_signed_amount(t)) if p]
            sublabel = " • ".join(parts)
            out.append(
                _row(
                    "transaction",
                    t["id"],
                    t.get("description") or t["id"],
                    sublabel,
                    t["id"],
                    search=synonym_hint,
                )
            )
        if len(out) >= _ROW_CAP:
            break
    return out


# ── account ────────────────────────────────────────────────────────────────────


def _account_balances() -> list[tuple[dict, float]]:
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger

    accounts = AccountStore().query_accounts()
    txns = TransactionStore().all()
    idx = ledger.opening_index(txns, accounts)
    return [
        (a, ledger.account_with_balance(a, txns, idx=idx).get("balance_base", 0) or 0)
        for a in accounts
    ]


def search_account(query: SearchQuery) -> list[dict]:
    from backend.core.fx import base_currency

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("account", q) if q else None
    base = base_currency()
    out: list[dict] = []
    for a, bal in corpus(_account_balances):
        if q and not synonym_hint and not _matches(q, a.get("name")):
            continue
        sublabel = f"{bal} {base}"
        out.append(
            _row(
                "account",
                a["id"],
                a.get("name", a["id"]),
                sublabel,
                a["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


# ── instrument ─────────────────────────────────────────────────────────────────


def _all_instruments() -> list[dict]:
    from backend.db.instrument_store import InstrumentStore

    return InstrumentStore().all()


def search_instrument(query: SearchQuery) -> list[dict]:
    from backend.services import positions as positions_service

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("instrument", q) if q else None
    # Exclude instruments the user currently holds — they already surface as Holding rows,
    # so showing them again here as Securities is redundant and confusing.
    held_ids = {
        row.get("instrument_id")
        for row in corpus(positions_service.hydrated)
        if row.get("instrument_id")
    }
    instruments = corpus(_all_instruments)
    out: list[dict] = []
    for i in instruments:
        if i["id"] in held_ids:
            continue
        symbol = i.get("symbol") or ""
        name = i.get("name") or ""
        if q and not synonym_hint and not _matches(q, symbol, name):
            continue
        label = symbol or name
        sublabel = name if symbol else (i.get("asset_class") or "")
        out.append(
            _row(
                "instrument",
                i["id"],
                label,
                sublabel,
                i["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


# ── holding ────────────────────────────────────────────────────────────────────


def search_holding(query: SearchQuery) -> list[dict]:
    from backend.core.fx import base_currency
    from backend.services import ledger
    from backend.services import positions as positions_service

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("holding", q) if q else None
    base = base_currency()
    out: list[dict] = []
    for row in corpus(positions_service.hydrated):
        symbol = row.get("symbol") or ""
        label_field = row.get("label") or ""
        account_name = row.get("account_name") or ""
        if q and not synonym_hint and not _matches(q, symbol, label_field, account_name):
            continue
        # Sublabel leads with the holding's VALUE (base, recomputed via row_base_value — never a
        # stored amount), matching the chip + the user's "show a number" expectation; account is
        # secondary. Falls back to account•qty when the value can't be computed (e.g. unpriced).
        value_base = ledger.row_base_value(row, base) or 0
        qty = row.get("quantity", 0) or 0
        if value_base:
            sublabel = f"{base} {value_base:,.0f}"
            if account_name:
                sublabel += f" • {account_name}"
        else:
            sublabel = f"{account_name} • {qty}".strip(" •")
        out.append(
            _row(
                "holding",
                row.get("position_id", ""),
                symbol or label_field,
                sublabel,
                row.get("position_id", ""),
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


# ── category ───────────────────────────────────────────────────────────────────


def search_category(query: SearchQuery) -> list[dict]:
    from backend.services import categories, planning

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("category", q) if q else None
    today = user_today()
    month = f"{today.year:04d}-{today.month:02d}"
    table = corpus(planning.budget_month, month)
    out: list[dict] = []
    seen: set[str] = set()
    for grp in table.get("groups", []):
        grp_name = grp.get("name") or ""
        for cat in grp.get("categories", []):
            # A zero-category envelope has no real category to reference — the "category"
            # mention type is a real category's own identity, never the row's envelope id
            # (docs/adr/2026-07-05-envelope-id-native.md).
            cat_keys = cat.get("category_ids") or []
            if not cat_keys:
                continue
            seen.update(cat_keys)
            primary = cat_keys[0]
            cat_label = cat.get("label") or ""
            if q and not synonym_hint and not _matches(q, cat_label):
                continue
            out.append(
                _row(
                    "category",
                    primary,
                    cat_label,
                    grp_name,
                    primary,
                    search=synonym_hint,
                    color=cat.get("color"),
                )
            )
            if len(out) >= _ROW_CAP:
                return out
    # Categories with NO budget row this month — income/transfer/investment kinds and
    # unlinked expense categories (the Manage-categories universe). The structural "split"
    # sentinel is a marker, not a real category (ADR 2026-07-11).
    for row in corpus(categories.all_category_rows):
        cid = row.get("id") or ""
        if not cid or cid in categories.STRUCTURAL_KEYS or cid in seen:
            continue
        label = row.get("label") or ""
        if q and not synonym_hint and not _matches(q, label):
            continue
        out.append(
            _row(
                "category",
                cid,
                label,
                row.get("kind") or "",
                cid,
                search=synonym_hint,
                color=row.get("color"),
            )
        )
        if len(out) >= _ROW_CAP:
            return out
    return out


# ── envelope ───────────────────────────────────────────────────────────────────


def search_envelope(query: SearchQuery) -> list[dict]:
    """Every budget row, keyed by its own ENVELOPE id — the counterpart of
    ``search_category`` for the envelope⊥category split: a zero-category envelope
    (commitment pot, card-payment envelope, named savings pot) has no category row
    to surface it, so this type is its only mention surface."""
    from backend.services import planning

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("envelope", q) if q else None
    today = user_today()
    month = f"{today.year:04d}-{today.month:02d}"
    table = corpus(planning.budget_month, month)
    out: list[dict] = []
    for grp in table.get("groups", []):
        grp_name = grp.get("name") or ""
        for row in grp.get("categories", []):
            # Synthetic display rows (Uncategorized / Unbudgeted) carry no envelope_id —
            # an envelope mention always references a real envelope.
            env_id = row.get("envelope_id")
            if not env_id:
                continue
            label = row.get("label") or ""
            if q and not synonym_hint and not _matches(q, label):
                continue
            out.append(
                _row(
                    "envelope",
                    env_id,
                    label,
                    grp_name,
                    env_id,
                    search=synonym_hint,
                    color=row.get("color"),
                )
            )
            if len(out) >= _ROW_CAP:
                return out
    return out


# ── target ─────────────────────────────────────────────────────────────────────


def search_target(query: SearchQuery) -> list[dict]:
    from backend.services import targets

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("target", q) if q else None
    out: list[dict] = []
    for t in corpus(targets.list_hydrated):
        name = t.get("name") or ""
        if q and not synonym_hint and not _matches(q, name):
            continue
        remaining = t.get("remaining_base", 0) or 0
        status = t.get("status") or ""
        sublabel = f"{remaining} remaining • {status}".strip(" •")
        out.append(
            _row(
                "target",
                t["id"],
                name,
                sublabel,
                t["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


# ── net_worth (singleton) ──────────────────────────────────────────────────────


def search_net_worth(query: SearchQuery) -> list[dict]:
    from backend.core.fx import base_currency
    from backend.services import ledger

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("net_worth", q) if q else None
    if q and not synonym_hint and not _matches(q, "net worth", "net"):
        return []
    nw = corpus(ledger.net_worth)
    base = base_currency()
    val = nw.get("net_worth", 0) or 0
    return [
        _row(
            "net_worth",
            "net_worth",
            "Net worth",
            f"{val} {base}",
            "",
            search=synonym_hint,
        )
    ]


# ── returns ────────────────────────────────────────────────────────────────────

_RETURN_PERIODS = ["YTD", "1M", "3M", "6M", "1Y", "3Y", "5Y", "All"]


def search_returns(query: SearchQuery) -> list[dict]:
    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("returns", q) if q else None
    out: list[dict] = []
    for period in _RETURN_PERIODS:
        if q and not synonym_hint and not _matches(q, period, "return", "returns"):
            continue
        out.append(
            _row(
                "returns",
                period,
                f"{period} return",
                "",
                period,
                search=synonym_hint,
            )
        )
    return out


# ── budget_month ───────────────────────────────────────────────────────────────


def search_budget_month(query: SearchQuery) -> list[dict]:
    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("budget_month", q) if q else None
    today = user_today()
    month_names = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    out: list[dict] = []
    for offset in range(12):
        # Walk back from current month (offset 0 = this month, 11 = 11 months ago)
        total_months = today.month - 1 - offset
        year = today.year + total_months // 12
        month_idx = total_months % 12  # 0-based
        month_str = f"{year:04d}-{month_idx + 1:02d}"
        friendly = f"{month_names[month_idx]} {year}"
        if q and not synonym_hint and not _matches(q, friendly, month_str):
            continue
        out.append(
            _row(
                "budget_month",
                month_str,
                friendly,
                month_str,
                month_str,
                search=synonym_hint,
            )
        )
    return out


# ── allocation_slice ───────────────────────────────────────────────────────────


def _payees_with_usage() -> list[dict]:
    from backend.services import payee_registry

    return payee_registry.list_registry(with_usage=True)


def search_payee(query: SearchQuery) -> list[dict]:
    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("payee", q) if q else None
    out: list[dict] = []
    # Source from list_registry() so the sublabel shows the LIVE txn count (recomputed from the
    # ledger), matching Manage Payees — NOT the stored monotonic usage stat (txn_count), which is
    # only bumped, never decremented, so an orphaned auto-minted payee wrongly read "1 txns" while
    # Manage Payees correctly showed "0 txns".
    for p in corpus(_payees_with_usage):
        # `hidden` already means "suppress from autocomplete" — Manage Payees' own
        # editor reads/writes it (PayeesModal.tsx) and its checkbox already worked;
        # this provider was the one surface that never honored it, so hiding a junk
        # auto-minted payee there (observed case — a payee literally named after a raw
        # brokerage trade memo, e.g. "ORCL BUY 5 @ 170 - VNTG") had no effect on what
        # Lucius's @ picker still offered.
        if p.get("hidden"):
            continue
        name = p.get("name") or ""
        if q and not synonym_hint and not _matches(q, name):
            continue
        txn_count = p.get("txn_count", 0) or 0
        out.append(
            _row(
                "payee",
                p["id"],
                name,
                f"{txn_count} txns",
                p["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


def search_watchlist(query: SearchQuery) -> list[dict]:
    from backend.db.watchlist_store import WatchlistStore

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("watchlist", q) if q else None
    store = WatchlistStore()
    out: list[dict] = []
    for wl in store.list_watchlists():
        name = wl.get("name") or ""
        if q and not synonym_hint and not _matches(q, name):
            continue
        description = (wl.get("description") or "").strip()
        try:
            item_count = store.count(watchlist_id=wl["id"])
        except Exception:
            item_count = 0
        sublabel = description if description else f"{item_count} items"
        out.append(
            _row(
                "watchlist",
                wl["id"],
                name,
                sublabel,
                wl["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


def search_automation(query: SearchQuery) -> list[dict]:
    from backend.db.automation_store import AutomationStore
    from backend.services.automations.schedules import describe_trigger

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("automation", q) if q else None
    out: list[dict] = []
    for automation in AutomationStore().all():
        name = automation.get("name") or ""
        searchable = " ".join(
            (
                name,
                str(automation.get("description") or ""),
                str(automation.get("instructions") or ""),
            )
        )
        if q and not synonym_hint and not _matches(q, searchable):
            continue
        out.append(
            _row(
                "automation",
                automation["id"],
                name,
                describe_trigger(automation.get("trigger") or {"kind": "manual"}),
                automation["id"],
                search=synonym_hint,
            )
        )
        if len(out) >= _ROW_CAP:
            break
    return out


def search_skill(query: SearchQuery) -> list[dict]:
    """Multi-surface skill rows (RFC §10). Inspects ``query.surfaces``:

    * ``"verb"`` → ENABLED skills only (the invokable / verbs), each emitted with ``surface="verb"``.
    * ``"entity"`` → ALL skills (talk-about context chips), ``surface="entity"``.

    Both surfaces may be requested together; a row may appear once per surface.
    ``surfaces is None`` is treated as both. Substring on name and description.
    ``value`` is the immutable skill id — what ``resolve_slash_skills`` / ``activate_skill`` expect.
    """
    from backend.db.skill_store import SkillStore

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("skill", q) if q else None
    surfaces = query.surfaces  # None ⇒ serve both surfaces this provider declares
    want_verb = surfaces is None or "verb" in surfaces
    want_entity = surfaces is None or "entity" in surfaces

    try:
        all_skills = SkillStore().all()
    except Exception:
        all_skills = []

    out: list[dict] = []

    if want_verb:
        for s in all_skills:
            if not s.get("enabled", True):
                continue
            name = s.get("name") or s.get("id") or ""
            desc = s.get("description") or ""
            if q and not synonym_hint and not _matches(q, name, desc):
                continue
            out.append(
                _row(
                    "skill",
                    s["id"],
                    name,
                    (desc[:80] + "…") if len(desc) > 80 else desc,
                    s["id"],
                    "verb",
                    search=synonym_hint,
                )
            )
            if len(out) >= _ROW_CAP:
                break

    if want_entity:
        for s in all_skills:
            name = s.get("name") or s.get("id") or ""
            desc = s.get("description") or ""
            if q and not synonym_hint and not _matches(q, name, desc):
                continue
            out.append(
                _row(
                    "skill",
                    s["id"],
                    name,
                    (desc[:80] + "…") if len(desc) > 80 else desc,
                    s["id"],
                    "entity",
                    search=synonym_hint,
                )
            )
            if len(out) >= _ROW_CAP + _ROW_CAP:
                break

    return out


def search_allocation_slice(query: SearchQuery) -> list[dict]:
    from backend.services import positions as positions_service

    q = (query.q or "").strip().lower()
    synonym_hint = _type_synonym_match("allocation_slice", q) if q else None
    # Collect distinct asset_class and currency values from live positions.
    asset_classes: set[str] = set()
    currencies: set[str] = set()
    for row in corpus(positions_service.hydrated):
        ac = (row.get("asset_class") or "").strip()
        if ac:
            asset_classes.add(ac)
        cur = (row.get("currency") or "").strip()
        if cur:
            currencies.add(cur)

    out: list[dict] = []
    for ac in sorted(asset_classes):
        value = f"asset_class:{ac}"
        label = f"{ac} (asset_class)"
        if q and not synonym_hint and not _matches(q, ac, "asset_class"):
            continue
        out.append(
            _row("allocation_slice", value, label, "asset_class", value, search=synonym_hint)
        )

    for cur in sorted(currencies):
        value = f"currency:{cur}"
        label = f"{cur} (currency)"
        if q and not synonym_hint and not _matches(q, cur, "currency"):
            continue
        out.append(_row("allocation_slice", value, label, "currency", value, search=synonym_hint))

    return out[:_ROW_CAP]
