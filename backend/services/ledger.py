"""
ledger_service — composition layer over AccountStore + TransactionStore.

Holds the business logic the API routes and AI tools call:
  - to_base(): base-currency normalisation (generic; replaces the SGD-only _to_sgd)
  - account_balance() / balances(): the balance fold (Σ legs past the tracking start)
  - opening_*(): an account's opening — ONE fact, its `opening_txn_id` row
  - net_worth(): add-everything fold over accounts + data.csv market values
  - reconcile(): assert a real-world balance → emits an `adjustment` transaction

Stores stay single-responsibility (CRUD + queries); all cross-store logic lives here.
See .claude/rfcs/account-ledger.md.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
from calendar import monthrange
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from backend.core.errors import (
    ConflictError,
    DataIntegrityError,
    DuplicateTransactionError,
    ImplausibleTransferAmountError,
    MissingFilterError,
    NotFoundError,
    ValidationError,
    duplicate_hint,
    recovery_hint,
)
from backend.core.txn_sources import default_needs_review, ext_ref_pairs, primary_source
from backend.core.user_time import user_today
from backend.db import read_cache
from backend.db.account_store import AccountStore
from backend.db.engine import locked_unit_of_work, on_commit
from backend.db.import_batch_store import ImportBatchStore
from backend.db.merge_journal_store import MergeJournalStore
from backend.db.statement_store import StatementStore
from backend.db.tables import accounts as accounts_table
from backend.db.tables import commitments as commitments_table
from backend.db.tables import merge_journal as merge_journal_table
from backend.db.tables import rejected_identities as rejected_identities_table
from backend.db.tables import transactions as transactions_table
from backend.db.tables import txn_audit as txn_audit_table
from backend.db.transaction_store import TransactionStore
from backend.models.account import ACCOUNT_BAND, band_of, kind_of
from backend.models.transaction import SPLIT
from backend.services.review_approval import acting_as_automation, refuse_automation_approval

# asset_classes that live in data.csv as genuine market-valued positions.
# cash / receivable rows move to accounts and are excluded from the market fold.
MARKET_ASSET_CLASSES = {"equity", "commodity", "crypto", "bond", "property"}

# Transaction kinds — mirrors api/ledger.ALL_KINDS; defined here so services/tools can use it
# without importing the api layer.
ALL_KINDS = {"expense", "income", "transfer", "adjustment", "investment"}

_log = logging.getLogger(__name__)

accounts = AccountStore()
transactions = TransactionStore()
import_batches = ImportBatchStore()
statements = StatementStore()
merge_journal = MergeJournalStore()

# Transaction-provenance suppression flag (transaction-provenance RFC). update_transaction
# re-runs create_transaction internally to rebuild legs; without this the inner create would
# emit a phantom "create" audit event for every edit. update_transaction wraps that inner
# call in _suppress_inner_create_audit() and emits the single real "update" event itself.
_suppress_create_audit: ContextVar[bool] = ContextVar("_suppress_create_audit", default=False)


def _capture_automation_balance_snapshot() -> dict[str, float] | None:
    if _suppress_create_audit.get():
        return None
    try:
        from backend.services.automations.events import capture_balance_snapshot

        return capture_balance_snapshot()
    except Exception:
        _log.exception("automation pre-commit balance snapshot failed")
        return None


def _publish_committed_transaction_events(
    operation: str, rows: list[dict], *, balance_before: dict[str, float] | None = None
) -> None:
    """Best-effort event publication after the ledger write has committed."""
    if not rows:
        return
    try:
        from backend.core.audit_context import get_audit_scope
        from backend.services.automations.events import publish_automation_events

        scope = get_audit_scope()
        origin = (
            {
                "source_automation_id": scope.automation_id,
                "source_automation_run_id": scope.automation_run_id,
            }
            if scope is not None
            and scope.surface == "automation"
            and scope.automation_id
            and scope.automation_run_id
            else {}
        )
        payloads = []
        for row in rows:
            payload = {**dict(row), **origin}
            payload["operation"] = operation
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            version = hashlib.sha256(encoded.encode()).hexdigest()[:20]
            payload["event_key"] = f"txn:{operation}:{row.get('id')}:{version}"
            payloads.append(payload)
        publish_automation_events("txn", payloads)
    except Exception:
        # The ledger write is already committed. A durable event-outbox failure must be
        # visible in logs but can never make the caller retry a transaction that exists.
        _log.exception("automation transaction event publication failed after commit")
    try:
        from backend.services.automations.events import observe_balance_events

        observe_balance_events(before=balance_before)
    except Exception:
        _log.exception("automation balance event observation failed after commit")


def _after_outer_commit(composed: bool, fn: Callable[[], None]) -> None:
    """Run ``fn`` now when ``composed=False`` (``conn=None`` — every pre-Task-4 call site of
    create_transaction / bulk_delete_transactions): by the time any caller of this helper
    runs, ``_txn_unit_of_work``'s OWN ``locked_unit_of_work`` has already committed AND
    released its locks, so immediate execution is unchanged, byte-identical behavior.

    Defer ``fn`` via ``db.engine.on_commit`` when ``composed=True`` (a caller-supplied
    ``conn`` — a trade choke, e.g. a vest's income leg or a dispose's proceeds leg,
    composing this write into its OWN still-open ``locked_unit_of_work``): the OUTER
    transaction has not committed yet at this point in the call stack, and every current
    caller of this helper writes to a store that is now DB-backed (AutomationStore,
    PayeeStore — storage plan 4c/4d) and opens its OWN independent transaction when not
    given a ``conn`` of its own. Running ``fn`` immediately would therefore open a SECOND
    SQLite write transaction against an engine that still holds the outer one's write lock
    — SQLite's single-writer model blocks it behind ``PRAGMA busy_timeout`` (~5s) and then
    raises ``database is locked``, which every current caller of this helper swallows in
    its own best-effort ``except Exception`` — so on the composed path this was never just
    a stall, it was a SILENTLY DROPPED write (the review-gate BLOCKER this helper closes;
    empirically reproduced: a composed vest income leg left ``payee_registry.note_usage``
    dropped — ``persisted_last_used=None`` after a multi-second stall — see
    ``test_vest_apply_composed_income_leg_persists_payee_and_automation_writes_after_commit``
    in ``tests/services/test_trade_choke_atomicity.py``). ``on_commit`` fires ``fn`` exactly
    once, after the OUTER transaction actually commits — never on a rollback, so a
    rolled-back trade leaves no match-usage / payee-learning / event trace of a transaction
    that never ends up existing."""
    if composed:
        on_commit(fn)
        return
    fn()


def _publish_transaction_events_after_commit(
    composed: bool,
    operation: str,
    rows: list[dict],
    *,
    balance_before: dict[str, float] | None,
) -> None:
    """Fire ``_publish_committed_transaction_events`` at the right commit boundary — see
    ``_after_outer_commit`` for the composed/deferred mechanics every post-write side
    effect on this path (this publish step, automation match-usage accounting, payee
    usage/category learning) now shares.

    ``composed=False`` (``conn=None`` — every pre-Task-4 caller of create_transaction /
    bulk_delete_transactions): by the time this runs, ``_txn_unit_of_work``'s OWN
    ``locked_unit_of_work`` has already committed AND released its locks, so publishing
    immediately is unchanged, byte-identical behavior.

    ``composed=True`` (a caller-supplied ``conn`` — a trade choke, e.g. a vest's income
    leg or a dispose's proceeds leg, composing this write into its OWN still-open
    ``locked_unit_of_work``): the OUTER transaction has not committed yet at this point in
    the call stack. Publishing now would open a SECOND DB write transaction
    (automation_runtime is DB-backed, storage plan 4c) against an engine that still holds
    an open writer — SQLite's single-writer model then blocks it behind
    ``PRAGMA busy_timeout`` and raises ``database is locked`` (the self-deadlock this
    function exists to prevent; empirically reproduced by
    ``test_vest_apply_does_not_self_deadlock_on_lot_mint``). Deferred via
    ``db.engine.on_commit`` to fire exactly once, after the OUTER transaction actually
    commits — and not at all if it rolls back, which also closes the sibling
    phantom-event-on-rollback gap for the composed path (a rolled-back trade must publish
    no automation event for a transaction that never ends up existing)."""
    _after_outer_commit(
        composed,
        lambda: _publish_committed_transaction_events(
            operation, rows, balance_before=balance_before
        ),
    )


@contextlib.contextmanager
def _suppress_inner_create_audit():
    token = _suppress_create_audit.set(True)
    try:
        yield
    finally:
        _suppress_create_audit.reset(token)


@contextlib.contextmanager
def _txn_unit_of_work(conn: Connection | None):
    """The composing half of the ledger choke's conn= contract (Task 4, storage plan 4b
    — extending Plan 3's own money-core chokes to accept a FOREIGN conn, which they did
    not yet support): reuse a caller's own open transaction when given — the caller MUST
    already hold BOTH transactions_table's AND txn_audit_table's locks (this choke always
    writes both as one unit, via locked_unit_of_work(transactions_table, txn_audit_table)
    below), typically by including both tables in its OWN locked_unit_of_work(...) call.
    conn=None (every pre-Task-4 call site) is byte-identical: opens the normal standalone
    locked_unit_of_work(transactions_table, txn_audit_table).

    The standalone set also holds ``rejected_identities``: the delete choke remembers a deleted
    row's refs in the same transaction that deletes it, and a resurrecting create forgets them
    in the same transaction that books it. A composed caller (trade undo, vest) holds only the
    two ledger locks — and passes ``reject_identities=False``, so it never writes that table."""
    if conn is not None:
        yield conn
        return
    with locked_unit_of_work(
        transactions_table, txn_audit_table, rejected_identities_table
    ) as owned:
        yield owned


# ── Base currency + FX ─────────────────────────────────────────────────────────
# The pure conversion math moved to backend.core.fx (RFC P1-A1 §3.6); re-exported
# here so the ~17 `ledger_service.to_base(...)` / `base_currency()` callers don't
# change in this slice. The bodies live in core/fx.py.
from backend.core.fx import _load_rates as _fx_load_rates
from backend.core.fx import (  # noqa: F401  (re-export shim)
    allocated_leg_amount,
    base_currency,
    known_rate,
    to_base,
    txn_base_amount,
    txn_split_base_amount,
)

# ── Balance fold ─────────────────────────────────────────────────────────────


def is_before_tracking_start(leg_date: str | None, opening_date: str | None) -> bool:
    """True iff there IS a tracking start and ``leg_date`` falls strictly before it — i.e. the
    leg is summarized into the opening, not counted. Null cutoff or null/empty date → False
    (the leg counts).

    A pure date comparison. The cutoff is resolved by the CALLER (``opening_date`` /
    ``cutoffs_by_account``) rather than looked up off an account dict here, so a fold site
    that forgets to supply it is a TypeError the type checker catches — not a silent None
    that quietly counts pre-start legs."""
    return bool(opening_date and leg_date and leg_date < opening_date)


# ── The opening pointer: account["opening_txn_id"] → its Starting Balance row ──────
# An account's opening is ONE fact. The amount and the tracking start are BOTH read off the
# pointed-at transaction; neither is stored on the account. `None` = no tracking start.


def opening_txn_ids() -> frozenset[str]:
    """Every account's ``opening_txn_id``, as a set — "is this row an opening?" in O(1).

    Archived accounts INCLUDED (``accounts.all()``): a closed account still owns its opening
    row, and the row must stay protected from sweeps/merges/deletes exactly as a live one is.
    One accounts.json read (29 rows on the live book), on paths that already load the whole
    transaction store."""
    return frozenset(oid for a in accounts.all() if (oid := a.get("opening_txn_id")))


def opening_index(txns: list[dict], accts: list[dict] | None = None) -> dict[str, dict]:
    """``txn_id`` → row for every opening transaction, built in ONE pass over ``txns``.

    ``txns`` MUST be the complete transaction list — a caller that hands a date-filtered list
    would drop opening rows out of the index and turn a healthy pointer into a
    ``DataIntegrityError``. Cutoffs are resolved from the full list; the FOLD may then run
    over a filtered one.

    ``accts`` restricts the pointer set to those accounts (so a caller folding a specific list
    — including a synthetic one in a test — indexes exactly its own rows); omitted, every
    account's pointer is used."""
    ids = (
        frozenset(oid for a in accts if (oid := a.get("opening_txn_id")))
        if accts is not None
        else opening_txn_ids()
    )
    return {t["id"]: t for t in txns if not t.get("deleted") and t.get("id") in ids and t.get("id")}


def _opening_row(
    account: dict, txns: list[dict] | None = None, *, idx: dict[str, dict] | None = None
) -> dict | None:
    """The account's Starting Balance row, or None when it has no opening at all.

    A pointer that resolves to a MISSING or DELETED row RAISES ``DataIntegrityError``. It does
    not degrade to None: that would silently un-curtain the account's pre-start history and
    move its balance. A loud outage is fixable; a wrong net worth is not."""
    oid = account.get("opening_txn_id")
    if not oid:
        return None
    if idx is not None:
        row = idx.get(oid)
    elif txns is not None:
        row = next((t for t in txns if t.get("id") == oid and not t.get("deleted")), None)
    else:
        row = transactions.find_by_id(oid)
        if row is not None and row.get("deleted"):
            row = None
    if row is None:
        raise DataIntegrityError(
            f"account {account.get('id')} ({account.get('name') or '?'}) points at opening "
            f"transaction {oid}, which is missing or deleted"
        )
    return row


def opening_date(
    account: dict, txns: list[dict] | None = None, *, idx: dict[str, dict] | None = None
) -> str | None:
    """The account's tracking start — the DATE of its Starting Balance row. None = no start."""
    row = _opening_row(account, txns, idx=idx)
    return row.get("date") if row else None


def _signed_opening(row: dict | None) -> float:
    """A Starting Balance row's signed native amount: the to-leg, or the negated from-leg."""
    if row is None:
        return 0.0
    if row.get("to_amount") is not None:
        return round(float(row["to_amount"]), 2)
    if row.get("from_amount") is not None:
        return round(-float(row["from_amount"]), 2)
    return 0.0


def opening_amount(
    account: dict | None, txns: list[dict] | None = None, *, idx: dict[str, dict] | None = None
) -> float:
    """The account's opening, signed in its native currency (asset positive, liability
    raw-negative) — the signed leg of its Starting Balance row.

    0.0 when the account has no opening. This is the value the public (input-and-response,
    never stored) ``initial_balance`` field derives from."""
    return _signed_opening(_opening_row(account, txns, idx=idx) if account else None)


def cutoffs_by_account(accts: list[dict], txns: list[dict]) -> dict[str, str | None]:
    """``account_id`` → tracking start (or None), resolved in ONE pass over ``txns``.

    The loop-hoisted form every caller that folds MANY accounts uses: it is the only shape
    that avoids rebuilding the opening index per account. ``txns`` must be the complete
    transaction list (see ``opening_index``)."""
    idx = opening_index(txns, accts)
    return {a["id"]: opening_date(a, idx=idx) for a in accts}


def account_balance(account: dict, txns: list[dict], opening_date: str | None) -> float:
    """Native-currency balance = Σ to_amount (legs entering) − Σ from_amount (legs leaving),
    over non-deleted legs dated on/after ``opening_date``.

    There is no separate opening seed: the Starting Balance row is itself a leg in ``txns``,
    dated exactly at the cutoff, and the strict ``<`` in ``is_before_tracking_start`` always
    counts it."""
    acct_id = account.get("id")
    bal = 0.0
    for t in txns:
        if t.get("deleted"):
            continue
        if is_before_tracking_start(t.get("date"), opening_date):
            continue  # summarized into the opening
        if t.get("from_account_id") == acct_id and t.get("from_amount") is not None:
            bal -= float(t["from_amount"])
        if t.get("to_account_id") == acct_id and t.get("to_amount") is not None:
            bal += float(t["to_amount"])
    return round(bal, 2)


def period_net(
    txns: list[dict] | None,
    account_id: str,
    base: str,
    rates: dict | None = None,
    *,
    net: float | None = None,
) -> tuple[float, float]:
    """The account-scoped "period net" fold `GET /api/transactions` reports alongside a
    single-account page: Σ to_amount − Σ from_amount for `account_id`'s own legs over an
    ALREADY-FILTERED candidate set (the caller's date/kind/category/etc. window — this
    does no filtering of its own), converted to `base`. Returns (net_native, net_base).

    `net` (optional): pass an already-computed native net to skip the Python fold —
    `db.transaction_store.query_txns_page`'s SQL-summed `to_sum − from_sum` is the fast
    (DB-paginated) path's twin of the loop below, computed over the full filtered set
    (not just the returned page), so that caller has the number already and must not
    re-derive it from a page. `txns` is unused (may be None) when `net` is given.

    MATH-48: this fold used to live three places — the router's Python loop (twice, one
    per pagination path), the SQL twin (`query_txns_page`), and `account_balance` (a
    different fold, whole-ledger-as-of rather than period-scoped) — this is the ONE
    Python-side implementation; the router calls it from both paths."""
    if net is None:
        net = 0.0
        for t in txns or []:
            if t.get("to_account_id") == account_id and t.get("to_amount") is not None:
                net += float(t["to_amount"])
            if t.get("from_account_id") == account_id and t.get("from_amount") is not None:
                net -= float(t["from_amount"])
    acct = accounts.find_by_id(account_id)
    acct_cur = acct.get("currency", base) if acct else base
    return net, to_base(net, acct_cur, base, rates=rates)


def cleared_account_balance(account: dict, txns: list[dict], opening_date: str | None) -> float:
    """Native-currency CLEARED balance — same fold as ``account_balance`` but counting
    only legs the user has marked ``cleared`` (cleared/reconcile MVP).

    The cleared balance is the sum the user has reconciled against the bank; it is the
    figure ``reconcile`` diffs an asserted real balance against. The Starting Balance row is
    always ``cleared=True`` (invariant #2), so it lands in BOTH balances and an all-cleared
    book has cleared == working."""
    acct_id = account.get("id")
    bal = 0.0
    for t in txns:
        if t.get("deleted") or not t.get("cleared"):
            continue
        if is_before_tracking_start(t.get("date"), opening_date):
            continue  # summarized into the opening
        if t.get("from_account_id") == acct_id and t.get("from_amount") is not None:
            bal -= float(t["from_amount"])
        if t.get("to_account_id") == acct_id and t.get("to_amount") is not None:
            bal += float(t["to_amount"])
    return round(bal, 2)


def balances(include_archived: bool = False) -> dict[str, float]:
    """Map account_id → native-currency balance, computed in one pass over txns."""
    txns = transactions.all()
    accts = accounts.query_accounts(include_archived=include_archived)
    cutoffs = cutoffs_by_account(accts, txns)
    return {a["id"]: account_balance(a, txns, cutoffs[a["id"]]) for a in accts}


def account_with_balance(
    account: dict, txns: list[dict] | None = None, *, idx: dict[str, dict] | None = None
) -> dict:
    """The stored account + its computed balances + the two DERIVED opening fields.

    ``initial_balance`` / ``initial_balance_date`` are response shaping only — the wire
    contract the frontend and the AI tools have always seen. Neither is stored; both are read
    off the row ``opening_txn_id`` points at."""
    if txns is None:
        txns = transactions.all()
    row = _opening_row(account, txns, idx=idx)
    cutoff = row.get("date") if row else None
    bal = account_balance(account, txns, cutoff)
    cleared = cleared_account_balance(account, txns, cutoff)
    base = base_currency()
    cur = account.get("currency") or base
    external_ids = account.get("external_ids") or []
    return {
        **account,
        "initial_balance": _signed_opening(row),
        "initial_balance_date": cutoff,
        "balance": bal,
        "balance_base": to_base(bal, cur, base),
        "cleared_balance": cleared,
        "cleared_balance_base": to_base(cleared, cur, base),
        # DERIVED, never a stored second fact (RFC connectors-as-data-sources §D) — a linked
        # account is one with at least one external_ids entry, full stop.
        "connected": bool(external_ids),
        "external_ids": _hydrated_external_ids(external_ids),
    }


def _hydrated_external_ids(ids: list[dict]) -> list[dict]:
    """``external_ids``, each entry carrying its provider's display ``provider_label``
    (UI spec: server-side labels, no frontend copy of the provider-name table)."""
    from backend.services.accounts import provider_label

    return [
        {**entry, "provider_label": provider_label(entry.get("source"))}
        for entry in ids
        if isinstance(entry, dict)
    ]


# ── Net worth ──────────────────────────────────────────────────────────────────


def account_kind_and_band(account: dict) -> tuple[str, str]:
    """``(kind, band)`` for a STORED account row, refusing a type this build cannot classify.

    A row typed outside the seven can only arrive by restoring a pre-2026-09-07 backup or by a
    hand edit — every write choke refuses one. ``band_of``/``kind_of`` raise a bare ``ValueError``
    on it, which reached the client as an opaque 500 naming nothing, from whichever read
    happened to touch the row first. This names the account, so the person holding the broken
    book knows which row to fix. Every services-tier classification of a stored row goes through
    here."""
    type_ = account.get("type")
    try:
        return kind_of(type_), band_of(type_)
    except ValueError as exc:
        raise DataIntegrityError(
            f"account {account.get('id')!r} ({account.get('name') or 'unnamed'}) has unknown "
            f"type {type_!r}; this build knows {', '.join(sorted(ACCOUNT_BAND))}. The row "
            "predates the seven-type account schema (2026-09-07) — restore that backup on a "
            "build from before that date, let it upgrade, then export again."
        ) from exc


def row_base_value(
    row: dict, base: str, *, price: float | None = None, rates: dict | None = None
) -> float:
    """A hydrated position row's value in `base` currency — the ONE canonical market
    fold: qty × price × quote_per_unit, FX-converted from the row's native currency.

    `price` defaults to the row's own ``price`` (last-known); pass an explicit price for
    an as-of valuation (e.g. net-worth-history reconstruction). Returns 0.0 on a bad row
    (never raises). Shared by net worth, the cash-flow market baseline, and the X-ray so
    all three value a holding identically (a non-default quote_per_unit can't diverge).

    `rates` pins the FX table for the whole fold. Omitted, every call re-reads the shared
    cache — fine for a one-off, but a caller valuing a WHOLE BOOK (portfolio_rows) must pass
    the rate set it fetched, so that every row in one response is converted at one set of
    rates and its own cost/day-P&L legs agree with its market leg."""
    try:
        qty = float(row.get("quantity") or 0)
        px = float(row.get("price") or 0) if price is None else float(price)
        qpu = float(row.get("quote_per_unit") or 1.0) or 1.0
    except (TypeError, ValueError):
        return 0.0
    # qty × price × qpu is expressed in quote_currency (the currency the market price is
    # denominated in), NOT cost_currency.  When a holding is bought in USD but quoted in
    # HKD (e.g. an HKD-listed ETF held in a USD account) the legacy "currency" field
    # equals cost_currency="USD" — using it would apply a USD→base rate to an HKD number,
    # giving a ~8× error.  prefer quote_currency; fall back to "currency" for synthetic
    # rows (net-worth-history reconstructions) that may not carry the new field.
    price_currency = row.get("quote_currency") or row.get("currency", base)
    return to_base(qty * px * qpu, price_currency, base, rates)


def _market_values_base(
    base: str, rates: dict | None = None, retirement_ids: set[str] | None = None
) -> tuple[float, float]:
    """Σ market-valued positions (qty × price → base). Excludes cash/receivable rows,
    which are modelled as accounts, AND `vesting_state="unvested"` rows (unvested equity — see
    ADR 2026-07-31-unvested-equity-excluded-from-net-worth: forfeited if the grantee
    leaves, so not owned yet; reported separately via `_unvested_value_base`). Reads the
    normalized position store via positions_service (last-known prices); same shape the
    old data.csv read produced."""
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    if rates is None:
        rates = load_fx_cache()  # once per fold, never once per row
    total = 0.0
    retirement = 0.0
    for row in positions_service.hydrated():
        if row.get("asset_class") not in MARKET_ASSET_CLASSES:
            continue
        if row.get("vesting_state") == "unvested":
            continue
        value = row_base_value(row, base, rates=rates)
        total += value
        if retirement_ids and row.get("account_id") in retirement_ids:
            retirement += value
    return round(total, 2), round(retirement, 2)


def _market_value_base(base: str, rates: dict | None = None) -> float:
    return _market_values_base(base, rates)[0]


def _unvested_value_base(base: str, rates: dict | None = None) -> float:
    """Σ `vesting_state="unvested"` positions (unvested equity, base currency) — the mirror image
    of `_market_value_base`'s exclusion. Reported alongside net worth, never folded into
    it (ADR 2026-07-31-unvested-equity-excluded-from-net-worth)."""
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    if rates is None:
        rates = load_fx_cache()  # once per fold, never once per row
    total = 0.0
    for row in positions_service.hydrated():
        if row.get("asset_class") not in MARKET_ASSET_CLASSES:
            continue
        if row.get("vesting_state") != "unvested":
            continue
        total += row_base_value(row, base, rates=rates)
    return round(total, 2)


@read_cache.cached(lambda base=None: base)
def net_worth(base: str | None = None) -> dict:
    """Add-everything fold: Σ account balances (assets + raw-negative liabilities)
    + Σ market values, all normalised to base currency. `unvested_base` (unvested
    equity) is reported alongside but deliberately excluded from `net_worth` itself.

    `breakdown.accounts_asset` is the total across ALL non-liability accounts — kept
    for callers that reconcile against it as one figure. `breakdown.account_classes`
    additionally splits that same total by `band_of(type)` (cash / fixed_deposit /
    settlement_cash / retirement / receivable, plus owned-asset bands), so a caller —
    notably the AI snapshot in ai/prompts.py — can label each part correctly instead
    of calling the whole thing "cash" (an `investment` account's settlement cash must
    never be reported as bank cash on hand). Likewise `breakdown.liability_classes`
    splits `accounts_liability` by `band_of(type)` (`credit` / `loan`) — a lump
    "liabilities" figure hides that some of it may be high-APR credit-card debt and
    some a low-rate mortgage, which matters for any "what should I do with my money"
    answer. `breakdown.liabilities` lists each liability account individually
    (id/name/type/apr/balance_base, raw-negative, most-negative first) for a caller
    that needs to name the specific account, not just its band.

    `breakdown.retirement_base` includes both cash and vested market positions held in
    retirement accounts. `net_worth_excl_retirement` is `net_worth` minus that figure.

    Owned assets (ADR 2026-09-06-assets-tab-faithful-port, decision 6) fold into
    `account_classes` by band (property/commodity/other_asset) so `net_worth` moves
    with them for free; `breakdown.owned_assets` additionally surfaces their Σ so a
    caller can label that contribution on its own."""
    from backend.services import owned_assets_valuation
    from backend.services.fx_cache import load_fx_cache

    base = (base or base_currency()).upper()
    # ONE FX load for the whole fold: to_base(rates=None) re-reads the FX cache AND the
    # profile row per call — profiled 2026-08-12 at 186 fx loads + 192 profile reads in a
    # single /api/net-worth/stats request, almost all from these conversion loops.
    rates = load_fx_cache()
    txns = transactions.all()
    accts = accounts.query_accounts()
    cutoffs = cutoffs_by_account(accts, txns)

    account_classes: dict[str, float] = {}
    liability_classes: dict[str, float] = {}
    liabilities_rows: list[dict] = []
    liability_total = 0.0  # stored raw-negative; summed as-is
    retirement_base = 0.0
    for a in accts:
        bal_base = to_base(
            account_balance(a, txns, cutoffs[a["id"]]), a.get("currency", base), base, rates=rates
        )
        kind, band = account_kind_and_band(a)
        if kind == "liability":
            liability_total += bal_base
            liability_classes[band] = liability_classes.get(band, 0.0) + bal_base
            liabilities_rows.append(
                {
                    "id": a["id"],
                    "name": a.get("name", ""),
                    "type": a["type"],
                    "apr": a.get("apr"),
                    "balance_base": round(bal_base, 2),
                }
            )
        else:
            account_classes[band] = account_classes.get(band, 0.0) + bal_base
            if band == "retirement":
                retirement_base += bal_base
    liabilities_rows.sort(key=lambda r: r["balance_base"])

    owned_assets_by_band = owned_assets_valuation.values_by_band(base, rates=rates)
    for band, val in owned_assets_by_band.items():
        account_classes[band] = account_classes.get(band, 0.0) + val
    owned_assets_total = round(sum(owned_assets_by_band.values()), 2)

    asset_total = sum(account_classes.values())
    retirement_ids = {a["id"] for a in accts if a.get("type") == "retirement"}
    market_total, retirement_positions = _market_values_base(base, rates, retirement_ids)
    options_mark = _compute_options_mark_base(base)
    unvested_base = _unvested_value_base(base, rates=rates)
    net = round(asset_total + liability_total + market_total + options_mark, 2)
    retirement_base = round(retirement_base + retirement_positions, 2)
    return {
        "base_currency": base,
        "net_worth": net,
        "net_worth_excl_retirement": round(net - retirement_base, 2),
        "unvested_base": unvested_base,
        "breakdown": {
            "accounts_asset": round(asset_total, 2),
            "accounts_liability": round(liability_total, 2),
            "account_classes": {k: round(v, 2) for k, v in account_classes.items()},
            "liability_classes": {k: round(v, 2) for k, v in liability_classes.items()},
            "liabilities": liabilities_rows,
            "retirement_base": retirement_base,
            "retirement_positions_base": retirement_positions,
            "owned_assets": owned_assets_total,
            "market_positions": market_total,
            "options_mark": round(options_mark, 2),
        },
    }


def net_worth_bands(base: str | None = None) -> dict:
    """TODAY's net worth decomposed into asset-class bands — the same
    {classes, liabilities, accounts, net_worth, unvested_base} shape ``net_worth_history``
    emits per month, for the current moment only.

    Exists so a caller that needs one band (e.g. the Overview's "Cash on hand" KPI, which is the
    `cash` band alone — ADR 2026-09-07-seven-account-types-one-axis) can read it without either
    re-deriving the split or paying for a full monthly history reconstruction.
    """
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import owned_assets_valuation
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    base = (base or base_currency()).upper()
    load_fx_cache()  # one FX load for the whole breakdown
    today_iso = user_today().isoformat()
    store = PriceHistoryStore()
    market_since = store.earliest_date()
    accts = accounts.query_accounts()
    txns = [t for t in transactions.all() if not t.get("deleted")]
    return _breakdown_as_of(
        today_iso,
        store.as_of_maps([today_iso])[today_iso],
        accts,
        positions_service.hydrated(),
        txns,
        base,
        bool(market_since and today_iso >= market_since),
        cutoffs_by_account(accts, txns),
        option_mark_base=_compute_options_mark_base(base),
        manual_assets_base=owned_assets_valuation.values_by_band(base),
    )


# ── Transaction creation (enforces the leg-currency invariant) ──────────────────


def _normalise_leg(
    prefix: str,
    data: dict,
    *,
    account_rows: list[dict] | None = None,
    base: str | None = None,
    rates: dict | None = None,
) -> tuple[str | None, float | None, str | None, float | None, str | None]:
    """Return (account_id, amount, currency, orig_amount, orig_currency) for a from/to leg, with
    the amount coerced into the ACCOUNT's native currency. The model invariant is that a leg's
    amount is denominated in its account's currency; a foreign-currency input (e.g. a USD amount
    entered against an SGD card) is converted via FX. When that conversion happens, the
    pre-conversion (amount, currency) is captured as the original foreign charge — display/audit
    only — before it is overwritten; orig_* are None when no conversion occurs."""
    acc_id = data.get(f"{prefix}_account_id")
    amt = data.get(f"{prefix}_amount")
    cur = (data.get(f"{prefix}_currency") or "").upper() or None
    if amt is None:
        return acc_id, None, cur, None, None
    try:
        amt = float(amt)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{prefix}_amount must be a finite non-negative number") from exc
    if not math.isfinite(amt) or amt < 0:
        raise ValidationError(
            f"{prefix}_amount must be zero or greater; use the transaction kind and account leg "
            "to represent direction"
        )
    orig_amt = orig_cur = None
    if acc_id:
        acc = _find_account(acc_id, account_rows)
        if acc:
            acc_cur = (acc.get("currency") or base or base_currency()).upper()
            if cur and cur != acc_cur:
                orig_amt, orig_cur = amt, cur  # capture the foreign charge before converting
                amt = to_base(amt, cur, acc_cur, rates=rates)  # convert into the account's currency
            cur = acc_cur
    return acc_id, amt, cur, orig_amt, orig_cur


def _resolve_original(
    kind: str,
    data: dict,
    famt: float | None,
    from_orig: tuple[float | None, str | None],
    tamt: float | None,
    to_orig: tuple[float | None, str | None],
) -> tuple[float | None, str | None]:
    """The original foreign charge to stamp on the row. Explicit ``original_amount``/
    ``original_currency`` in ``data`` win; otherwise it is auto-captured from the converted
    PRIMARY leg (the same leg ``base_amount`` uses: to-leg for income, else from-leg). Transfers
    carry no single original. Display/audit only — never fed into FX/balance/split math."""
    cap = (None, None)
    if kind == "income":
        cap = to_orig  # income reports on the to-leg (mirror txn_base_amount), never the from-leg
    elif kind != "transfer":
        cap = from_orig if famt is not None else to_orig
    orig_amount = data.get("original_amount")
    orig_currency = data.get("original_currency")
    return (
        orig_amount if orig_amount is not None else cap[0],
        orig_currency if orig_currency is not None else cap[1],
    )


# A cross-currency transfer whose legs imply a rate this far from the known one is an
# arithmetic impossibility, not a transfer. The bug class it catches is 1,000x–20,000x (a
# number carried across a currency boundary without converting); the honest error sources are
# a bank spread, a stale cache, or a historical transfer entered at its own day's rate — all
# percentages, occasionally a factor of two on a long-depreciating pair. An order of magnitude
# sits far outside anything real and far inside the bug.
_TRANSFER_RATE_SANITY_FACTOR = 10.0


def _assert_transfer_rate_plausible(
    famt: float | None,
    fcur: str | None,
    tamt: float | None,
    tcur: str | None,
    rates: dict | None = None,
) -> None:
    """Refuse a transfer whose two legs imply an impossible exchange rate.

    The 2026-08-23 incident booked ₫300,000,000 as S$300,000,000 on an SGD receivable —
    caught only because a human read the net worth. One caller had split an `amount` from its
    `currency` across two legs (fixed at that caller, ADR
    `2026-08-23-a-transfer-figure-and-its-unit-belong-to-the-same-leg`); this is the guard that
    makes the SHAPE unrepresentable from any caller — REST, the import paths, the AI tools,
    code not yet written — instead of fixed in the one place it happened to come from.

    Deliberately a RATE comparison, not the "equal amounts in different currencies" test it
    replaced in review: equality catches only an exact copy (a caller that copies-then-rounds,
    or converts with a rate wrong by 1000x, walks straight past it) and is permanently wrong
    for a genuinely 1:1 pair, which a USD↔USDC wallet transfer really is.

    Never fires on ignorance. `known_rate` returns None for a pair absent from the table and
    this declines to judge — `to_base` would have passed that pair through 1:1, which is
    indistinguishable from a real 1:1 rate, and refusing a write for a reason the user cannot
    act on is worse than the row it would have stopped.
    """
    if not famt or not tamt or not fcur or not tcur or fcur == tcur:
        return
    rate = known_rate(fcur, tcur, rates)
    if not rate or rate <= 0:
        return
    expected = abs(float(famt)) * rate
    if expected <= 0:
        return
    ratio = abs(float(tamt)) / expected
    if 1 / _TRANSFER_RATE_SANITY_FACTOR <= ratio <= _TRANSFER_RATE_SANITY_FACTOR:
        return
    implied = abs(float(tamt)) / abs(float(famt))
    off_by = max(ratio, 1 / ratio)
    raise ValidationError(
        f"this transfer's legs imply 1 {fcur} = {implied:,.6g} {tcur}, but the known rate is "
        f"1 {fcur} = {rate:,.6g} {tcur} — off by {off_by:,.0f}x. "
        f"{abs(float(famt)):,.2f} {fcur} is about {expected:,.2f} {tcur}, not "
        f"{abs(float(tamt)):,.2f}. The usual cause is an amount carried across a currency "
        f"boundary without being converted — check which leg the {tcur} figure belongs to."
    )


# How far a pair's known rate may sit from 1.0 and still count as "genuinely 1:1" (a real
# USD<->USDC wallet transfer). Wide enough to absorb a stablecoin's own tiny de-peg wobble,
# nowhere near wide enough to mistake EUR/USD (~1.1-1.2) or EUR/SGD (~1.4-1.5) for parity.
_TRANSFER_ONE_TO_ONE_TOLERANCE = 0.02


def _assert_transfer_amount_converted(
    famt: float | None,
    fcur: str | None,
    tamt: float | None,
    tcur: str | None,
    rates: dict | None = None,
) -> None:
    """Refuse a cross-currency transfer whose two legs carry the exact same NUMBER, when a
    known rate says that number should not be the same.

    ``_assert_transfer_rate_plausible`` above only refuses a rate off by 10x or more — the
    1,000x "unit carried across a currency boundary" class it was built for. That band is far
    too wide to catch the SAME mistake on an ordinary fiat pair: a caller that has the correct
    rate sitting right in its own description/memo but never actually multiplies by it lands on
    an implied rate of 1.0, which for EUR/USD (~1.16) or EUR/SGD (~1.47) is nowhere near 10x off
    and sails through unrefused (the incident this guard was written for: 2026-08-31, an IBKR
    FX-conversion automation logged both legs of a EUR→USD and a EUR→SGD conversion at the raw
    EUR figure, having correctly written the real rate into the row's own memo text).

    Deliberately the narrow reintroduction of the "equal amounts in different currencies" check
    ``_assert_transfer_rate_plausible``'s docstring says was removed — scoped to fire ONLY when
    ``known_rate`` says the pair is NOT genuinely 1:1 (outside +/- 2%), so the case that check's
    removal was FOR (a real USD<->USDC wallet transfer, equal amounts by construction) still
    passes untouched.

    Refusable, not permanent: a transfer that truly cleared at parity (a stale quote, a peg, a
    fee-inclusive settlement the source only shows net) is real, so ``force_transfer_rate=True``
    at the call site books it anyway.
    """
    if not famt or not tamt or not fcur or not tcur or fcur == tcur:
        return
    if round(abs(float(famt)), 2) != round(abs(float(tamt)), 2):
        return
    rate = known_rate(fcur, tcur, rates)
    if not rate or rate <= 0:
        return
    if abs(rate - 1.0) <= _TRANSFER_ONE_TO_ONE_TOLERANCE:
        return
    expected = abs(float(famt)) * rate
    raise ImplausibleTransferAmountError(
        f"{fcur} {abs(float(famt)):,.2f} and {tcur} {abs(float(tamt)):,.2f} are the exact same "
        f"number, but 1 {fcur} = {rate:,.6g} {tcur} — this looks like the {fcur} figure was "
        f"carried across the currency boundary without converting, not a real 1:1 transfer. "
        f"At today's rate {fcur} {abs(float(famt)):,.2f} is about {tcur} {expected:,.2f}. If "
        f"this genuinely cleared at parity, retry with force_transfer_rate=true.",
        expected_to_amount=expected,
        rate=rate,
    )


def _transfer_base_value(
    famt: float | None,
    fcur: str | None,
    tamt: float | None,
    tcur: str | None,
    base: str,
    rates: dict | None = None,
) -> tuple[float | None, float | None]:
    """The base value that MOVED in a cross-account transfer, at the write-time rate, stamped so
    a later reader (``planning._cash_at_cost``'s unrealized-FX figure) can reconstruct a
    cross-currency boundary transfer exactly instead of approximating. Prefer whichever leg is
    already in base currency (exact, no conversion); else convert the from-leg. Returns
    ``(base_amount, src_amt)`` — ``src_amt`` is the native amount ``base_amount`` was derived
    from, so ``fx_rate`` is computed the same way as the non-transfer path. ``(None, None)`` when
    neither leg carries an amount."""
    if (fcur or base).upper() == base and famt is not None:
        return round(float(famt), 2), famt
    if (tcur or base).upper() == base and tamt is not None:
        return round(float(tamt), 2), tamt
    if famt is not None:
        return to_base(famt, fcur or base, base, rates=rates), famt
    if tamt is not None:
        return to_base(tamt, tcur or base, base, rates=rates), tamt
    return None, None


_KIND_NAMES = {"expense", "income", "transfer", "adjustment", "investment"}
# Categories that collide with transaction kind names — refused on expense/income writes so
# the data model stays unambiguous: a category is a spending label, never a kind sentinel.
_RESERVED_CATEGORIES = {"transfer", "income", "investment"}


def _validate_reserved_category(kind: str, category: str | None) -> None:
    """Raise ValidationError when an expense/income/transfer row carries a kind name as its
    category.

    'transfer' and 'income' are transaction KINDS, not spending labels. Storing them as
    categories was a historical accident — this guard closes the collision permanently at
    the write choke point so no path (REST, AI, rules, payee-defaults) can reintroduce it.
    Transfers joined this guard when the "transfers carry category=None" invariant was
    retired — a categorized transfer's category now validates exactly like an expense's
    (owner decision 2026-07-03, docs/adr/2026-07-03-categorized-transfers.md).
    """
    if kind not in ("expense", "income", "transfer"):
        return
    cat = (category or "").strip()
    if cat == "transfer":
        raise ValidationError(
            "'transfer' is a transaction kind, not a category — use kind='transfer' with "
            "from_account_id+to_account_id for internal moves, or a real expense category "
            "for money sent externally"
        )
    if cat == "income":
        raise ValidationError(
            "'income' is a transaction kind, not a category — use kind='income' (direction="
            "credit) to record money arriving, and leave category blank or pick a real label "
            "such as 'other'"
        )
    if cat == "investment":
        raise ValidationError(
            "'investment' is a transaction kind, not a category — use kind='investment' with "
            "a single cash leg (from_account_id for a buy, to_account_id for a sell)"
        )


def _unknown_category_message(ref: str) -> str:
    """Prescriptive message for a rejected category ref. ERROR PATH ONLY — the store read
    here never runs on a successful write."""
    from backend.services import categories as categories_service

    labels = [(r.get("label") or "").strip() for r in categories_service.all_category_rows()]
    return f"Unknown category '{ref}'." + recovery_hint(ref, labels, tool_hint="list_categories")


def _unknown_account_message(field: str, account_id: str) -> str:
    """Prescriptive message for a leg pointing at a non-existent account. ERROR PATH ONLY.

    An id is opaque, so string distance rarely helps — the value here is the roster of
    real account NAMES, which is what the caller needs to pick the right id from. Since the
    choke now resolves a NAME as readily as an id, a close name in the hint is directly
    usable: the caller can re-send it verbatim rather than making a second lookup call."""
    names = [(a.get("name") or "").strip() for a in accounts.all()]
    return f"{field} '{account_id}' does not exist." + recovery_hint(
        account_id, names, tool_hint="list_accounts"
    )


def _sorted_pairs(pairs) -> list[tuple[str | None, str]]:
    """Deterministic ordering over a set/iterable of ``(source, ref)`` pairs. A bare
    ``sorted()`` raises ``TypeError`` the moment a ``None``-source legacy pair sits beside a
    normal one in the same batch (``None < "plaid"`` is unorderable) — security-review fix
    2026-09-07: reachable from the bulk import loop (would abort the WHOLE batch, breaking
    the "one duplicate never costs the other 299" property) and from the attach choke."""
    return sorted(pairs, key=lambda p: (p[0] or "", p[1]))


def _external_identity(source: str | None, ref: str) -> str:
    """Unambiguous encoding for an external ``(source, ref)`` pair used as a match-decision
    identity (security-review fix 2026-09-07) — NOT ``f"{source}:{ref}"``, which collides a
    ``None`` source with the literal string ``"None"`` and breaks on a ref that itself
    contains ``":"``. Shared by the writer (``detach_external_ref``) and the reader
    (``_dedup_decided_different``); this key shape exists only on this branch, so there is no
    migration to reconcile."""
    return json.dumps([source, ref])


def _dedup_decided_different(cand, incoming: dict | None) -> bool:
    """True when a user already DETACHED one of the incoming row's external-ref pairs from
    ``cand`` — recorded as a ``different`` decision keyed on ``_external_identity(source,
    ref)`` vs ``cand.id`` (ADR 2026-09-07, ``services.ledger.detach_external_ref``). Both
    strong-duplicate refusal sites check this BEFORE refusing, so a re-fetch of the same
    provider row books on its own instead of colliding with the row the user already said is
    a DIFFERENT event. Local import: ``match_review`` imports this module."""
    if cand is None or not incoming:
        return False
    from backend.services.match_review import decisions

    for source, ref in ext_ref_pairs(incoming):
        remembered = decisions.get_for_pair(_external_identity(source, ref), cand.id)
        if remembered and remembered.get("verdict") == "different":
            return True
    return False


# The attach-hint names at most this many pairs verbatim — a row absorbing dozens of
# provenance pairs (a heavily-merged statement import) would otherwise inflate the refusal
# into an unbounded prompt-injection surface; "+N more" tells the caller there IS more
# without rendering it.
_ATTACH_HINT_MAX_PAIRS = 3


def _attach_hint_for(existing_id: str, incoming: dict | None) -> str:
    """The THIRD refusal exit (ADR 2026-09-07): when the row that just got refused itself
    carries external refs, name ``add_transaction_refs`` with the exact pair(s) filled in so
    the caller can link it to the row it collided with instead of re-presenting it forever.
    Empty when the incoming row carries no provenance — most hand-typed/manual refusals.

    Security review (2026-09-07): ``source``/``ref`` are PROVIDER-SUPPLIED strings landing
    verbatim in a MODEL-FACING refusal sentence — ``services.connector_fetch`` deliberately
    never sanitizes an identity field because it was "never rendered as prose" before this
    hint existed. Both are run through ``sanitize_untrusted`` here (the STORED pair is never
    touched — only what this sentence renders), and the list is capped at
    ``_ATTACH_HINT_MAX_PAIRS``. The rendered pairs are valid JSON (``json.dumps``), not a
    hand-rolled ``repr`` interpolation, so the sentence is copy-pasteable as-is."""
    from backend.core.untrusted import sanitize_untrusted

    pairs = _sorted_pairs(ext_ref_pairs(incoming)) if incoming else []
    if not pairs:
        return ""
    shown = pairs[:_ATTACH_HINT_MAX_PAIRS]
    refs_json = json.dumps(
        [
            {
                "source": sanitize_untrusted(s, max_len=200) if s else s,
                "ref": sanitize_untrusted(r, max_len=200),
            }
            for s, r in shown
        ]
    )
    extra = len(pairs) - len(shown)
    sentence = (
        f"If this is the SAME event, call add_transaction_refs(transaction_id={existing_id!r}, "
        f"refs={refs_json}) to link this record to it."
    )
    if extra:
        sentence += f" ({extra} more pair{'s' if extra != 1 else ''} not shown.)"
    return sentence


def _duplicate_error(cand, incoming: dict | None = None) -> DuplicateTransactionError:
    """Build the refusal for a pre-write duplicate. ERROR PATH ONLY — the account read below
    never runs on a successful write.

    The message must let the caller DECIDE without a second round trip, so it describes the row
    it collided with in the caller's own terms (date, signed amount + currency, description,
    account name) rather than naming an opaque id alone. ``context`` carries the same facts
    structurally for a UI, plus the score/class so a reviewer can see WHY.

    ``incoming`` (ADR 2026-09-07), when given, is the row that just got refused — passed through
    ONLY to build the attach-hint third exit above; it never changes score/klass/reasons, which
    describe ``cand`` (the EXISTING row), not the incoming one."""
    txn = cand.txn
    existing_id = txn.get("id") or "?"
    acct_id = txn.get("from_account_id") or txn.get("to_account_id")
    acct = accounts.find_by_id(acct_id) if acct_id else None
    # A transfer carries NO top-level ``amount`` — it stores from_amount/to_amount per leg — so
    # reading ``amount`` alone left the figure blank on exactly the class the guard most often
    # refuses (the transfer-leg echo). Fall through the legs, and take the currency from the
    # same leg the figure came from.
    amount, ccy = txn.get("amount"), txn.get("currency")
    if amount is None:
        for amt_key, ccy_key in (("from_amount", "from_currency"), ("to_amount", "to_currency")):
            if txn.get(amt_key) is not None:
                amount, ccy = txn[amt_key], txn.get(ccy_key) or ccy
                break
    facts = [
        str(txn.get("date") or ""),
        f"{float(amount):,.2f} {ccy or ''}".strip() if amount is not None else "",
        repr(txn.get("description")) if txn.get("description") else "",
        f"in {(acct or {}).get('name')!r}" if acct else "",
    ]
    head = f"Refused: this looks like a transaction already in your ledger. {cand.reasons[0]}"
    return DuplicateTransactionError(
        head.rstrip(".")
        + "."
        + duplicate_hint(
            existing_id, facts=facts, attach_hint=_attach_hint_for(existing_id, incoming)
        ),
        existing_id=existing_id,
        existing={
            "id": existing_id,
            "date": txn.get("date"),
            "amount": amount,
            "currency": txn.get("currency"),
            "description": txn.get("description"),
            "account_id": acct_id,
            "account_name": (acct or {}).get("name"),
        },
        score=cand.evidence,
        match_class=cand.klass.value,
        reasons=list(cand.reasons),
    )


# The account-ref fields every ledger write accepts. Resolved (name → id) at the choke, so
# REST, the AI tools, and every import path inherit it from one place.
_ACCOUNT_REF_FIELDS = ("from_account_id", "to_account_id")


def _find_account(account_id: str | None, rows: list[dict] | None = None) -> dict | None:
    if not account_id:
        return None
    if rows is not None:
        return next((row for row in rows if row.get("id") == account_id), None)
    return accounts.find_by_id(account_id)


def _unresolved_account_fields(data: dict, *, rows: list[dict] | None = None) -> list[str]:
    """The account-ref fields carrying something that is NOT already a known id.

    A cheap per-field ``find_by_id`` (an indexed point lookup) instead of a full-store scan:
    the overwhelmingly common write already supplies ids, and that case must stay free."""
    return [f for f in _ACCOUNT_REF_FIELDS if data.get(f) and not _find_account(data[f], rows)]


def _resolve_account_refs(data: dict, *, rows: list[dict] | None = None) -> dict:
    """Resolve a leg's account NAME to its id before any of the leg machinery runs. Returns
    ``data`` UNTOUCHED (same object) when every ref is already an id — the hot path.

    Must happen before ``matching.check`` (the dedup key is built from account ids — a name
    and its id would hash to two different rows and defeat L0) and before ``_validate_legs``
    (whose distinctness + existence checks compare ids).

    A ref that resolves to nothing is left EXACTLY as the caller sent it, so the existence
    check downstream still produces its own "does not exist" message (with the closest-name
    hint) rather than a confusing "missing field".

    The full-store read happens ONLY when a non-id ref is actually present, and reads THIS
    module's ``accounts`` instance rather than a fresh one built inside the resolver: the
    ledger's own existence checks read through it and callers/tests monkeypatch it by
    convention, so a second store identity would let resolution and validation disagree about
    which accounts exist."""
    from backend.services import accounts as accounts_service

    pending = _unresolved_account_fields(data, rows=rows)
    if not pending:
        return data
    rows = rows if rows is not None else accounts.all()
    resolved = {}
    for field in pending:
        hit = accounts_service.resolve_account_ref(str(data[field]), rows=rows)
        if hit is not None:
            resolved[field] = hit
    return {**data, **resolved} if resolved else data


def _account_currency_of(
    account_id: str | None, account_rows: list[dict] | None = None
) -> str | None:
    """An account's own currency, or None when the id resolves to nothing (error paths run
    before the existence check on some kinds, so a miss here must stay silent)."""
    acc = _find_account(account_id, account_rows)
    if not acc:
        return None
    return (acc.get("currency") or "").upper() or None


def _missing_amount_message(
    prefix: str,
    account_id: str,
    other_id: str | None = None,
    other_amount: float | None = None,
    account_rows: list[dict] | None = None,
) -> str:
    """Name the EXACT field to add, in the currency it has to be stated in.

    A transfer carries an amount PER LEG because the two legs can be denominated differently
    (an SGD wallet funding a USD wallet moves two different numbers). The old message said
    only which field was missing; a caller that didn't already know the per-leg rule had
    nothing to act on. When the two legs share a currency the second amount is simply the
    first one, so say that outright with the actual number filled in — a next-action hint the
    caller can apply without a second lookup."""
    field = f"{prefix}_amount"
    cur = _account_currency_of(account_id, account_rows)
    where = f" (in {cur}, that account's own currency)" if cur else ""
    head = f"{prefix}_account_id is set but {field} is missing."
    if other_amount is not None and cur and _account_currency_of(other_id, account_rows) == cur:
        return (
            f"{head} Both legs are in {cur}, so add {field}={abs(float(other_amount)):g} "
            "— the same value as the other leg."
        )
    if other_amount is not None and cur:
        other_cur = _account_currency_of(other_id, account_rows)
        converted = (
            f" — the {other_cur}→{cur} value of {abs(float(other_amount)):g}" if other_cur else ""
        )
        return f"{head} Add {field}{where}{converted}."
    return f"{head} Add {field}{where}."


def _validate_legs(kind: str, data: dict, *, account_rows: list[dict] | None = None) -> None:
    """Raise ValidationError / ValueError on malformed legs.

    Transfer integrity (always enforced at this choke point):
    - both from_account_id and to_account_id must be present
    - they must differ (a self-transfer is never meaningful)
    - both must resolve to a real account (archived counts as existing)

    Every other kind (investment, expense/income/adjustment): each leg PRESENT gets the SAME
    existence check the transfer branch gives both of its legs (archived counts as existing),
    then the id⇒amount rule.
    """
    if kind == "transfer":
        from_id = data.get("from_account_id")
        to_id = data.get("to_account_id")
        if not from_id or not to_id:
            raise ValidationError(
                "a transfer needs both from_account_id and to_account_id — "
                "for money sent to an external party, record an expense instead"
            )
        if from_id == to_id:
            same = _find_account(from_id, account_rows)
            label = f" ({(same.get('name') or '').strip()!r})" if same else ""
            raise ValidationError(
                f"from_account_id and to_account_id are both {from_id}{label} — they "
                "must be different accounts. To move money between two currency wallets "
                "of the same custodian, name each wallet separately (e.g. 'IBKR USD' → "
                "'IBKR SGD'); to record a fee or interest ON one account, use an expense or "
                "income instead of a transfer."
            )
        # Existence check (archived accounts count as existing — a closed account still has
        # a transaction history; refusing the write would orphan legitimate history repairs).
        if not _find_account(from_id, account_rows):
            raise ValidationError(_unknown_account_message("from_account_id", from_id))
        if not _find_account(to_id, account_rows):
            raise ValidationError(_unknown_account_message("to_account_id", to_id))
        if data.get("from_amount") is None:
            raise ValueError(
                _missing_amount_message("from", from_id, to_id, data.get("to_amount"), account_rows)
            )
        if data.get("to_amount") is None:
            raise ValueError(
                _missing_amount_message("to", to_id, from_id, data.get("from_amount"), account_rows)
            )
        return
    if kind == "investment":
        fa = data.get("from_account_id")
        ta = data.get("to_account_id")
        if not fa and not ta:
            raise ValidationError(
                "an investment transaction requires either from_account_id (buy/debit) "
                "or to_account_id (sell/credit) — not neither"
            )
        if fa and ta:
            raise ValidationError(
                "an investment transaction takes exactly one cash leg: "
                "from_account_id for a buy (cash out) or to_account_id for a sell (cash in)"
            )
        # Existence check (archived accounts count as existing — same rule as the transfer
        # branch above) — FIX ROUND 1 (plan 3 task 5 review, FIX 3): extends the transfer
        # branch's/Inclusion B's own mechanism to investment's single cash leg — the sibling
        # gap Inclusion B's own task report flagged as a concern but deliberately left
        # untouched (named out of that task's scope: "the non-transfer tail" meant
        # expense/income/adjustment, not investment). Reuses `_find_account`/
        # `_unknown_account_message` verbatim, same as every other leg-existence check in
        # this function.
        if fa and not _find_account(fa, account_rows):
            raise ValidationError(_unknown_account_message("from_account_id", fa))
        if ta and not _find_account(ta, account_rows):
            raise ValidationError(_unknown_account_message("to_account_id", ta))
        if fa and data.get("from_amount") is None:
            raise ValueError(_missing_amount_message("from", fa, account_rows=account_rows))
        if ta and data.get("to_amount") is None:
            raise ValueError(_missing_amount_message("to", ta, account_rows=account_rows))
        return
    # A normal transaction's direction is its leg, not only its ``kind``. Accepting an
    # income with a from-leg (or an expense with a to-leg) lets an update change the label
    # without moving the money, leaving the register and detail view contradictory.
    from_id = data.get("from_account_id")
    to_id = data.get("to_account_id")
    if kind == "expense":
        if not from_id or to_id:
            raise ValidationError(
                "expense requires a source leg (from_account_id and from_amount) only; "
                "use income for money entering an account or transfer for two accounts"
            )
    elif kind == "income":
        if not to_id or from_id:
            raise ValidationError(
                "income requires a destination leg (to_account_id and to_amount) only; "
                "use expense for money leaving an account or transfer for two accounts"
            )
    elif kind == "adjustment" and bool(from_id) == bool(to_id):
        raise ValidationError(
            "adjustment requires exactly one leg: from_account_id/from_amount for a "
            "decrease or to_account_id/to_amount for an increase"
        )

    # Non-transfer tail (expense/income/adjustment) — existence-checked the SAME way the
    # transfer branch above checks each of its two legs (PLAN 3 TASK 5, INCLUSION B — T4
    # concern 1, controller-ruled industrial-grade improvement). Before this, an unresolvable
    # single-leg account id sailed past this function untouched and hit `transactions.
    # from_account_id`/`to_account_id`'s real FOREIGN KEY (plan 3 task 2) at INSERT time — a
    # raw, uncaught `sqlalchemy.exc.IntegrityError` surfacing as an unstructured 500 to any
    # REST caller that reaches this gap without going through the AI-tool layer's
    # `_validate_account_id` first (pinned as a characterization gap in plan 3 task 4's own
    # sweep — see `tests/test_account_ref_resolution.py`). Reusing `_find_account`/
    # `_unknown_account_message` verbatim (the transfer branch's own mechanism) means a
    # single-leg miss now fails the SAME clean, typed, 422 way a transfer's does.
    if from_id:
        if not _find_account(from_id, account_rows):
            raise ValidationError(_unknown_account_message("from_account_id", from_id))
        if data.get("from_amount") is None:
            raise ValueError(_missing_amount_message("from", from_id, account_rows=account_rows))
    if to_id:
        if not _find_account(to_id, account_rows):
            raise ValidationError(_unknown_account_message("to_account_id", to_id))
        if data.get("to_amount") is None:
            raise ValueError(_missing_amount_message("to", to_id, account_rows=account_rows))


# Built-in category keys that are structural (transfer/adjustment legs) and so can never carry
# an expense/income split line. Owned here (services tier) so split validation never imports api.
# Note: "investment" was removed — it is now a transaction KIND, not a category key.
_NON_SPENDING_KEYS = {"transfer", "adjustment"}


def _validate_splits(
    kind: str,
    leg_amount: float | None,
    splits: list,
    *,
    category_rows: list[dict] | None = None,
) -> list[dict]:
    """Validate + normalise a split allocation (P3-SP). Raises ``ValidationError`` (→ HTTP 422)
    on any breach; returns the cleaned ``[{category, amount, note}]`` list (abs amounts).

    Constraints (RFC §1.3):
      1. kind gate — splits only on expense/income (a transfer/adjustment carries at most
         ONE plain category — never a split allocation; see ``_validate_legs``).
      2. each amount > 0; each category a valid expense/income key (not the SPLIT sentinel,
         not a transfer/adjustment-structural key).
      3. sum(amounts) == the allocated leg amount (±0.01) — a mismatch is REFUSED, never
         silently re-scaled (a silent re-scale would hide a data-entry error / drift a budget).
    The 1-line→unsplit normalisation is the caller's (it needs to clear the sentinel)."""
    from backend.services import categories as categories_service

    if kind not in ("expense", "income"):
        raise ValidationError("splits are only allowed on expense/income transactions")
    lines = [s for s in (splits or []) if isinstance(s, dict)]
    if not lines:
        raise ValidationError("a split must have at least one line")
    if leg_amount is None:
        raise ValidationError("a split transaction must have an amount to allocate")

    cleaned: list[dict] = []
    total = 0.0
    for s in lines:
        cat = (s.get("category") or "").strip()
        try:
            amt = abs(float(s.get("amount")))
        except (TypeError, ValueError):
            raise ValidationError("each split line needs a numeric amount")
        if amt <= 0:
            raise ValidationError("each split amount must be greater than 0")
        if not cat:
            raise ValidationError("each split line needs a category")
        if cat == SPLIT or cat in _NON_SPENDING_KEYS:
            raise ValidationError(f"'{cat}' cannot be a split category")
        # Resolve a label OR an id (services.categories.resolve_category_ref) — an LLM/import
        # may supply either; the stored value is always the resolved opaque id.
        resolved = categories_service.resolve_category_ref(cat, rows=category_rows)
        if resolved is None:
            raise ValidationError(
                f"Unknown split category '{cat}'. Use an existing category or create it first."
            )
        cleaned.append({"category": resolved, "amount": round(amt, 2), "memo": s.get("memo", "")})
        total += amt
    if abs(round(total, 2) - round(float(leg_amount), 2)) > 0.01:
        raise ValidationError(
            f"split amounts ({round(total, 2)}) must sum to the transaction amount "
            f"({round(float(leg_amount), 2)})"
        )
    return cleaned


def _apply_splits(row: dict, *, category_rows: list[dict] | None = None) -> dict:
    """Validate the row's ``splits`` against its allocated leg (post-normalisation), normalise a
    1-line split to a plain category (no sentinel hiding one category), and stamp the SPLIT
    sentinel when a real (≥2-line) split survives. A no-split row is returned untouched EXCEPT
    that a bare SPLIT sentinel with no lines is cleared (see below)."""
    raw = row.get("splits")
    if not raw:
        # No split lines. A top-level SPLIT sentinel with nothing behind it is never valid — it
        # survives an "unsplit" edit because the update PUT's exclude_none drops the null category
        # the client sends to clear it, so ``merged`` keeps the old sentinel. Clear it here so the
        # row falls back to Uncategorized instead of persisting a phantom "Split" the UI can't render.
        if row.get("category") == SPLIT:
            row["category"] = None
        return row
    leg = allocated_leg_amount(row)
    cleaned = _validate_splits(row.get("kind", "expense"), leg, raw, category_rows=category_rows)
    if len(cleaned) == 1:
        # Degenerate single line → a plain categorized txn (RFC §1.3 rule 3).
        row["category"] = cleaned[0]["category"]
        row["splits"] = None
    else:
        row["category"] = SPLIT
        row["splits"] = cleaned
    return row


def create_transaction(
    data: dict,
    force: bool = False,
    *,
    force_transfer_rate: bool = False,
    _skip_payee_resolve: bool = False,
    meta_out: dict | None = None,
    conn: Connection | None = None,
    account_rows: list[dict] | None = None,
    resurrect: bool = False,
) -> dict:
    """Normalise legs into account currencies, compute the base-currency reporting
    snapshot (skipped for transfers), and persist. Returns the stored transaction.

    ``resurrect`` is the ONLY way past a remembered deletion: the row's ref identity was deleted
    on purpose (``rejected_identities``), the caller has an explicit ``create`` decision for it,
    and the record is forgotten in the same transaction that re-books it. ``force`` does not
    reach it — "skip duplicate detection" is about a second booking of a live event, not about
    overriding the user's delete (ADR 2026-09-02-a-deleted-identity-is-remembered).

    ``conn``, when given, composes this write into a caller's own open transaction (Task
    4, storage plan 4b — a non-ledger choke, e.g. a lot dispose's proceeds deposit or a
    vest's income leg, booking a transaction as ONE of several tables it commits
    atomically). See ``_txn_unit_of_work``'s docstring for the caller's lock-set
    obligation. ``conn=None`` (the default, every pre-Task-4 call site) is
    byte-identical: opens its own standalone transaction.

    ``meta_out``, when given, is FILLED with write-side facts the stored row cannot carry —
    today ``payee_minted_txn_ids``: the ids whose payee this call invented from the raw
    description (see ``resolve_transaction_row``). An out-param rather than a widened return
    because this is a choke point with many callers, none of which should have to change to
    ignore a fact they don't use. ``payee_minted_txn_ids`` is populated only when a write is
    actually attempted; the external-refs idempotency no-op below mints nothing and instead
    reports ``idempotent_existing_id`` — the row it returned WITHOUT writing. A caller that
    does not pass ``meta_out`` cannot distinguish that no-op from a create, because the
    returned row is a stored row either way (scar 2026-08-12: a scheduled run re-presented two
    already-logged emails, got the create-shaped ``ok: true`` back for both, and reported "3
    new transactions logged" having written one).

    Idempotency: when ``data`` carries a non-empty ``external_refs`` entry (a stable
    real-world ``{source, ref}`` pair, e.g. source ``email``/ref ``<message_id>`` or source
    ``recurring``/ref ``<task_id>:<run_iso>``) and a transaction with that ``(source, ref)``
    already exists, this is a SAFE NO-OP — the existing row is returned and no second row is
    written. This is the load-bearing guard that makes re-presenting a fetched-but-not-logged
    email (the re-scan window) impossible to double-log, independent of LLM behavior. Behavior
    is unchanged when no external_refs entry is supplied.

    The exact-id rule is no longer a bespoke check here — it is layer L0 of the ONE match
    engine (RFC transaction-dedup-engine), reached via the FLOOR profile so the engine owns the
    single definition of "duplicate". FLOOR is O(1) (L0 only) and unbypassable. Local import
    breaks the ledger→matching→ledger cycle (matching reads the ledger for its other layers)."""
    # An autonomous agent run logs through the same AI tools (source "ai"), but it's NOT interactive
    # Lucius — record it as "agent" so the ledger/source column distinguishes agent-logged rows from
    # Lucius ones (the audit context already knows WHO; mirror it onto the stored source).
    from backend.core.audit_context import get_audit_scope as _get_audit_scope

    _scope = _get_audit_scope()
    if _scope is not None and _scope.surface == "automation" and not _suppress_create_audit.get():
        # Creation origin must outlive the capped audit log; an edit's inner rebuild
        # must not turn an attended transaction into an automation-created one.
        data = {**data, "source": "automation"}
    elif _scope is not None and _scope.actor == "agent" and data.get("source") in (None, "ai"):
        data = {**data, "source": "agent"}
    # F24: source validation — warn on unrecognized source but never refuse (legacy rows must work)
    _src = data.get("source")
    if _src is not None:
        from backend.core.txn_sources import KNOWN_SOURCES as _KNOWN_SOURCES

        if _src not in _KNOWN_SOURCES:
            _log.warning(
                "create_transaction: unrecognized source %r (not in KNOWN_SOURCES); row will be stored as-is",
                _src,
            )

    # Account NAME → id, before anything reads a leg. matching.check keys its L0 dedup on the
    # account ids, so a name resolved after it would hash to a different row and slip the guard.
    data = _resolve_account_refs(data, rows=account_rows)

    from backend.services import matching

    floor = matching.check(data, profile=matching.FLOOR)
    if floor.action == "noop" and floor.canonical_id:
        existing = transactions.find_by_id(floor.canonical_id)
        if existing is not None:
            if meta_out is not None:
                meta_out["idempotent_existing_id"] = existing["id"]
            return existing

    # Semantic duplicate REFUSAL (above the write, unlike the post-insert review queue). The
    # floor above only catches an identical (source, ref) pair; this catches the same event
    # arriving under a different id — which is the normal case for a book fed by statements,
    # connectors, scheduled agents and hand entry at once. "Created-then-flagged" only protects
    # a book whose queue is READ, and most of those paths have no reader.
    # ``force`` is the documented escape hatch (add_transaction's own schema already promises
    # "set to true to skip duplicate detection"); it is checked FIRST so a forced write never
    # pays for the candidate scan.
    # ``_suppress_create_audit`` marks an INNER REBUILD (update_transaction, and the merge that
    # rides on it) rather than a new event. Such a rebuild is by construction identical to the
    # row it replaces, so the guard would match it against its own original and refuse — which
    # is exactly what broke two live statement merges ("merge failed for row=13/14", surfacing
    # to the user as an unexplained "2 left out"). A rebuild is not a second booking.
    if not force and not _suppress_create_audit.get():
        dup = matching.blocking_duplicate(matching.check(data, profile=matching.FULL))
        if dup is not None and not _dedup_decided_different(dup, data):
            raise _duplicate_error(dup, data)

    from backend.services import rejected_identities as _rejected

    rejection = None
    if not _suppress_create_audit.get():
        rejection = _rejected.rejection_for(data)
        if rejection is not None and not resurrect:
            raise _rejected.previously_deleted_error(rejection)

    row, meta = resolve_transaction_row(
        data,
        skip_payee_resolve=_skip_payee_resolve,
        account_rows=account_rows,
        force_transfer_rate=force_transfer_rate,
    )
    automation_matches = meta["automation_matches"]
    automation_hint = meta["automation_hint"]
    # Review queue (Accounts redesign): an import/AI CREATE lands in review by default. Gated on
    # a genuine create — the update rebuild runs inside _suppress_inner_create_audit(), so editing
    # a legacy import/AI row never silently re-flags it. An explicit needs_review in `data` wins.
    if data.get("needs_review") is None and not _suppress_create_audit.get():
        if default_needs_review(row.get("source")):
            row["needs_review"] = True
    balance_before = _capture_automation_balance_snapshot()
    # PLAN 3 TASK 4: transactions.append + the audit record below are now ONE transaction
    # (locked_unit_of_work) — the vest-era "4-store sequence becomes one BEGIN…COMMIT"
    # promise. Post-write bookkeeping that used to run unrelated-file-store-safe INSIDE
    # this block (email snapshot promote / payee usage / automation match recording) has
    # since moved OUT, below — see the "MOVED out of the transaction above" comment there
    # for why (storage plan 4e task 1 email-snapshot finding, mirroring plan 4d task 2's
    # identical payee/automation-store fix in this same function).
    # TASK 4 (storage plan 4b): `_txn_unit_of_work` reuses the CALLER's conn (the `conn`
    # parameter above) when given, else opens the same standalone transaction as before —
    # rebinding the name `conn` here is deliberate and harmless (a given conn yields itself
    # back unchanged; see that helper's docstring).
    # `composed` is captured BEFORE that rebinding — see _publish_transaction_events_after_commit.
    composed = conn is not None
    _stamp_ref_emitters([row])
    with _txn_unit_of_work(conn) as conn:
        stored = transactions.append(row, conn=conn)
        if rejection is not None:
            _rejected.forget_matching([data], conn=conn)
            if meta_out is not None:
                meta_out["resurrected_from"] = rejection.get("txn_id")
        if meta_out is not None:
            meta_out["payee_minted_txn_ids"] = (
                [stored["id"]] if meta.get("payee_is_new") and stored.get("payee_id") else []
            )
            meta_out["automation_matches"] = automation_matches
        # Provenance (transaction-provenance RFC): record the create, UNLESS this call is the
        # inner rebuild of update_transaction (which emits its own "update" event with the diff).
        if not _suppress_create_audit.get():
            from backend.services import audit as audit_service

            audit_service.record_txn_event(
                "create",
                stored["id"],
                source=stored.get("source"),
                automation_applied=_automation_category_provenance(
                    automation_hint, stored.get("category")
                ),
                conn=conn,
            )
    # Automation match usage accounting + payee usage/category learning — moved out of the
    # transaction above (plan 4d task 2 finding, mirroring the corrections fix from plan 4a
    # task 2 in this same file): AutomationStore/PayeeStore are now DB-backed and each opens
    # its OWN independent transaction (automations/payees are not among this block's
    # locked_unit_of_work tables) — calling either from INSIDE the still-open
    # transactions/txn_audit transaction self-deadlocks SQLite's single-writer model (a
    # second, nested BEGIN on the same engine blocks behind the first, which never commits
    # until the nested call returns). Harmless before this task (both stores were
    # file-backed with their own independent per-file lock); a live hazard now that both ride
    # the same database.
    # REVIEW-GATE FIX (plan 4d task 2 follow-up, BLOCKER): moving them out of the LOCAL
    # transaction above is NOT "safe to run after commit" on its own — that was only true
    # for composed=False. When `composed=True` the OUTER transaction (a trade choke's own
    # locked_unit_of_work — equity/options/lots) is STILL OPEN at this exact point in the
    # call stack, so calling either immediately here hits the identical database-is-locked
    # self-deadlock the publish step below was already fixed for, just one step earlier in
    # this tail — and because both callees swallow their own exception, the deadlock never
    # surfaced as a loud failure: it surfaced as a SILENTLY DROPPED write (every composed
    # vest income leg lost its payee usage/category-learning update, and any composed leg
    # matching a legacy automation lost its match-usage stats) plus a multi-second stall per
    # call while holding the outer choke's locks. Both now route through
    # `_after_outer_commit` — the SAME on_commit seam `_publish_transaction_events_after_commit`
    # uses below — so they defer to AFTER the outer commit on the composed path (fire
    # exactly once, never on a rollback) and stay immediate, byte-identical, otherwise.
    # `stored`/`automation_matches` already hold their final shape by here, and neither
    # write was ever meant to be atomic with the ledger write (both best-effort,
    # exception-guarded — now able to actually succeed instead of losing the write to a
    # swallowed `database is locked`). learn_category skips None/structural keys (no-op
    # when category is absent); because update_transaction re-runs this path, editing a
    # category teaches here too.
    if not _suppress_create_audit.get():
        from backend.services.automations.transaction_events import record_committed_matches

        _after_outer_commit(
            composed,
            lambda: record_committed_matches(
                automation_matches, transaction_id=stored["id"], context="transaction_pre_commit"
            ),
        )
    if stored.get("payee_id"):
        from backend.services import payee_registry

        def _learn_payee_from_stored() -> None:
            payee_registry.note_usage(stored["payee_id"], stored.get("date"))
            payee_registry.learn_category(
                stored["payee_id"], stored.get("category"), stored.get("kind", "expense")
            )

        _after_outer_commit(composed, _learn_payee_from_stored)
    # Email-snapshot promote — MOVED out of the transaction above (storage plan 4e task 1
    # finding: EmailSnapshotStore is now DB-backed and opens its OWN independent
    # transaction when not given a conn of its own, so calling it from inside the
    # still-open transactions/txn_audit transaction hits the identical
    # database-is-locked self-deadlock the payee/automation-match writes above were
    # fixed for — harmless before this task, when email_snapshots was still a JSON file
    # with its own independent per-file lock). Routes through the same
    # `_after_outer_commit` seam for the same reason: defer to AFTER the outer commit on
    # the composed path (fire exactly once, never on a rollback), stay immediate,
    # byte-identical, otherwise. `stored` already holds its final shape by here, and this
    # write was never meant to be atomic with the ledger write (best-effort,
    # exception-guarded inside `_promote_source_snapshots` itself).
    _after_outer_commit(composed, lambda: _promote_source_snapshots(stored))
    # _publish_committed_transaction_events is documented as running only AFTER the ledger
    # write has committed — moved here (out of the transaction above) for exactly that
    # reason; it used to run after two ALREADY-independently-committed store writes, and
    # now runs after the one shared commit instead. Same gate, same arguments, same position
    # relative to everything else — only WHERE "committed" falls has moved.
    # TASK 4 caveat (storage plan 4b), FIXED (storage plan 4d task 2): when `conn` was
    # supplied EXTERNALLY (a composing choke, e.g. a lot dispose's proceeds leg or a
    # vest's income leg), this call used to sit at the SAME relative point as always while
    # the outer transaction was still open — harmless while automation_runtime was a JSON
    # file, but a self-deadlock once it became DB-backed (storage plan 4c): publishing
    # opened a SECOND write transaction against an engine still holding the outer one's
    # write lock. `_publish_transaction_events_after_commit` now defers the composed case
    # to `db.engine.on_commit`, firing once the OUTER transaction actually commits — never
    # on a rollback, which also closes the sibling phantom-event-on-rollback gap this same
    # caveat used to accept ("a later step fails and rolls everything back, reference a txn
    # that ends up never existing").
    if not _suppress_create_audit.get():
        _publish_transaction_events_after_commit(
            composed, "create", [stored], balance_before=balance_before
        )
    return stored


def resolve_transaction_row(
    data: dict,
    *,
    mint_payee: bool = True,
    skip_payee_resolve: bool = False,
    automations: list[dict] | None = None,
    category_rows: list[dict] | None = None,
    rates: dict | None = None,
    account_rows: list[dict] | None = None,
    base: str | None = None,
    force_transfer_rate: bool = False,
    labels_as_given: bool = False,
) -> tuple[dict, dict]:
    """Run the full create-time RESOLUTION pipeline on ``data`` and return ``(row, meta)``
    WITHOUT persisting anything.

    This is the body ``create_transaction`` used to inline: leg normalisation, the
    base-currency snapshot, split allocation, transaction Automations, payee resolution and
    the category-precedence fold (explicit > Automation > payee-default > None). It is the ONE
    implementation of "what does this draft actually become" — ``create_transaction`` calls
    it and then appends, and ``services.ledger_preview`` calls it to render a confirm card
    showing the SAME resolved values the write would store. Extracting it (rather than
    adding a ``dry_run`` flag to the writer) is deliberate: this function contains no store
    write at all, so a preview cannot commit by taking a wrong branch.

    ``mint_payee=False`` is the PREVIEW mode and is what keeps a preview pure per ADR
    2026-07-18 (`previews-must-not-write`): payee resolution still looks the merchant up by
    rule / alias / alias-prefix, but the last-resort ``create=True`` mint is suppressed and
    reported as ``meta["payee_is_new"]`` instead — the same "resolve read-only, report the
    intent" shape ``propose_position_update`` uses for a not-yet-existing account. The
    category outcome is unaffected: a freshly minted payee has no default category by
    construction, so the blank-category fold lands on ``None`` (Uncategorized) either way.

    ``meta`` carries what only the resolver can know: ``automation_matches`` (for the
    post-commit usage update), ``automation_hint`` (for the audit provenance
    payload), ``category_source`` (WHICH precedence tier actually decided the stored
    category — the thing a confirm card has to show, because it is decided silently in
    here) and ``payee_is_new``.

    ``automations`` / ``category_rows`` / ``rates`` / ``account_rows`` / ``base`` let a caller
    resolving MANY rows preload each store and the profile currency once instead of per row —
    the same optimisation
    ``bulk_create_transactions`` already does inline. Without it, previewing a 500-row batch
    would re-read the rule, category, fx and payee stores per row and be markedly more
    expensive than the write it previews.

    ``force_transfer_rate`` bypasses ONLY ``_assert_transfer_amount_converted`` (the
    equal-amount guard) — never ``_assert_transfer_rate_plausible`` (the 10x magnitude guard),
    which has no escape hatch by design. Same shape as ``create_transaction``'s ``force``,
    kept as a separate flag because the two refusals mean different things to a caller: a
    duplicate is "this event already exists", an implausible transfer amount is "these two
    numbers don't square with each other" — a caller confident about one is not thereby
    confident about the other.
    """
    # Account NAME → id happens HERE, not only in create_transaction, so a PREVIEW resolves
    # the same refs the write does. Two things depend on it: _validate_legs (below) compares
    # ids, so a draft naming "Vantage Brokerage USD" would otherwise be refused by the preview while the
    # write accepted it; and the confirm card renders the resolved account NAME rather than
    # echoing back whatever string the model sent.
    #
    # create_transaction ALSO calls this, before matching.check, and must keep doing so: the
    # L0 dedup key is built from account ids, so a name resolved only here would hash to a
    # different row and slip the duplicate guard. The double call is free and safe —
    # _resolve_account_refs returns `data` untouched once every ref is an id, so the second
    # call is two indexed point lookups and no store read.
    data = _resolve_account_refs(data, rows=account_rows)
    kind = data.get("kind", "expense")
    if kind not in _KIND_NAMES:
        raise ValidationError(f"unknown transaction kind: {kind}")
    _validate_reserved_category(kind, data.get("category"))
    _validate_legs(kind, data, account_rows=account_rows)
    base = (base or base_currency()).upper()
    fa, famt, fcur, foa, foc = _normalise_leg(
        "from", data, account_rows=account_rows, base=base, rates=rates
    )
    ta, tamt, tcur, toa, toc = _normalise_leg(
        "to", data, account_rows=account_rows, base=base, rates=rates
    )
    # Both legs are now in their accounts' own currencies, which is the only point where the
    # rate they imply between each other is meaningful. Here rather than in `_validate_legs`
    # (which runs pre-normalisation, on whatever currencies the caller happened to state) and
    # here rather than in `create_transaction` (so a PREVIEW refuses too — a confirm card must
    # never render a row the write would then reject).
    if kind == "transfer":
        _assert_transfer_rate_plausible(famt, fcur, tamt, tcur, rates)
        if not force_transfer_rate:
            _assert_transfer_amount_converted(famt, fcur, tamt, tcur, rates)
    orig_amount, orig_currency = _resolve_original(kind, data, famt, (foa, foc), tamt, (toa, toc))

    # `rates` stays None unless a bulk caller preloaded one: to_base then resolves rates
    # itself via core.fx._load_rates, which is NOT the same source as
    # services.fx_cache.load_fx_cache. Eagerly defaulting to the latter here silently
    # switched the single-write path's rate source (caught by
    # test_merge_amount_changing_statement_merge_still_recomputes_base).
    base_amount = base_cur = fx = src_amt = None
    if kind == "transfer":
        base_amount, src_amt = _transfer_base_value(famt, fcur, tamt, tcur, base, rates)
    else:
        # The "primary" leg defines the reporting figure: to-leg for income, else from-leg.
        if kind == "income" and tamt is not None:
            src_amt, src_cur = tamt, tcur
        elif famt is not None:
            src_amt, src_cur = famt, fcur
        else:
            src_amt, src_cur = tamt, tcur
        if src_amt is not None:
            base_amount = to_base(src_amt, src_cur or base, base, rates=rates)
    # base_amount is STAMPED ONCE (ADR 2026-07-09, historical-rate lock): an explicit caller-
    # supplied value wins — the SGD amount the user actually transacted at ("Value in SGD"), or the
    # value threaded through unchanged from an edit (update_transaction preserves it by default and
    # drops it only when the native leg changes). No service caller passes base_amount, so honoring
    # it here is safe. Sign follows the native leg; fx_rate is derived.
    explicit = data.get("base_amount")
    # `is not None`, NOT truthiness — same reason as the stamp below: a zero native leg is a
    # real amount, and dropping an explicit base_amount on it would leave the row unstamped.
    if explicit is not None and src_amt is not None:
        sign = -1.0 if (base_amount is not None and base_amount < 0) else 1.0
        base_amount = round(sign * abs(float(explicit)), 2)
    if base_amount is not None and src_amt is not None:
        # `is not None`, NOT truthiness: 0.0 is falsy, so a zero-amount row (a "my balance was
        # zero when tracking started" opening) would store base_amount=0.0 with
        # base_currency=None — the exact unstamped shape the historical-rate lock distrusts.
        # fx_rate stays None at zero: a rate is undefined there, and 0.0 would be a lie.
        base_cur = base
        fx = round(base_amount / src_amt, 6) if src_amt else None

    row = {
        **data,
        "kind": kind,
        "from_account_id": fa,
        "from_amount": famt,
        "from_currency": fcur,
        "to_account_id": ta,
        "to_amount": tamt,
        "to_currency": tcur,
        "original_amount": orig_amount,
        "original_currency": orig_currency,
        "base_amount": base_amount,
        "base_currency": base_cur,
        "fx_rate": fx,
    }
    row.pop("base_amount_pinned", None)  # retired flag — drop any legacy value threaded in via data
    # Split allocation (P3-SP): validate Σ lines == the allocated leg amount + categories,
    # then stamp the SPLIT sentinel (or normalise a 1-line split to a plain category). Done
    # BEFORE rules so the rules engine sees the sentinel and skips category-setting.
    row = _apply_splits(row, category_rows=category_rows)
    # Transaction Automations: label-only mutation (category/tags/note) applied
    # as the LAST transform before persist, so what's stored is what the rules
    # produced — and it's inside the same checkpoint'd write. Applies on EVERY
    # import path because they all converge here (manual/AI/email/statement).
    from backend.services.automations.transaction_events import (
        active_transaction_transforms,
        apply_transaction_automations,
    )

    automation_rows = active_transaction_transforms(automations)
    row, automation_matches = apply_transaction_automations(row, automation_rows)
    automation_hint = next(
        (match for match in reversed(automation_matches) if match.category_applied),
        None,
    )
    row.pop("category_explicit", None)  # in-flight edit-path flag — never persisted
    # Payee resolution (P4-PAYEE phase 1): expense/income resolve the merchant entity from
    # the note (or honor a caller-supplied payee_id). Local import breaks the
    # ledger→payee_registry→payees→ledger cycle.
    from backend.services import payee_registry

    payee_is_new = False
    if kind in ("expense", "income"):
        if skip_payee_resolve:
            # Update path: payee_id is already set from the merged record (explicit change —
            # validated in update_transaction — or preserved from existing). Never auto-resolve
            # from the note on an edit.
            pid = row.get("payee_id")
        else:
            pid = row.get("payee_id")
            if pid:
                # Caller-supplied (REST or AI tool): must reference a real entity, or a
                # hallucinated id would be stored as a dangling link.
                payee_registry.require_payee(pid)
            else:
                # A caller-supplied clean payee NAME (import/AI) mints/attaches that merchant
                # entity by name; else derive it from the note. This lets an importer keep the
                # RAW bank descriptor in the note (so the cross-source match engine can still
                # pair it with a bank-feed line) while the register shows a clean payee.
                # Precedence: payee_id > payee name > note.
                # Clean-name first, but the RAW note is the fallback MATCHER before anything
                # mints: an LLM's short clean payee ("7-Eleven") can miss a book whose alias
                # was learned from the long bank line — which the note still matches. Minting
                # a fresh payee from the clean name is the LAST resort (it has no default
                # category, so a silent mint books Uncategorized — seen live).
                pid = payee_registry.resolve_payee_id(row.get("payee") or "", create=False)
                if not pid:
                    pid = payee_registry.resolve_payee_id(
                        row.get("description") or "", create=False
                    )
                if not pid:
                    # The one MINTING step in this function. Under mint_payee=False (preview)
                    # it is skipped and reported instead — see the docstring's ADR note.
                    fallback_ref = row.get("payee") or row.get("description", "")
                    if mint_payee:
                        pid = payee_registry.resolve_payee_id(fallback_ref)
                        # Report the mint we just PERFORMED, not only the one a preview would
                        # have performed. Both create=False lookups above already missed, so
                        # reaching here and getting an id means this call invented the entity —
                        # named after a raw bank string (refs and amounts included) and with no
                        # default category, i.e. the "silent mint books Uncategorized" scar. Callers
                        # that could clean it up were previously never told it happened.
                        payee_is_new = bool(pid)
                    elif (fallback_ref or "").strip():
                        payee_is_new = True
        row["payee_id"] = pid
    row.pop("payee", None)  # transient input — resolved into payee_id, never a stored column
    # Per-kind payee default (per-kind since 2026-07-10): fill a STILL-blank category from
    # the payee's default FOR THIS KIND — never cross-kind (an expense default no longer
    # bleeds onto income/transfer/investment). Covers every categorizable kind that carries
    # a payee_id. Ordered AFTER the rules engine and BEFORE the None-when-absent path, so
    # precedence is: explicit > rule > payee (per-kind) > None.
    category_before_payee_default = row.get("category") or None
    if kind != "adjustment" and not labels_as_given:
        pid = row.get("payee_id")
        if pid and not row.get("category"):
            row["category"] = payee_registry.default_category_for(
                pid, kind, category_rows=category_rows
            )
    # Category enforcement: adjustment carries NO category (stripped to None — a balance
    # correction is never a spending label). Every other kind (expense/income/transfer/
    # investment) is OPTIONAL — keep a provided category and validate it exists; if absent,
    # leave None. No forced "uncategorized" sink. A transfer's category no longer gets
    # stripped (owner decision 2026-07-03): the "transfers carry category=None" invariant
    # existed only because category==budget-row made any transfer label a double-count; the
    # envelope⊥category split made category an inert label, so the ON-BUDGET-LEG-aware fold
    # in planning._activity_by_category decides what counts, not the write path. See
    # docs/adr/2026-07-03-categorized-transfers.md.
    # Precedence: explicit > rule > payee-default > None.
    if kind == "adjustment":
        row["category"] = None
    else:
        cat = row.get("category") or None  # "" → None
        if cat:
            from backend.services import categories as categories_service

            resolved = categories_service.resolve_category_ref(cat, rows=category_rows)
            if resolved is None:
                raise ValidationError(_unknown_category_message(cat))
            cat = resolved
        row["category"] = cat
    return row, {
        "matched_automation_ids": [match.automation_id for match in automation_matches],
        "automation_matches": automation_matches,
        "automation_hint": automation_hint,
        "category_source": _category_source(
            final=row.get("category"),
            automation_hint=automation_hint,
            before_payee_default=category_before_payee_default,
        ),
        "payee_is_new": payee_is_new,
    }


def _category_source(
    *,
    final: str | None,
    automation_hint,
    before_payee_default: str | None,
) -> str:
    """Which precedence tier actually DECIDED the stored category.

    ``explicit > automation > payee-default > None`` is resolved silently inside
    ``resolve_transaction_row``, which is exactly why a committed row could differ from what
    the user meant with nothing on screen to explain it (the measured 17%-redo failure on
    ``update_transaction``). A confirm card has to name the tier, so the resolver reports it
    rather than the card re-deriving it and drifting.

    ``split`` is its own answer: the sentinel is a marker, not a categorisation decision."""
    if final == "split":
        return "split"
    if not final:
        return "none"
    if not before_payee_default:
        return "payee_default"
    if automation_hint:
        return "automation"
    return "explicit"


def _automation_category_provenance(
    automation_hint,
    post_id: str | None,
    *,
    category_rows: list[dict] | None = None,
) -> dict | None:
    """Audit payload for an Automation-decided stored category.

    Best-effort, NEVER raises: it runs AFTER the row has durably persisted, and its
    resolve re-reads categories.json (fail-closed on corruption) — a provenance failure
    must not break a create whose write already succeeded (same contract as
    ``_promote_source_snapshots`` / ``record_committed_matches``)."""
    try:
        if not automation_hint or not post_id:
            return None
        pre_id = None
        pre_ref = automation_hint.category_from
        if pre_ref:
            from backend.services import categories as categories_service

            pre_id = (
                categories_service.resolve_category_ref(str(pre_ref), rows=category_rows)
                or str(pre_ref)  # unresolvable caller ref — keep the raw string for the record
            )
        if pre_id == post_id:
            return None
        return {
            "automation_id": automation_hint.automation_id,
            "automation_name": automation_hint.automation_name,
            "category_from": pre_id,
            "category_to": post_id,
        }
    except Exception:
        _log.warning(
            "automation provenance stamp failed (audit-only, write unaffected)", exc_info=True
        )
        return None


def known_external_refs(source: str) -> dict[tuple[str | None, str], str]:
    """Every LIVE ledger transaction id, keyed by each ``(source, ref)`` pair it carries for
    ``source`` — lifted from ``services.ingestion.orchestrator._known_external_refs`` (RFC
    connectors-as-data-sources §C), now a public ledger helper so the connector fetch tools
    (L2) and ``services.connector_snapshots`` (booked_txn_id resolution) share ONE definition
    of "is this row already booked" with the dedup floor.

    Same semantics as the orchestrator's private helper: a user-deleted row (``deleted=True``,
    no ``merged_into``) is EXCLUDED — its ref is free again, so the next fetch should offer it
    as unbooked. A ref absorbed into a canonical row by a dedup merge is INCLUDED (via
    ``ext_ref_pairs``, which folds in the merged-away rows' ``external_refs``), so a re-fetch of
    a merged source still resolves to the canonical row's id, never re-offers it as new."""
    out: dict[tuple[str | None, str], str] = {}
    for t in transactions.all():
        if t.get("deleted") and not t.get("merged_into"):
            continue
        for pair in ext_ref_pairs(t):
            if pair[0] == source:
                out[pair] = t["id"]
    return out


def _resolve_emitter(source: str | None, ref: str | None) -> str | None:
    """Dispatch an emitter lookup to the store that actually holds the snapshot: email keeps
    its own module (unchanged), every other source routes to the connector snapshot service
    (RFC connectors-as-data-sources §C, last paragraph)."""
    from backend.services import connector_snapshots, email_snapshots

    if source == "email":
        return email_snapshots.emitter_for(source, ref)
    return connector_snapshots.emitter_for(source, ref)


def _stamp_ref_emitters(rows: list[dict]) -> None:
    """Resolve and stamp ``emitter`` on every authoritative provenance entry BEFORE the store
    write — so it lands in the row's payload and every later reader (the match engine above all)
    gets it for free instead of re-querying per pair.

    Replaces the row's list with fresh entry dicts rather than mutating what it holds:
    ``create_transaction`` builds its row as ``{**data, ...}``, a SHALLOW copy, so the list and
    its dicts are the CALLER's — stamping in place would write the emitter back into the
    argument a caller passed and may still be holding.

    Server-resolved, never caller-supplied: see ``_resolve_emitter``. An entry that already
    carries one is left alone, so a merge that absorbed refs keeps what was resolved at each
    ref's own creation time.

    Best-effort — an unresolved emitter is a real, expected state that the veto reads as "may be
    the same emitter" and refuses to loosen on. Failing the write instead would trade a
    conservative match decision for a lost transaction."""
    try:
        for row in rows:
            entries = row.get("external_refs")
            if not entries:
                continue
            stamped = []
            for entry in entries:
                if isinstance(entry, dict) and not entry.get("emitter"):
                    emitter = _resolve_emitter(entry.get("source"), entry.get("ref"))
                    if emitter:
                        entry = {**entry, "emitter": emitter}
                stamped.append(entry)
            row["external_refs"] = stamped
    except Exception:
        _log.warning("emitter stamp failed (rows book unstamped)", exc_info=True)


def _promote_pairs(pairs: set[tuple[str | None, str]]) -> None:
    """Promote every ``(source, ref)`` pair to ``referenced`` in whichever snapshot store
    actually holds it — email keeps its own module (unchanged), every other source routes to
    the connector snapshot service. Best-effort — never fails or rolls back the write."""
    if not pairs:
        return
    try:
        from backend.services import connector_snapshots, email_snapshots

        for source, ref in pairs:
            if source == "email":
                email_snapshots.promote_for_email(source, ref)
            else:
                connector_snapshots.promote(source, ref)
    except Exception:
        _log.warning("snapshot promote failed", exc_info=True)


def _promote_source_snapshots(stored: dict) -> None:
    """Promote hook (was ``_promote_email_snapshot``, EM1-only): a booked row carrying any
    ``{"source", "ref"}`` provenance entry marks each matching stashed snapshot as referenced,
    so it survives its store's unreferenced-row prune. Dispatches per pair (``_promote_pairs``)
    — email keeps its own module, every other source routes to the connector snapshot service
    (RFC connectors-as-data-sources §C, last paragraph). Best-effort — never fails or rolls
    back the write."""
    _promote_pairs(ext_ref_pairs(stored))


def bulk_create_transactions(
    data_list: list[dict],
    batch_id: str | None = None,
    force: bool = False,
    *,
    dedup_by_external_ref: bool = False,
    meta_out: dict | None = None,
    force_transfer_rate: bool = False,
    resurrect_refs: set[tuple[str | None, str]] | None = None,
    labels_as_given: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Normalise and persist multiple transactions in a single file write.

    Compared to calling create_transaction() in a loop this reads base_currency()
    and load_fx_cache() once and does a single read-write cycle on transactions.json
    regardless of batch size. If batch_id is given it is stamped on every row.

    Returns (created, skipped). By DEFAULT every row is always inserted and ``skipped``
    is always ``[]`` — callers handle duplicate detection and user confirmation
    separately; this is unchanged for every existing caller (CSV/QIF import, the AI
    bulk_add_transactions tool, ...).

    ``meta_out``, when given, is FILLED with ``payee_minted_txn_ids`` — the created rows whose
    payee this call invented from the raw description (same contract as ``create_transaction``;
    see ``resolve_transaction_row`` for why a mint is worth reporting).

    ``dedup_by_external_ref`` (opt-in, keyword-only) is the ingestion orchestrator's L0
    dedup FLOOR (CONC-C1/FAIL-C3): when True, a row any of whose ``external_refs`` pairs
    already exists in the ledger (live, non-deleted — mirrors ``TransactionStore.has_external_ref``)
    or repeats one already kept earlier in this same batch is skipped instead of inserted,
    and returned in ``skipped``. It is a belt-and-suspenders backstop for the case where
    the caller's own pre-commit dedup snapshot went stale (e.g. a gap in the per-source
    sync mutex) — never the primary dedup mechanism.

    A row whose ref identity the user DELETED (``rejected_identities``) is skipped on every
    path, ``force`` included — a forced write skips duplicate detection, not the user's delete
    — and returned in ``skipped`` with ``_previously_deleted: True``. ``resurrect_refs`` names
    the ``(source, ref)`` pairs an explicit ``create`` decision re-books; those rows pass and
    their records are forgotten in the write transaction (ADR
    2026-09-02-a-deleted-identity-is-remembered).

    ``labels_as_given`` books each row's category exactly as sent — no Automation, no payee
    default — for a migration copying another app's labels, where a blank category is a
    decision (YNAB's Ready to Assign inflow), not a gap to fill.
    """
    from backend.core.audit_context import get_audit_scope as _get_audit_scope
    from backend.services import categories as categories_service
    from backend.services import payee_registry
    from backend.services import rejected_identities as _rejected
    from backend.services.automations.transaction_events import (
        active_transaction_transforms,
        record_committed_matches,
    )

    _scope = _get_audit_scope()
    skipped: list[dict] = []
    if _scope is not None and _scope.surface == "automation":
        data_list = [{**data, "source": "automation"} for data in data_list]
    indexed: list[tuple[int, dict]] = list(enumerate(data_list))
    if dedup_by_external_ref:
        seen: set[tuple[str | None, str]] = set()
        kept: list[tuple[int, dict]] = []
        for _i, data in indexed:
            pairs = ext_ref_pairs(data)
            if pairs and any(key in seen or transactions.has_external_ref(*key) for key in pairs):
                skipped.append({**data, "_batch_index": _i})
                continue
            seen |= pairs
            kept.append((_i, data))
        indexed = kept

    # One immutable resolution snapshot for the whole batch, shared with every call to the
    # same resolver preview uses. Loading these before duplicate matching also lets account
    # names and FX-sensitive match keys use the identical account/rate view as the write.
    base = base_currency()
    rates = _fx_load_rates()
    automation_set = active_transaction_transforms()
    category_rows = categories_service.all_category_rows()
    account_rows = accounts.all()

    indexed = [(_i, _resolve_account_refs(d, rows=account_rows)) for _i, d in indexed]

    resurrect_refs = resurrect_refs or set()
    resurrecting: list[dict] = []
    if indexed:
        rejections = _rejected.rejections_for([d for _, d in indexed])
        if rejections:
            kept_after_reject: list[tuple[int, dict]] = []
            for pos, (_i, resolved) in enumerate(indexed):
                record = rejections.get(pos)
                if record is None:
                    kept_after_reject.append((_i, resolved))
                    continue
                if ext_ref_pairs(resolved) & resurrect_refs:
                    resurrecting.append(resolved)
                    kept_after_reject.append((_i, resolved))
                    continue
                skipped.append(
                    {
                        **resolved,
                        "_batch_index": _i,
                        "_previously_deleted": True,
                        "_duplicate_of": record.get("txn_id"),
                        "_duplicate_reason": _rejected.previously_deleted_error(record).message,
                    }
                )
            indexed = kept_after_reject

    # Semantic duplicate refusal, batch edition (mirrors create_transaction's guard — bulk does
    # NOT route through it). A refused row is REPORTED in ``skipped`` carrying its reason rather
    # than raised on: one duplicate in a 300-row statement must not cost the caller the other
    # 299. That makes this the load-bearing half for the import path (decisions.py commits
    # through here), where no reader of the review queue is guaranteed.
    #
    # Account refs resolve FIRST for the same reason create_transaction resolves before its
    # floor: matching keys on account ids, so a name checked before resolution hashes to a
    # different row and slips the guard. Intra-batch pairs are deliberately NOT compared against
    # each other — a statement legitimately lists two identical charges, which is exactly what
    # the engine's own same-feed veto already encodes.
    if not force and indexed:
        from datetime import timedelta as _td

        from backend.services import matching as _matching

        # PER-BATCH, never per row. ``check()`` left to its own devices re-reads the WHOLE ledger
        # for every row (``known_transfer_routes`` calls ``transactions.all()``), re-queries the
        # date window, and reloads the FX cache — O(rows x ledger), which made a 4-row import
        # visibly hang on a real book. All three are hoisted and passed in; ``candidates=`` exists
        # for exactly this. Semantics are unchanged: the per-row slice below reproduces
        # ``_narrow_candidates`` (+/- FETCH_WINDOW_DAYS, merged rows dropped).
        _dates = [x for x in (_matching.parse_date(d.get("date")) for _, d in indexed) if x]
        _pool: list[dict] = []
        if _dates:
            _w = _td(days=_matching.FETCH_WINDOW_DAYS)
            _pool = [
                t
                for t in transactions.query_txns(
                    date_from=(min(_dates) - _w).isoformat(),
                    date_to=(max(_dates) + _w).isoformat(),
                )
                if not t.get("merged_into")
            ]
        _routes = _matching.known_transfer_routes()
        kept_rows: list[tuple[int, dict]] = []
        for _i, resolved in indexed:
            d = _matching.parse_date(resolved.get("date"))
            near = (
                [
                    t
                    for t in _pool
                    if (td := _matching.parse_date(t.get("date"))) is not None
                    and abs((td - d).days) <= _matching.FETCH_WINDOW_DAYS
                ]
                if d is not None
                else []
            )
            dup = _matching.blocking_duplicate(
                _matching.check(
                    resolved,
                    profile=_matching.FULL,
                    candidates=near,
                    rates=rates,
                    known_routes=_routes,
                )
            )
            if dup is not None and not _dedup_decided_different(dup, resolved):
                skipped.append(
                    {
                        **resolved,
                        "_batch_index": _i,
                        "_duplicate_of": dup.id,
                        "_duplicate_reason": _duplicate_error(dup, resolved).message,
                    }
                )
                continue
            kept_rows.append((_i, resolved))
        indexed = kept_rows
    data_list = [d for _, d in indexed]

    rows = []
    # Per-row matched transforms and category provenance, parallel to ``rows``.
    automation_matches_by_row: list[list] = []
    automation_hints: list = []
    # Parallel to `rows`: did THIS row's payee get invented from its raw description?
    minted_row_flags: list[bool] = []
    for data in data_list:
        if _scope is not None and _scope.actor == "agent" and data.get("source") in (None, "ai"):
            data = {**data, "source": "agent"}
        row, resolution_meta = resolve_transaction_row(
            data,
            automations=[] if labels_as_given else automation_set,
            labels_as_given=labels_as_given,
            category_rows=category_rows,
            rates=rates,
            account_rows=account_rows,
            base=base,
            force_transfer_rate=force_transfer_rate,
        )
        if batch_id:
            row["batch_id"] = batch_id
        # Review queue (Accounts redesign): import-sourced rows land in review by default; an
        # explicit needs_review on the row wins. Bulk creates are always genuine creates.
        if data.get("needs_review") is None and default_needs_review(row.get("source")):
            row["needs_review"] = True
        rows.append(row)
        automation_matches_by_row.append(resolution_meta["automation_matches"])
        automation_hints.append(resolution_meta["automation_hint"])
        minted_row_flags.append(bool(resolution_meta["payee_is_new"]))

    balance_before = _capture_automation_balance_snapshot()
    # PLAN 3 TASK 4: bulk_append + the batched audit write below are now ONE transaction.
    _stamp_ref_emitters(rows)
    with locked_unit_of_work(
        transactions_table, txn_audit_table, rejected_identities_table
    ) as conn:
        created = transactions.bulk_append(rows, conn=conn)
        if resurrecting:
            _rejected.forget_matching(resurrecting, conn=conn)
        if meta_out is not None:
            # bulk_append preserves submitted order, so `created` parallels `rows`/`minted_row_flags`
            # (data_list was already narrowed to `kept` before the loop when deduping).
            meta_out["payee_minted_txn_ids"] = [
                stored["id"]
                for stored, minted in zip(created, minted_row_flags)
                if minted and stored.get("payee_id")
            ]
        # Provenance (transaction-provenance RFC): one batched audit write for the whole import.
        from backend.services import audit as audit_service

        automation_applied_by_id: dict[str, dict] = {}
        for stored, hint in zip(created, automation_hints):
            prov = _automation_category_provenance(
                hint, stored.get("category"), category_rows=category_rows
            )
            if prov:
                automation_applied_by_id[stored["id"]] = prov
        audit_service.record_txn_events(
            "create",
            created,
            note=f"batch:{batch_id}" if batch_id else None,
            automation_applied_by_id=automation_applied_by_id or None,
            conn=conn,
        )
    # Payee usage + silent default-category learning + automation match usage accounting —
    # MOVED out of the transaction above (plan 4d task 2 finding; see create_transaction's
    # identical, more detailed note for the full self-deadlock account: AutomationStore/
    # PayeeStore are now DB-backed and each opens its OWN independent transaction, so calling
    # either from inside the still-open transactions/txn_audit transaction self-deadlocks
    # SQLite's single-writer model).
    for stored in created:
        if stored.get("payee_id"):
            payee_registry.note_usage(stored["payee_id"], stored.get("date"))
            payee_registry.learn_category(
                stored["payee_id"], stored.get("category"), stored.get("kind", "expense")
            )
    for stored, matches in zip(created, automation_matches_by_row):
        record_committed_matches(
            matches, transaction_id=stored["id"], context="transaction_pre_commit"
        )
    # Email-snapshot promote — MOVED out of the transaction above (storage plan 4e task 1
    # finding; see create_transaction's identical, more detailed note for the full
    # self-deadlock account: EmailSnapshotStore is now DB-backed and opens its OWN
    # independent transaction, so calling it from inside the still-open
    # transactions/txn_audit transaction self-deadlocks SQLite's single-writer model).
    for stored in created:
        _promote_source_snapshots(stored)
    # Moved out of the transaction above — see create_transaction's identical note.
    _publish_committed_transaction_events("create", created, balance_before=balance_before)
    return created, skipped


# ── Cleared toggle ──────────────────────────────────────────────────────────────


def set_transaction_cleared(txn_id: str, cleared: bool) -> dict | None:
    """Set the cleared flag on one transaction (cleared/reconcile MVP). Returns the
    updated transaction, or None if it doesn't exist. Toggling cleared NEVER changes the
    working balance — only which legs the cleared balance folds. Records ONE "update"
    audit event when the flag actually changes (a no-op toggle stays silent — the trail
    captures state changes, not repeated clicks). Refuses to uncleared the Starting Balance
    transaction — invariant #2 of the Phase 2 relocation: cleared=True always, or
    cleared_account_balance drops and reconcile misfires."""
    existing = transactions.find_by_id(txn_id)
    if _is_opening_txn(existing) and not cleared:
        raise ConflictError(
            "the Starting Balance transaction can't be uncleared — it must stay cleared "
            "for the account's balance to reconcile correctly"
        )
    updated = transactions.set_cleared(txn_id, cleared)
    if updated is None:
        return None
    prior = bool(existing.get("cleared")) if existing else None
    if prior == bool(cleared):
        return updated
    from backend.services import audit as audit_service

    audit_service.record_txn_event(
        "update",
        txn_id,
        source=updated.get("source"),
        before={"cleared": prior},
        after={"cleared": bool(cleared)},
    )
    return updated


# ── Edit (the single per-txn update choke point) ─────────────────────────────────


def resolve_external_refs_patch(txn_id: str, existing: dict, incoming_refs: object) -> list[dict]:
    """Return the append-only, de-duplicated provenance list for an update.

    Append-only is deliberate — provenance is an audit trail and losing it silently would be
    worse than keeping a stale entry. What was NOT deliberate is what that did to a caller
    asking for a removal: it seeded from the stored refs and only added, so a SHORTER list was
    a no-op that still returned a normally-updated row. The owner hit this repairing a
    mis-attached ref (2026-08-05), got "written" back, and only a re-read off disk showed
    nothing had changed.

    A caller asking for a REMOVAL therefore gets its refs back unchanged. That is not detected
    here on purpose: ``update_transaction`` compares every patched field against what actually
    landed, so a dropped ``external_refs`` is reported by the same mechanism that reports any
    other field the write declined — no per-field check to add, and no per-field check to
    forget the next time a field turns out to be append-only.

    Why it matters that the caller HEARS it: the thing silently declined here is a WRONG FACT.
    A ref naming a transaction it does not describe makes ``by_external_ref`` ambiguous for
    that identity — in a reported case, ``email:f98a01fd0157`` sat on both the Wise
    payout it describes and the Meridian Bank funding row it does not. Whether removal
    should be POSSIBLE is a separate open question.
    """
    if not isinstance(incoming_refs, list):
        raise ValidationError("external_refs must be a list of {source, ref} objects")
    appended_refs = list(existing.get("external_refs") or [])
    known_pairs = ext_ref_pairs(existing)
    for entry in incoming_refs:
        if not isinstance(entry, dict):
            raise ValidationError("each external_refs entry must be an object")
        source, ref = entry.get("source"), entry.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError("each external_refs entry requires a non-empty ref")
        if source is not None and not isinstance(source, str):
            raise ValidationError("external_refs source must be a string or null")
        pair = (source, ref)
        if pair in known_pairs:
            # The row already holds this pair — re-asserting it changes nothing, so there is no
            # claim to contest and the ownership check below does not apply. It ran here anyway
            # until 2026-08-27, and `by_external_ref` answers with the FIRST live owner, which
            # need not be this row: on a book where three live rows shared one Apple message-id
            # a statement merge was refused for continuing to hold its own ref
            # (`apply_import_merge` resubmits `existing.external_refs` verbatim), surfacing as
            # an unexplained "1 left out" that came back on every re-import.
            continue
        owner = transactions.by_external_ref(source, ref)
        if owner is not None and owner.get("id") != txn_id:
            # existing_id in context (security review R3): lets a caller like
            # ai.tools.provenance.add_transaction_refs surface duplicate_of/existing the same
            # way DuplicateTransactionError's callers already do, instead of a bare string.
            raise ConflictError(
                f"external reference ({source!r}, {ref!r}) already belongs to "
                f"transaction {owner.get('id')}",
                existing_id=owner.get("id"),
            )
        appended_refs.append({"source": source, "ref": ref})
        known_pairs.add(pair)
    return appended_refs


def _normalized_ref_entries(refs: object) -> list[dict]:
    """Validate + STRIP every ``{source, ref}`` entry (security review, L1) — a caller-
    supplied ``"t1 "`` and ``"t1"`` must be the SAME identity everywhere: the ownership
    lookup, the held-elsewhere refusal, and the stored pair. Validating stripped but storing
    raw let a whitespace variant evade the held-elsewhere guard. Shared by attach and detach."""
    if not isinstance(refs, list) or not refs:
        raise ValidationError("refs must be a non-empty list of {source, ref} objects")
    out = []
    for entry in refs:
        if not isinstance(entry, dict):
            raise ValidationError("each ref entry must be an object")
        source, ref = entry.get("source"), entry.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError("each ref entry requires a non-empty ref")
        if isinstance(source, str):
            source = source.strip()
        out.append({"source": source, "ref": ref.strip()})
    return out


def attach_external_refs(txn_id: str, refs: object, *, reason: str | None = None) -> dict:
    """Link one or more ``{source, ref}`` provenance pairs onto an EXISTING row (ADR
    2026-09-07, CDS-5) — the explicit, narrowly-named write behind the ``add_transaction_refs``
    tool and the strong-duplicate refusal's third exit (``_attach_hint_for``).

    Validation and the "pair belongs to a different live row" refusal are ``resolve_external_
    refs_patch``'s job (shared with ``update_transaction``, so a caller gets the SAME
    ownership-conflict message either way); this wraps it with the two things that call
    specifically needs: reporting which of the requested pairs were newly attached vs already
    held (idempotent), and a purpose-built ``attach_refs`` audit entry rather than a generic
    ``update``.

    Deliberately NOT routed through ``update_transaction``/``create_transaction``'s rebuild —
    that re-runs the rules engine and would silently recategorize an Uncategorized row as a side
    effect of a metadata-only link (a blank ``category`` has no ``category_explicit`` stamp to
    protect it). ``_stamp_ref_emitters``/``_promote_pairs`` are called directly instead, so the
    new pairs get the same emitter-resolution and snapshot-promotion every other booking path
    gets, without the heavier machinery.

    A deleted/merge-tombstoned target, or the Starting Balance transaction, is refused —
    security review M3: an opening txn is a computed-balance anchor
    ``update_transaction`` already refuses to touch generically; attaching an unbooked
    connector ref to it would make the next fetch of that ref an L0 no-op FOREVER (the ref
    now reads as already-booked) while the real deposit/withdrawal it describes never lands,
    silently understating the balance.

    Security review R1 (TOCTOU): the ownership check below (``resolve_external_refs_patch``)
    re-reads the row and re-validates INSIDE ``locked_unit_of_work``'s lock, immediately
    before the write — the pre-lock read/validation above this comment is advisory only (an
    optimization to skip the lock entirely on the common already-fully-attached no-op), never
    the authority a write relies on."""
    existing = transactions.find_by_id(txn_id)
    if existing is None or existing.get("deleted"):
        raise ValidationError(f"transaction {txn_id!r} does not exist or is deleted")
    if _is_opening_txn(existing):
        raise ValidationError(
            f"transaction {txn_id!r} is the Starting Balance transaction and cannot carry "
            "provenance refs"
        )
    normalized = _normalized_ref_entries(refs)
    requested_pairs = _sorted_pairs({(e["source"], e["ref"]) for e in normalized})

    before_pairs_hint = ext_ref_pairs(existing)
    if all(p in before_pairs_hint for p in requested_pairs):
        return {
            "ok": True,
            "transaction_id": txn_id,
            "attached": [],
            "already": [{"source": s, "ref": r} for s, r in requested_pairs],
        }

    attached_pairs: set[tuple[str | None, str]] = set()
    already_pairs: set[tuple[str | None, str]] = set()
    with locked_unit_of_work(transactions_table, txn_audit_table) as conn:
        # R1: re-read the row and re-run the ownership check with the table's lock HELD —
        # another writer could have claimed one of these pairs, or deleted/tombstoned this
        # row, in the gap between the pre-lock checks above and here.
        live = transactions.find_by_id(txn_id)
        if live is None or live.get("deleted"):
            raise ValidationError(f"transaction {txn_id!r} does not exist or is deleted")
        before_pairs = ext_ref_pairs(live)
        already_pairs = {p for p in requested_pairs if p in before_pairs}
        # resolve_external_refs_patch is the single owner of the ownership-conflict check +
        # the append itself — a pair held by a DIFFERENT live row raises ConflictError here,
        # naming that row, exactly as an update's own external_refs patch would.
        patched_refs = resolve_external_refs_patch(txn_id, live, normalized)
        after_pairs = ext_ref_pairs({"external_refs": patched_refs})
        attached_pairs = after_pairs - before_pairs

        if attached_pairs:
            row = {**live, "external_refs": patched_refs}
            _stamp_ref_emitters([row])
            transactions.upsert(row, conn=conn)
            from backend.services import audit as audit_service

            audit_service.record_txn_event(
                "attach_refs",
                txn_id,
                source=live.get("source"),
                after={
                    "attached": [{"source": s, "ref": r} for s, r in _sorted_pairs(attached_pairs)]
                },
                note=reason,
                conn=conn,
            )

    if attached_pairs:
        _promote_pairs(attached_pairs)

    return {
        "ok": True,
        "transaction_id": txn_id,
        "attached": [{"source": s, "ref": r} for s, r in _sorted_pairs(attached_pairs)],
        "already": [{"source": s, "ref": r} for s, r in _sorted_pairs(already_pairs)],
    }


# The 422 refusal below must not echo an attacker-controlled ref verbatim and unbounded —
# security review L4.
_DETACH_ERROR_REF_MAX_ECHO = 64


def detach_external_ref(
    txn_id: str, source: str | None, ref: str, *, reason: str | None = None
) -> dict:
    """Remove exactly one ``{source, ref}`` pair from a row's provenance (ADR 2026-09-07,
    CDS-5) — the ONE deliberate removal path; ``resolve_external_refs_patch``'s own append-only
    contract is untouched (a caller asking THAT function for a shorter list still gets its
    refs back unchanged).

    Records a ``different`` decision in the match-engine's own verdict store, keyed on the
    external identity (``_external_identity(source, ref)``) against this row
    (``canonical_id=txn_id``) — the guard both strong-duplicate refusal sites check
    (``_dedup_decided_different``) before refusing a future re-fetch of the same provider row,
    so detaching a wrong link also teaches the guard not to re-collide on it. Refuses when the
    row does not currently hold the pair — there is no removal to perform or decision to record
    for a pair it never carried.

    Security review L2: the decision is recorded INSIDE the same ``locked_unit_of_work`` as
    the removal + audit event (``MatchDecisionStore.remember`` accepts ``conn=``), so a crash
    or a decision-store failure rolls back the WHOLE detach atomically — never a removal that
    "succeeded" while the guard it exists to arm silently never got recorded."""
    existing = transactions.find_by_id(txn_id)
    if existing is None or existing.get("deleted"):
        raise NotFoundError(f"transaction {txn_id!r} does not exist")
    if not isinstance(ref, str) or not ref.strip():
        raise ValidationError("ref must be a non-empty string")
    ref = ref.strip()
    if isinstance(source, str):
        source = source.strip()
    pair = (source, ref)
    current_refs = list(existing.get("external_refs") or [])
    remaining_refs = [
        e
        for e in current_refs
        if not (isinstance(e, dict) and (e.get("source"), e.get("ref")) == pair)
    ]
    if len(remaining_refs) == len(current_refs):
        shown_ref = ref[:_DETACH_ERROR_REF_MAX_ECHO]
        if len(ref) > _DETACH_ERROR_REF_MAX_ECHO:
            shown_ref += "…"
        raise ValidationError(
            f"transaction {txn_id!r} does not hold external ref ({source!r}, {shown_ref!r})"
        )

    row = {**existing, "external_refs": remaining_refs}
    from backend.db.tables import match_decisions as match_decisions_table
    from backend.services import audit as audit_service
    from backend.services.match_review import decisions

    with locked_unit_of_work(transactions_table, txn_audit_table, match_decisions_table) as conn:
        transactions.upsert(row, conn=conn)
        audit_service.record_txn_event(
            "detach_ref",
            txn_id,
            source=existing.get("source"),
            before={"external_refs": current_refs},
            after={"external_refs": remaining_refs},
            note=reason,
            conn=conn,
        )
        decisions.remember(
            {
                "incoming_id": _external_identity(source, ref),
                "canonical_id": txn_id,
                "verdict": "different",
                "source": "user",
            },
            conn=conn,
        )
    return {"ok": True, "transaction_id": txn_id, "external_refs": remaining_refs}


def booked_row_snapshot(txn_id: str | None, *, prefer_account_id: str | None = None) -> dict | None:
    """The leg ``{date, amount, currency, description}`` for a LIVE booked ledger row, read
    FRESH so a connector's change annotation (CDS-6, ``connector_snapshots``) reflects edits
    the user made since the row was booked, never the stashed source snapshot.

    ``amount`` is SIGNED to match a provider fetch's own convention (``services.aggregation.
    simplefin`` etc.: positive = money in, negative = money out) — NOT the ledger's own
    always-positive ``from_amount``/``to_amount`` magnitude, and deliberately different from
    ``_duplicate_error``'s unsigned facts. ``from_amount`` (money OUT) negates; ``to_amount``
    (money IN) does not.

    ``prefer_account_id`` (security review R2): for a TWO-LEG transfer, which leg to report.
    A ref attached to the DESTINATION leg of a transfer must compare against that leg's own
    amount/currency, never the origin leg's — the caller (``connector_snapshots``) resolves
    the fetched row's own linked account id and passes it here. When it names a leg of this
    row, that leg wins outright; otherwise falls back to the original from-then-to precedence
    (a single-leg expense/income row, or no linked account resolved).

    ``None`` when the row is missing, deleted, or ``txn_id`` itself is falsy (not yet
    booked)."""
    if not txn_id:
        return None
    row = transactions.find_by_id(txn_id)
    if row is None or row.get("deleted"):
        return None
    amount, ccy = None, None
    if (
        prefer_account_id
        and row.get("to_account_id") == prefer_account_id
        and row.get("to_amount") is not None
    ):
        amount, ccy = abs(float(row["to_amount"])), row.get("to_currency")
    elif (
        prefer_account_id
        and row.get("from_account_id") == prefer_account_id
        and row.get("from_amount") is not None
    ):
        amount, ccy = -abs(float(row["from_amount"])), row.get("from_currency")
    elif row.get("from_amount") is not None:
        amount, ccy = -abs(float(row["from_amount"])), row.get("from_currency")
    elif row.get("to_amount") is not None:
        amount, ccy = abs(float(row["to_amount"])), row.get("to_currency")
    return {
        "date": row.get("date"),
        "amount": amount,
        "currency": ccy,
        "description": row.get("description"),
    }


def _fields_the_write_declined(requested: dict, before: dict, after: dict) -> list[dict]:
    """Every patched field whose stored value did not move — the GENERIC form of "a write that
    did not do what it was asked must not report success".

    Field-specific checks do not scale and are the wrong shape: the append-only
    ``external_refs`` no-op cost the owner a repair that reported "written" and changed nothing
    (2026-08-05), and answering it with an ``external_refs``-shaped guard just means the next
    append-only or read-only field ships the same bug unnoticed. This asks one question of
    every field instead: *you asked me to set this — did it change?*

    Compared against the STORED before/after, so field-level transformation is invisible to it
    and no per-field normalisation is needed: a ``category`` given as a label lands as
    ``cat_<hex>``, which differs from what was there, so it reads as applied. Only a field that
    is byte-identical to its pre-write value is reported.

    That deliberately also catches the value-equality case — patching a field to what it
    already held. Both answers are the same sentence the caller needs ("this field is not what
    you just asked for, or it already was"), and distinguishing them would require exactly the
    per-field knowledge this exists to avoid. It is a REPORT, never an exception: a partial
    patch whose other fields landed is a real write, and an idempotent re-send is legitimate.
    """
    declined = []
    for key, wanted in requested.items():
        if key not in after:
            continue
        if after.get(key) != before.get(key):
            continue
        declined.append({"field": key, "requested": wanted, "stored": after.get(key)})
    return declined


def update_transaction(
    txn_id: str, changes: dict, *, preserve_base: bool = False, meta_out: dict | None = None
) -> dict | None:
    """Update one transaction in place by id and return the rebuilt row (or None if it
    doesn't exist). ``changes`` is a partial field map (already filtered of unset fields).

    This is the SINGLE per-txn edit choke point — it re-runs the create logic so legs +
    the base-currency snapshot stay consistent (and the spend-rules engine fires), then
    swaps the rebuilt row back in place under the original id, and records a category
    correction (P2-SP2). Shared by the REST PUT route and the bulk update path so rules /
    corrections / checkpoint capture all fire identically for one-off and bulk edits.

    base_amount is STAMPED ONCE and preserved on edit by default (the pre-edit value rides through
    ``merged`` and create_transaction honors it); it recomputes ONLY when the native leg VALUE
    changes and the edit didn't set a fresh ``base_amount``. ``preserve_base=True`` (merge choke,
    FIX 4 2026-07-16) forces the preserve even across a leg change — a merge that absorbs a
    reference id may restamp the leg to the same value, and this guarantees no reprice regardless
    of how the merge computed its own amount-changed check.

    The Starting Balance transaction takes a NARROW edit path (``_apply_opening_txn_edit``):
    amount and date only, routed through ``set_account_opening`` so the account's cutoff moves
    with it. Editing that row in the register is the supported way to correct an opening — the
    account editor no longer carries those fields (ADR 2026-07-28).

    ``meta_out``, when given, is FILLED with ``declined_fields`` — every patched field whose
    stored value did not move (see ``_fields_the_write_declined``). A caller that does not pass
    it cannot tell a partly-applied patch from a fully-applied one, which is how an
    append-only ``external_refs`` no-op reported success for years."""
    requested_fields = dict(changes)
    existing = transactions.find_by_id(txn_id)
    if existing is None:
        return None
    refuse_automation_approval(txn_id, existing, changes)
    if _is_opening_txn(existing):
        balance_before = _capture_automation_balance_snapshot()
        rebuilt = _apply_opening_txn_edit(existing, changes)
        if rebuilt is not None:
            _publish_committed_transaction_events(
                "update", [rebuilt], balance_before=balance_before
            )
        return rebuilt
    balance_before = _capture_automation_balance_snapshot()
    # An EXPLICIT payee_id change must reference a real entity (None/"" = clear is fine).
    # Validated here — not in the inner create (_skip_payee_resolve) — so a legacy row
    # whose stored payee_id has gone stale can still be edited without tripping the guard.
    if changes.get("payee_id"):
        from backend.services import payee_registry

        payee_registry.require_payee(changes["payee_id"])
    if "original_amount" in changes or "original_currency" in changes:
        next_original_amount = changes.get("original_amount", existing.get("original_amount"))
        next_original_currency = changes.get("original_currency", existing.get("original_currency"))
        if (next_original_amount is None) != (next_original_currency is None):
            raise ValidationError(
                "original_amount and original_currency must both be set or both be cleared"
            )
    if "external_refs" in changes:
        changes = {
            **changes,
            "external_refs": resolve_external_refs_patch(
                txn_id, existing, changes["external_refs"]
            ),
        }
    # Re-run through create logic so legs + base snapshot stay consistent, then overwrite
    # in place (preserving id) — create_transaction appends a normalised copy.
    merged = {**existing, **changes}
    # On the EDIT path a settled category is explicit — whether just chosen in `changes` or
    # already on the stored row — so the rules engine must never re-categorize it (an
    # override_existing rule used to revert every manual recategorization on this rebuild).
    # A blank category still invites rules / the payee default, same as create.
    if merged.get("category"):
        merged["category_explicit"] = True
    # base_amount is STAMPED ONCE and preserved on edit (ADR 2026-07-09, historical-rate lock):
    # `merged` carries the existing value and create_transaction now honors it, so an edit that
    # never changes the native leg (a payee / category / note / cleared change) keeps the
    # historical SGD value byte-identical — no silent re-pricing at today's rate. It recomputes
    # ONLY when the native leg VALUE actually changes (and the edit didn't set a fresh "Value in
    # SGD"). A base_amount in `changes` is the explicit override and flows straight through. The
    # `amount`/`currency` shorthand (if a caller ever uses it) also counts as a leg change.
    _leg_changed = bool({"amount", "currency"} & set(changes)) or any(
        merged.get(k) != existing.get(k)
        for k in ("from_amount", "from_currency", "to_amount", "to_currency")
    )
    _recompute_base = "base_amount" not in changes and _leg_changed and not preserve_base
    if _recompute_base:
        merged.pop("base_amount", None)
        merged.pop("fx_rate", None)
        merged.pop("base_currency", None)
    # Strip external_refs before passing to create_transaction so the idempotency guard cannot
    # match the existing row and return it unchanged (which would silently discard all edits for
    # any bank-imported / recurring / email-logged transaction).  The original provenance list
    # is re-attached below so the dedup keys are never lost.
    original_external_refs = merged.pop("external_refs", None)
    # Strip reconciled_at before the rebuild: make_transaction does not emit this field,
    # so it would be silently dropped from the rebuilt row.  Re-attach below unless the
    # caller's changes explicitly set (or clear) it.
    original_reconciled_at = merged.pop("reconciled_at", None)
    # Suppress the inner create's audit event — this is an UPDATE, not a create; the single
    # "update" event with the before/after diff is emitted below (transaction-provenance RFC).
    # Legs the edit did not touch were already accepted by the write that stored them —
    # possibly under force_transfer_rate (a genuine parity settlement). Re-judging them here
    # would refuse a note/category edit for a shape the store already holds; only a leg this
    # edit actually changes is judged fresh, exactly as on create.
    rebuild_meta: dict = {}
    with _suppress_inner_create_audit():
        rebuilt = create_transaction(
            merged,
            _skip_payee_resolve=True,
            meta_out=rebuild_meta,
            force_transfer_rate=not _leg_changed,
        )
    # PLAN 3 TASK 4: the cleanup below (drop the appended dup, restamp the stable id, restore
    # the row in place) + the update audit event are now ONE transaction. The inner
    # create_transaction call above stays OUTSIDE it — it already committed its own money-core
    # write via its OWN locked_unit_of_work by the time it returns, so this is a SEQUENCE of
    # two independent transactions, never a nested one (locked_unit_of_work's per-table locks
    # are non-reentrant; nesting would self-deadlock).
    with locked_unit_of_work(transactions_table, txn_audit_table) as conn:
        transactions.delete(rebuilt["id"], conn=conn)  # remove the appended dup
        rebuilt["id"] = txn_id
        # Re-attach the original provenance list so the dedup keys are preserved on the stored row.
        if original_external_refs:
            rebuilt["external_refs"] = original_external_refs
        # Re-attach reconciled_at unless the caller's changes explicitly overrode it.
        if "reconciled_at" not in changes and original_reconciled_at is not None:
            rebuilt["reconciled_at"] = original_reconciled_at
        # Faithful preserve: when we did NOT recompute and the caller didn't override, restore the
        # pre-edit base byte-identical. create_transaction stamps base_currency to the CURRENT base —
        # restoring the ORIGINAL matters after a base-currency change (else a stale SGD value would be
        # mislabeled under the new base, and the read-side lock would then wrongly TRUST it).
        if not _recompute_base and "base_amount" not in changes:
            rebuilt["base_amount"] = existing.get("base_amount")
            rebuilt["base_currency"] = existing.get("base_currency")
            # `fx_rate` is NOT restored verbatim (MATH-5): `_leg_changed` above only looked at
            # the PRE-resolution merged dict, so an edit that changes only an account id (moving
            # a leg to a different-currency account) reads as "leg unchanged" even though
            # `_normalise_leg` just re-expressed that leg's amount in the new account's currency.
            # Restoring the stale rate would then disagree with the row's own (freshly resolved)
            # from_amount/to_amount. Only restore it when the resolved leg is byte-identical to
            # what was already stored — otherwise keep the rate `resolve_transaction_row` just
            # derived from the (locked) base_amount and the newly resolved native leg.
            _leg_resolved_unchanged = all(
                rebuilt.get(k) == existing.get(k)
                for k in ("from_amount", "from_currency", "to_amount", "to_currency")
            )
            rebuilt["fx_rate"] = (
                existing.get("fx_rate") if _leg_resolved_unchanged else rebuilt.get("fx_rate")
            )
        transactions.upsert(rebuilt, conn=conn)  # replace original in place
        # Provenance (transaction-provenance RFC): record the edit with a sparse before/after
        # field diff. Skip the no-op case (an edit that changed no meaningful field).
        from backend.services import audit as audit_service

        before_diff, after_diff = audit_service.changed_fields(existing, rebuilt)
        if after_diff is not None:
            audit_service.record_txn_event(
                "update",
                txn_id,
                source=rebuilt.get("source"),
                before=before_diff,
                after=after_diff,
                conn=conn,
            )
    # Automation match usage accounting — MOVED out of the transaction above (plan 4d task 2
    # finding, the identical class of bug the corrections fix just below closed for plan 4a
    # task 2): AutomationStore is now DB-backed and opens its OWN independent transaction
    # (automations is not one of this block's locked_unit_of_work tables) — calling it from
    # inside the still-open transactions/txn_audit transaction self-deadlocks SQLite's
    # single-writer model. Safe to run after commit: `rebuild_meta` was already fully
    # populated by the inner create_transaction call above, and this write was never meant to
    # be atomic with the ledger write (best-effort, exception-guarded).
    from backend.services.automations.transaction_events import record_committed_matches

    record_committed_matches(
        rebuild_meta.get("automation_matches") or [],
        transaction_id=txn_id,
        context="transaction_pre_commit",
    )
    # Snapshot promote — MOVED out of the transaction above (storage plan 4e task 1 finding,
    # the identical class of bug the automation-match fix just above closed): both snapshot
    # services are DB-backed and each opens its OWN independent transaction — calling either
    # from inside the still-open transactions/txn_audit transaction self-deadlocks SQLite's
    # single-writer model. Safe to run after commit: `rebuilt` already holds its final shape
    # by here, and this write was never meant to be atomic with the ledger write (best-effort,
    # exception-guarded inside the helper itself).
    #
    # ONLY the NEWLY APPENDED pairs (RFC connectors-as-data-sources §C, last paragraph) —
    # `external_refs` is append-only (`resolve_external_refs_patch`), so `existing`'s own
    # pairs were already promoted at their own creation time; re-promoting them here would be
    # harmless (promote is idempotent) but pointless work on every unrelated edit that merely
    # re-asserts its own refs.
    if "external_refs" in changes:
        _promote_pairs(ext_ref_pairs(rebuilt) - ext_ref_pairs(existing))
    # Learn-from-corrections (P2-SP2): the single recorder. If this edit changed the category
    # of an expense/income txn, log a correction signal so the detector can later PROPOSE a
    # categorization rule. Best-effort — never blocks the edit. Moved out of the transaction
    # above (plan 4a task 2, own finding): CorrectionStore is now DB-backed and opens its OWN
    # independent transaction (corrections is not one of this block's locked_unit_of_work
    # tables) — calling it from INSIDE the still-open transactions/txn_audit transaction
    # self-deadlocks SQLite's single-writer model (a second, nested BEGIN on the same
    # connection pool blocks on the first, which never commits until the nested call
    # returns). Safe to run after commit: `rebuilt` already holds its final shape by here
    # (every mutation above happened in place on the same dict), and this write was never
    # meant to be atomic with the ledger write anyway (checkpoint-excluded, best-effort).
    from backend.services import corrections as corrections_service

    corrections_service.record_correction(existing, rebuilt)
    # Moved out of the transaction above — see create_transaction's identical note.
    if after_diff is not None:
        _publish_committed_transaction_events("update", [rebuilt], balance_before=balance_before)
    if meta_out is not None:
        meta_out["declined_fields"] = _fields_the_write_declined(
            requested_fields, existing, rebuilt
        )
    return rebuilt


_MERGE_FACTS_MODES = frozenset({"amount_only", "statement", "reference_only"})
# Absolute tolerance for "the merge patch doesn't actually move the money" (FIX 4) — a hair
# below cent-rounding noise, so a draft that restates the SAME settled figure (float round-trip
# aside) never trips a base-amount recompute.
_MERGE_AMOUNT_UNCHANGED_TOL = 0.005


def _merge_leg_unchanged(
    draft_amt: float | None,
    draft_cur: str | None,
    existing_amt: float | None,
    existing_cur: str | None,
) -> bool:
    """True when a merge patch's leg amount/currency doesn't actually move the money — the
    historical-rate lock (ADR 2026-07-09) should survive the merge verbatim in that case,
    instead of the create_transaction rebuild silently repricing it at today's FX cache rate."""
    if draft_amt is None or existing_amt is None:
        return True
    if draft_cur and existing_cur and draft_cur != existing_cur:
        return False
    return abs(float(draft_amt) - float(existing_amt)) <= _MERGE_AMOUNT_UNCHANGED_TOL


def apply_import_merge(existing_id: str, draft: dict, *, facts: str = "amount_only") -> dict:
    """Merge an imported row INTO an existing transaction instead of creating a duplicate.

    Used when the import preview matched a draft to a row the user already has (a manual
    entry, an earlier import, a bank-sync line) and the user chose "merge" over skip/import-new.
    The reconciliation is deliberate (import-dedup-merge design; ``facts`` extends it per RFC
    batch-aware-dedup §Merge semantics — facts vs labels):

    - **The existing row keeps its LABELS, always** — category, note, tags, payee, splits are
      preserved (they are never in ``patch``, so the ``{**existing, **changes}`` rebuild in
      ``update_transaction`` keeps them) — in every ``facts`` mode.
    - **Idempotency, always**: the import's ``{source, ref}`` pair(s) are appended to the
      existing row's ``external_refs`` so a future re-import is an exact L0 no-op, and the row is marked
      ``cleared`` (it appeared on a statement/feed, so it settled).
    - ``facts`` selects which side wins on the MONEY FACTS (date/amount) — never the labels:
        - ``"amount_only"`` (default — pre-RFC behavior, byte-identical): the imported row's
          settled amount wins, but ONLY when both rows are the same single-leg kind
          (expense/income) — a two-leg transfer's legs are never rewritten here (the movement
          is already booked; it is linked + enriched only). The existing row's DATE is never
          touched. Foreign-charge gap-fill: the pre-conversion original amount is copied in
          when the existing row lacks one.
        - ``"statement"`` (the three-state review flow's link action, all bands): everything
          ``"amount_only"`` does, PLUS the existing row's DATE is re-stamped to the imported
          row's — the statement is authoritative on WHEN + HOW MUCH; a kind mismatch (e.g. a
          single-leg statement row merging into an existing TRANSFER via transfer_leg_echo)
          still never touches date or amount, exactly as ``"amount_only"``.
        - ``"reference_only"`` (RFC open question #2 — a later phase's consumer, e.g. a
          non-statement link with no fact authority over the canonical): ONLY the reference +
          ``cleared`` are absorbed — date, amount, and the foreign-charge gap-fill are NEVER
          touched.

    **Historical-rate lock (FIX 4, security review 2026-07-16)**: whenever this merge does NOT
    change the native leg amount/currency — always true for ``"reference_only"``; true for
    ``"amount_only"``/``"statement"`` whenever the draft's settled figure equals the existing
    leg within ``_MERGE_AMOUNT_UNCHANGED_TOL`` — the existing row's stored ``base_amount``/
    ``base_currency``/``fx_rate`` survive byte-identical (via ``update_transaction(...,
    preserve_base=True)``) instead of being silently recomputed at TODAY's FX cache rate by the
    rebuild. A genuinely amount-changing merge still recomputes, exactly as before.

    Returns the rebuilt existing row. Raises ``NotFoundError`` if ``existing_id`` is gone,
    ``ValidationError`` on an unknown ``facts`` mode.
    """
    if facts not in _MERGE_FACTS_MODES:
        raise ValidationError(f"unknown facts mode {facts!r} (use {sorted(_MERGE_FACTS_MODES)})")
    existing = transactions.find_by_id(existing_id)
    if existing is None:
        raise NotFoundError(f"transaction {existing_id!r} not found — can't merge into it")

    patch: dict = {"cleared": True}
    existing_pairs = ext_ref_pairs(existing)
    inherited = [
        {"source": src, "ref": ref}
        for src, ref in ext_ref_pairs(draft)
        if (src, ref) not in existing_pairs
    ]
    if inherited:
        patch["external_refs"] = list(existing.get("external_refs") or []) + inherited

    amount_changed = False
    if facts in ("amount_only", "statement"):
        kind = existing.get("kind")
        if kind == draft.get("kind") and kind in ("expense", "income"):
            if kind == "expense" and draft.get("from_amount") is not None:
                if not _merge_leg_unchanged(
                    draft.get("from_amount"),
                    draft.get("from_currency"),
                    existing.get("from_amount"),
                    existing.get("from_currency"),
                ):
                    amount_changed = True
                patch["from_amount"] = draft["from_amount"]
                patch["from_currency"] = draft.get("from_currency") or existing.get("from_currency")
            elif kind == "income" and draft.get("to_amount") is not None:
                if not _merge_leg_unchanged(
                    draft.get("to_amount"),
                    draft.get("to_currency"),
                    existing.get("to_amount"),
                    existing.get("to_currency"),
                ):
                    amount_changed = True
                patch["to_amount"] = draft["to_amount"]
                patch["to_currency"] = draft.get("to_currency") or existing.get("to_currency")
            if facts == "statement" and draft.get("date"):
                patch["date"] = draft["date"]
        elif facts == "statement" and kind == "transfer":
            # A single-leg statement row merging into a two-leg transfer: the statement is
            # authoritative for the leg on ITS OWN account and silent about the other one, so
            # exactly that leg is re-amounted. Refusing outright left a transfer booked at the
            # pre-fee figure while the statement showed what actually left the account (owner
            # repro 2026-08-09: -294.86 booked, -296.79 settled, and "choose Link to adopt the
            # settled ones" adopted nothing).
            for side in ("from", "to"):
                draft_account = draft.get(f"{side}_account_id")
                draft_amount = draft.get(f"{side}_amount")
                if not draft_account or draft_amount is None:
                    continue
                if draft_account != existing.get(f"{side}_account_id"):
                    continue
                if not _merge_leg_unchanged(
                    draft_amount,
                    draft.get(f"{side}_currency"),
                    existing.get(f"{side}_amount"),
                    existing.get(f"{side}_currency"),
                ):
                    amount_changed = True
                patch[f"{side}_amount"] = draft_amount
                patch[f"{side}_currency"] = draft.get(f"{side}_currency") or existing.get(
                    f"{side}_currency"
                )
                break

        if not existing.get("original_amount") and draft.get("original_amount"):
            patch["original_amount"] = draft["original_amount"]
            patch["original_currency"] = draft.get("original_currency")

    return update_transaction(existing_id, patch, preserve_base=not amount_changed)


def apply_import_transfer_pair_merge(existing_id: str, draft: dict) -> dict:
    """Promote a lone single-leg EXISTING transaction + a lone single-leg IMPORTED draft —
    flagged ``transfer_pair`` by the match engine because they look like the opposite-direction
    legs of ONE real movement (e.g. a wallet debit and the linked bank account's credit, both
    read from the same combined statement) — into ONE genuine two-leg transfer, instead of the
    same-kind-only fact merge ``apply_import_merge`` does for ``near_duplicate``/
    ``transfer_leg_echo`` (where the movement is ALREADY fully booked and linking just marks
    the statement row as covered — nothing to build).

    A ``transfer_pair`` match has NEITHER side recorded as a transfer yet; this is what
    actually creates it — the right direction, the right amount, the right from/to account —
    and replaces the existing row's stale single-leg shape entirely (never leaves an orphaned
    ``to_amount`` sitting on what is now the debit leg, or vice versa).

    Preserved from the existing row, like ``apply_import_merge``: note, tags, payee, and its
    own ``external_refs`` — the draft's are ADDED, never replacing them, so a future re-import
    of either original row still recognizes this transfer. Category is the ONE label this
    call does NOT preserve — it clears to ``None`` rather than carrying over a spending
    category assigned back when the row looked like a real expense/income, since an internal
    transfer between the owner's own accounts is never a spending category (a reported bug:
    "an internal transfer can't be category").

    Requires ``draft.kind``/``existing.kind`` to be exactly one ``expense`` + one ``income`` —
    the same debit/credit shape ``matching.py``'s TRANSFER_PAIR classification already
    verified before ever proposing this pair. Anything else raises rather than silently
    misdirect money."""
    existing = transactions.find_by_id(existing_id)
    if existing is None:
        raise NotFoundError(f"transaction {existing_id!r} not found — can't merge into it")
    existing_kind = existing.get("kind")
    draft_kind = draft.get("kind")
    if {existing_kind, draft_kind} != {"expense", "income"}:
        raise ValidationError(
            "transfer_pair merge needs one expense leg and one income leg, got "
            f"existing={existing_kind!r} draft={draft_kind!r}"
        )
    debit = draft if draft_kind == "expense" else existing
    credit = draft if draft_kind == "income" else existing
    patch: dict = {
        "kind": "transfer",
        "from_account_id": debit.get("from_account_id"),
        "from_amount": debit.get("from_amount"),
        "from_currency": debit.get("from_currency"),
        "to_account_id": credit.get("to_account_id"),
        "to_amount": credit.get("to_amount"),
        "to_currency": credit.get("to_currency"),
        "cleared": True,
        # An internal transfer moves money between the owner's OWN accounts — the existing
        # row's category (assigned back when this looked like a real expense/income) no
        # longer applies once it's recognized as one movement between two of their accounts.
        "category": None,
    }
    existing_pairs = ext_ref_pairs(existing)
    inherited = [
        {"source": src, "ref": ref}
        for src, ref in ext_ref_pairs(draft)
        if (src, ref) not in existing_pairs
    ]
    if inherited:
        patch["external_refs"] = list(existing.get("external_refs") or []) + inherited
    if not existing.get("original_amount") and draft.get("original_amount"):
        patch["original_amount"] = draft["original_amount"]
        patch["original_currency"] = draft.get("original_currency")
    # The statement is authoritative on WHEN + HOW MUCH — same convention as
    # apply_import_merge's "statement" facts mode.
    if draft.get("date"):
        patch["date"] = draft["date"]
    return update_transaction(existing_id, patch, preserve_base=False)


# ── Bulk operations (loop the per-txn choke points; never bypass them) ────────────

# Field names the bulk update path accepts in ``changes``. ``needs_review`` (approve/reject
# the review queue) and ``to_account_id`` (MOVE the rows to another account — re-point the
# account-bearing leg) join the original label-only set.
_BULK_UPDATE_FIELDS = {
    "category",
    "tags",
    "cleared",
    "payee_id",
    "needs_review",
    "to_account_id",
    "description",  # bank/matching string (ADR 2026-07-17)
    "memo",  # personal, display-only
}


def _move_leg_field(kind: str, existing: dict) -> str | None:
    """The account-bearing leg to re-point for a 'move to account' bulk op, or None when the
    move is ambiguous (a transfer has two real legs — skip it). Single-leg kinds are
    unambiguous: expense → from leg, income → to leg, adjustment → whichever leg is set."""
    if kind == "expense":
        return "from_account_id"
    if kind == "income":
        return "to_account_id"
    if kind == "adjustment":
        return "from_account_id" if existing.get("from_account_id") else "to_account_id"
    return None  # transfer — ambiguous, never auto-moved


# Kinds that never carry a category (stripped to None; bulk-update category changes skip them).
# Transfer is NOT here — a transfer's category validates like an expense's (owner decision
# 2026-07-03: docs/adr/2026-07-03-categorized-transfers.md).
_NO_CATEGORY_KINDS = {"adjustment"}
# Kinds that carry no payee. Investment is NOT here: its payee is the broker/platform (ADR
# 2026-08-25-sale-proceeds-are-an-investment-inflow-not-a-transfer), set by the trade paths
# and correctable by hand like any other row's.
_NO_PAYEE_KINDS = {"transfer", "adjustment"}


def bulk_update_transactions(ids: list[str], changes: dict) -> dict:
    """Apply the same label-only ``changes`` to many transactions, looping the EXISTING
    per-txn choke points so the spend-rules engine, correction recorder and turn-checkpoint
    capture all fire per row (never a bulk store write that bypasses them).

    ``changes`` supports:
      - ``category`` (str): SET the category. Validated against the live registry. An
        adjustment row is skipped (the no-category invariant), not failed. A transfer row
        is NOT skipped — it validates and sets like an expense/income row.
      - ``tags`` (list[str]): ADD tags — additive UNION with the row's existing tags
        (never clobbers / drops existing tags).
      - ``cleared`` (bool): SET the cleared flag (via set_transaction_cleared).
      - ``payee_id`` (str | None): LINK each row to the payee entity (must exist —
        validated up front so an unknown id fails the whole batch before any row is
        touched). Pass ``None`` to CLEAR the payee link. A transfer/adjustment row is
        skipped (transfers carry no payee), not failed.
      - ``needs_review`` (bool): SET the review flag — ``False`` Approves (clears review);
        ``True`` re-flags. (Reject = soft-delete, via ``bulk_delete_transactions``.)
      - ``to_account_id`` (str): MOVE each row to this account by re-pointing its
        account-bearing leg (expense → from, income → to, adjustment → its single leg).
        Transfers are skipped (two legs — ambiguous). The target must exist (validated up
        front). A cross-currency move FX-converts the leg into the target's currency.

    Returns a per-id result summary: ``{updated: [ids], not_found: [ids],
    skipped: [{id, reason}], count: int}``. Raises ValidationError on an empty id list,
    an empty/unknown change set, or an unknown category; NotFoundError on an unknown
    payee_id or move target."""
    if not ids:
        raise ValidationError("At least one transaction id is required")
    unknown = set(changes) - _BULK_UPDATE_FIELDS
    if unknown:
        raise ValidationError(
            f"Unsupported bulk change field(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_BULK_UPDATE_FIELDS))}."
        )
    if not changes:
        raise ValidationError("No changes supplied")

    set_category = changes.get("category")
    add_tags = changes.get("tags")
    set_cleared = changes.get("cleared")
    has_payee_change = "payee_id" in changes
    set_payee = changes.get("payee_id")  # None = clear the link
    has_review_change = "needs_review" in changes
    set_review = changes.get("needs_review")
    move_to = changes.get("to_account_id")  # MOVE: re-point the account-bearing leg

    if set_category is not None:
        from backend.services import categories as categories_service

        valid = categories_service._all_ids()
        if set_category and set_category not in valid:
            raise ValidationError(
                f"Unknown category '{set_category}'. Use an existing category "
                "(list_categories) or create it first."
            )
    if add_tags is not None and not isinstance(add_tags, list):
        raise ValidationError("tags must be a list of strings")
    if has_payee_change and set_payee is not None:
        from backend.services import payee_registry

        payee_registry.require_payee(set_payee)
    if move_to is not None:
        # Accepts a name as readily as an id (same resolver as the create choke); an ambiguous
        # name raises there rather than re-pointing a whole batch of legs at a guess.
        from backend.services import accounts as accounts_service

        move_to = accounts_service.resolve_account_ref(move_to) or move_to
        # Fail the whole batch up front on an unknown target (never re-point a leg to nowhere).
        if not accounts.find_by_id(move_to):
            raise NotFoundError(_unknown_account_message("to_account_id", move_to))

    if has_review_change and not set_review and acting_as_automation():
        # Refused before any row is touched — update_transaction would refuse the first flagged
        # row only after earlier rows of this loop had already been written.
        for txn_id in ids:
            existing = transactions.find_by_id(txn_id)
            if existing is not None:
                refuse_automation_approval(txn_id, existing, changes)

    updated: list[str] = []
    not_found: list[str] = []
    skipped: list[dict] = []

    for txn_id in ids:
        existing = transactions.find_by_id(txn_id)
        if existing is None:
            not_found.append(txn_id)
            continue

        per_txn: dict = {}
        if set_category is not None:
            if existing.get("kind") in _NO_CATEGORY_KINDS:
                # The no-category invariant: an adjustment never gets a category.
                skipped.append({"id": txn_id, "reason": "category not allowed on this kind"})
            else:
                per_txn["category"] = set_category
        if add_tags:
            # Additive union — never drop tags the row already carries.
            merged_tags = list(existing.get("tags") or [])
            for tag in add_tags:
                if tag and tag not in merged_tags:
                    merged_tags.append(tag)
            per_txn["tags"] = merged_tags
        if has_payee_change:
            if existing.get("kind") in _NO_PAYEE_KINDS:
                # Transfers/adjustments never carry a payee.
                skipped.append({"id": txn_id, "reason": "payee not allowed on this kind"})
            else:
                per_txn["payee_id"] = set_payee
        if has_review_change:
            per_txn["needs_review"] = bool(set_review)
        if "description" in changes:
            per_txn["description"] = changes.get("description") or ""
        if "memo" in changes:
            per_txn["memo"] = changes.get("memo") or ""
        if move_to is not None:
            leg = _move_leg_field(existing.get("kind", "expense"), existing)
            if leg is None:
                skipped.append({"id": txn_id, "reason": "move not supported on transfers"})
            elif existing.get(leg) == move_to:
                # already on the target — nothing to re-point on this leg
                pass
            else:
                per_txn[leg] = move_to

        touched = False
        if per_txn:
            update_transaction(txn_id, per_txn)
            touched = True
        if set_cleared is not None:
            set_transaction_cleared(txn_id, bool(set_cleared))
            touched = True
        if touched:
            updated.append(txn_id)
        elif txn_id not in (s["id"] for s in skipped):
            skipped.append({"id": txn_id, "reason": "no applicable change"})

    return {
        "updated": updated,
        "not_found": not_found,
        "skipped": skipped,
        "count": len(updated),
    }


def approve_transactions(ids: list[str]) -> dict:
    """APPROVE: clear ``needs_review`` on each id — the one NAMED approve operation, behind the
    ``approve_transactions`` agent tool and an import batch's "mark reviewed". (The register's own
    PUT / bulk edit still carries the flag as a plain field; ``update_transaction`` is where an
    Automation is refused, whichever door it comes through.)

    Every id is judged before any row is written, so a refusal under an Automation (see
    ``review_approval``) leaves the whole batch untouched. Already-reviewed and deleted rows are
    reported, not errors — approving twice is harmless. Returns ``{approved: [ids],
    already_reviewed: [ids], not_found: [ids], count: int}``. Raises ``ValidationError`` on an
    empty id list and ``ReviewApprovalRefusedError`` under an Automation."""
    if not ids:
        raise ValidationError("At least one transaction id is required")
    flagged: list[str] = []
    already_reviewed: list[str] = []
    not_found: list[str] = []
    for txn_id in dict.fromkeys(ids):
        row = transactions.find_by_id(txn_id)
        if row is None or row.get("deleted"):
            not_found.append(txn_id)
        elif not row.get("needs_review"):
            already_reviewed.append(txn_id)
        else:
            refuse_automation_approval(txn_id, row, {"needs_review": False})
            flagged.append(txn_id)
    approved: list[str] = []
    for txn_id in flagged:
        if update_transaction(txn_id, {"needs_review": False}) is None:
            not_found.append(txn_id)
        else:
            approved.append(txn_id)
    return {
        "approved": approved,
        "already_reviewed": already_reviewed,
        "not_found": not_found,
        "count": len(approved),
    }


def mark_batch_reviewed(batch_id: str) -> dict:
    """Approve every still-flagged, non-deleted transaction booked in this import batch via
    ``approve_transactions``. A merged/linked row was never tagged with this ``batch_id`` (only a
    newly CREATED row carries it) and an already-reviewed row is filtered out up front, so this
    is always safe to re-run — it only ever narrows, never re-flags. Raises ``NotFoundError`` on
    an unknown batch id. Returns ``{updated: int}``."""
    if import_batches.find_by_id(batch_id) is None:
        raise NotFoundError(f"Import batch {batch_id!r} not found")
    txns = transactions.query_by_batch(batch_id, include_deleted=False)
    ids = [t["id"] for t in txns if t.get("needs_review")]
    if not ids:
        return {"updated": 0}
    return {"updated": approve_transactions(ids)["count"]}


def bulk_delete_transactions(
    ids: list[str], *, conn: Connection | None = None, reject_identities: bool = True
) -> dict:
    """Soft-delete many transactions by id, looping the per-txn soft-delete (so each write
    is checkpoint-captured). Returns ``{deleted: [ids], not_found: [ids], count: int,
    rejected: int}``. Raises ValidationError on an empty id list (no accidental whole-ledger
    wipe).

    A delete is a decision about the EVENT, not just the row: the deleted rows' ref identities
    are remembered (``rejected_identities``, in this same transaction) so a re-delivery of the
    same source event is refused at the create chokes rather than re-booked. ``reject_identities
    =False`` is for a caller undoing its OWN booking (a trade undo reversing a linked cash leg)
    — reversing is not rejecting. Batch rollback never reaches this choke at all.

    Refuses OUTRIGHT (ConflictError) — before deleting anything — when ANY requested id is
    a Starting Balance transaction (ledger-tracking-start Phase 2): change the account's
    opening balance instead. Pre-scanned so the refusal is atomic — no partial delete of
    the rest of the batch.

    ``conn``, when given, composes this write into a caller's own open transaction (Task
    4, storage plan 4b — e.g. a lot dispose_undo reversing its linked cash leg alongside
    the lot restore). See ``_txn_unit_of_work``'s docstring for the caller's lock-set
    obligation. ``conn=None`` (the default, every pre-Task-4 call site) is
    byte-identical."""
    if not ids:
        raise ValidationError("At least one transaction id is required")
    protected = [tid for tid in ids if _is_opening_txn(transactions.find_by_id(tid, conn=conn))]
    if protected:
        raise ConflictError(
            "the Starting Balance transaction can't be deleted — change the account's "
            f"opening balance instead: {', '.join(protected)}"
        )
    deleted: list[str] = []
    not_found: list[str] = []
    deleted_rows: list[dict] = []
    balance_before = _capture_automation_balance_snapshot()
    # PLAN 3 TASK 4: the whole soft-delete sweep + the batched "delete" audit write are now
    # ONE transaction — a crash partway through used to leave some rows deleted with no audit
    # trail at all (audit was batched at the END); now it rolls back to zero rows touched.
    # STORAGE PLAN 4B TASK 4: `_txn_unit_of_work` composes into a caller's conn when given.
    # `composed` is captured BEFORE that rebinding — see _publish_transaction_events_after_commit.
    composed = conn is not None
    with _txn_unit_of_work(conn) as conn:
        for txn_id in ids:
            row = transactions.soft_delete(txn_id, conn=conn)
            if row is None:
                not_found.append(txn_id)
            else:
                deleted.append(txn_id)
                deleted_rows.append(row)
        # Provenance (transaction-provenance RFC): one batched "delete" audit write.
        from backend.services import audit as audit_service

        audit_service.record_txn_events("delete", deleted_rows, conn=conn)
        rejected: list[dict] = []
        if reject_identities and deleted_rows:
            from backend.services import rejected_identities as _rejected

            rejected = _rejected.remember_deleted(deleted_rows, conn=conn)
    # Same composed-conn handling as create_transaction's own publish call above — see
    # _publish_transaction_events_after_commit for why the composed case must defer.
    _publish_transaction_events_after_commit(
        composed, "delete", deleted_rows, balance_before=balance_before
    )
    return {
        "deleted": deleted,
        "not_found": not_found,
        "count": len(deleted),
        "rejected": len(rejected),
    }


# ── Merge primitive (dedup / match engine) ───────────────────────────────────────
# RFC transaction-dedup-engine §merge primitive. Folds an ABSORBED transaction into a
# CANONICAL one: the canonical survives and inherits the absorbed source key(s); the absorbed
# is tombstoned (``merged_into`` set + ``deleted`` so every existing balance/net-worth fold
# excludes it for free) yet fully recoverable via the merge journal. Reversible byte-for-byte.


def _debit_credit_legs(rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Across the given single-/two-leg rows, pick the debit leg (account money LEFT) and the
    credit leg (account money ENTERED) for synthesising a transfer. A ``become_transfer`` merge
    of a Meridian Bank debit (expense) + a QuickPay credit (income) yields from=Meridian Bank, to=QuickPay."""
    debit = credit = None
    for r in rows:
        if r.get("from_account_id") and r.get("from_amount") is not None and debit is None:
            debit = r
        if r.get("to_account_id") and r.get("to_amount") is not None and credit is None:
            credit = r
    return debit, credit


def _legs_compatible(debit: dict, credit: dict) -> bool:
    """The debit and credit legs of a synthesised transfer must be the SAME movement: their base
    amounts within a small tolerance (5% covers FX capture-vs-settle drift, plus a cent floor)."""
    da = txn_base_amount(debit)
    cr = txn_base_amount(credit)
    return abs(da - cr) <= max(0.01, 0.05 * max(abs(da), abs(cr)))


def assert_merge_allowed(canonical: dict | None, absorbed: dict | None) -> None:
    """Refuse a merge involving an account's Starting Balance row, from EITHER side.

    As the ABSORBED row it would be tombstoned AND hand its ``("opening", <account id>)``
    provenance pair to the canonical — so ``opening_amount``, which finds the opening by
    exactly that pair, would then read a FOREIGN row's leg as the account's opening balance.
    As the CANONICAL it survives but swallows the other row's provenance (a re-import of that
    real statement line becomes a no-op at L0) and its own leg amounts can be rewritten
    underneath it by the settled-amount adoption in ``_maybe_adopt_settled_amounts``, or its
    single leg rebuilt into a two-leg transfer by ``become_transfer`` — the same silent
    corruption of the same number, reached from the other end. Which row the dedup engine
    names canonical is not the user's choice, so a one-sided guard would be bypassable by
    argument order.

    Part of ``assert_mergeable``, the full pre-write refusal set.
    """
    protected = [
        row["id"] for row in (canonical, absorbed) if _is_opening_txn(row) and row is not None
    ]
    if protected:
        raise ConflictError(
            "the Starting Balance transaction can't be merged — change the account's "
            f"opening balance instead: {', '.join(protected)}"
        )


def _transfer_legs(canonical: dict, absorbed: dict) -> tuple[dict, dict]:
    """The (debit, credit) legs a ``become_transfer`` merge rebuilds the canonical from —
    raising ValidationError when the pair can't form one plausible, non-looping transfer."""
    debit, credit = _debit_credit_legs([canonical, absorbed])
    if debit is None or credit is None or debit is credit:
        # Not a clean pair: need ONE row with only a from-leg (debit) and ONE with only a
        # to-leg (credit). ``debit is credit`` happens when a row is already a two-leg transfer
        # (it satisfies both) — refusing prevents silently rebuilding a transfer from its own
        # legs while ignoring the absorbed row.
        raise ValidationError(
            "become_transfer requires one row with only an outgoing (debit) leg and one with "
            "only an incoming (credit) leg"
        )
    # Pairing sanity (defense-in-depth on the direct merge path): the two legs must be the
    # same movement, so their base amounts must be within tolerance. Refuse reshaping two
    # unrelated rows of different value into a transfer.
    if not _legs_compatible(debit, credit):
        raise ValidationError(
            "become_transfer requires the two legs to have a matching amount (not a plausible pair)"
        )
    # F23(b) — self-transfer guard: debit.from == credit.to is a nonsensical loop.
    if debit.get("from_account_id") == credit.get("to_account_id"):
        raise ValidationError(
            "become_transfer: debit and credit are on the same account — "
            "merging would create a self-transfer"
        )
    return debit, credit


def assert_mergeable(
    canonical: dict,
    absorbed: dict,
    *,
    become_transfer: bool = False,
    override: bool = False,
) -> None:
    """Every refusal ``merge_transactions`` can raise for an existing pair, with no write.

    The ONE definition: the merge choke calls it, and so does any wrapper that WRITES before
    reaching the choke (``match_review.merge_transactions_preserving_fields`` patches the
    canonical first) — a check the wrapper skipped would refuse the merge after the canonical
    had already taken the absorbed row's memo/tags/category/payee.

    Raises ValidationError on an identical/deleted/already-merged row, an F23(a) engine
    invariant breach (unless ``override``) or an unbuildable ``become_transfer``;
    ConflictError when either row is a Starting Balance (never bypassable)."""
    if canonical.get("id") == absorbed.get("id"):
        raise ValidationError("Cannot merge a transaction into itself")
    for label, row in (("canonical", canonical), ("absorbed", absorbed)):
        if row.get("deleted"):
            raise ValidationError(f"The {label} transaction is deleted")
        if row.get("merged_into"):
            raise ValidationError(f"The {label} transaction is already merged")

    # ABOVE the override branch on purpose: ``override`` exists to force past the matching
    # engine's same-event/same-feed heuristics, not to license destroying an opening balance —
    # and the delete guard this mirrors has no bypass either.
    assert_merge_allowed(canonical, absorbed)

    # F23(a) — engine invariant re-checks (LOCAL import breaks ledger→matching→ledger cycle).
    if not override:
        from backend.services import matching as _matching  # local import — see module docstring

        if _matching._same_source_event_veto(canonical, absorbed):
            raise ValidationError(
                "cannot merge: both rows carry distinct authoritative ids from the same source "
                "namespace — they are two real events (override=True to force)"
            )
        if _matching._same_feed(canonical, absorbed):
            raise ValidationError(
                "cannot merge: both rows are from the same import batch — "
                "the feed already de-duplicated its own statement (override=True to force)"
            )
    if become_transfer:
        _transfer_legs(canonical, absorbed)


def merge_transactions(
    canonical_id: str,
    absorbed_id: str,
    *,
    reason: str = "",
    become_transfer: bool = False,
    override: bool = False,
) -> dict:
    """Fold ``absorbed_id`` into ``canonical_id`` and return the updated canonical row.

    Steps (each reversible by ``unmerge_transactions``):
      1. Validate both exist, then ``assert_mergeable``: distinct, neither already
         deleted/merged, neither a Starting Balance (not bypassable by override), the engine's
         invariants don't flag the pair (unless override=True), and a ``become_transfer`` pair
         forms one plausible transfer. All before any write.
      2. Snapshot both rows + the expected POST-merge canonical into the merge journal
         (byte-exact undo; unmerge refuses if the live row was edited/deleted).
      3. If ``become_transfer``: rebuild the canonical as a two-leg transfer spanning both
         accounts, keeping the EARLIER date (fixes the Meridian Bank settlement-timing gap).
         Refuse when debit.from_account_id == credit.to_account_id (self-transfer).
         Clear splits and payee_id on the result (a transfer carries neither).
         Note: cleared/reconciled_at inheritance is deliberately skipped on become_transfer —
         the rebuilt transfer was never itself user-cleared; the user can clear it afterward.
      4. Inherit cleared/reconciled_at from the absorbed row when the canonical lacks them.
      5. Settled-amount adoption: when the absorbed row is feed-grade and the canonical is
         estimate-grade (email or no batch id), adopt the absorbed leg amounts.
      6. Append the absorbed's provenance pairs (primary + its ``external_refs``) into the
         canonical's ``external_refs`` so a re-import of the absorbed source is a no-op at L0.
      7. Tombstone the absorbed (``merged_into`` + ``deleted``); record ``merged_from`` on the
         canonical. Persist through the store write path (fires the checkpoint hook) and emit
         a pair of ``merge`` audit events (canonical + absorbed id).

    ``override``: bypasses the engine invariant checks (F23a) ONLY. FOR TESTS/MAINTENANCE ONLY —
    MUST NOT be exposed via REST or AI tool surfaces.

    Raises ValidationError on a missing/identical/already-merged row, or an invariant breach;
    ConflictError when either row is a Starting Balance."""
    if canonical_id == absorbed_id:
        raise ValidationError("Cannot merge a transaction into itself")
    canonical = transactions.find_by_id(canonical_id)
    absorbed = transactions.find_by_id(absorbed_id)
    if canonical is None:
        raise ValidationError(f"Canonical transaction {canonical_id} not found")
    if absorbed is None:
        raise ValidationError(f"Absorbed transaction {absorbed_id} not found")
    assert_mergeable(canonical, absorbed, become_transfer=become_transfer, override=override)

    import copy

    # 2) journal snapshot (deep-copied so later mutation can't corrupt the undo record).
    # F18: also store the expected POST-merge canonical snapshot so unmerge can detect edits.
    # We compute post_canonical after building the patch below and update the entry then.
    journal_entry: dict = {
        "absorbed_id": absorbed_id,
        "canonical_id": canonical_id,
        "canonical_before": copy.deepcopy(canonical),
        "absorbed_before": copy.deepcopy(absorbed),
        "become_transfer": bool(become_transfer),
        # reason is set AFTER F16(c) amount-adoption enrichment so the journal reflects the
        # same note that appears in the audit event (see below).
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # post_canonical_snapshot will be added after patch is computed
    }

    # 3) collect the absorbed provenance pairs to inherit (the absorbed's primary + its own list),
    #    minus anything the canonical already carries.
    canonical_pairs = ext_ref_pairs(canonical)
    new_external_refs = list(canonical.get("external_refs") or [])
    for src, ref in ext_ref_pairs(absorbed):
        if (src, ref) not in canonical_pairs and not any(
            e.get("source") == src and e.get("ref") == ref for e in new_external_refs
        ):
            new_external_refs.append({"source": src, "ref": ref})

    patch: dict = {
        "external_refs": new_external_refs,
        "merged_from": list(canonical.get("merged_from") or []) + [absorbed_id],
    }
    # Carry the absorbed row's batch id when the canonical has none (RFC 2026-08-02). A merge
    # appended the absorbed provenance pairs but never its batch, so anything a statement
    # LINKED rather than CREATED was invisible to every batch-scoped query — 63 live rows on
    # the owner's book carried a statement ref with no batch id. That is what made a
    # batch-scoped balance check compare a statement's full swing against only its 2 newly
    # created rows and report a phantom mismatch. Never OVERWRITES an existing batch id: the
    # canonical's own write is the one that owns it.
    if not canonical.get("batch_id") and absorbed.get("batch_id"):
        patch["batch_id"] = absorbed["batch_id"]

    if become_transfer:
        debit, credit = _transfer_legs(canonical, absorbed)
        earlier = min(canonical.get("date", ""), absorbed.get("date", ""))
        patch |= {
            "kind": "transfer",
            "category": None,
            # F16(a): clear splits and payee_id — a transfer carries neither
            "splits": None,
            "payee_id": None,
            "from_account_id": debit.get("from_account_id"),
            "from_amount": debit.get("from_amount"),
            "from_currency": debit.get("from_currency"),
            "to_account_id": credit.get("to_account_id"),
            "to_amount": credit.get("to_amount"),
            "to_currency": credit.get("to_currency"),
            "base_amount": None,
            "base_currency": None,
            "fx_rate": None,
            "date": earlier,
        }

    # F16(b) — cleared/reconciled_at inheritance: the absorbed row (bank-confirmed) may carry
    # cleared=True even when the canonical (estimate/email) doesn't. Union the cleared flags so
    # a confirmed charge is never treated as uncleared after a merge. Likewise inherit the
    # absorbed's reconciled_at when the canonical has none.
    if not become_transfer:
        if absorbed.get("cleared") and not canonical.get("cleared"):
            patch["cleared"] = True
        if absorbed.get("reconciled_at") and not canonical.get("reconciled_at"):
            patch["reconciled_at"] = absorbed["reconciled_at"]

    # F16(c) — settled-amount adoption: when the absorbed row is feed-grade (has batch_id
    # OR an authoritative non-email origin source) AND the canonical is estimate-grade (no
    # batch id AND namespace is None or 'email'), AND the matching leg is same account + same
    # currency, adopt the absorbed leg amounts and record the old value in the merge reason.
    if not become_transfer:
        amount_note = _maybe_adopt_settled_amounts(canonical, absorbed, patch)
        if amount_note:
            reason = f"{reason}; {amount_note}" if reason else amount_note

    # Record the journal entry BEFORE writing (so the post-merge snapshot can be computed
    # after applying the patch to a copy of the canonical).
    # Assign reason here so the journal captures the F16(c) enrichment (e.g. "amount updated …").
    journal_entry["reason"] = reason
    post_canonical = {**canonical, **patch}
    journal_entry["post_canonical_snapshot"] = copy.deepcopy(post_canonical)
    # PLAN 3 TASK 4: the journal record, the canonical+absorbed patch, and both merge audit
    # events are now ONE transaction (the "4-store sequence becomes one BEGIN…COMMIT" promise
    # for the merge choke specifically — merge_journal + transactions + txn_audit).
    with locked_unit_of_work(transactions_table, txn_audit_table, merge_journal_table) as conn:
        merge_journal.record(journal_entry, conn=conn)

        # 4+5) Commit the canonical patch AND the absorbed tombstone in ONE atomic write so a crash
        # can never leave the canonical updated while the absorbed row is still live (double-count).
        patched = transactions.bulk_patch(
            {
                canonical_id: patch,
                absorbed_id: {"merged_into": canonical_id, "deleted": True},
            },
            conn=conn,
        )
        updated = patched.get(canonical_id)

        # F23(c) — emit a merge event on the canonical WITH before/after snapshots + absorbed id,
        # and a second merge event on the absorbed id referencing the canonical.
        from backend.services import audit as audit_service

        canonical_before_snap = journal_entry["canonical_before"]
        before_diff, after_diff = audit_service.changed_fields(canonical_before_snap, updated)
        audit_service.record_txn_event(
            "merge",
            canonical_id,
            source=canonical.get("source"),
            before=before_diff,
            after=after_diff,
            note=f"absorbed={absorbed_id}" + (f"; {reason}" if reason else ""),
            conn=conn,
        )
        audit_service.record_txn_event(
            "merge",
            absorbed_id,
            source=absorbed.get("source"),
            note=f"merged_into={canonical_id}" + (f"; {reason}" if reason else ""),
            conn=conn,
        )
    return updated


def _maybe_adopt_settled_amounts(canonical: dict, absorbed: dict, patch: dict) -> str | None:
    """F16(c) — if the absorbed row is feed-grade and the canonical is estimate-grade,
    and the primary leg is same account + same currency, adopt the absorbed amounts on the
    patch and return a human-readable note for the merge reason. Returns None when the
    preconditions aren't met.

    Feed-grade: has batch_id OR an authoritative non-email origin source.
    Estimate-grade: no batch_id AND (origin source is None OR is 'email').
    """
    from backend.core.txn_sources import is_authoritative_source

    def _is_feed_grade(row: dict) -> bool:
        if row.get("batch_id"):
            return True
        src = primary_source(row)
        return is_authoritative_source(src) and src != "email"

    def _is_estimate_grade(row: dict) -> bool:
        if row.get("batch_id"):
            return False
        src = primary_source(row)
        return src is None or src == "email"

    if not _is_feed_grade(absorbed) or not _is_estimate_grade(canonical):
        return None

    # Identify the shared leg (same account_id + same currency) to adopt.
    # For an expense the shared leg is the from-leg; for income the to-leg.
    kind = canonical.get("kind", "expense")
    if kind == "income":
        can_acct = canonical.get("to_account_id")
        can_cur = canonical.get("to_currency")
        ab_acct = absorbed.get("to_account_id")
        ab_cur = absorbed.get("to_currency")
        can_amt = canonical.get("to_amount")
        ab_amt = absorbed.get("to_amount")
        amt_key = "to_amount"
    elif kind == "expense":
        can_acct = canonical.get("from_account_id")
        can_cur = canonical.get("from_currency")
        ab_acct = absorbed.get("from_account_id")
        ab_cur = absorbed.get("from_currency")
        can_amt = canonical.get("from_amount")
        ab_amt = absorbed.get("from_amount")
        amt_key = "from_amount"
    else:
        return None  # transfers/adjustments: skip

    if not (can_acct and can_acct == ab_acct and can_cur and can_cur == ab_cur):
        return None
    if can_amt is None or ab_amt is None or can_amt == ab_amt:
        return None

    patch[amt_key] = ab_amt
    # Recompute base_amount/fx_rate from the adopted amount so API responses show
    # consistent money math (stored base_amount would otherwise reflect the old canonical amount).
    new_base = to_base(ab_amt, can_cur or base_currency(), base_currency())
    patch["base_amount"] = new_base
    patch["base_currency"] = base_currency()
    patch["fx_rate"] = round(new_base / ab_amt, 6) if ab_amt else None
    return f"amount updated from {can_amt} to {ab_amt} (feed-grade absorbed row)"


def unmerge_transactions(absorbed_id: str) -> dict | None:
    """Reverse a merge: restore both rows to their exact pre-merge state from the journal and
    drop the journal entry. Returns the restored canonical row, or None if no merge is recorded
    for ``absorbed_id``. Byte-for-byte inverse of ``merge_transactions``.

    F18: refuses with ValidationError when the live canonical has been edited or deleted since
    the merge (compare against the stored post_canonical_snapshot). Entries without that field
    (pre-hardening merges) proceed as before — no back-compat shim needed, just a missing key
    check. Keeps the byte-exact round-trip test green: an unedited merge is always undoable."""
    entry = merge_journal.pop(absorbed_id)
    if entry is None:
        return None

    canonical_id = entry["canonical_id"]
    canonical_before = entry["canonical_before"]
    absorbed_before = entry["absorbed_before"]

    # F18: if the journal recorded a post-merge snapshot, check the live canonical matches it.
    post_snap = entry.get("post_canonical_snapshot")
    if post_snap is not None:
        live = transactions.find_by_id(canonical_id)
        if live is None or live.get("deleted"):
            # Re-insert the entry we just popped so the state is consistent.
            merge_journal.record(entry)
            raise ValidationError(
                "the merged transaction was edited/deleted since the merge — undo manually"
            )
        # Compare live vs expected post-merge state (ignore volatile fields).
        # reconciled_at: reconcile() stamps it in the background on cleared rows — a
        # reconcile cycle after a merge must not block undo with a spurious "edited" error.
        # cleared: set_transaction_cleared flips it via UI; the merge patch itself may have
        # already set it (captured in the snapshot), so a UI clear-toggle is not an "edit".
        _VOLATILE = {"created_at", "updated_at", "reconciled_at", "cleared"}
        live_cmp = {k: v for k, v in live.items() if k not in _VOLATILE}
        snap_cmp = {k: v for k, v in post_snap.items() if k not in _VOLATILE}
        if live_cmp != snap_cmp:
            merge_journal.record(entry)
            raise ValidationError(
                "the merged transaction was edited/deleted since the merge — undo manually"
            )

    # PLAN 3 TASK 4: the two-row restore + both unmerge audit events are now ONE transaction.
    # The pop/re-record-on-refusal sequence ABOVE stays unwrapped/independent, exactly as
    # before — it is its own explicit compensating write (pop, then explicitly re-record on
    # a validation failure), not part of the "happy path" this choke's promise covers.
    with locked_unit_of_work(transactions_table, txn_audit_table) as conn:
        transactions.upsert(
            canonical_before, conn=conn
        )  # restore canonical legs/external_refs/merged_from
        transactions.upsert(absorbed_before, conn=conn)  # un-tombstone the absorbed row

        # F23(c) — emit symmetric pair of unmerge events.
        from backend.services import audit as audit_service

        audit_service.record_txn_event(
            "unmerge",
            canonical_id,
            source=canonical_before.get("source"),
            note=f"restored_absorbed={absorbed_id}",
            conn=conn,
        )
        audit_service.record_txn_event(
            "unmerge",
            absorbed_id,
            source=absorbed_before.get("source"),
            note=f"unmerged_from={canonical_id}",
            conn=conn,
        )
    return canonical_before


# ── Reconciliation (= an adjustment transaction) ────────────────────────────────


def reconcile(
    account_id: str,
    real_balance: float,
    on_date: str | None = None,
    *,
    against: str = "cleared",
) -> dict | None:
    """Assert the real-world native balance of an account (cleared/reconcile MVP). If it
    differs from the account's CLEARED balance, emit a single `adjustment` transaction
    for the delta (auto-`cleared`), then LOCK the reconcile: mark every cleared leg
    reconciled and stamp ``last_reconciled_at``. Returns the adjustment txn (or None if
    already in sync).

    ``against`` selects the balance the asserted figure is diffed against — ``"cleared"``
    (the YNAB reconcile-to-cleared flow, the default) or ``"working"`` (the legacy
    statement-driven true-up that diffs the full computed balance)."""
    account = accounts.find_by_id(account_id)
    if account is None:
        return None
    on_date = on_date or user_today().isoformat()
    currency = account.get("currency") or base_currency()
    txns = transactions.all()
    cutoff = opening_date(account, txns)
    current = (
        cleared_account_balance(account, txns, cutoff)
        if against == "cleared"
        else account_balance(account, txns, cutoff)
    )
    delta = round(float(real_balance) - current, 2)

    txn = None
    if delta != 0:
        # delta > 0 → balance must increase → money enters (to leg)
        # delta < 0 → balance must decrease → money leaves (from leg)
        # The adjustment is auto-cleared: it's part of the reconciled point-in-time truth.
        data = {
            "date": on_date,
            "kind": "adjustment",
            "description": f"Balance adjustment — {account.get('name', account_id)}",
            "source": "manual",
            "cleared": True,
        }
        if delta > 0:
            data |= {"to_account_id": account_id, "to_amount": delta, "to_currency": currency}
        else:
            data |= {
                "from_account_id": account_id,
                "from_amount": -delta,
                "from_currency": currency,
            }
        txn = create_transaction(data)

    # Lock the agreed point-in-time: every cleared leg (incl. the just-written adjustment)
    # is stamped reconciled, so the reconcile is auditable + freezeable.
    transactions.mark_cleared_reconciled(account_id, on_date)
    accounts.update_by_id(account_id, {"last_reconciled_at": on_date})
    return txn


# ── Starting Balance — the write half of the opening pointer ─────────────────────
# The read half (opening_txn_ids / opening_index / opening_date / opening_amount /
# cutoffs_by_account) lives at the top of this module, beside the balance fold.


def _opening_fallback_date(account_id: str, txns: list[dict] | None = None) -> str:
    """Null-date opening fallback: the account's own earliest transaction date, else today.
    (This schema has no account-created-date field, so that middle tier of the design
    collapses into "else today" — see the Phase 2 migration notes.)"""
    rows = txns if txns is not None else transactions.all()
    dates = [
        t.get("date")
        for t in rows
        if not t.get("deleted")
        and t.get("date")
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]
    return min(dates) if dates else user_today().isoformat()


def _opening_row_to_patch(account: dict, *, conn: Connection | None = None) -> dict | None:
    """The row ``set_account_opening`` will patch — or None, meaning "mint a fresh one".

    The WRITE half's resolve, and the ONE place a dangling ``opening_txn_id`` does not raise.
    Reads stay loud (``_opening_row``): a missing opening row silently un-curtains an account's
    pre-start history, so no read may guess. But a book CAN reach that state — most cheaply via
    ``restore_checkpoint``, which is model-callable — and if the repair surface resolved through
    the loud path too, every repair would raise on the very pointer it exists to fix, leaving a
    hand-edit of accounts.json as the only way out. So the write choke treats a pointer at a
    missing/deleted row as "there is no row": it re-mints and re-points, which is exactly the
    repair. Nothing is lost — the row it pointed at is already gone."""
    oid = account.get("opening_txn_id")
    if not oid:
        return None
    row = transactions.find_by_id(oid, conn=conn)
    if row is None or row.get("deleted"):
        _log.warning(
            "account %s pointed at missing opening transaction %s — re-minting",
            account.get("id"),
            oid,
        )
        return None
    return row


def opening_for_edit(account: dict) -> tuple[float, str | None]:
    """The (amount, date) an edit that supplies only ONE of them carries forward.

    Resolved through the WRITE-side tolerant lookup, not the loud read: an edit that omits a
    field is on its way to ``set_account_opening``, which treats a dangling pointer as "no
    row" and re-mints. Reading loudly here would make the caller raise on exactly the pointer
    the call would have repaired. Reads that REPORT a balance stay loud (``opening_amount`` /
    ``opening_date``); this one only fills in a field the user did not send."""
    row = _opening_row_to_patch(account)
    return _signed_opening(row), (row.get("date") if row else None)


def set_account_opening(
    account_id: str,
    amount: float,
    as_of: str | None = None,
    *,
    conn: Connection | None = None,
) -> dict:
    """The single choke point for an account's Starting Balance. Mints or patches the
    dedicated 'Starting Balance' adjustment transaction and points the account's
    ``opening_txn_id`` at it. Returns the updated ACCOUNT row.

    ``amount``: the signed opening in the account's native currency (asset positive,
    liability raw-negative). A positive amount books a TO-leg (money entering); negative books
    a FROM-leg — mirrors ``reconcile()``'s delta>0→to / delta<0→from convention. ``amount ==
    0`` is a REAL assertion ("my balance was zero when tracking started") and books a zero
    to-leg; it never deletes.

    ``as_of``: the date to book the row at — which IS the tracking start, since the cutoff is
    read back off this same row. ``None`` resolves to the null-date fallback
    (``_opening_fallback_date``). There is no second field to keep in lockstep, so the drift
    this function used to guard against is now unrepresentable.

    Idempotent: a same amount/date call is a no-op past the rebuilt row's own diff (no audit
    noise). A changed amount/date — including a sign flip that crosses zero — patches the SAME
    transaction id in place (mirrors ``update_transaction``'s rebuild-and-swap technique).

    Self-healing: a pointer at a missing row is treated as no row and re-minted
    (``_opening_row_to_patch``) — this is the repair path for a partial checkpoint restore.

    ``conn`` composes the complete row + pointer + audit write into a caller's transaction.
    Without one, this choke opens that same transaction itself."""
    opening_uow = (
        contextlib.nullcontext(conn)
        if conn is not None
        else locked_unit_of_work(transactions_table, accounts_table, txn_audit_table)
    )
    with opening_uow as write_conn:
        account = accounts.find_by_id(account_id, conn=write_conn)
        if account is None:
            raise NotFoundError(f"unknown account {account_id}")

        existing = _opening_row_to_patch(account, conn=write_conn)
        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValidationError("opening balance must be a finite number") from exc
        if not math.isfinite(amount):
            raise ValidationError("opening balance must be a finite number")
        amount = round(amount, 2)
        currency = (account.get("currency") or base_currency()).upper()
        resolved_date = as_of or _opening_fallback_date(account_id)
        data = {
            "date": resolved_date,
            "kind": "adjustment",
            "description": "Starting Balance",
            "cleared": True,
        }
        if amount < 0:
            data |= {
                "from_account_id": account_id,
                "from_amount": -amount,
                "from_currency": currency,
            }
        else:
            data |= {"to_account_id": account_id, "to_amount": amount, "to_currency": currency}

        with _suppress_inner_create_audit():
            rebuilt = create_transaction(data, conn=write_conn, account_rows=[account])

        from backend.services import audit as audit_service

        if existing is not None:
            transactions.delete(rebuilt["id"], conn=write_conn)
            rebuilt["id"] = existing["id"]  # keep the opening txn's id stable across edits
            # The rebuild starts from a fresh dict, not the stored row — so when it was
            # written (and reconciled) must be carried over, or an edit reads as a re-creation.
            for key in ("created_at", "reconciled_at"):
                if existing.get(key):
                    rebuilt[key] = existing[key]
            transactions.upsert(rebuilt, conn=write_conn)

        # The pointer is written BEFORE the audit — readability/convention now, not a
        # partial-failure hedge (fix round 1, plan 3 task 4 — stale comment corrected).
        # A Starting Balance row that no account points at is an ordinary unprotected
        # `adjustment` — deletable, sweepable, mergeable, and double-counted beside the
        # row a retry mints, which is still why the pointer belongs first conceptually.
        # But both writes now ride the SAME shared `locked_unit_of_work` transaction: a
        # raise anywhere in this block rolls back the pointer write too, not just the
        # audit line — there is no more "one committed, the other didn't" ordering
        # trade-off to reason about. `record_txn_event` itself still never raises to this
        # call site (documented NEVER-RAISES contract, unchanged) — a failing audit
        # statement is savepoint-isolated (`TransactionAuditStore.append`) and swallowed
        # internally, so the pointer write commits normally either way.
        if account.get("opening_txn_id") != rebuilt["id"]:
            account = (
                accounts.update_by_id(
                    account_id, {"opening_txn_id": rebuilt["id"]}, conn=write_conn
                )
                or account
            )

        if existing is not None:
            before_diff, after_diff = audit_service.changed_fields(existing, rebuilt)
            if after_diff is not None:
                audit_service.record_txn_event(
                    "update",
                    rebuilt["id"],
                    before=before_diff,
                    after=after_diff,
                    conn=write_conn,
                )
        else:
            audit_service.record_txn_event("create", rebuilt["id"], conn=write_conn)
    return account


def _is_opening_txn(row: dict | None) -> bool:
    """Is this row some account's Starting Balance? Resolved through the ONE marker — the
    ``opening_txn_id`` pointer set — never a source string on the row itself."""
    return bool(row) and row.get("id") in opening_txn_ids()


# Keys a Starting Balance row's edit may actually CHANGE. Every other key may still be PRESENT
# in the patch — the register's edit form re-sends the whole row — but must arrive unchanged;
# a real attempt to repurpose the opening into an ordinary transaction is refused. Currency is
# ignored rather than honored: set_account_opening always books in the account's own currency.
_OPENING_EDITABLE_KEYS = frozenset(
    {
        "date",
        "to_amount",
        "from_amount",
        "to_account_id",
        "from_account_id",
        "to_currency",
        "from_currency",
        "base_amount",
        "cleared",
    }
)


def _blankish(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _opening_field_unchanged(before: object, after: object) -> bool:
    """Empty-ish values compare equal — the edit form sends `[]`/`""` where the stored row
    simply omits the key, and that is not a user changing anything."""
    return before == after or (_blankish(before) and _blankish(after))


def _apply_opening_txn_edit(existing: dict, changes: dict) -> dict | None:
    """The Starting Balance row's edit path — amount and date only, routed through
    ``set_account_opening`` so the row keeps its id (and with it the account's pointer, and so
    the cutoff — which IS this row's date).

    Editing this row IS the supported way to correct an opening (ADR 2026-07-28); the account
    editor no longer carries those fields. Deleting it stays refused."""
    account_id = existing.get("to_account_id") or existing.get("from_account_id")
    to_id = (
        changes["to_account_id"] if "to_account_id" in changes else existing.get("to_account_id")
    )
    from_id = (
        changes["from_account_id"]
        if "from_account_id" in changes
        else existing.get("from_account_id")
    )
    if to_id and from_id:
        if to_id != from_id:
            raise ConflictError(
                "a Starting Balance has a single leg — it can't be turned into a transfer"
            )
        # Same account on both sides is a SIGN FLIP, not a transfer: a negative opening is
        # stored as a from-leg and a positive one as a to-leg, so correcting -1,000.00 to
        # +96.67 necessarily switches legs. The edit form sends the new leg while the row
        # still carries the old, which read as "both set" and refused every negative→positive
        # repair — the exact edit a damaged opening needs (a reported bug, Meridian Bank).
        if "to_account_id" in changes and "from_account_id" not in changes:
            from_id = None
        elif "from_account_id" in changes and "to_account_id" not in changes:
            to_id = None
    if (to_id or from_id) != account_id:
        raise ConflictError(
            "a Starting Balance belongs to its own account — it can't be moved to another one; "
            "edit that account's own Starting Balance instead"
        )
    if "cleared" in changes and not changes["cleared"]:
        raise ConflictError(
            "the Starting Balance transaction can't be uncleared — it must stay cleared "
            "for the account's balance to reconcile correctly"
        )
    # An import merge reaches update_transaction with only {external_refs, cleared} — both
    # otherwise-harmless keys — so without this the opening row could be absorbed as a
    # statement line, inheriting foreign provenance. An opening row carries NO external refs;
    # it is not a thing callers append to.
    if "external_refs" in changes and not _opening_field_unchanged(
        existing.get("external_refs"), changes["external_refs"]
    ):
        raise ConflictError(
            "the Starting Balance transaction can't take imported references — it is not a "
            "statement line and must not be merged into"
        )
    for key, value in changes.items():
        if key in _OPENING_EDITABLE_KEYS:
            continue
        if not _opening_field_unchanged(existing.get(key), value):
            raise ConflictError(
                f"the Starting Balance transaction carries only an amount and a date — "
                f"'{key}' can't be set on it"
            )

    increase = bool(to_id)
    raw = changes.get("to_amount") if increase else changes.get("from_amount")
    if raw is None:
        raw = existing.get("to_amount") if increase else existing.get("from_amount")
    amount = round(abs(float(raw or 0)), 2) * (1 if increase else -1)
    # Zero is a LEGAL opening ("my balance was zero when tracking started") — it patches the
    # row to a zero leg and keeps it. Deleting the row is still refused, elsewhere.
    set_account_opening(account_id, amount, changes.get("date") or existing.get("date"))
    return transactions.find_by_id(existing["id"])


# ── Net worth history ────────────────────────────────────────────────────────────


def account_balances_over_dates(
    accts: list[dict], dates: list[str], txns: list[dict], cutoffs: dict[str, str | None]
) -> dict[str, dict[str, float]]:
    """``{as_of: {account_id: balance}}`` for MANY dates in ONE chronological pass.

    ``account_balance_as_of`` rescans the entire ledger per (account, date). ``net_worth_history``
    calls it for every account at every charted date, so on this book that was 28 accounts x 8
    dates x 2003 rows = 409,212 inner iterations, ~1s — and profiling under concurrency showed
    it is the SHARED single-flight leader several endpoints queue behind, so a 9ms endpoint
    (``/api/net-worth/stats``) reported 8s while waiting for it (live 2026-08-12).

    Same arithmetic, same cutoff rule, same rounding — only the loop order changes: walk the
    dates in order and advance one transaction pointer, keeping a running balance per account.
    O(T log T + T + D x A) instead of O(D x A x T).
    """
    running: dict[str, float] = {a["id"]: 0.0 for a in accts}
    ordered = sorted(
        (t for t in txns if not t.get("deleted") and t.get("date")), key=lambda t: t["date"]
    )
    out: dict[str, dict[str, float]] = {}
    i = 0
    for as_of in sorted(set(dates)):
        while i < len(ordered) and ordered[i]["date"] <= as_of:
            t = ordered[i]
            i += 1
            t_date = t["date"]
            frm = t.get("from_account_id")
            to = t.get("to_account_id")
            if (
                frm in running
                and t.get("from_amount") is not None
                and not is_before_tracking_start(t_date, cutoffs.get(frm))
            ):
                running[frm] -= float(t["from_amount"])
            if (
                to in running
                and t.get("to_amount") is not None
                and not is_before_tracking_start(t_date, cutoffs.get(to))
            ):
                running[to] += float(t["to_amount"])
        out[as_of] = {aid: round(bal, 2) for aid, bal in running.items()}
    return out


def account_balance_as_of(
    account: dict, as_of: str, txns: list[dict], opening_date: str | None
) -> float:
    """Balance fold capped at as_of date (inclusive). Respects the tracking-start cutoff."""
    bal = 0.0
    for t in txns:
        if t.get("deleted"):
            continue
        t_date = t.get("date", "")
        if is_before_tracking_start(t_date, opening_date):
            continue
        if t_date > as_of:
            continue
        if t.get("from_account_id") == account.get("id") and t.get("from_amount") is not None:
            bal -= float(t["from_amount"])
        if t.get("to_account_id") == account.get("id") and t.get("to_amount") is not None:
            bal += float(t["to_amount"])
    return round(bal, 2)


# Instrument asset_class → the display band used by the stacked net-worth chart. Keeps the
# bands aligned with the Overview allocation donut's classes (frontend ASSET_CLASS_*).
_MARKET_BAND = {
    "equity": "equity",
    # bonds-first-class: bonds are their own band (no longer folded into equity). Bond is not
    # lot-backed-reconstructed, so _breakdown_as_of values it on the rows-path like property
    # — bond ETFs chart historically (Yahoo price_history); computed-mark bonds (T-bill/FD/SSB)
    # only have a current mark, so historical points fall back to absent. Current net worth /
    # allocation is always correct.
    "bond": "bond",
    "crypto": "crypto",
    "commodity": "commodity",
    "property": "property",
    # sys-options-data-model: option contracts are their own signed band. Short contracts
    # contribute NEGATIVE mark (a liability), so this band can be net-negative — the first
    # asset-or-liability holding in the NW fold. Values are injected by net_worth_history
    # via _options_mark_base(), not read from the rows path (OptionContractStore is the
    # source, not positions.json).
    "option": "option",
}


_LOT_BACKED_BANDS = frozenset({"equity", "crypto", "commodity"})


def _locked_row_value(row: dict, as_of_prices: dict[str, float], base: str) -> float | None:
    """Base-currency value of a `bucket="locked"` (unvested equity) row at `as_of`, or
    None when the row isn't a priceable market class or has no historical price for that
    date — the same "absent, not zero" convention every other rows-path band already
    uses. Never added to a `classes` band (ADR 2026-07-31-unvested-equity-excluded-from-
    net-worth) — the caller folds this into the separate `unvested_base` total instead."""
    if not _MARKET_BAND.get(row.get("asset_class")):
        return None
    price = as_of_prices.get(row.get("price_source_id"))
    if price is None:
        return None
    return row_base_value(row, base, price=price)


def _breakdown_as_of(
    as_of: str,
    as_of_prices: dict[str, float],
    accts: list[dict],
    rows: list[dict],
    txns: list[dict],
    base: str,
    market_tracked: bool,
    cutoffs: dict[str, str | None],
    *,
    market_overlay: dict[str, float] | None = None,
    option_mark_base: float | None = None,
    manual_assets_base: dict[str, float] | None = None,
    balances: dict[str, float] | None = None,
    rates: dict | None = None,
    market_fx_approximate: bool = False,
) -> dict:
    """Net worth at a historical date, decomposed into positive asset-class bands plus a
    (raw-negative) liabilities figure — the data behind the stacked area chart.

    Accounts classify by `band_of(type)`: `cash` (bank/e-wallet) / `fixed_deposit` /
    `settlement_cash` (an `investment` account's brokerage/exchange cash, margin debit
    included) / `retirement` / `receivable` — `credit`/`loan` accounts are liabilities instead;
    market positions add to `equity` / `crypto` / `commodity` / `property` (only when
    `market_tracked`). See ADR 2026-07-31-liquid-net-worth-bands: `cash` used to also catch
    brokerage/crypto settlement cash, mislabeling it as plain "cash" to callers (the AI, the
    Overview KPI) that never saw the Accounts tab's own (correct) grouping.

    A `bucket="locked"` market position (unvested equity — forfeited if the grantee leaves
    the company) is excluded from every `classes` band and from `net_worth`; its value is
    summed into the separate `unvested_base` field instead (ADR 2026-07-31-unvested-equity-
    excluded-from-net-worth). `bucket="liquid"`/`"contingent"` rows are unaffected.

    ``option_mark_base``: when provided (by net_worth_history for the today point), adds
    the signed options mark into the "option" band. Short ITM contracts are negative;
    OTM or None-underlying contracts contribute 0. Historical dates omit this (None)
    since options have no historical mark feed in v1 — same as computed-mark bonds.

    ``manual_assets_base``: owned-assets valuation banded by
    ``owned_assets_valuation.values_by_band`` (ADR 2026-09-06-assets-tab-faithful-port,
    decision 6) — property/commodity/other_asset, folded into ``classes`` the same way as
    ``market_overlay``. The caller supplies the AS-OF value (``net_worth_history`` re-derives
    it per point with ``as_of=<point date>`` — a manual asset's marks ARE its price series).

    ``market_overlay``: when provided (by ``net_worth_history``), supplies reconstructed
    per-band values for the lot-backed bands (equity/crypto/commodity) from the Phase 1
    primitive — replacing the current-quantity back-projection for those three bands.
    Property is not lot-backed (transfer-funded) so it stays on the ``rows`` path.
    When ``market_overlay`` is None the function falls back to the full ``rows`` loop
    (used when calling ``_breakdown_as_of`` standalone / from tests).

    Returns {classes, liabilities, accounts, net_worth, unvested_base, fx_approximate} —
    `accounts` = cash+fixed_deposit+settlement_cash+retirement+receivable+liabilities (the
    account-only figure, a consistent basis for the headline delta). ``fx_approximate`` (MATH-4)
    flags this POINT
    as revalued at TODAY's FX rather than the date's own rate (no as-of FX history yet) —
    true when any non-base-currency exposure existed on this date: a non-base account with
    a non-zero balance, a non-base market row that contributed value, or (via
    ``market_fx_approximate``) a non-base instrument anywhere in the lot-backed overlay's
    window. Mirrors the definition ``portfolio_history.portfolio_value_series`` already
    uses for its own ``data_quality.fx_approximate``."""
    classes: dict[str, float] = {}
    liabilities = 0.0
    fx_approximate = False
    for a in accts:
        # `balances` (optional): this date's per-account fold, already computed for EVERY
        # charted date in one pass by account_balances_over_dates. Without it each account
        # rescans the whole ledger here — see that function for the measurement.
        raw = (
            balances[a["id"]]
            if balances is not None
            else account_balance_as_of(a, as_of, txns, cutoffs[a["id"]])
        )
        ccy = a.get("currency", base)
        if raw and ccy != base:
            fx_approximate = True
        bal = to_base(raw, ccy, base, rates=rates)
        kind, band = account_kind_and_band(a)
        if kind == "liability":
            liabilities += bal  # stored raw-negative
        else:
            classes[band] = classes.get(band, 0.0) + bal

    accounts_nw = round(sum(classes.values()) + liabilities, 2)

    # Unvested equity (bucket="locked") is NEVER added to a `classes` band — it is
    # forfeited if the grantee leaves, so it isn't owned yet (ADR 2026-07-31-unvested-
    # equity-excluded-from-net-worth). Reported separately via `unvested_base`.
    unvested_base = 0.0
    if market_tracked:
        if market_overlay is not None:
            # Lot-backed bands (equity/crypto/commodity) come from the reconstructed-holdings
            # primitive, which already excludes locked-position lots (portfolio_history
            # .holdings_as_of); property positions remain on the rows path below. The overlay
            # itself carries no per-band currency, so its FX-approximate contribution comes
            # from the primitive's own `market_fx_approximate` (same definition, whole window).
            if market_fx_approximate:
                fx_approximate = True
            for band, val in market_overlay.items():
                if val:
                    classes[band] = classes.get(band, 0.0) + val
            for row in rows:
                if row.get("vesting_state") == "unvested":
                    val = _locked_row_value(row, as_of_prices, base)
                    if val is not None:
                        unvested_base += val
                    continue
                band = _MARKET_BAND.get(row.get("asset_class"))
                if not band or band in _LOT_BACKED_BANDS:
                    continue  # skip unmapped classes and lot-backed bands (property only)
                price = as_of_prices.get(row.get("price_source_id"))
                if price is not None:
                    val = row_base_value(row, base, price=price)
                    classes[band] = classes.get(band, 0.0) + val
                    if val and (row.get("quote_currency") or row.get("currency") or base) != base:
                        fx_approximate = True
        else:
            for row in rows:
                if row.get("vesting_state") == "unvested":
                    val = _locked_row_value(row, as_of_prices, base)
                    if val is not None:
                        unvested_base += val
                    continue
                band = _MARKET_BAND.get(row.get("asset_class"))
                if not band:
                    continue
                # `as_of_prices` (PriceHistoryStore) is keyed by price_source_id, NOT the
                # display `ticker` — those diverge for crypto (_row_ticker in positions.py
                # renders a CoinGecko id like "bitcoin" as "BTC" for display only). Looking
                # up by `ticker` here silently dropped every crypto row from this band.
                price = as_of_prices.get(row.get("price_source_id"))
                if price is None:
                    continue
                val = row_base_value(row, base, price=price)
                classes[band] = classes.get(band, 0.0) + val
                if val and (row.get("quote_currency") or row.get("currency") or base) != base:
                    fx_approximate = True

    # Inject signed option mark (current-only — no historical feed in v1). The option band
    # can be net-negative (short ITM contracts are a liability). A None option_mark_base
    # (historical dates, unknown underlying) leaves the option band absent, same as a
    # computed-mark bond with no historical price.
    if option_mark_base is not None and option_mark_base != 0.0:
        classes["option"] = round(classes.get("option", 0.0) + option_mark_base, 2)

    # Owned assets (manually marked — property/commodity/other_asset), banded by the
    # caller's `manual_assets_base` (today: current marks; net_worth_history: the newest
    # mark on/before THIS point's date). Same precedent as option_mark_base above — folded
    # into `classes`/`net_worth`, not into the accounts-only `accounts_nw` figure.
    if manual_assets_base:
        for band, val in manual_assets_base.items():
            classes[band] = classes.get(band, 0.0) + val

    classes = {k: round(v, 2) for k, v in classes.items()}
    return {
        "classes": classes,
        "liabilities": round(liabilities, 2),
        "accounts": accounts_nw,
        "net_worth": round(sum(classes.values()) + liabilities, 2),
        "unvested_base": round(unvested_base, 2),
        "fx_approximate": fx_approximate,
    }


def _compute_options_mark_base(base: str) -> float:
    """Sum the signed option marks (base currency) for the current snapshot.

    Best-effort — a missing OptionContractStore or any error returns 0 so the NW
    fold is never blocked by a broken option contract."""
    try:
        from backend.services import options as options_service

        s = options_service.summary(base=base)
        return float(s.get("total_mark_base") or 0.0)
    except Exception:
        _log.debug("options mark unavailable for NW fold (non-fatal)", exc_info=True)
        return 0.0


@read_cache.cached(
    lambda all_txns=None: None,
    when=lambda all_txns=None: all_txns is None,
)
def net_worth_history(all_txns: list[dict] | None = None) -> list[dict]:
    """Reconstruct monthly net worth, decomposed by asset class — the stacked-area data.

    Returns [{date, classes, liabilities, accounts, net_worth, unvested_base, market_tracked,
    fx_approximate}] from the earliest relevant date to today (inclusive). Each entry uses the
    last day of the month, except the current month which uses today so the chart stays live.
    `unvested_base` is unvested equity (bucket="locked") — excluded from `net_worth`/`classes`,
    reported alongside (ADR 2026-07-31-unvested-equity-excluded-from-net-worth).

    `classes` maps asset-class band → value (cash [bank-only]/fixed_deposit/settlement_cash
    [an `investment` account's brokerage/exchange cash, margin debit included]/retirement/
    receivable always; equity/crypto/commodity/property once price collection has begun
    on/before that date — see ADR 2026-07-31-liquid-net-worth-bands). `market_tracked` flags
    the point as including market value. `accounts` =
    cash+fixed_deposit+settlement_cash+retirement+receivable+liabilities (the account-only
    figure, before any market value — a consistent basis for the headline delta). `net_worth`
    = Σ class bands + liabilities.

    Lot-backed market positions (equity/crypto/commodity) use the Phase 1 primitive
    (portfolio_history.portfolio_value_series) — reconstructed historical quantities rather
    than current-quantity back-projection. Property positions remain on the legacy rows path
    (property is transfer-funded, not lot-backed; the primitive excludes it by design).
    FX uses current cached rates (as before), NOT the date's own rate — a limitation shared
    with `returns.py`/`daily_total_value.py`. `fx_approximate` (MATH-4) surfaces that: true
    when the point had any non-base-currency exposure (a foreign account balance or a
    non-base market holding), so a caller can flag a step in the chart driven by FX drift
    rather than an actual saved/invested move (e.g. a cross-currency transfer between the
    user's own accounts). See ADR 0019 / RFC portfolio-performance-returns.
    """
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import owned_assets_valuation
    from backend.services import portfolio_history as ph
    from backend.services import positions as positions_service

    # `all_txns` (optional): an already-loaded, deleted-filtered ledger — the shared-read
    # seam (same convention as cashflow's probes); a caller composing several ledger folds
    # (forecast scorecard) passes one load through all of them.
    txns = (
        all_txns
        if all_txns is not None
        else [t for t in transactions.all() if not t.get("deleted")]
    )
    accts = accounts.query_accounts()
    base = base_currency()
    from backend.services.fx_cache import load_fx_cache

    rates = load_fx_cache()  # ONE FX load threaded through every per-date breakdown below

    cutoffs = cutoffs_by_account(accts, txns)
    # Every tracking start IS an opening transaction's date, so txn dates already cover them.
    candidate_dates: list[str] = [t["date"] for t in txns if t.get("date")]

    if not candidate_dates and not accts:
        return []

    today = user_today()
    # rows is kept for the property band (transfer-funded, not lot-backed).
    rows = positions_service.hydrated()
    # ONE store read for every charted point's owned-asset fold below — `values_by_band`
    # calls `OwnedAssetsStore().all()` on every invocation, which used to mean a fresh read
    # per charted month (account_balances_over_dates' own measurement is the same story: a
    # per-date recompute that should be a per-date REFOLD of one shared load).
    owned_asset_rows = OwnedAssetsStore().all()
    market_since = PriceHistoryStore().earliest_date()

    # No transactions yet but accounts exist → single current-snapshot point.
    if not candidate_dates:
        as_of = today.isoformat()
        as_of_prices = PriceHistoryStore().as_of_maps([as_of])[as_of]
        tracked = bool(market_since and as_of >= market_since)
        overlay: dict[str, float] | None = None
        overlay_fx_approximate = False
        if tracked:
            # inception=as_of: no account/txn reads needed in the primitive (accounts-only book).
            pv_single = ph.portfolio_value_series(as_of, as_of, "monthly", inception=as_of)
            for pt in pv_single["series"]:
                if pt["date"] == as_of:
                    overlay = pt["classes"]
                    break
            overlay_fx_approximate = bool(
                (pv_single.get("data_quality") or {}).get("fx_approximate")
            )
        opt_mark = _compute_options_mark_base(base)
        bd = _breakdown_as_of(
            as_of,
            as_of_prices,
            accts,
            rows,
            txns,
            base,
            tracked,
            cutoffs,
            market_overlay=overlay,
            option_mark_base=opt_mark,
            manual_assets_base=owned_assets_valuation.values_by_band_from_rows(
                owned_asset_rows, base, as_of=as_of, rates=rates
            ),
            rates=rates,
            market_fx_approximate=overlay_fx_approximate,
        )
        return [{"date": as_of, "market_tracked": tracked, **bd}]

    oldest = date.fromisoformat(min(candidate_dates)[:10])
    oldest_iso = oldest.isoformat()
    y, m = oldest.year, oldest.month

    as_ofs: list[str] = []
    while date(y, m, 1) <= today:
        if y == today.year and m == today.month:
            as_ofs.append(today.isoformat())
        else:
            as_ofs.append(date(y, m, monthrange(y, m)[1]).isoformat())
        m += 1
        if m > 12:
            y, m = y + 1, 1

    # ONE ledger pass for every charted date's account balances — the same shape as the
    # portfolio primitive below, and for the same reason. Previously each _breakdown_as_of
    # rescanned the whole ledger per account per date (409,212 inner iterations on this book,
    # ~1s), and profiling under concurrency showed net_worth_history is the shared
    # single-flight leader that /api/net-worth/stats and /api/forecast-scorecard queue behind.
    balances_by_date = account_balances_over_dates(accts, as_ofs, txns, cutoffs)

    # ONE primitive call for all month-end dates — O(events + months), not O(months × events).
    # Pass inception explicitly: the primitive already knows the oldest account/txn date (oldest_iso)
    # and we avoid an unnecessary AccountStore/TransactionStore read inside portfolio_value_series.
    pv = ph.portfolio_value_series(oldest_iso, today.isoformat(), "monthly", inception=oldest_iso)
    overlays: dict[str, dict[str, float]] = {pt["date"]: pt["classes"] for pt in pv["series"]}
    # Whole-window flag from the primitive (it has no per-date currency breakdown to give a
    # tighter one) — same convention `returns.py`/`daily_total_value.py` already propagate.
    overlay_fx_approximate = bool((pv.get("data_quality") or {}).get("fx_approximate"))

    as_of_prices = PriceHistoryStore().as_of_maps(as_ofs)  # one history read for all months
    today_iso = today.isoformat()
    # Compute current option mark once; injected only into the "today" data point (no historical
    # option feed in v1 — same limitation as computed-mark bonds on the rows path).
    options_mark = _compute_options_mark_base(base)
    result = []
    for as_of in as_ofs:
        tracked = bool(market_since and as_of >= market_since)
        overlay = overlays.get(as_of) if tracked else None
        if tracked and overlay is None:
            _log.warning(
                "net_worth_history: no market overlay for %s; falling back to rows path", as_of
            )
        opt_mark = options_mark if as_of == today_iso else None
        bd = _breakdown_as_of(
            as_of,
            as_of_prices[as_of],
            accts,
            rows,
            txns,
            base,
            tracked,
            cutoffs,
            market_overlay=overlay,
            option_mark_base=opt_mark,
            manual_assets_base=owned_assets_valuation.values_by_band_from_rows(
                owned_asset_rows, base, as_of=as_of, rates=rates
            ),
            balances=balances_by_date[as_of],
            rates=rates,
            market_fx_approximate=overlay_fx_approximate if tracked else False,
        )
        result.append({"date": as_of, "market_tracked": tracked, **bd})
    return _from_first_tracked_point(result)


def _is_untracked(point: dict) -> bool:
    """A point where nothing was held yet: every band, every liability and unvested equity at
    zero. It is the month-end before the first account's Starting Balance (a statement imported
    with rows older than its opening), not a net worth of zero."""
    return (
        not point.get("liabilities")
        and not point.get("unvested_base")
        and not any((point.get("classes") or {}).values())
    )


def _from_first_tracked_point(points: list[dict]) -> list[dict]:
    """The series from the first point that holds anything — a chart that opens at S$0 on the
    month-end before the account existed draws a climb that never happened (persona QA round 7).
    The last point always stays, so a book with nothing in it still answers."""
    start = 0
    while start < len(points) - 1 and _is_untracked(points[start]):
        start += 1
    return points[start:]


def net_worth_on(as_of: str) -> dict:
    """Net worth on ONE past date, by exactly the rebuild ``net_worth_history`` uses for a
    point — so a date figure always agrees with the history at that date.

    The history charts month-ends only, so a weekly report had no point for its Sunday and
    could only show "Not shown" (live, 2026-10-07). Returns ``{date, net_worth, classes,
    liabilities, accounts, unvested_base, market_tracked, fx_approximate}``. A future date is
    refused; today is ``net_worth()``'s job.
    """
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import owned_assets_valuation
    from backend.services import portfolio_history as ph
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    try:
        day = date.fromisoformat(as_of)
    except (TypeError, ValueError):
        raise ValidationError("as_of must be an ISO date (YYYY-MM-DD)", as_of=as_of) from None
    if day >= user_today():
        raise ValidationError("as_of must be a past date — use net worth for today", as_of=as_of)
    txns = [t for t in transactions.all() if not t.get("deleted")]
    accts = accounts.query_accounts()
    base, rates = base_currency(), load_fx_cache()
    cutoffs = cutoffs_by_account(accts, txns)
    oldest = min((t["date"][:10] for t in txns if t.get("date")), default=as_of)
    market_since = PriceHistoryStore().earliest_date()
    tracked = bool(market_since and as_of >= market_since)
    overlay, fx_approx = None, False
    if tracked:
        pv = ph.portfolio_value_series(as_of, as_of, "monthly", inception=oldest)
        overlay = next((pt["classes"] for pt in pv["series"] if pt["date"] == as_of), None)
        fx_approx = bool((pv.get("data_quality") or {}).get("fx_approximate"))
    bd = _breakdown_as_of(
        as_of,
        PriceHistoryStore().as_of_maps([as_of])[as_of],
        accts,
        positions_service.hydrated(),
        txns,
        base,
        tracked,
        cutoffs,
        market_overlay=overlay,
        option_mark_base=None,  # no historical option feed, as in the history
        manual_assets_base=owned_assets_valuation.values_by_band_from_rows(
            OwnedAssetsStore().all(), base, as_of=as_of, rates=rates
        ),
        balances=account_balances_over_dates(accts, [as_of], txns, cutoffs)[as_of],
        rates=rates,
        market_fx_approximate=fx_approx,
    )
    return {"date": as_of, "market_tracked": tracked, "base_currency": base, **bd}


# ── Uncategorized summary ────────────────────────────────────────────────────


def is_uncategorized(t: dict) -> bool:
    """True for a top-level expense/income transaction with no category (None/blank) — the
    "Uncategorized" bucket ``uncategorized_summary`` and the Plan tab's derived Uncategorized row both
    surface. A split transaction carries the SPLIT sentinel top-level (never blank), so it's
    excluded even if an individual split line is blank — single source of truth for both
    ``uncategorized_summary`` and the ``GET /api/transactions?uncategorized=true`` filter."""
    return t.get("kind") in ("expense", "income") and not t.get("category")


def uncategorized_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = 5,
) -> dict:
    """Count + total (base) of top-level unsorted expense/income transactions.

    "Uncategorized" means the top-level category is None/blank — not yet assigned a meaningful
    label. Split transactions carry the SPLIT sentinel top-level so they are excluded even
    if individual split lines are blank.

    Args:
        date_from: ISO date string lower bound (inclusive). None = no lower bound.
        date_to:   ISO date string upper bound (inclusive). None = no lower bound.
        limit:     Number of most-recent rows to include in the ``recent`` peek (None or
                   0 = omit the list entirely).

    Returns:
        {count, total_base, base_currency, recent: [{id, date, note, amount_base}]}
        Note: ``total_base`` is the sum of absolute values (unsigned magnitudes via
        ``txn_base_amount``), so expense and income rows both add to the total.
    """
    from backend.services.fx_cache import load_fx_cache

    base = base_currency()
    rates = load_fx_cache()

    # Filter in-memory: unsorted = no category (None/blank) + expense/income kind only.
    # Split txns carry the SPLIT sentinel top-level so they're excluded by the falsy check.
    all_txns = transactions.query_txns(
        date_from=date_from,
        date_to=date_to,
    )
    rows = [t for t in all_txns if is_uncategorized(t)]

    count = len(rows)
    total = round(sum(txn_base_amount(t, base, rates) for t in rows), 2)

    recent_items: list[dict] = []
    if limit:
        # Most-recent first (dates are ISO so lexicographic sort suffices).
        sorted_rows = sorted(rows, key=lambda t: t.get("date", ""), reverse=True)
        for t in sorted_rows[:limit]:
            recent_items.append(
                {
                    "id": t.get("id"),
                    "date": t.get("date"),
                    "description": t.get("description", ""),
                    "amount_base": txn_base_amount(t, base, rates),
                }
            )

    return {
        "count": count,
        "total_base": total,
        "base_currency": base,
        "recent": recent_items,
    }


# ── Per-transaction lookup + query (service twins of api/ledger endpoints) ────────


def resolve_category_refs(refs: str | list[str] | None) -> str | list[str] | None:
    """Resolve each caller-supplied category ref (id OR label, per
    services.categories.resolve_category_ref) to its stored id for a read filter.
    An unresolved ref falls through unchanged — a read never fails on a bad filter,
    it just matches no rows. Accepts a single ref or a repeated-query-param list,
    mirroring query_txns; list twin of get_transactions' single-ref resolve."""
    if not refs:
        return refs
    from backend.services import categories as categories_service

    rows = categories_service.all_category_rows()
    if isinstance(refs, str):
        return categories_service.resolve_category_ref(refs, rows=rows) or refs
    return [categories_service.resolve_category_ref(ref, rows=rows) or ref for ref in refs]


def _resolve_bulk_delete_filters(
    *,
    date_from: str | None,
    date_to: str | None,
    account_id: str | None,
    kind: str | None,
    category: str | None,
    batch_id: str | None,
) -> dict:
    """The guard + category resolution both the preview and the delete must run.

    Shared so a preview cannot report a set the delete would not touch: same
    at-least-one-filter refusal, same unresolvable-category refusal, same resolved id.
    """
    if not any([date_from, date_to, account_id, kind, category, batch_id]):
        raise MissingFilterError("At least one filter is required")
    if category:
        from backend.services import categories as categories_service

        resolved = categories_service.resolve_category_ref(category)
        if resolved is None:
            raise ValidationError(f"Unknown or ambiguous category: {category!r}")
        category = resolved
    return {
        "date_from": date_from,
        "date_to": date_to,
        "account_id": account_id,
        "kind": kind,
        "category": category,
        "batch_id": batch_id,
    }


def preview_delete_transactions_where(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    batch_id: str | None = None,
) -> list[dict]:
    """Rows ``delete_transactions_where`` WOULD delete with these filters. Pure read.

    Exists because a filtered delete is the one ledger write whose SELECTION the user
    cannot verify from the call itself: the arguments name a filter, not rows. A count and
    a sample are the only way to see what is about to happen, so this runs the identical
    guard, resolution and predicate the delete runs (never a parallel query).
    """
    return transactions.select_where(
        **_resolve_bulk_delete_filters(
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            kind=kind,
            category=category,
            batch_id=batch_id,
        ),
        protected_ids=opening_txn_ids(),
    )


def delete_transactions_where(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    batch_id: str | None = None,
) -> int:
    """Bulk soft-delete transactions matching filters — service twin of the AI
    ``delete_transactions_where`` tool and ``DELETE /api/transactions``. THE choke point:
    every caller (REST, AI tool, and any future scheduled task/script) must route through
    here rather than ``transactions.bulk_delete_where`` directly, so neither guard below can
    be bypassed by a caller that forgets its own pre-check.

    Guards:
    - At least one filter is required — called with none, this would match (and wipe)
      every non-deleted transaction in the ledger. Raises ``MissingFilterError`` (HTTP 400).
    - A category ref (label or id) is resolved to its stored opaque id like every other
      category filter. Unlike a READ filter (``get_transactions``/``resolve_category_refs``,
      which fall through unchanged on a bad ref so a query just returns no rows), this is
      DESTRUCTIVE: an unresolvable ref REFUSES loudly (``ValidationError``) rather than
      silently deleting nothing — a label that no longer matches any category must not look
      like a successful no-op delete (CATF2).
    - Every account's Starting Balance row is spared: the store's sweep predicate skips any id
      in ``protected_ids``, which THIS choke supplies. The store owns no account knowledge, so
      a caller that bypassed this function would lose the exemption — pinned by the AST test
      asserting ``bulk_delete_where`` has exactly one caller.
    - The deleted rows' ref identities are remembered (``rejected_identities``) in the same
      transaction, as in ``bulk_delete_transactions`` — EXCEPT under a ``batch_id`` filter,
      which is a rollback of an import, and rolling back is not rejecting."""
    filters = _resolve_bulk_delete_filters(
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        kind=kind,
        category=category,
        batch_id=batch_id,
    )
    protected = opening_txn_ids()
    selected = transactions.select_where(**filters, protected_ids=protected)
    balance_before = _capture_automation_balance_snapshot()
    with locked_unit_of_work(transactions_table, rejected_identities_table) as conn:
        deleted = transactions.bulk_delete_where(**filters, protected_ids=protected, conn=conn)
        if deleted and batch_id is None:
            from backend.services import rejected_identities as _rejected

            _rejected.remember_deleted(selected, conn=conn)
    if deleted:
        _publish_committed_transaction_events("delete", selected, balance_before=balance_before)
    return deleted


def get_transaction(txn_id: str) -> dict | None:
    """Fetch a single non-deleted transaction by id, WITH its receipts (cross-channel-
    attachments RFC item 26 — the transaction-detail read; ``get_transactions``' bulk list
    path deliberately does NOT carry this per-row, to avoid an N-row extra query on every
    page). The only two callers are both genuine detail reads: ``GET
    /api/transactions/{txn_id}`` and the AI tool's ``get_transactions(transaction_id=...)``
    branch — both benefit automatically from being enriched here, once."""
    t = transactions.find_by_id(txn_id)
    if not t or t.get("deleted"):
        return None
    from backend.services.receipts import receipts_for_transaction

    t = dict(t)
    t["receipts"] = receipts_for_transaction(txn_id)
    return t


Q_FIELDS = ("any", "payee", "description", "memo", "note", "category")


def txn_matches_q(
    t: dict, needle: str, q_field: str, payee_names: dict[str, str], cat_labels: dict[str, str]
) -> bool:
    """Free-text search — a cross-store join (payee name, category label) the store
    can't do on its own, so it runs here rather than in TransactionStore.query_txns.
    ``any``/``payee`` need the payee's NAME; ``category`` needs the category LABEL
    (falling back to the raw key, same as the client's textcategory match).

    ``description`` vs ``memo`` are genuinely different questions since ADR
    2026-07-17: ``description`` is the bank/matching string dedup, spend rules and payee
    resolution read; ``memo`` is the user's own annotation that none of them read.
    """
    if q_field == "note":
        # The pre-split field's alias (ADR 2026-07-17): `note` was ONE field, so scoping to
        # it means "either of the two fields it became". This is what the register's
        # `note:` search token sends, so it stays span-both.
        return (
            needle in str(t.get("description") or "").lower()
            or needle in str(t.get("memo") or "").lower()
        )
    if q_field in ("description", "memo"):
        # Scoped to the ONE field named. These two used to both fall into the `note` branch
        # above and return identical results, which made the description/memo distinction
        # unexpressible — the entire point of scoping a search after the provenance split.
        # No UI sends either value today (the register uses any/payee/note/category), so
        # honouring them costs nothing and makes "where the BANK wrote X" a different
        # question from "where I wrote X", as the data model already intends.
        return needle in str(t.get(q_field) or "").lower()
    if q_field == "payee":
        return needle in payee_names.get(t.get("payee_id") or "", "").lower()
    if q_field == "category":
        cat = str(t.get("category") or "")
        return needle in cat.lower() or needle in cat_labels.get(cat, "").lower()
    # any: transaction id OR note/category-key (the same note-or-category substring test
    # TransactionStore._matches_text does for its own `q` param) OR payee name OR category
    # label. Including the opaque id lets the register's ordinary search box reopen a row
    # copied from an audit log, URL, or tool result without requiring a separate id syntax.
    if any(
        needle in str(t.get(field) or "").lower()
        for field in ("id", "description", "memo", "category")
    ):
        return True
    if needle in cat_labels.get(str(t.get("category") or ""), "").lower():
        return True
    return needle in payee_names.get(t.get("payee_id") or "", "").lower()


def _apply_q_filter(txns: list[dict], q: str | None, q_field: str) -> list[dict]:
    """The REST route's free-text post-filter, shared with the service twin.

    Runs as a POST-filter rather than being pushed into ``query_txns(q=...)`` on purpose:
    the store's own ``q`` only sees description/memo/category, so pre-filtering with it
    would drop rows before a ``q_field='payee'`` search ever got to check the payee name.
    """
    needle = (q or "").strip().lower()
    if not needle:
        return txns
    from backend.services import labels as labels_service

    qf = q_field if q_field in Q_FIELDS else "any"
    # Same id->label tables the ledger READ attaches per row (services.labels) — built here
    # only for the map(s) this q_field actually needs, so a `q`-less call still pays nothing.
    maps = labels_service.label_maps(
        payees=qf in ("any", "payee"),
        categories=qf in ("any", "category"),
    )
    payee_names = maps.get("payees", {})
    cat_labels = maps.get("categories", {})
    return [t for t in txns if txn_matches_q(t, needle, qf, payee_names, cat_labels)]


def _any_filter_value(value: str | list[str] | None) -> bool:
    """Truthy test that works for both the singular and plural filter shapes."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def get_transactions(
    *,
    month: int | None = None,
    year: int | None = None,
    kind: str | list[str] | None = None,
    category: str | list[str] | None = None,
    account_id: str | None = None,
    account_ids: str | list[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    q: str | None = None,
    q_field: str = "any",
    tag: str | list[str] | None = None,
    payee_id: str | list[str] | None = None,
    needs_review: bool | None = None,
) -> dict:
    """Service-layer transaction query.

    Returns ``{"transactions": [...], "total": int, "has_more": bool}`` when limit is
    set; ``{"transactions": [...]}`` otherwise. Service twin of api/ledger.get_transactions.

    ``kind``/``category``/``tag``/``payee_id``/``account_ids`` accept a single value OR a
    list (OR'd within a field, AND'd across fields) — ``query_txns`` has always supported
    both shapes; this signature just stopped hiding it. Answering "what did I spend across
    my three cards" in ONE call instead of three is the point: three calls each re-sent the
    whole conversation context, which is where the cost actually lived.
    """

    from backend.services.fx_cache import load_fx_cache

    # A caller may pass a category LABEL (an AI prompt teaches "category=food") as readily
    # as an id — resolve it so the filter still matches; an unresolved value falls through
    # unchanged (a read path never fails on a bad filter, it just returns no rows).
    # resolve_category_refs handles the single-ref and list shapes alike.
    category = resolve_category_refs(category)

    txns = transactions.query_txns(
        kind=kind,
        category=category,
        account_id=account_id,
        account_ids=account_ids,
        tag=tag,
        payee_id=payee_id,
        needs_review=needs_review,
    )
    # q is applied here, not inside query_txns — see _apply_q_filter for why.
    txns = _apply_q_filter(txns, q, q_field)

    if date_from or date_to:
        if date_from:
            txns = [t for t in txns if t.get("date", "") >= date_from]
        if date_to:
            txns = [t for t in txns if t.get("date", "") <= date_to]
    else:
        amount_filter = min_amount is not None or max_amount is not None
        search_filter = bool((q or "").strip()) or _any_filter_value(tag)
        skip_date_filter = (
            (
                account_id
                or _any_filter_value(account_ids)
                or limit
                or amount_filter
                or search_filter
                or needs_review is not None
            )
            and month is None
            and year is None
        )
        if not skip_date_filter:
            today = user_today()
            m = month or today.month
            y = year or today.year
            prefix = f"{y}-{m:02d}"
            txns = [t for t in txns if t.get("date", "").startswith(prefix)]

    _base = base_currency()
    _rates = load_fx_cache()
    for t in txns:
        if t.get("kind") not in ("transfer",):
            t["base_amount"] = txn_base_amount(t, _base, _rates)
            t["base_currency"] = _base

    if min_amount is not None or max_amount is not None:

        def _magnitude(t: dict) -> float:
            if account_id:
                if t.get("from_account_id") == account_id and t.get("from_amount") is not None:
                    return abs(float(t["from_amount"]))
                if t.get("to_account_id") == account_id and t.get("to_amount") is not None:
                    return abs(float(t["to_amount"]))
            if t.get("base_amount") is not None:
                return abs(float(t["base_amount"]))
            return abs(float(t.get("from_amount") or t.get("to_amount") or 0))

        if min_amount is not None:
            txns = [t for t in txns if _magnitude(t) >= min_amount]
        if max_amount is not None:
            txns = [t for t in txns if _magnitude(t) <= max_amount]

    txns.sort(
        key=lambda t: (t["date"], abs(float(t.get("from_amount") or t.get("to_amount") or 0))),
        reverse=True,
    )
    if limit:
        total = len(txns)
        start = offset or 0
        page = txns[start : start + limit]
        return {
            "transactions": page,
            "total": total,
            "has_more": start + len(page) < total,
            **_txn_base_totals(txns),
        }
    return {"transactions": txns}


def _txn_base_totals(txns: list[dict]) -> dict:
    """Income/expense base-currency totals over the FULL filtered set — computed
    before pagination so a page slice can never change reported money math."""
    total_in = sum(
        abs(float(t["base_amount"]))
        for t in txns
        if t.get("kind") == "income" and t.get("base_amount") is not None
    )
    total_out = sum(
        abs(float(t["base_amount"]))
        for t in txns
        if t.get("kind") == "expense" and t.get("base_amount") is not None
    )

    return {
        "total_income_base": round(total_in, 2),
        "total_expenses_base": round(total_out, 2),
        "net_base": round(total_in - total_out, 2),
    }


# ── Amount direction/magnitude math (the register's rich amount filter) ──────────
# All monetary math lives here per CLAUDE.md — api/ledger.get_transactions calls
# these, it never re-derives them.


def txn_magnitude(t: dict, account_id: str | None) -> float:
    """The amount relevant to a query. When an account is specified, that's the leg
    in that account's native currency (so ">350M VND in Mekong Bank" compares
    VND-to-VND, never base currency). Otherwise fall back to base_amount, then
    native leg magnitude."""
    if account_id:
        if t.get("from_account_id") == account_id and t.get("from_amount") is not None:
            return abs(float(t["from_amount"]))
        if t.get("to_account_id") == account_id and t.get("to_amount") is not None:
            return abs(float(t["to_amount"]))
    if t.get("base_amount") is not None:
        return abs(float(t["base_amount"]))
    return abs(float(t.get("from_amount") or t.get("to_amount") or 0))


def txn_direction(t: dict, account_id: str | None) -> str:
    """'in' or 'out' relative to the scoped account_id (the register's sidebar
    single VIEW scope — never an OR'd multi-account filter). The leg touching the
    scoped account decides direction; with no account scoped, income is 'in' and
    everything else is 'out' (mirrors the register's client-side txAmountCtx)."""
    if account_id:
        if t.get("from_account_id") == account_id:
            return "out"
        if t.get("to_account_id") == account_id:
            return "in"
    return "in" if t.get("kind") == "income" else "out"


def parse_amount_constraint(raw: str) -> tuple[str, str, float] | None:
    """Parse one ``op:dir:value`` amount constraint (e.g. ``>=:any:100``) out of the
    register's comma-separated ``amount`` query param (each constraint AND'd — this
    is how the register builds a range: an "Amount >= X" token + an "Amount <= Y"
    token). Returns None for a malformed entry (silently ignored — never 500s on a
    bad query param)."""
    parts = raw.strip().split(":")
    if len(parts) != 3:
        return None
    op, direction, value_s = parts
    if op not in ("=", "<=", ">=") or direction not in ("any", "in", "out"):
        return None
    try:
        value = float(value_s)
    except ValueError:
        return None
    return op, direction, value


def matches_amount_constraint(
    t: dict, account_id: str | None, op: str, direction: str, value: float
) -> bool:
    """Whether ``t`` satisfies one parsed amount constraint (see
    ``parse_amount_constraint``), direction-aware relative to ``account_id``."""
    if direction != "any" and txn_direction(t, account_id) != direction:
        return False
    mag = txn_magnitude(t, account_id)
    if op == "=":
        return abs(mag - value) < 0.005
    if op == "<=":
        return mag <= value + 1e-9
    return mag >= value - 1e-9


def get_ledger_summary(month: int | None = None, year: int | None = None) -> dict:
    """Service-layer ledger P&L summary for a month.

    Service twin of api/ledger.get_ledger_summary.
    """

    from backend.services.fx_cache import load_fx_cache

    today = user_today()
    m = month or today.month
    y = year or today.year
    prefix = f"{y}-{m:02d}"

    txns = [
        t
        for t in transactions.all()
        if not t.get("deleted") and t.get("date", "").startswith(prefix)
    ]

    by_kind: dict[str, dict] = {
        k: {"in": 0.0, "out": 0.0, "net": 0.0, "count": 0} for k in ALL_KINDS
    }
    total_in = total_out = 0.0

    _base = base_currency()
    _rates = load_fx_cache()

    for t in txns:
        kind = t.get("kind", "expense")
        amt = txn_base_amount(t, _base, _rates)
        bucket = by_kind.setdefault(kind, {"in": 0.0, "out": 0.0, "net": 0.0, "count": 0})
        bucket["count"] += 1
        if kind == "income":
            bucket["in"] += amt
            total_in += amt
        elif kind == "expense":
            bucket["out"] += amt
            total_out += amt

    for bucket in by_kind.values():
        bucket["in"] = round(bucket["in"], 2)
        bucket["out"] = round(bucket["out"], 2)
        bucket["net"] = round(bucket["in"] - bucket["out"], 2)

    return {
        "month": m,
        "year": y,
        "by_kind": by_kind,
        "total_in": round(total_in, 2),
        "total_out": round(total_out, 2),
        "net": round(total_in - total_out, 2),
        "transaction_count": len(txns),
        "base_currency": base_currency(),
    }


def _assert_account_not_net_worth_linked(account_id: str, acc: dict) -> None:
    """Refuse deleting an account still tied into net worth through a reference the DB does
    NOT enforce with a real FK: ``owned_assets.loan_account_id`` (a mortgage/loan's linked
    liability account — ``docs/DATA_MODEL.md``/``owned_assets_store``'s own docstring call
    this "FK-by-convention, no DB FK, matching every Tier-1 store"), a live position's
    ``account_id``, a live LOT's ``account_id`` (an open brokerage/crypto holding, position or
    lot), and an open option contract's ``account_id``/``collateral_account_id``. None of these
    are real ``transactions``-style FKs, so nothing at the DB layer would stop the account
    underneath any one of them from vanishing.

    Losing the account here would leave the asset's equity fold
    (``frontend/assetsData.ts``'s ``debtOf``/``equityOf``), the portfolio's holdings math, or
    the options mark fold (``_compute_options_mark_base``, whose covered-call check keys on
    ``(instrument_id, account_id)``) reading a MISSING account as if it simply weren't there —
    the debt silently drops to zero, a holding's venue disappears, or a covered call silently
    reclassifies as uncovered — moving a real number with no error, no banner, nothing to
    notice (INVARIANTS.md: "a loud outage is fixable; a wrong net worth is not").

    Runs before EVERY delete path this choke can take (no references, opening-only, and
    force-cascade alike) — this hazard has nothing to do with how many TRANSACTIONS the
    account carries, so it is checked before that question is even asked.

    Every raise here carries ``error_code="account_net_worth_linked"`` — the API layer routes
    on that, not on ``transaction_count``, so this refusal never renders as the OTHER 409's
    "force delete N transaction(s)" escalation: force=true cannot fix a linked loan/position/
    lot/option, so offering that button would just 409 again (security review 2026-09-06,
    finding #2 on the first fix round).

    Deliberately bounded (security review 2026-09-06, HIGH-2): does NOT also check
    commitments/income_events/statements_meta/import_batches, which reference an account by
    convention too but don't move a live net-worth number the way these four do. Commitment
    links are instead CLEARED in the delete's own unit of work
    (``_unlink_account_from_commitments``, 2026-10-13); the rest stay the ADR's named gap."""
    from backend.db.lot_store import LotStore
    from backend.db.option_contract_store import OptionContractStore
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.db.position_store import PositionStore

    linked_assets = [a for a in OwnedAssetsStore().all() if a.get("loan_account_id") == account_id]
    if linked_assets:
        names = ", ".join(a.get("name") or a.get("id", "?") for a in linked_assets)
        raise ConflictError(
            f"Account '{acc.get('name', account_id)}' is linked to the loan on {names} — "
            "unlink it (clear the asset's loan account) before deleting this account",
            error_code="account_net_worth_linked",
            linked_asset_ids=[a.get("id") for a in linked_assets],
        )

    live_positions = [
        p
        for p in PositionStore().query_positions(account_id=account_id)
        if float(p.get("quantity") or 0) != 0
    ]
    if live_positions:
        raise ConflictError(
            f"Account '{acc.get('name', account_id)}' still holds {len(live_positions)} "
            "open position(s) — close or move them before deleting this account",
            error_code="account_net_worth_linked",
            open_position_count=len(live_positions),
        )

    live_lots = [
        lot
        for lot in LotStore().all()
        if lot.get("account_id") == account_id and float(lot.get("remaining_qty") or 0) != 0
    ]
    if live_lots:
        raise ConflictError(
            f"Account '{acc.get('name', account_id)}' still holds {len(live_lots)} open "
            "lot(s) — close or move them before deleting this account",
            error_code="account_net_worth_linked",
            open_lot_count=len(live_lots),
        )

    open_options = [
        c
        for c in OptionContractStore().all()
        if c.get("status") == "open"
        and (c.get("account_id") == account_id or c.get("collateral_account_id") == account_id)
    ]
    if open_options:
        raise ConflictError(
            f"Account '{acc.get('name', account_id)}' still has {len(open_options)} open "
            "option contract(s) — close or move them before deleting this account",
            error_code="account_net_worth_linked",
            open_option_count=len(open_options),
        )


def _unlink_account_from_commitments(account_id: str, *, conn: Connection) -> list[str]:
    """Clear every commitment reference to the account being deleted — the commitment's
    ``linked_account_id`` and each funding source's ``ref_account_id`` — inside the CALLER's
    unit of work, so the account and every id pointing at it go together. These links move
    no live number (nothing computes from them), so unlinking is safe where the
    net-worth-linked references refuse instead; leaving them dangling was the gap (found live
    2026-10-13: a deleted account still named by one commitment and 26 of its funding
    sources). Returns the ids of the commitments it changed."""
    from backend.db.commitments_store import CommitmentsStore

    store = CommitmentsStore()
    changed: list[str] = []
    for commitment in store.all(conn=conn):
        patch: dict = {}
        if commitment.get("linked_account_id") == account_id:
            patch["linked_account_id"] = None
        plan = commitment.get("funding_plan") or []
        if any(src.get("ref_account_id") == account_id for src in plan):
            patch["funding_plan"] = [
                {**src, "ref_account_id": None} if src.get("ref_account_id") == account_id else src
                for src in plan
            ]
        if patch:
            store.update_by_id(commitment["id"], patch, conn=conn)
            changed.append(commitment["id"])
    if changed:
        _log.info("delete_account %s: unlinked commitments %s", account_id, changed)
    return changed


def _other_accounts_touched(account_id: str, refs: list[dict]) -> list[dict]:
    """Every account (as ``{"id", "name"}``), OTHER than this one, holding the far leg of a
    transfer among ``refs`` — force-deleting this account also silently wipes THEIR side of
    each such row, so the 409 and the confirm UI both need to say so up front rather than let
    the owner discover it after the fact (security review 2026-09-06, MEDIUM-4)."""
    other_ids: set[str] = set()
    for row in refs:
        if row.get("kind") != "transfer":
            continue
        for leg_id in (row.get("from_account_id"), row.get("to_account_id")):
            if leg_id and leg_id != account_id:
                other_ids.add(leg_id)
    others = []
    for oid in sorted(other_ids):
        other_acc = accounts.find_by_id(oid)
        others.append({"id": oid, "name": (other_acc or {}).get("name") or oid})
    return others


def _cascade_delete_account_transactions(
    account_id: str, live_refs: list[dict], *, conn: Connection
) -> list[dict]:
    """Soft-delete every row in ``live_refs`` — mirroring ``bulk_delete_transactions``'s own
    soft-delete loop and rejected-identities bookkeeping exactly — then hard-delete EVERY row
    (still) referencing this account, live or already-deleted, so the account's
    ``ON DELETE RESTRICT`` foreign key (``transactions.from_account_id``/``to_account_id`` →
    ``accounts.id``, tables.py) is satisfied when the CALLER (``delete_account``) removes the
    account row next, in the SAME transaction.

    The hard-delete sweep below re-queries with ``include_deleted=True`` rather than reusing
    ``live_refs`` — a row an EARLIER, unrelated delete already soft-deleted still physically
    exists and still carries the account id, and RESTRICT cares only whether the row EXISTS,
    never its ``deleted`` flag. Skipping that re-query was a real bug (security review
    2026-09-06, HIGH-1): ANY account that had ever had so much as one transaction deleted from
    it before this feature shipped would hit an unhandled ``IntegrityError`` the moment its
    own force-delete (or even its opening-only delete) tried to remove the account row, since
    that stale row was invisible to the live-only query this function used to hard-delete off
    of.

    Unlike ``bulk_delete_transactions``, this does NOT pre-scan/refuse when an opening
    (Starting Balance) transaction is among ``live_refs`` — that guard protects a fact meant
    to outlive its account; here the account itself is being deleted, so its own opening row
    is not history worth protecting, it goes with it.

    Audit events are written ONE AT A TIME with the row's OWN fields as ``before`` (unlike
    ``bulk_delete_transactions``'s batched call, which always writes ``before=None`` because
    its rows stay soft-deleted and so stay reconstructable from the live table). This is the
    one delete in the codebase that leaves NO row behind at all — the audit trail (no FK onto
    ``transactions``, by design, so it "remains valid even against a hypothetical future
    hard-delete", tables.py's own ``txn_audit`` comment) is the only place a force-deleted
    transaction's own fields survive at all, so a fuller snapshot earns its keep here
    specifically (security review 2026-09-06, MEDIUM-5). A row the hard-delete sweep below
    finds ALREADY soft-deleted (from an earlier, unrelated delete — see the HIGH-1 note above)
    was NOT soft-deleted by the loop above, so it never got this round's full-row audit event
    either; the sweep writes it ONE MORE delete event, now WITH the full row, since hard-delete
    is the last moment its fields exist anywhere (MEDIUM-5's residual gap, closed on the second
    review pass).

    Composes into the CALLER's already-open transaction only — never opens its own; not a
    general-purpose helper."""
    from backend.services import audit as audit_service
    from backend.services import rejected_identities as _rejected

    deleted_rows: list[dict] = []
    for row in live_refs:
        soft = transactions.soft_delete(row["id"], conn=conn)
        if soft is not None:
            deleted_rows.append(soft)
    for row in deleted_rows:
        audit_service.record_txn_event(
            "delete", row["id"], source=row.get("source"), before=row, after=None, conn=conn
        )
    if deleted_rows:
        _rejected.remember_deleted(deleted_rows, conn=conn)

    swept_ids = {row["id"] for row in deleted_rows}
    for row in transactions.query_txns(account_id=account_id, include_deleted=True):
        if row["id"] not in swept_ids:
            audit_service.record_txn_event(
                "delete", row["id"], source=row.get("source"), before=row, after=None, conn=conn
            )
        transactions.delete(row["id"], conn=conn)
    return deleted_rows


def delete_account(account_id: str, *, force: bool = False) -> bool:
    """Delete an account by id. Returns True if deleted, False if the account was not found.

    Commitment references to the account (``linked_account_id``, a funding source's
    ``ref_account_id``) are cleared in the same unit of work, never left dangling.

    Refused OUTRIGHT — before any of the transaction-shaped logic below runs — when the
    account is still tied into net worth through a soft (no-DB-FK) reference:
    ``owned_assets.loan_account_id``, a live position, a live lot, or an open option contract
    (``_assert_account_not_net_worth_linked``). This applies to every branch below, including
    "no references at all".

    Three-way behavior on referencing LIVE transactions (``transactions.query_txns`` matches
    EITHER leg, so a transfer's other side counts too):
      - No references at all: delete the account outright.
      - The ONLY reference is the account's own opening-balance (Starting Balance) row
        (``_is_opening_txn``): delete the opening transaction and the account together,
        atomically — this isn't history a user would ever want kept once the account
        itself is gone, so no ``force`` is needed for it.
      - Other references, ``force=True``: cascade — soft-delete every transaction
        referencing the account (both legs of any transfer), with the same audit +
        rejected-identities bookkeeping ``bulk_delete_transactions`` performs, then delete
        the account — all atomically.
      - Other references, ``force=False`` (default): raise ConflictError naming the
        transaction count, any OTHER account whose history is also touched via a shared
        transfer, and pointing at ``force=true`` — archive instead to keep the history
        intact.

    EVERY one of those four cases falls through to the SAME single unit of work
    (``_cascade_delete_account_transactions`` + a guarded ``accounts.delete``) — there is no
    early-return special case for "no live references", because "no LIVE references" is not
    the same as "no references at all": an account whose transactions were ALL soft-deleted
    already (a plain ``delete_transactions_where`` sweep, an import-batch rollback, or simply
    deleting every row by hand before finally deleting the account) still has rows physically
    present with the account's id on them. The hard-delete sweep + the ``try/except
    IntegrityError`` belt-and-brace inside that ONE code path is what actually closes that
    case, for every caller, for free — an early return around it was exactly the bug a second
    review pass found (security review 2026-09-06, HIGH-1 residual gap: the FIRST fix round
    put the sweep INSIDE the cascade branch only, so "zero live refs, but stale soft-deleted
    rows exist" still hit an unhandled ``IntegrityError`` through the early return).

    A ``ConflictError`` with ``error_code="account_net_worth_linked"`` in its context comes
    from the net-worth-link guard; one with a ``transaction_count`` context key comes from the
    plain reference guard — the API layer surfaces each as ITS OWN structured JSON shape (never
    conflating the two), specifically so the frontend never (a) regexes a message containing a
    free-text account name to recover the numbers, nor (b) offers a "force delete N
    transaction(s)" escalation for a refusal `force=true` cannot do anything about (security
    review 2026-09-06, MEDIUM-3 and the second pass's finding #2).
    """
    acc = accounts.find_by_id(account_id)
    if acc is None:
        return False

    _assert_account_not_net_worth_linked(account_id, acc)

    refs = transactions.query_txns(account_id=account_id)  # LIVE only — count/opening-check
    only_opening = len(refs) == 1 and _is_opening_txn(refs[0])
    if refs and not only_opening and not force:
        others = _other_accounts_touched(account_id, refs)
        extra = ""
        if others:
            extra = (
                " (including transfers also affecting " + ", ".join(o["name"] for o in others) + ")"
            )
        raise ConflictError(
            f"Account '{acc.get('name', account_id)}' still has {len(refs)} transaction(s)"
            f"{extra} — archive it instead (PUT /api/accounts/{{id}} with archived=true) to "
            f"preserve the history, or pass force=true to delete it and all {len(refs)} "
            "transaction(s) together",
            transaction_count=len(refs),
            other_accounts=others,
        )

    balance_before = _capture_automation_balance_snapshot()
    with locked_unit_of_work(
        transactions_table,
        accounts_table,
        txn_audit_table,
        rejected_identities_table,
        commitments_table,
    ) as conn:
        deleted_rows = _cascade_delete_account_transactions(account_id, refs, conn=conn)
        _unlink_account_from_commitments(account_id, conn=conn)
        try:
            deleted = bool(accounts.delete(account_id, conn=conn))
        except IntegrityError as exc:
            # Belt and braces (security review 2026-09-06, HIGH-1): the hard-delete sweep
            # above is meant to have already cleared every `transactions` reference, live or
            # stale, for EVERY caller of this choke (no early return skips it any more). If
            # some OTHER reference this choke doesn't know about still trips the FK, the
            # caller gets a typed 409 it can act on, never a raw 500.
            raise ConflictError(
                f"Account '{acc.get('name', account_id)}' still has data referencing it that "
                "could not be cleared automatically"
            ) from exc
    _publish_transaction_events_after_commit(
        False, "delete", deleted_rows, balance_before=balance_before
    )
    return deleted
