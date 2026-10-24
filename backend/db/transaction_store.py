"""
TransactionStore — the unified money-movement ledger. Replaces the old
single-account transactions.json schema with a two-leg model.

Each transaction is one money movement expressed as up to two legs (from / to). See
.claude/rfcs/account-ledger.md.

Transaction schema:
    id              : str
    date            : "YYYY-MM-DD"
    kind            : "expense" | "income" | "transfer" | "adjustment" | "investment"
    category        : str | None   (optional on expense/income/transfer/investment; always
                                     null on adjustment — see services.ledger.create_transaction)
    description     : str          (bank/matching string — read by dedup/rules/payees; ADR 2026-07-17)
    memo            : str          (personal display-only text — never read by matching)

    # FROM leg — account money leaves. null only when kind == "income".
    from_account_id : str | None
    from_amount     : float | None  (positive magnitude, native to from-account)
    from_currency   : str | None

    # TO leg — account money enters. null only when kind == "expense".
    to_account_id   : str | None
    to_amount       : float | None  (positive magnitude, native to to-account)
    to_currency     : str | None

    # base-currency reporting snapshot — ALL kinds incl. transfers (write-time rate)
    base_amount     : float | None
    base_currency   : str | None
    fx_rate         : float | None  (native→base at txn date; source of truth for recompute)

    # optional investment annotation (cash leg only)
    ticker          : str | None
    quantity        : float | None

    tags            : [str]        free-form labels (additive; set by the rules engine — P2-SP1)
    cleared         : bool         reconciliation state — matched against the bank (cleared/reconcile MVP)
    reconciled_at   : str | None   set by a reconcile run that locked this cleared leg in (YYYY-MM-DD)

    source          : "manual" | "ai" | "email" | "telegram" | "csv_import"
    external_refs   : [{source, ref}]  (provenance/dedup pairs; [0] = the creation-time origin,
                                        later entries absorbed by merges — ADR 2026-07-21)
    batch_id : str | None   (UUID of the statement-import batch; null for non-bulk imports)
    created_at      : str          (ISO 8601 UTC timestamp when the record was written)
    deleted         : bool

Leg rules by kind:
    expense    : from set, to null
    income     : from null, to set
    transfer   : both set   (same-currency: from_amount == to_amount; cross: FX implicit)
    adjustment : exactly one leg set (to=increase, from=decrease)

PLAN 3 TASK 4: DB-backed (Core, tables ``transactions``/``transaction_splits``/
``transaction_external_refs`` — backend/db/tables.py). ``transactions`` is
promoted-columns-over-verbatim-payload: ``payload`` is the round-trip source of truth
(recomputed on every write via ``money_io._txn_row``), the rest is the derived query
surface. ``splits``/``external_refs`` are ALSO exploded into their own child tables on
every write (``money_io._explode_children`` — the SAME deterministic explosion the plan-3
task-3 absorb uses), by DELETE-then-reinsert scoped to one ``txn_id`` — cheap (a
transaction has a handful of splits/refs, never thousands) and makes "what are this
transaction's current children" always exactly a fresh explode of its own payload, never
driftable. Domain reads (``query_txns`` and friends) stay Python-side scans over
``payload`` dicts, per the task's binding — EXCEPT ``has_external_ref``/``by_external_ref``,
which hit ``transaction_external_refs``' (book_id, source, ref) index via SQL (the one
sanctioned optimization; parity-tested against the scan in ``tests/db``). That index is a
lookup accelerant only, deliberately NON-unique — see ``tables.py``'s comment on it and ADR
2026-08-07-external-ref-index-non-unique.

ID-COLLISION-TOLERANT INSERT (a load-bearing finding, not a design preference): the OLD
JsonStore-backed store never enforced ``id`` uniqueness (a flat list — ``insert`` just
appends). ``services.ledger.update_transaction``/``set_account_opening`` exploit this: both
call ``create_transaction`` — which calls THIS store's ``append``/``insert`` — with a draft
whose ``id`` may equal an ALREADY-STORED row's id (``update_transaction``'s ``merged =
{**existing, **changes}`` keeps ``existing["id"]``), relying on the old store silently
tolerating the transient duplicate, then cleans up with an explicit
``delete(id)`` + ``upsert(rebuilt)`` pair a few lines later. ``id`` is now a real DB PRIMARY
KEY — a literal duplicate INSERT would raise ``IntegrityError`` and break both chokes on
their very next call, and the task's own binding forbids touching either choke's call
sequence to work around it ("logic/validation/ordering is untouched"). The fix lives here:
``insert``/``upsert``/``append`` on THIS store TOLERATE a colliding id by replacing that
row's content in place (same seq) instead of raising — ``update_transaction``'s subsequent
``delete``+``upsert`` still runs unchanged and still produces the exact same observable end
state (content under the stable id, at the then-current end of insertion order) it always
did, because delete-then-upsert-fresh is idempotent to whatever insert() did in between.
``bulk_insert`` (no caller ever seeds it a colliding id) stays a STRICT raw multi-row
INSERT — an unexpected collision there IS a bug and should raise, matching the stricter
philosophy already established for the plan-2 ``payload_table()`` stores.

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: every mutate here writes
far more than the bare ``{seq, id, payload}`` shape ``PayloadTableStore`` writes (many
promoted columns, plus two child tables). The conn=/lock/hook plumbing mirrors
``PayloadTableStore``'s own (see that module's docstring for the CONN= COMPOSITION and
CHECKPOINTS contracts — honored identically here; before-images are captured and hooked
PER TABLE — ``transactions``, then ``transaction_splits``/``transaction_external_refs`` when
touched — since ``write_log`` before-images are keyed by ``(table_name, pk)``).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, cast

from sqlalchemy import (
    Connection,
    Engine,
    Float,
    and_,
    case,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy import cast as sa_cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlalchemy.sql.elements import ColumnElement

from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.money_io import _explode_children, _txn_row
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    transaction_external_refs,
    transaction_splits,
    transactions,
    txn_audit,
)

KINDS = {"expense", "income", "transfer", "adjustment", "investment"}


def txn_text(t: dict) -> str:
    """The transaction's bank/matching string: ``description`` (the post-split field, ADR
    2026-07-17). ``memo`` — the personal field — is NEVER consulted: matching/rules/payees key
    on the merchant string only, never on a user's personal note.

    The pre-split ``note`` fallback is GONE (2026-08-02), together with the read-time
    normaliser and both one-shot migration scripts: no writer has emitted a transaction-level
    ``note`` since the split and the live book carries zero, so the fallback could only fire on
    a hand-built dict — where silently accepting a retired field name hides the mistake instead
    of surfacing it. ``note`` now means nothing on a transaction; the per-split-line annotation
    was renamed ``TxnSplit.memo`` in the same pass."""
    return str(t.get("description") or "")


class TransactionStore:
    def __init__(self, engine: Engine | None = None):
        self._table = transactions
        self._engine = engine
        self._lock = _lock_for_table(transactions.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is never cached here — resolved fresh via
        ``get_engine()`` on every call. This store can be used as a long-lived MODULE
        singleton (e.g. ``services.ledger.transactions``/``services.audit._audit``),
        constructed once at import time and shared for the process's whole lifetime;
        caching the lazily-resolved default would pin it to whichever engine happened
        to be live the FIRST time it was ever used, silently ignoring every later
        ``db_isolated``-style redirect (env var + ``engine.dispose_engine()``) —
        exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PayloadTableStore._use_conn`` exactly — see that module's CONN=
        COMPOSITION docstring section for the full contract.

        CHILD-LOCK SCOPE (FR-M8): ``self._lock`` is the ``transactions`` table lock ONLY, yet
        a write through this store also mutates ``transaction_splits`` /
        ``transaction_external_refs`` (``_sync_children`` below) without taking THEIR locks.
        That is safe because those two child tables have no independent writer path: they are
        only ever written here, derived from and scoped to their parent transaction's
        single-writer choke (``_child_id`` keys every child off ``txn_id``). Nothing else in
        the codebase takes a lock on the child tables, so there is no lock-ordering hazard to
        guard and no concurrent child-only writer for a separate lock to exclude."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    # ── Row + child-table helpers ─────────────────────────────────────────────

    def _row(self, conn: Connection, id_val: str) -> dict | None:
        row = conn.execute(select(transactions).where(transactions.c.id == id_val)).first()
        return dict(row._mapping) if row else None

    def _next_seq(self, conn: Connection) -> int:
        return (
            conn.execute(select(func.coalesce(func.max(transactions.c.seq), -1))).scalar_one() + 1
        )

    def _existing_children(
        self, conn: Connection, table, pk_name: str, txn_id: str
    ) -> dict[str, dict]:
        rows = conn.execute(select(table).where(table.c.txn_id == txn_id))
        return {r._mapping[pk_name]: dict(r._mapping) for r in rows}

    def _sync_children(
        self,
        conn: Connection,
        txn_id: str,
        payload: dict,
        existing_splits: dict[str, dict],
        existing_refs: dict[str, dict],
    ) -> None:
        """Re-derive ``txn_id``'s split/ref child rows from ITS OWN ``payload`` (the same
        deterministic explosion the plan-3 task-3 absorb uses) and replace whatever child
        rows currently exist for it — delete-then-reinsert scoped to this one txn_id, cheap
        at real-world row counts (a handful of splits/refs, never thousands)."""
        splits, refs = _explode_children(txn_id, payload)
        split_ids = {s["split_id"] for s in splits}
        ref_ids = {r["ref_id"] for r in refs}
        split_hook_rows = list(existing_splits.items())
        split_hook_rows += [(sid, None) for sid in split_ids if sid not in existing_splits]
        ref_hook_rows = list(existing_refs.items())
        ref_hook_rows += [(rid, None) for rid in ref_ids if rid not in existing_refs]
        if split_hook_rows:
            fire_db_write_hook("transaction_splits", split_hook_rows, conn=conn)
        if ref_hook_rows:
            fire_db_write_hook("transaction_external_refs", ref_hook_rows, conn=conn)
        # CHILD-LOCK SCOPE (FR-M8): these child-table writes run under the parent's
        # ``transactions`` lock only (see ``_use_conn``) — safe because splits/external_refs
        # have no independent writer path; they exist solely as this transaction's derived
        # children, replaced wholesale here and nowhere else.
        conn.execute(sa_delete(transaction_splits).where(transaction_splits.c.txn_id == txn_id))
        conn.execute(
            sa_delete(transaction_external_refs).where(transaction_external_refs.c.txn_id == txn_id)
        )
        if splits:
            conn.execute(
                sa_insert(transaction_splits),
                [{**s, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id} for s in splits],
            )
        if refs:
            conn.execute(
                sa_insert(transaction_external_refs),
                [{**r, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id} for r in refs],
            )

    def _write_parent(
        self, conn: Connection, txn_id: str, seq: int, payload: dict, *, is_update: bool
    ) -> None:
        row = _txn_row(seq, payload)
        if is_update:
            conn.execute(
                sa_update(transactions)
                .where(transactions.c.id == txn_id)
                .values(**{k: v for k, v in row.items() if k != "id"})
            )
        else:
            conn.execute(sa_insert(transactions).values(**row))

    def _patch_parent_only(
        self, conn: Connection, txn_id: str, existing_row: dict, changes: dict
    ) -> dict:
        """Patch ONLY the parent row (fires just the ``transactions`` hook) — for the bulk
        sweeps below (soft-delete/reconcile-lock/bulk-set-deleted), whose ``changes`` are
        KNOWN to never touch ``splits``/``external_refs``, so a full children resync would be
        pure waste repeated over potentially many rows."""
        merged = {**existing_row["payload"], **changes}
        fire_db_write_hook("transactions", [(txn_id, existing_row)], conn=conn)
        row = _txn_row(existing_row["seq"], merged)
        conn.execute(
            sa_update(transactions)
            .where(transactions.c.id == txn_id)
            .values(**{k: v for k, v in row.items() if k != "id"})
        )
        return merged

    def _write_full(self, conn: Connection, item: dict) -> dict:
        """Insert fresh, or — TOLERATING a colliding id — replace an existing row's content
        in place (same seq). See the module docstring's ID-COLLISION-TOLERANT INSERT section
        for why this must never raise on a duplicate id the way a bare INSERT would.

        Parent write BEFORE children sync — on a genuinely fresh insert (``existing is
        None``) the parent row does not exist yet, and both child tables FK ``transactions.
        id`` (no ``ON DELETE CASCADE`` helps an INSERT order problem): inserting a split/ref
        first would violate that FK. Safe either order on a replace (the parent row already
        exists throughout), so writing the parent first is correct — and required — for both
        branches, not just the fresh-insert one."""
        txn_id = cast(str, item.get("id"))
        existing = self._row(conn, txn_id)
        seq = existing["seq"] if existing is not None else self._next_seq(conn)
        existing_splits = (
            self._existing_children(conn, transaction_splits, "split_id", txn_id)
            if existing
            else {}
        )
        existing_refs = (
            self._existing_children(conn, transaction_external_refs, "ref_id", txn_id)
            if existing
            else {}
        )
        fire_db_write_hook("transactions", [(txn_id, existing)], conn=conn)
        self._write_parent(conn, txn_id, seq, item, is_update=existing is not None)
        self._sync_children(conn, txn_id, item, existing_splits, existing_refs)
        return item

    # ── Query API (lock-free, mirrors JsonStore's read methods) ─────────────────

    def all(self, *, conn: Connection | None = None) -> list[dict]:
        if conn is not None:
            return [
                r.payload for r in conn.execute(select(transactions).order_by(transactions.c.seq))
            ]
        with self._eng().connect() as read_conn:
            return [
                r.payload
                for r in read_conn.execute(select(transactions).order_by(transactions.c.seq))
            ]

    def transfer_routes(self) -> frozenset:
        """``frozenset({from, to})`` for every live, unmerged transfer — read from the indexed
        columns, never the JSON payload (decoding every row made each bulk import O(ledger))."""
        t = transactions.c
        stmt = (
            select(t.from_account_id, t.to_account_id)
            .where(t.kind == "transfer", t.deleted == 0, t.merged_into.is_(None))
            .where(t.from_account_id.is_not(None), t.to_account_id.is_not(None))
            .distinct()
        )
        with self._eng().connect() as read_conn:
            return frozenset(frozenset(r) for r in read_conn.execute(stmt))

    def find_by_id(self, id_val: str, *, conn: Connection | None = None) -> dict | None:
        """``conn`` reads THROUGH the caller's open transaction (same contract as
        ``PayloadTableStore.find_by_id``). ``conn=None`` is a fresh, LOCK-FREE read — never
        ``_use_conn``: that takes ``self._lock``, and a conn=-composed choke that still reads
        one row bare (the delete choke's Starting-Balance pre-scan under ``_undo_dispose``)
        would re-take its own non-reentrant table lock and hang the server thread forever
        (live instance, 2026-09-21 20:50)."""
        if conn is not None:
            row = self._row(conn, id_val)
        else:
            with self._eng().connect() as read_conn:
                row = self._row(read_conn, id_val)
        return row["payload"] if row else None

    def query(self, **filters: Any) -> list[dict]:
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    def payee_usage(self) -> dict[str, tuple[int, str]]:
        """Per payee id: how many live (non-deleted) transactions carry it, and the latest
        date among them — one grouped query over the columns, never a decode of every row."""
        day = func.substr(transactions.c.date, 1, 10)
        stmt = (
            select(transactions.c.payee_id, func.count(), func.max(day))
            .where(
                transactions.c.deleted == 0,
                transactions.c.payee_id.is_not(None),
                transactions.c.payee_id != "",
            )
            .group_by(transactions.c.payee_id)
        )
        with self._eng().connect() as conn:
            return {pid: (int(n), last or "") for pid, n, last in conn.execute(stmt)}

    # ── Domain queries ────────────────────────────────────────────────────────

    def query_by_batch(self, batch_id: str, include_deleted: bool = True) -> list[dict]:
        items = self.all()
        items = [t for t in items if t.get("batch_id") == batch_id]
        if not include_deleted:
            items = [t for t in items if not t.get("deleted")]
        return items

    def _txn_where(
        self,
        *,
        kind: str | list[str] | None = None,
        category: str | list[str] | None = None,
        account_id: str | None = None,
        account_ids: str | list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        payee_id: str | list[str] | None = None,
        cleared: bool | None = None,
        needs_review: bool | None = None,
        uncategorized: bool | None = None,
        include_deleted: bool = False,
    ) -> list[ColumnElement]:
        """SQL WHERE predicates for every SQL-pushable `query_txns`/`query_txns_page` filter
        (AND semantics — matches the Python filter chain this replaces exactly). NOT `q`/`tag`:
        free-text needs a cross-store (payee/category label) join and JSON-array-of-tags
        containment isn't one portable clause across SQLite and Postgres, so both stay Python
        post-filters applied to the rows this narrows down to."""
        clauses: list[ColumnElement] = []
        if not include_deleted:
            clauses.append(transactions.c.deleted == 0)
        if kind is not None:
            clauses.append(transactions.c.kind.in_(_as_set(kind)))
        if category is not None:
            cats = _as_set(category)
            # A split txn carries the SPLIT sentinel top-level; match it when ANY of its split
            # lines is in a requested category too, so the Planning Activity→register deep-link
            # surfaces the split txn under each category it contributed to (P3-SP).
            split_match = exists(
                select(1).where(
                    transaction_splits.c.txn_id == transactions.c.id,
                    transaction_splits.c.category.in_(cats),
                )
            )
            clauses.append(or_(transactions.c.category.in_(cats), split_match))
        if account_id is not None:
            clauses.append(
                or_(
                    transactions.c.from_account_id == account_id,
                    transactions.c.to_account_id == account_id,
                )
            )
        if account_ids is not None:
            ids = _as_set(account_ids)
            clauses.append(
                or_(transactions.c.from_account_id.in_(ids), transactions.c.to_account_id.in_(ids))
            )
        if payee_id is not None:
            clauses.append(transactions.c.payee_id.in_(_as_set(payee_id)))
        if date_from is not None:
            clauses.append(transactions.c.date >= date_from)
        if date_to is not None:
            clauses.append(transactions.c.date <= date_to)
        if cleared is not None:
            clauses.append(transactions.c.cleared == (1 if cleared else 0))
        if needs_review is not None:
            clauses.append(transactions.c.needs_review == (1 if needs_review else 0))
        if uncategorized is not None:
            # Mirrors services.ledger.is_uncategorized exactly: a top-level expense/income row
            # with no category. A split txn's top-level category is the SPLIT sentinel — never
            # blank — so it is excluded even if an individual split line is blank.
            is_unsorted = and_(
                transactions.c.kind.in_(("expense", "income")),
                or_(transactions.c.category.is_(None), transactions.c.category == ""),
            )
            clauses.append(is_unsorted if uncategorized else ~is_unsorted)
        return clauses

    def query_txns(
        self,
        kind: str | list[str] | None = None,
        category: str | list[str] | None = None,
        account_id: str | None = None,
        account_ids: str | list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        tag: str | list[str] | None = None,
        payee_id: str | list[str] | None = None,
        include_deleted: bool = False,
        needs_review: bool | None = None,
    ) -> list[dict]:
        """Filtered transaction list (AND semantics across every supplied filter).

        `kind`/`category`/`payee_id`/`tag` accept a single value OR a list of values
        (OR'd against each other — e.g. two payee ids matches either); a plain single
        value behaves exactly as before (backward compatible for every direct caller —
        AI tools, services, other routers, and the FastAPI route which passes through
        whatever a repeated query param parsed to). NEVER comma-split: a category value
        (an opaque id, resolved from a caller-supplied label upstream in
        `services.ledger.get_transactions` — never split here) or a free-form tag may
        legitimately contain a literal comma. `account_id` matches EITHER leg (the single
        VIEW-scope filter);
        `account_ids` is a distinct OR filter (also either leg) that ANDs with
        `account_id` when both are supplied. Dates are ISO 'YYYY-MM-DD' strings;
        lexical comparison is correct for ISO. `q` is a case-insensitive substring
        matched across description/memo/category. `tag` matches a transaction carrying any of the
        requested tags (case-insensitive). Money-aware amount filtering (min/max on
        the re-based reporting amount) lives in the API route, where the FX recompute
        and account-leg magnitude logic already run.

        Every filter but `q`/`tag` runs as a real SQL WHERE clause now (see `_txn_where`) —
        this no longer loads the whole book into Python to filter it in a list comprehension.
        `q`/`tag` still apply as post-filters, to whatever the SQL WHERE already narrowed down
        to, so a caller combining either with a large book still does far less Python-side
        work than before."""
        clauses = self._txn_where(
            kind=kind,
            category=category,
            account_id=account_id,
            account_ids=account_ids,
            date_from=date_from,
            date_to=date_to,
            payee_id=payee_id,
            include_deleted=include_deleted,
            needs_review=needs_review,
        )
        with self._eng().connect() as conn:
            stmt = select(transactions).order_by(transactions.c.seq)
            if clauses:
                stmt = stmt.where(and_(*clauses))
            items = [r.payload for r in conn.execute(stmt)]
        if q:
            needle = q.strip().lower()
            if needle:
                items = [t for t in items if _matches_text(t, needle)]
        if tag:
            wants = {v.lower() for v in _as_set(tag)}
            if wants:
                items = [
                    t
                    for t in items
                    if any(str(tg).lower() in wants for tg in (t.get("tags") or []))
                ]
        return items

    def recent_by_created_at(self, limit: int) -> list[dict]:
        """Last `limit` non-deleted transactions ordered by `created_at` (when the row was
        WRITTEN), not `date` (what it claims happened) — surfaces a just-imported row even
        when its own date is backdated into last month. Ties broken by `seq` desc, the same
        insertion-order tie-break every other listing uses."""
        page, _ = self.recent_page_by_created_at(limit=limit)
        return page

    def recent_page_by_created_at(
        self,
        *,
        limit: int,
        offset: int = 0,
        needs_review: bool | None = None,
        automation_only: bool = False,
        exclude_ids: frozenset[str] = frozenset(),
    ) -> tuple[list[dict], int]:
        """One insertion-ordered page plus the matching total.

        The review filter is applied before LIMIT so a burst of non-review rows cannot crowd
        older pending rows out of the queue. `exclude_ids` is applied the same way, so skipping
        rows (the accounts' opening balances) never shortens the page.
        """
        clauses = self._txn_where(needs_review=needs_review)
        if exclude_ids:
            clauses.append(transactions.c.id.not_in(list(exclude_ids)))
        if automation_only:
            # AI automation writes may retain source=ai; creation provenance distinguishes
            # them from attended chat. An automation UPDATE never qualifies a manual row.
            created_by_automation = exists(
                select(txn_audit.c.id).where(
                    txn_audit.c.book_id == transactions.c.book_id,
                    txn_audit.c.txn_id == transactions.c.id,
                    txn_audit.c.surface == "automation",
                    txn_audit.c.payload["op"].as_string() == "create",
                )
            )
            clauses.append(
                or_(
                    transactions.c.payload["source"].as_string().in_(["automation", "recurring"]),
                    created_by_automation,
                )
            )
        where = and_(*clauses) if clauses else literal(True)
        with self._eng().connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(transactions).where(where)
            ).scalar_one()
            stmt = (
                select(transactions)
                .where(where)
                .order_by(transactions.c.created_at.desc(), transactions.c.seq.desc())
                .limit(limit)
                .offset(offset)
            )
            return [r.payload for r in conn.execute(stmt)], int(total)

    def query_txns_page(
        self,
        *,
        kind: str | list[str] | None = None,
        category: str | list[str] | None = None,
        account_id: str | None = None,
        account_ids: str | list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        payee_id: str | list[str] | None = None,
        cleared: bool | None = None,
        needs_review: bool | None = None,
        uncategorized: bool | None = None,
        include_deleted: bool = False,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[dict], int, str | None, str | None, float, float]:
        """SQL-paginated twin of `query_txns`, for a caller that wants one PAGE plus the
        filtered set's total count, date span, and per-leg net (never the whole book loaded
        into Python to slice/sum in place). Does NOT accept `q`/`tag`/amount/source filters —
        those need a cross-store join, JSON-array containment, or the FX-aware magnitude
        recompute that only runs in the API route; a caller using any of those pages in Python
        instead, over the SQL-narrowed candidate set `query_txns` already returns (see
        `api/ledger.py::get_transactions`, which picks whichever path a request's filters
        allow).

        Returns `(page, total, first_date, last_date, to_sum, from_sum)`. `to_sum`/`from_sum`
        are the filtered set's `to_amount`/`from_amount` totals restricted to `account_id`'s own
        legs (0.0 when `account_id` is None) — the SQL twin of `services.ledger.period_net`'s
        Python fold, so a single-account page never needs the full filtered set materialized
        just to sum it."""
        clauses = self._txn_where(
            kind=kind,
            category=category,
            account_id=account_id,
            account_ids=account_ids,
            date_from=date_from,
            date_to=date_to,
            payee_id=payee_id,
            cleared=cleared,
            needs_review=needs_review,
            uncategorized=uncategorized,
            include_deleted=include_deleted,
        )
        where = and_(*clauses) if clauses else literal(True)
        # Same tie-break as the route's own sort (date desc, then the populated leg's
        # magnitude desc). Amount columns are Text — exact-decimal strings, per the table's own
        # comment; CAST to Float here is for ordering only, never a stored or returned value.
        magnitude = func.abs(
            func.coalesce(
                sa_cast(transactions.c.from_amount, Float),
                sa_cast(transactions.c.to_amount, Float),
                0.0,
            )
        )
        with self._eng().connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(transactions).where(where)
            ).scalar_one()
            first_date, last_date = (None, None)
            to_sum, from_sum = (0.0, 0.0)
            if total:
                first_date, last_date = conn.execute(
                    select(func.min(transactions.c.date), func.max(transactions.c.date)).where(
                        where
                    )
                ).one()
                if account_id is not None:
                    to_sum, from_sum = conn.execute(
                        select(
                            func.coalesce(
                                func.sum(
                                    case(
                                        (
                                            transactions.c.to_account_id == account_id,
                                            sa_cast(transactions.c.to_amount, Float),
                                        ),
                                        else_=0.0,
                                    )
                                ),
                                0.0,
                            ),
                            func.coalesce(
                                func.sum(
                                    case(
                                        (
                                            transactions.c.from_account_id == account_id,
                                            sa_cast(transactions.c.from_amount, Float),
                                        ),
                                        else_=0.0,
                                    )
                                ),
                                0.0,
                            ),
                        ).where(where)
                    ).one()
            stmt = (
                select(transactions)
                .where(where)
                .order_by(transactions.c.date.desc(), magnitude.desc())
                .limit(limit)
                .offset(offset)
            )
            page = [r.payload for r in conn.execute(stmt)]
        return page, int(total), first_date, last_date, float(to_sum), float(from_sum)

    def has_external_ref(self, source: str | None, ref: str | None) -> bool:
        if not ref:
            return False
        return self.by_external_ref(source, ref) is not None

    def by_external_ref(self, source: str | None, ref: str | None) -> dict | None:
        """Return the first non-deleted transaction carrying ``(source, ref)`` anywhere in its
        ``external_refs`` list (the idempotency key), or ``None`` (ADR 2026-07-21 — one list,
        primary + absorbed pairs alike).

        Identity contract (F13 fix — two cases):
        1. User-delete (``deleted=True``, ``merged_into`` falsy): row is excluded → the ref is
           FREE again. A re-import / re-log of the same ref recreates the transaction. This is
           the "unblock after accidental delete" guarantee.
        2. Merge tombstone (``deleted=True``, ``merged_into`` set): also excluded, BUT the
           canonical row already carries the absorbed pair in its own ``external_refs[]``, so
           the canonical row covers the no-op guarantee — a re-import of an absorbed source does
           NOT recreate because it hits the canonical row here.

        PLAN 3 TASK 4: the ONE sanctioned SQL optimization (the task's own binding) — hits
        ``transaction_external_refs``' (book_id, source, ref) index instead of a full Python
        scan. The index is deliberately NON-unique (own finding — see its comment in
        tables.py and ADR 2026-08-07-external-ref-index-non-unique: a merge legitimately
        leaves the same non-null (source, ref) pair on both the canonical row and the
        tombstoned absorbed row) — it is a lookup accelerant only, never a dedup enforcement
        mechanism; the app-level Python tuple-compare below (`target = (source, ref)`) is the
        sole dedup authority, unchanged by this store's rewrite. A NULL ``source`` query is
        filtered with ``IS NULL`` explicitly (SQL ``NULL <> NULL`` means an index equality
        lookup can't match it directly); the result set is then joined back to non-deleted
        transactions and the earliest by ``seq`` is returned, matching the Python scan's
        "first in file/insertion order" contract exactly (parity-tested against the scan in
        ``tests/db``, including both the NULL-source duplicate case and the merge-tombstone
        non-null-source case). Backs the no-op return of create_transaction's dedup path."""
        if not ref:
            return None
        with self._eng().connect() as conn:
            source_clause = (
                transaction_external_refs.c.source.is_(None)
                if source is None
                else transaction_external_refs.c.source == source
            )
            txn_ids = [
                row[0]
                for row in conn.execute(
                    select(transaction_external_refs.c.txn_id).where(
                        source_clause, transaction_external_refs.c.ref == ref
                    )
                )
            ]
            if not txn_ids:
                return None
            row = conn.execute(
                select(transactions)
                .where(transactions.c.id.in_(txn_ids), transactions.c.deleted == 0)
                .order_by(transactions.c.seq)
            ).first()
            return row.payload if row else None

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict, *, conn: Connection | None = None) -> dict:
        return self.insert(make_transaction(data), conn=conn)

    def bulk_append(self, data_list: list[dict], *, conn: Connection | None = None) -> list[dict]:
        """Normalise and persist multiple transactions in a single transaction."""
        rows = [make_transaction(d) for d in data_list]
        return self.bulk_insert(rows, conn=conn)

    @staticmethod
    def _matches_bulk_filters(
        t: dict,
        *,
        date_from: str | None,
        date_to: str | None,
        account_id: str | None,
        kind: str | None,
        category: str | None,
        batch_id: str | None,
        protected_ids: frozenset[str],
    ) -> bool:
        """Does one row fall inside a bulk filter sweep?

        Extracted so ``select_where`` (preview) and ``bulk_delete_where`` (destructive) share
        ONE predicate. A preview built from a second, parallel query would be worse than no
        preview at all: the moment the two drift, the card shows a set the delete does not
        touch, and the user approves rows they never saw. One predicate makes that
        divergence structurally impossible rather than merely unlikely.

        ``protected_ids`` are rows a sweep must never touch — the accounts' Starting Balance
        transactions, whose identity lives on the ACCOUNT (``opening_txn_id``). A store does
        not read another store, so the service choke
        (``ledger.delete_transactions_where``) supplies the set.
        """
        if t.get("deleted"):
            return False
        if t.get("id") in protected_ids:
            return False
        if kind is not None and t.get("kind") != kind:
            return False
        if category is not None and t.get("category") != category:
            return False
        if account_id is not None and (
            t.get("from_account_id") != account_id and t.get("to_account_id") != account_id
        ):
            return False
        if date_from is not None and t.get("date", "") < date_from:
            return False
        if date_to is not None and t.get("date", "") > date_to:
            return False
        if batch_id is not None and t.get("batch_id") != batch_id:
            return False
        return True

    def select_where(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        account_id: str | None = None,
        kind: str | None = None,
        category: str | None = None,
        batch_id: str | None = None,
        *,
        protected_ids: frozenset[str],
    ) -> list[dict]:
        """Rows a ``bulk_delete_where`` with these same filters WOULD delete. Pure read.

        No write lock: this is a snapshot read, and a row landing between preview and confirm
        is handled by re-previewing at confirm time, not by holding a lock across a user
        round-trip (which could be minutes).

        ``protected_ids`` is keyword-only and has NO default — see ``bulk_delete_where``. The
        preview must be spelled exactly like the delete or it shows rows the delete will spare."""
        return [
            t
            for t in self.all()
            if self._matches_bulk_filters(
                t,
                date_from=date_from,
                date_to=date_to,
                account_id=account_id,
                kind=kind,
                category=category,
                batch_id=batch_id,
                protected_ids=protected_ids,
            )
        ]

    def bulk_delete_where(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        account_id: str | None = None,
        kind: str | None = None,
        category: str | None = None,
        batch_id: str | None = None,
        *,
        protected_ids: frozenset[str],
        conn: Connection | None = None,
    ) -> int:
        """Soft-delete all non-deleted transactions matching the given filters in one
        transaction. Returns the number of rows affected.

        ``protected_ids`` is keyword-only and has NO DEFAULT on purpose. It is the ONLY thing
        standing between a filtered sweep and somebody's opening balance, and a default would
        make forgetting it silent — the sweep would run, delete the Starting Balance rows, and
        report success. Required, it is a TypeError the type checker catches at every call site
        (RFC 2026-07-31 §9). The set is supplied by ``ledger.delete_transactions_where``,
        because a store must not read another store.

        Holds the table's write lock for the full read-modify-write (BUD-SWEEP-01
        follow-up, 2026-07-04): this used to race a concurrent write to the same file — a bulk
        delete and, say, a Lucius ``add_transaction`` landing at the same time could each read
        the pre-delete snapshot and the later write would silently discard the other's
        change (lost update) on the single most money-critical store in the app."""
        with self._use_conn(conn) as conn:
            rows = conn.execute(select(transactions).order_by(transactions.c.seq)).all()
            count = 0
            for r in rows:
                row = dict(r._mapping)
                # ONE predicate, shared with select_where — see _matches_bulk_filters.
                if not self._matches_bulk_filters(
                    row["payload"],
                    date_from=date_from,
                    date_to=date_to,
                    account_id=account_id,
                    kind=kind,
                    category=category,
                    batch_id=batch_id,
                    protected_ids=protected_ids,
                ):
                    continue
                self._patch_parent_only(conn, row["id"], row, {"deleted": True})
                count += 1
        return count

    def soft_delete(self, txn_id: str, *, conn: Connection | None = None) -> dict | None:
        return self.update_by_id(txn_id, {"deleted": True}, conn=conn)

    def set_cleared(
        self, txn_id: str, cleared: bool, *, conn: Connection | None = None
    ) -> dict | None:
        """Set the reconciliation cleared flag on one transaction (cleared/reconcile MVP)."""
        return self.update_by_id(txn_id, {"cleared": bool(cleared)}, conn=conn)

    def mark_cleared_reconciled(
        self, account_id: str, on_date: str, *, conn: Connection | None = None
    ) -> int:
        """Mark every non-deleted CLEARED leg touching ``account_id`` as the locked-in
        reconciled point: stamp ``reconciled_at`` on each. Returns the count touched.

        v1 keeps the single ``cleared`` boolean (no separate enum) — the lock is a
        ``reconciled_at`` stamp on the already-cleared legs, so the cleared balance is
        unchanged but the reconcile is auditable.

        Holds the table's write lock for the full read-modify-write (BUD-SWEEP-01 follow-up,
        2026-07-04 — see ``bulk_delete_where``)."""
        with self._use_conn(conn) as conn:
            rows = conn.execute(select(transactions)).all()
            count = 0
            for r in rows:
                row = dict(r._mapping)
                payload = row["payload"]
                if payload.get("deleted") or not payload.get("cleared"):
                    continue
                if (
                    payload.get("from_account_id") != account_id
                    and payload.get("to_account_id") != account_id
                ):
                    continue
                if payload.get("reconciled_at") == on_date:
                    continue
                self._patch_parent_only(conn, row["id"], row, {"reconciled_at": on_date})
                count += 1
        return count

    def bulk_set_deleted(
        self, txn_ids: list[str], deleted: bool, *, conn: Connection | None = None
    ) -> tuple[list[str], list[str]]:
        """Set deleted flag on multiple transactions. Returns (affected_ids, not_found_ids).

        Holds the table's write lock for the full read-modify-write (BUD-SWEEP-01 follow-up,
        2026-07-04 — see ``bulk_delete_where``): an unlocked read-modify-write here silently
        loses a concurrent delete/undelete on the ledger."""
        id_set = set(txn_ids)
        with self._use_conn(conn) as conn:
            rows = conn.execute(
                select(transactions)
                .where(transactions.c.id.in_(id_set))
                .order_by(transactions.c.seq)
            ).all()
            affected = [r.id for r in rows]
            for r in rows:
                self._patch_parent_only(conn, r.id, dict(r._mapping), {"deleted": deleted})
            not_found = [tid for tid in txn_ids if tid not in set(affected)]
        return affected, not_found

    def bulk_soft_delete(
        self, txn_ids: list[str], *, conn: Connection | None = None
    ) -> tuple[list[str], list[str]]:
        """Delete multiple transactions in a single transaction. Returns (deleted_ids, not_found_ids)."""
        return self.bulk_set_deleted(txn_ids, deleted=True, conn=conn)

    # ── Write API (JsonStore parity) ─────────────────────────────────────────────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        with self._use_conn(conn) as conn:
            self._write_full(conn, item)
        return item

    def bulk_insert(self, new_items: list[dict], *, conn: Connection | None = None) -> list[dict]:
        """Append multiple items in one transaction. A STRICT raw multi-row insert — unlike
        the single-row ``insert``/``upsert``, this never tolerates a colliding id (no real
        caller ever seeds one; an unexpected collision here is a bug and should raise, per the
        stricter philosophy already established for the plan-2 ``payload_table()`` stores).
        Empty input is a true no-op."""
        if not new_items:
            return new_items
        with self._use_conn(conn) as conn:
            fire_db_write_hook(
                "transactions", [(item.get("id"), None) for item in new_items], conn=conn
            )
            seq = self._next_seq(conn)
            txn_rows = []
            split_rows: list[dict] = []
            ref_rows: list[dict] = []
            for i, item in enumerate(new_items):
                txn_id = item.get("id")
                txn_rows.append(_txn_row(seq + i, item))
                splits, refs = _explode_children(txn_id, item)
                split_rows.extend(
                    {**s, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id} for s in splits
                )
                ref_rows.extend({**r, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id} for r in refs)
            conn.execute(sa_insert(transactions), txn_rows)
            if split_rows:
                fire_db_write_hook(
                    "transaction_splits", [(s["split_id"], None) for s in split_rows], conn=conn
                )
                conn.execute(sa_insert(transaction_splits), split_rows)
            if ref_rows:
                fire_db_write_hook(
                    "transaction_external_refs", [(r["ref_id"], None) for r in ref_rows], conn=conn
                )
                conn.execute(sa_insert(transaction_external_refs), ref_rows)
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        """Insert or replace by id. Aliases ``insert`` for this store — see the module
        docstring's ID-COLLISION-TOLERANT INSERT section for why the two collapse to the
        same operation here."""
        with self._use_conn(conn) as conn:
            self._write_full(conn, item)
        return item

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch an item in-place (``{**item, **changes}``), same seq/position. No-op (no
        write, no hook) if the id doesn't exist — returns None, matching JsonStore."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            existing_splits = self._existing_children(conn, transaction_splits, "split_id", id_val)
            existing_refs = self._existing_children(
                conn, transaction_external_refs, "ref_id", id_val
            )
            fire_db_write_hook("transactions", [(id_val, existing)], conn=conn)
            self._write_parent(conn, id_val, existing["seq"], merged, is_update=True)
            self._sync_children(conn, id_val, merged, existing_splits, existing_refs)
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
        """Patch multiple ids in ONE transaction (atomic under the table lock — see
        JsonStore.bulk_patch's docstring for why two rows must commit together). Returns
        id -> patched item for FOUND ids only; ids absent from the table are silently
        skipped, same as JsonStore."""
        if not updates:
            return {}
        with self._use_conn(conn) as conn:
            existing_rows = {
                tid: row for tid in updates if (row := self._row(conn, tid)) is not None
            }
            patched: dict[str, dict] = {}
            hook_rows: list[tuple[str, dict | None]] = []
            for tid, changes in updates.items():
                if tid in existing_rows:
                    patched[tid] = {**existing_rows[tid]["payload"], **changes}
                    hook_rows.append((tid, existing_rows[tid]))
            if patched:
                fire_db_write_hook("transactions", hook_rows, conn=conn)
                for tid, merged in patched.items():
                    existing_splits = self._existing_children(
                        conn, transaction_splits, "split_id", tid
                    )
                    existing_refs = self._existing_children(
                        conn, transaction_external_refs, "ref_id", tid
                    )
                    self._write_parent(conn, tid, existing_rows[tid]["seq"], merged, is_update=True)
                    self._sync_children(conn, tid, merged, existing_splits, existing_refs)
        return patched

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        """Hard-delete one transaction (and, via ON DELETE CASCADE, its splits/external_refs
        rows). Real production callers: a row THEY just re-appended a second time
        (``update_transaction``/``set_account_opening``'s cleanup step) — a transaction is
        otherwise only ever soft-deleted; and ``services.ledger._cascade_delete_account_
        transactions``, the account-force-delete choke, which hard-deletes every row (live
        or already soft-deleted) referencing an account being removed so its
        ``ON DELETE RESTRICT`` foreign key onto ``accounts.id`` is satisfied — see ADR
        ``2026-09-06-force-delete-account-hard-removes-its-cascaded-transactions``."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return False
            existing_splits = self._existing_children(conn, transaction_splits, "split_id", id_val)
            existing_refs = self._existing_children(
                conn, transaction_external_refs, "ref_id", id_val
            )
            fire_db_write_hook("transactions", [(id_val, existing)], conn=conn)
            if existing_splits:
                fire_db_write_hook("transaction_splits", list(existing_splits.items()), conn=conn)
            if existing_refs:
                fire_db_write_hook(
                    "transaction_external_refs", list(existing_refs.items()), conn=conn
                )
            conn.execute(sa_delete(transactions).where(transactions.c.id == id_val))
        return True

    def replace_all(self, items: list[dict], *, conn: Connection | None = None) -> None:
        """Wipe the table and renumber every row's seq from 0, in ``items`` order. Splits/refs
        are wiped and re-exploded to match (ON DELETE CASCADE removes the old children when
        their parent rows are wiped)."""
        with self._use_conn(conn) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(transactions))}
            existing_splits_all = {
                r.split_id: dict(r._mapping) for r in conn.execute(select(transaction_splits))
            }
            existing_refs_all = {
                r.ref_id: dict(r._mapping) for r in conn.execute(select(transaction_external_refs))
            }
            incoming_ids = {item.get("id") for item in items}
            hook_rows = list(existing_map.items())
            hook_rows += [(iid, None) for iid in incoming_ids if iid not in existing_map]
            fire_db_write_hook("transactions", hook_rows, conn=conn)

            txn_rows = []
            split_rows: list[dict] = []
            ref_rows: list[dict] = []
            incoming_split_ids: set[str] = set()
            incoming_ref_ids: set[str] = set()
            for i, item in enumerate(items):
                txn_id = item.get("id")
                txn_rows.append(_txn_row(i, item))
                splits, refs = _explode_children(txn_id, item)
                for s in splits:
                    split_rows.append({**s, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id})
                    incoming_split_ids.add(s["split_id"])
                for r in refs:
                    ref_rows.append({**r, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id})
                    incoming_ref_ids.add(r["ref_id"])
            split_hook_rows = list(existing_splits_all.items())
            split_hook_rows += [
                (sid, None) for sid in incoming_split_ids if sid not in existing_splits_all
            ]
            ref_hook_rows = list(existing_refs_all.items())
            ref_hook_rows += [
                (rid, None) for rid in incoming_ref_ids if rid not in existing_refs_all
            ]
            if split_hook_rows:
                fire_db_write_hook("transaction_splits", split_hook_rows, conn=conn)
            if ref_hook_rows:
                fire_db_write_hook("transaction_external_refs", ref_hook_rows, conn=conn)

            conn.execute(
                transactions.delete()
            )  # CASCADEs transaction_splits/transaction_external_refs
            if txn_rows:
                conn.execute(sa_insert(transactions), txn_rows)
            if split_rows:
                conn.execute(sa_insert(transaction_splits), split_rows)
            if ref_rows:
                conn.execute(sa_insert(transaction_external_refs), ref_rows)


def _as_set(value: str | list[str]) -> set[str]:
    """Normalise a single value OR a list of values (e.g. from a repeated query
    param) into the OR-match set. NEVER comma-splits — a category key or tag may
    legitimately carry a literal comma (see query_txns's docstring), so the caller
    is responsible for handing this a real list when it wants multiple values."""
    if isinstance(value, str):
        return {value}
    return set(value)


def _matches_text(t: dict, needle: str) -> bool:
    """Case-insensitive substring match across the human-readable text fields of a
    transaction (description, memo, category). `needle` must already be lower-cased."""
    return any(
        needle in str(t.get(field) or "").lower() for field in ("description", "memo", "category")
    )


def _norm_splits(raw) -> list[dict] | None:
    """Normalise a split allocation into the stored shape (P3-SP), or None when unsplit.

    Each line is ``{category, amount (abs magnitude), note}``. Semantic validation
    (kind gate, sum-equals-leg, valid categories, the 1-line→unsplit normalisation) is
    enforced UPSTREAM in ``ledger_service.create_transaction`` (the choke point) before this
    runs; this is the dumb storage projection — empty/None → None (the unsplit canonical state)."""
    if not raw:
        return None
    out: list[dict] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        cat = s.get("category")
        amt = s.get("amount")
        if not cat or amt is None:
            continue
        out.append({"category": str(cat), "amount": abs(float(amt)), "memo": s.get("memo", "")})
    return out or None


def _norm_ext_refs(raw) -> list[dict]:
    """Normalise the provenance/dedup list into the stored shape: a list of
    ``{"source": str|None, "ref": str}`` (plus ``emitter`` when known), order-preserving ([0] is
    the creation-time origin — ADR 2026-07-21). Drops entries with an empty ref; a missing/empty
    source stays None (a bare no-prefix ref).

    ``emitter`` is omitted rather than stored as None when unresolved, so "we never looked" and
    "we looked and found nothing" stay the same absent key — both mean unresolved to every
    reader, and neither may be read as a wildcard."""
    out: list[dict] = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        ref = e.get("ref")
        if ref is None or not str(ref):
            continue
        src = e.get("source")
        entry = {"source": (str(src) if src is not None else None), "ref": str(ref)}
        emitter = e.get("emitter")
        if emitter:
            entry["emitter"] = str(emitter)
        out.append(entry)
    return out


def make_transaction(data: dict) -> dict:
    """Normalise raw input into a stored transaction. Magnitudes are stored positive;
    direction is carried by which leg (from/to) is populated."""
    kind = data.get("kind", "expense")
    if kind not in KINDS:
        kind = "expense"

    def _amt(v):
        return abs(float(v)) if v is not None else None

    def _cur(v):
        return v.upper() if v else None

    return {
        "id": data.get("id") or new_id("txn"),
        "date": data["date"],
        "kind": kind,
        "category": data.get("category"),
        "payee_id": data.get(
            "payee_id"
        ),  # → PayeeStore entity (P4-PAYEE); null for transfer/adjustment
        # Provenance split (ADR 2026-07-17): `description` = bank/matching string, `memo` =
        # personal display-only text. No `note` key is ever written (the retired input alias
        # was removed 2026-07-20 — every real caller of append()/bulk_append() now sends
        # `description` directly; `_load_items()` below is the READ-side safety net for a
        # row already on disk from before that fix).
        "description": data.get("description") or "",
        "memo": data.get("memo") or "",
        "from_account_id": data.get("from_account_id"),
        "from_amount": _amt(data.get("from_amount")),
        "from_currency": _cur(data.get("from_currency")),
        "to_account_id": data.get("to_account_id"),
        "to_amount": _amt(data.get("to_amount")),
        "to_currency": _cur(data.get("to_currency")),
        # Original foreign charge before any leg conversion (display/audit only; meaningful on any
        # kind). Coerced like a leg amount/currency; None when absent.
        "original_amount": _amt(data.get("original_amount")),
        "original_currency": _cur(data.get("original_currency")),
        # Transfers DO stamp (the service's _transfer_base_value writes the value that MOVED,
        # at the write-time rate — the planning FX reader depends on it). The old
        # "null on transfers" veto here silently stripped the service's stamp for every
        # transfer ever written (found live: 480/480 unstamped).
        "base_amount": _amt(data.get("base_amount")),
        "base_currency": _cur(data.get("base_currency")),
        "fx_rate": data.get("fx_rate"),
        "ticker": data.get("ticker"),
        "quantity": data.get("quantity"),
        "tags": [str(t) for t in (data.get("tags") or []) if str(t).strip()],
        "splits": _norm_splits(data.get("splits")),  # category allocation (P3-SP); None = unsplit
        "cleared": bool(data.get("cleared", False)),
        # Review queue (Accounts redesign): import/AI creates land True (the service decides at
        # the choke point); approve clears it, reject soft-deletes. Default False.
        "needs_review": bool(data.get("needs_review", False)),
        "source": data.get("source", "manual"),
        # Provenance/dedup {source, ref} pairs — ONE ordered list, [0] = origin (ADR 2026-07-21).
        "external_refs": _norm_ext_refs(data.get("external_refs")),
        "merged_from": [str(x) for x in (data.get("merged_from") or []) if str(x).strip()],
        "merged_into": data.get("merged_into"),
        "batch_id": data.get("batch_id"),
        "created_at": data.get("created_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deleted": bool(data.get("deleted", False)),
    }
