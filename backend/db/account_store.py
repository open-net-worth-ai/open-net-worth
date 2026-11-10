"""
AccountStore — financial accounts (bank, brokerage cash, e-wallet, credit card,
margin loan, loan-to-a-person). Replaces payment_sources.json.

Each account holds and reports its balance in its own native `currency`; the actual
balance is computed (never stored) by ledger_service by folding transaction legs past
the account's tracking start. See .claude/rfcs/account-ledger.md.

Account schema:
    id                   : str
    name                 : str
    kind                 : "asset" | "liability"   (derived from `type`, never stored raw)
    type                 : "cash" | "fixed_deposit" | "investment" | "retirement"
                           | "receivable" | "credit" | "loan"   (the ONE classification axis —
                           backend.models.account.AccountType)
    currency             : ISO code (native currency of this account)
    opening_txn_id       : str | None   (the account's Starting Balance transaction — its
                           amount IS the opening and its date IS the tracking-start cutoff;
                           None = no tracking start. Server-owned, written only by
                           services.ledger.set_account_opening.)
    credit_limit         : float | None
    last_four            : str
    note                 : str
    last_reconciled_at   : str | None   (ISO date truth was last asserted; drift indicator)
    archived             : bool

PLAN 3 TASK 4: DB-backed (Core, table ``accounts`` — backend/db/tables.py). Rows are
promoted-columns-over-verbatim-payload: ``payload`` is the round-trip source of truth (the
account dict exactly as ``_make_account`` built it), ``kind``/``type``/``currency``/
``archived``/``opening_txn_id`` are the derived query surface, recomputed from ``payload`` on
every write via ``money_io._account_row`` (never duplicated here).

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: every mutate here writes more
than the bare ``{seq, id, payload}`` shape ``PayloadTableStore`` writes (the five promoted
columns above). The conn=/lock/hook plumbing mirrors ``PayloadTableStore``'s own (see that
module's docstring for the CONN= COMPOSITION and CHECKPOINTS contracts — honored identically
here).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, cast

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.money_io import _account_row
from backend.db.tables import accounts
from backend.models.account import ACCOUNT_BAND, kind_of

KINDS = {"asset", "liability"}
TYPES = frozenset(ACCOUNT_BAND)  # the seven — one vocabulary, models/account.py


class AccountStore:
    def __init__(self, engine: Engine | None = None):
        self._table = accounts
        self._engine = engine
        self._lock = _lock_for_table(accounts.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is never cached here — resolved fresh via
        ``get_engine()`` on every call. This store is used as a long-lived MODULE
        singleton (``services.ledger.accounts``), constructed once at import time and
        shared for the process's whole lifetime; caching the lazily-resolved default
        would pin it to whichever engine happened to be live the FIRST time it was
        ever used, silently ignoring every later ``db_isolated``-style redirect
        (env var + ``engine.dispose_engine()``) — exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PayloadTableStore._use_conn`` exactly — see that module's CONN=
        COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _row(self, conn: Connection, id_val: str) -> dict | None:
        row = conn.execute(select(self._table).where(self._table.c.id == id_val)).first()
        return dict(row._mapping) if row else None

    def _next_seq(self, conn: Connection) -> int:
        return conn.execute(select(func.coalesce(func.max(self._table.c.seq), -1))).scalar_one() + 1

    def _write_existing(self, conn: Connection, id_val: str, row: dict) -> None:
        conn.execute(
            sa_update(self._table)
            .where(self._table.c.id == id_val)
            .values(**{k: v for k, v in row.items() if k != "id"})
        )

    # ── Query API (lock-free, mirrors JsonStore's read methods) ─────────────────

    def all(self, *, conn: Connection | None = None) -> list[dict]:
        if conn is not None:
            rows = [r.data for r in conn.execute(select(self._table).order_by(self._table.c.seq))]
            return _hydrate_institutions(rows, conn=conn)
        with self._eng().connect() as read_conn:
            rows = [
                r.data for r in read_conn.execute(select(self._table).order_by(self._table.c.seq))
            ]
        return _hydrate_institutions(rows)

    def find_by_id(self, id_val: str, *, conn: Connection | None = None) -> dict | None:
        """``conn`` reads THROUGH the caller's open transaction (``PayloadTableStore.find_by_id``'s
        contract). ``conn=None`` is a fresh, LOCK-FREE read — never ``_use_conn``, which takes
        ``self._lock``: a bare read under a choke already holding it would re-take the
        non-reentrant lock and hang (the transaction store's twin hung the live server,
        2026-09-21 20:50)."""
        if conn is not None:
            row = self._row(conn, id_val)
            return _hydrate_institutions([row["data"]], conn=conn)[0] if row else None
        with self._eng().connect() as read_conn:
            row = self._row(read_conn, id_val)
            return _hydrate_institutions([row["data"]], conn=read_conn)[0] if row else None

    def query(self, **filters: Any) -> list[dict]:
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    # ── Domain queries ────────────────────────────────────────────────────────

    def query_accounts(
        self,
        kind: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        include_archived: bool = False,
        *,
        conn: Connection | None = None,
    ) -> list[dict]:
        items = self.all(conn=conn)
        if not include_archived:
            items = [a for a in items if not a.get("archived")]
        if kind is not None:
            items = [a for a in items if a.get("kind") == kind]
        if type is not None:
            items = [a for a in items if a.get("type") == type]
        if currency is not None:
            cur = currency.upper()
            items = [a for a in items if (a.get("currency") or "").upper() == cur]
        return items

    # ── Write API (JsonStore parity) ─────────────────────────────────────────────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            fire_db_write_hook(self._table.name, [(id_val, None)], conn=conn)
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**_account_row(seq, item)))
        return item

    def bulk_insert(self, new_items: list[dict], *, conn: Connection | None = None) -> list[dict]:
        if not new_items:
            return new_items
        with self._use_conn(conn) as conn:
            fire_db_write_hook(
                self._table.name, [(item.get("id"), None) for item in new_items], conn=conn
            )
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table),
                [_account_row(seq + i, item) for i, item in enumerate(new_items)],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        id_val = cast(str, item.get("id"))
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            if existing is not None:
                self._write_existing(conn, id_val, _account_row(existing["seq"], item))
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**_account_row(seq, item)))
        return item

    def update_by_id(
        self, id_val: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            if "type" in changes:
                raise ValidationError(
                    "Account type is set when you create the account and cannot be changed. "
                    "Create a new account if it needs a different type."
                )
            if "currency" in changes:
                raise ValidationError(
                    "Account currency is set when you create the account and cannot be changed. "
                    "Create a new account if it needs a different currency."
                )
            if "color" in changes:
                raise ValidationError("Account colour has been removed and cannot be set.")
            if "platform" in changes:
                raise ValidationError("Account platform has been renamed to institution.")
            merged = {**existing["data"], **changes}
            for dead in ("asset_class", "locked", "on_budget", "group", "color"):
                merged.pop(dead, None)
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            self._write_existing(conn, id_val, _account_row(existing["seq"], merged))
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
        if not updates:
            return {}
        with self._use_conn(conn) as conn:
            existing_rows = {
                iid: row for iid in updates if (row := self._row(conn, iid)) is not None
            }
            patched: dict[str, dict] = {}
            hook_rows: list[tuple[str, dict | None]] = []
            for iid, changes in updates.items():
                if iid in existing_rows:
                    if "type" in changes:
                        raise ValidationError(
                            "Account type is set when you create the account and cannot be changed. "
                            "Create a new account if it needs a different type."
                        )
                    if "currency" in changes:
                        raise ValidationError(
                            "Account currency is set when you create the account and cannot be changed. "
                            "Create a new account if it needs a different currency."
                        )
                    if "color" in changes:
                        raise ValidationError("Account colour has been removed and cannot be set.")
                    if "platform" in changes:
                        raise ValidationError("Account platform has been renamed to institution.")
                    merged = {**existing_rows[iid]["data"], **changes}
                    for dead in ("asset_class", "locked", "on_budget", "group", "color"):
                        merged.pop(dead, None)
                    patched[iid] = merged
                    hook_rows.append((iid, existing_rows[iid]))
            if patched:
                fire_db_write_hook(self._table.name, hook_rows, conn=conn)
                for iid, merged in patched.items():
                    self._write_existing(conn, iid, _account_row(existing_rows[iid]["seq"], merged))
        return patched

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return False
            fire_db_write_hook(self._table.name, [(id_val, existing)], conn=conn)
            conn.execute(sa_delete(self._table).where(self._table.c.id == id_val))
        return True

    def replace_all(self, items: list[dict], *, conn: Connection | None = None) -> None:
        """Whole-store overwrite: afterward, ``self.all() == items`` (same order).

        PLAN 3 TASK 4 finding: diffs against current content instead of a blanket
        DELETE-then-INSERT-ALL. ``transactions.from_account_id``/``to_account_id`` are
        REAL ``ondelete="RESTRICT"`` foreign keys onto this table (task 2) — a blanket
        delete refuses outright the moment ANY live transaction still references an
        account id that is simply surviving into the new list unchanged (or lightly
        edited), which is the COMMON case for a caller rewriting most of the book with
        only a few rows actually touched (e.g.
        a one-shot migration (since removed) stripping legacy keys off
        otherwise-unchanged accounts, or any ``_normalize_accounts``-style test fixture).
        Reproduced directly via that script's own test suite. Only ids genuinely ABSENT
        from ``items`` are deleted; every id in ``items`` is an UPDATE (if it already
        existed) or an INSERT (if new) — so a surviving id's row is never transiently
        gone, and any FK still pointing at it never sees a gap. Hook/before-image
        semantics are unchanged (still one before-image per touched id, ``None`` for a
        genuinely new one) — only the SQL strategy that reaches the same end state
        changed."""
        with self._use_conn(conn) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            incoming_ids = {item.get("id") for item in items}
            hook_rows = list(existing_map.items())
            hook_rows += [(iid, None) for iid in incoming_ids if iid not in existing_map]
            fire_db_write_hook(self._table.name, hook_rows, conn=conn)
            removed_ids = [iid for iid in existing_map if iid not in incoming_ids]
            if removed_ids:
                conn.execute(self._table.delete().where(self._table.c.id.in_(removed_ids)))
            for i, item in enumerate(items):
                row = _account_row(i, item)
                if row["id"] in existing_map:
                    conn.execute(
                        sa_update(self._table)
                        .where(self._table.c.id == row["id"])
                        .values(**{k: v for k, v in row.items() if k != "id"})
                    )
                else:
                    conn.execute(sa_insert(self._table).values(**row))

    # ── Domain writes ─────────────────────────────────────────────────────────

    def create(self, data: dict, *, conn: Connection | None = None) -> dict:
        stored = self.insert(_make_account(data), conn=conn)
        return self.find_by_id(stored["id"], conn=conn) or stored


def _make_account(data: dict) -> dict:
    # base_currency is a pure profile read in core.fx — a store must not reach into
    # a service (RFC P1-A1 §3.6, clears the db→services violation).
    from backend.core.fx import base_currency

    type_ = data.get("type")
    if type_ not in TYPES:
        raise ValidationError(f"unknown account type {type_!r}; one of {sorted(TYPES)}")
    return {
        "id": data.get("id") or new_id("acc"),
        "name": data.get("name", ""),
        "kind": kind_of(type_),
        "type": type_,
        "institution_id": data.get("institution_id") or None,
        "currency": (data.get("currency") or base_currency()).upper(),
        # The account's opening is ONE fact: a pointer at its Starting Balance transaction.
        # Amount and tracking start are read off that row (services.ledger.opening_amount /
        # opening_date), never stored here. Written only by ledger.set_account_opening.
        "opening_txn_id": data.get("opening_txn_id"),
        "credit_limit": data.get("credit_limit"),
        "last_four": data.get("last_four", ""),
        "note": data.get("note", ""),
        "last_reconciled_at": data.get("last_reconciled_at"),
        "archived": bool(data.get("archived", False)),
        # Credit card statement cycle config (credit type only; None = unconfigured).
        "statement_close_day": data.get("statement_close_day") or None,
        "payment_due_day": data.get("payment_due_day") or None,
        "payment_due_date": data.get("payment_due_date") or None,
        "reminder_days_before": data.get("reminder_days_before") or None,
        # Accounts redesign (additive, default-empty; no migration). `connected` is a LEGACY
        # stored field, superseded by `external_ids` below — DERIVED at the real serializer
        # (services.ledger.account_with_balance: bool(external_ids), RFC
        # connectors-as-data-sources §D), never independently trusted; apr/principal/
        # payoff_date/min_payment = loan metadata; apy = savings/MMF metadata. METADATA
        # ONLY — none of these feed balance/net-worth math.
        "connected": bool(data.get("connected", False)),
        # Provider-account links (RFC connectors-as-data-sources §D) — a caller-supplied list
        # is already normalized by services.accounts before it reaches here (create_account/
        # update_account); a direct store call (tests, the migration backfill) passes the
        # canonical shape straight through.
        "external_ids": list(data.get("external_ids") or []),
        "apr": _opt_float(data.get("apr")),
        "principal": _opt_float(data.get("principal")),
        "payoff_date": data.get("payoff_date") or None,
        "min_payment": _opt_float(data.get("min_payment")),
        "apy": _opt_float(data.get("apy")),
        # Payment-envelope engine (ADR 2026-07-09): sync-writer-only FK to this card's
        # dedicated payment envelope. Set later via update_by_id by
        # services.planning.sync_card_payment_envelopes — never by a caller of create().
        "payment_envelope_id": data.get("payment_envelope_id") or None,
        # How an investment/retirement account is valued — "total" (Brokerage total) or
        # "holdings" (Live holdings); absent = not yet decided (services.account_value, ADR
        # 2026-11-10). Additive: written only when the caller sets it, read only while the
        # `investment_value_source` flag is on.
        **({"value_source": data["value_source"]} if data.get("value_source") else {}),
    }


def _hydrate_institutions(rows: list[dict], *, conn: Connection | None = None) -> list[dict]:
    if not rows:
        return rows
    from backend.db.institution_store import InstitutionStore

    names = InstitutionStore().names_by_id(conn=conn)
    return [{**row, "institution": names.get(row.get("institution_id"), "")} for row in rows]


def _opt_float(v) -> float | None:
    """Coerce an optional numeric metadata field — None/blank stays None (never 0.0)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
