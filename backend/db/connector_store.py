"""ConnectorStore — one row per connector, secrets in a separate locked-down table.

``connectors`` is an ordinary table, kept in a sanitised backup: ``config`` is what the user typed,
``state`` is what the app wrote (last sync, cursors, an MCP server's advertised tools, price-source
quota and circuit). Nothing in either is a credential — an MCP server's
``url``/``command``/``args``/``env`` embed credentials routinely (``postgresql://u:pw@…``,
``?api_key=``), so they live in ``connector_secrets`` with every other secret, as does Telegram's
chat map (the hidden secret ``sessions``). ``connector_secrets`` is in
``core.sensitive.SECRET_TABLES``: SQL-denied, stripped from a sanitised backup. ``connectors`` is
SQL-denied too (``db.sql_console``): it holds raw server-chosen MCP text and personal settings.
Both tables are checkpoint-excluded and non-restorable; connectors are instance-global (no
``book_id``). RFC ``connectors-table``.

One rule for every secret write, everywhere (``set_secrets``): a blank string KEEPS the saved
value, ``None`` CLEARS the field, anything else sets it. ``state.last_error`` / ``state.error``
are redacted at write time (``core.redact_error``) because an HTTP client's error text can quote
a credential-bearing URL.

Every mutation is ONE transaction through ``db.engine.locked_unit_of_work`` over both tables
(per-table locks in alphabetical order), plus a row-scoped advisory lock on Postgres so two
workers cannot interleave a read-modify-write of one connector. A caller already inside a unit
of work passes its own ``conn=`` and the store composes into it — that caller must have opened
it with ``serialized_unit_of_work()`` (or a store's ``transaction()``) so the table locks and the
database writer lock are held.

Data access only (``db`` imports ``core``): what a connector kind is, which fields are secret,
and when it counts as configured live in ``services/connector_catalog.py``.
"""

from __future__ import annotations

import builtins
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from sqlalchemy import Connection, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.errors import ConflictError, NotFoundError
from backend.core.redact_error import redact_error
from backend.db.engine import get_engine, locked_unit_of_work
from backend.db.tables import connector_secrets, connectors

# State keys whose string value is an error message that may quote a credential-bearing URL.
_ERROR_KEYS = ("last_error", "error", "install_error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_blank(value: Any) -> bool:
    """No information: None, an empty/whitespace string, an empty list/dict."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _merge(base: dict, changes: dict) -> dict:
    merged = dict(base)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


@contextmanager
def serialized_unit_of_work() -> Iterator[Connection]:
    """``locked_unit_of_work`` over both connector tables, serialised across processes.

    The in-process table locks only cover one process. SQLite therefore takes its database
    writer lock before the first read (``BEGIN IMMEDIATE``, the same seam
    ``NamespacedSettingsStore`` uses) — otherwise two workers could both read a state, both
    modify it and the second write would silently replace the first. Postgres is serialised
    per connector by the row advisory lock ``ConnectorStore`` takes on every mutation.
    """
    with locked_unit_of_work(connectors, connector_secrets) as conn:
        if conn.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        yield conn


def _row(raw: Any) -> dict:
    row = dict(raw._mapping)
    row["enabled"] = bool(row["enabled"])
    return row


class ConnectorStore:
    # `list` below shadows the builtin inside this class body, so later annotations say `builtins.list`.
    # ── reads ─────────────────────────────────────────────────────────────────

    def list(self, kind: Optional[str] = None, *, conn: Optional[Connection] = None) -> list[dict]:
        """Every connector (optionally one kind), oldest first. Rows never carry secrets."""
        stmt = select(connectors).order_by(connectors.c.created_at, connectors.c.id)
        if kind is not None:
            stmt = stmt.where(connectors.c.kind == kind)
        with self._reader(conn) as c:
            return [_row(r) for r in c.execute(stmt)]

    def get(self, connector_id: str, *, conn: Optional[Connection] = None) -> Optional[dict]:
        with self._reader(conn) as c:
            return self._load(c, connector_id)

    def enabled_flags(self, *, conn: Optional[Connection] = None) -> builtins.list[dict]:
        """``[{id, kind, enabled}]`` for every connector, oldest first — the lean read for a hot
        path that only needs to know what is switched on (no ``config``/``state`` JSON parsed)."""
        stmt = select(connectors.c.id, connectors.c.kind, connectors.c.enabled).order_by(
            connectors.c.created_at, connectors.c.id
        )
        with self._reader(conn) as c:
            return [_row(r) for r in c.execute(stmt)]

    def secrets(self, connector_id: str, *, conn: Optional[Connection] = None) -> dict:
        """The connector's stored credentials (``{}`` when it has none). For services that dial
        out and for the operator's reveal path — never for a view, a tool result or a log."""
        with self._reader(conn) as c:
            return self._load_secrets(c, connector_id)

    def secrets_by_kind(self, kind: str, *, conn: Optional[Connection] = None) -> dict[str, dict]:
        """``{connector_id: credentials}`` for every connector of ``kind`` that has any, in ONE
        query — for a list that needs each row's secrets without a read per row. Same caveat as
        ``secrets()``: for services that dial out or build an operator-only projection."""
        stmt = (
            select(connector_secrets.c.connector_id, connector_secrets.c.secrets)
            .join(connectors, connectors.c.id == connector_secrets.c.connector_id)
            .where(connectors.c.kind == kind)
        )
        with self._reader(conn) as c:
            return {cid: dict(secrets) for cid, secrets in c.execute(stmt)}

    def secret_fields(self, connector_id: str, *, conn: Optional[Connection] = None) -> set[str]:
        """Names of the secrets that hold a value — what a view may say (``has_<field>``)."""
        return {k for k, v in self.secrets(connector_id, conn=conn).items() if not _is_blank(v)}

    def secret_field_map(self, *, conn: Optional[Connection] = None) -> dict[str, frozenset[str]]:
        """``{connector_id: names of the secrets holding a value}`` for every connector, in ONE
        query — what a list view needs without a read per row. Values are discarded here."""
        with self._reader(conn) as c:
            rows = c.execute(
                select(connector_secrets.c.connector_id, connector_secrets.c.secrets)
            ).all()
        return {
            cid: frozenset(k for k, v in secrets.items() if not _is_blank(v))
            for cid, secrets in rows
        }

    # ── row lifecycle ─────────────────────────────────────────────────────────

    def create(
        self,
        connector_id: str,
        kind: str,
        name: str,
        *,
        config: Optional[dict] = None,
        state: Optional[dict] = None,
        enabled: bool = False,
        conn: Optional[Connection] = None,
    ) -> dict:
        with self._writer(connector_id, conn) as c:
            if self._load(c, connector_id) is not None:
                raise ConflictError(f"connector {connector_id!r} already exists")
            return self._insert(c, connector_id, kind, name, config, state, enabled)

    def ensure(
        self,
        connector_id: str,
        kind: str,
        name: str,
        *,
        config: Optional[dict] = None,
        enabled: bool = False,
        conn: Optional[Connection] = None,
    ) -> dict:
        """The connector's row, created first (disabled and empty unless ``config`` / ``enabled``
        say otherwise) if it is absent. An existing row is returned untouched."""
        with self._writer(connector_id, conn) as c:
            existing = self._load(c, connector_id)
            if existing is None:
                return self._insert(c, connector_id, kind, name, config, None, enabled)
            if existing["kind"] != kind:
                raise ConflictError(
                    f"connector {connector_id!r} is a {existing['kind']!r}, not a {kind!r}"
                )
            return existing

    def delete(self, connector_id: str, *, conn: Optional[Connection] = None) -> bool:
        """Remove the connector and its secrets. False when it did not exist."""
        with self._writer(connector_id, conn) as c:
            c.execute(
                sa_delete(connector_secrets).where(connector_secrets.c.connector_id == connector_id)
            )
            result = c.execute(sa_delete(connectors).where(connectors.c.id == connector_id))
            return result.rowcount > 0

    # ── connectors row ────────────────────────────────────────────────────────

    def patch_config(
        self, connector_id: str, changes: dict, *, conn: Optional[Connection] = None
    ) -> dict:
        """Merge ``changes`` into ``config``; a key whose value is ``None`` is removed."""
        return self._update(
            connector_id, conn, lambda row: {"config": _merge(row["config"], changes)}
        )

    def replace_config(
        self, connector_id: str, config: dict, *, conn: Optional[Connection] = None
    ) -> dict:
        return self._update(connector_id, conn, lambda row: {"config": dict(config)})

    def set_enabled(
        self, connector_id: str, enabled: bool, *, conn: Optional[Connection] = None
    ) -> dict:
        return self._update(connector_id, conn, lambda row: {"enabled": bool(enabled)})

    def set_name(self, connector_id: str, name: str, *, conn: Optional[Connection] = None) -> dict:
        return self._update(connector_id, conn, lambda row: {"name": name})

    def merge_state(
        self, connector_id: str, changes: dict, *, conn: Optional[Connection] = None
    ) -> dict:
        """Merge ``changes`` into ``state``; ``None`` removes a key. ``last_error`` / ``error``
        string values are redacted before they are stored."""
        safe = {
            k: redact_error(v) if k in _ERROR_KEYS and isinstance(v, str) else v
            for k, v in changes.items()
        }
        return self._update(connector_id, conn, lambda row: {"state": _merge(row["state"], safe)})

    def update_state(
        self,
        connector_id: str,
        change: Callable[[dict], dict],
        *,
        conn: Optional[Connection] = None,
    ) -> dict:
        """Atomic read-modify-write of ``state``: ``change`` receives a copy of the saved state
        and returns the state to store (``last_error`` / ``error`` redacted like ``merge_state``).
        For app-written documents whose next value depends on the previous one — a quota's
        rolling window, a circuit's failure streak — where a separate read and merge could lose
        a concurrent writer's update."""

        def apply(row: dict) -> dict:
            new = change(dict(row["state"]))
            return {
                "state": {
                    k: redact_error(v) if k in _ERROR_KEYS and isinstance(v, str) else v
                    for k, v in new.items()
                }
            }

        return self._update(connector_id, conn, apply)

    # ── secrets ───────────────────────────────────────────────────────────────

    def set_secrets(
        self, connector_id: str, changes: dict, *, conn: Optional[Connection] = None
    ) -> set[str]:
        """Apply a secrets patch and return the names that now hold a value.

        Per field: a blank string KEEPS the saved value, ``None`` CLEARS it, anything else sets
        it. An empty list/dict sets the field empty, which removes it (an empty value carries
        nothing a reader could use). Values are stored as given — trimming is the caller's job,
        a PEM key's whitespace is data.
        """
        with self._writer(connector_id, conn) as c:
            self._require(c, connector_id)
            merged = self._load_secrets(c, connector_id)
            for key, value in changes.items():
                if isinstance(value, str):
                    if not _is_blank(value):
                        merged[key] = value
                elif _is_blank(value):
                    merged.pop(key, None)
                else:
                    merged[key] = value
            self._store_secrets(c, connector_id, merged)
            return set(merged)

    def replace_secrets(
        self, connector_id: str, secrets: dict, *, conn: Optional[Connection] = None
    ) -> set[str]:
        """Replace the whole secrets set (reset, restore-from-Move). Blank values are dropped."""
        with self._writer(connector_id, conn) as c:
            self._require(c, connector_id)
            kept = {k: v for k, v in secrets.items() if not _is_blank(v)}
            self._store_secrets(c, connector_id, kept)
            return set(kept)

    # ── internals ─────────────────────────────────────────────────────────────

    @contextmanager
    def _reader(self, conn: Optional[Connection]) -> Iterator[Connection]:
        if conn is not None:
            yield conn
            return
        with get_engine().connect() as c:
            yield c

    @contextmanager
    def _writer(self, connector_id: str, conn: Optional[Connection]) -> Iterator[Connection]:
        """The transaction a mutation runs in: the caller's own ``conn``, else a fresh
        ``locked_unit_of_work`` over both tables. The connector's row lock is taken either way
        (a Postgres advisory transaction lock is re-entrant within one session)."""
        if conn is not None:
            self.lock_row(conn, connector_id)
            yield conn
            return
        with serialized_unit_of_work() as c:
            self.lock_row(c, connector_id)
            yield c

    @staticmethod
    def lock_row(conn: Connection, connector_id: str) -> None:
        """Postgres: serialise writers of one connector across workers (the in-process table
        locks only cover one process). SQLite's writer lock is taken by ``serialized_unit_of_work``.
        A caller that READS before it writes (a compare-and-swap) calls this first, so the read
        happens under the lock."""
        if conn.dialect.name != "postgresql":
            return
        digest = hashlib.blake2b(f"connectors:{connector_id}".encode(), digest_size=8).digest()
        key = int.from_bytes(digest, "big", signed=True)
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def _load(conn: Connection, connector_id: str) -> Optional[dict]:
        raw = conn.execute(select(connectors).where(connectors.c.id == connector_id)).first()
        return _row(raw) if raw is not None else None

    @staticmethod
    def _load_secrets(conn: Connection, connector_id: str) -> dict:
        raw = conn.execute(
            select(connector_secrets.c.secrets).where(
                connector_secrets.c.connector_id == connector_id
            )
        ).first()
        return dict(raw[0]) if raw is not None else {}

    def _require(self, conn: Connection, connector_id: str) -> dict:
        row = self._load(conn, connector_id)
        if row is None:
            raise NotFoundError(f"connector {connector_id!r} not found")
        return row

    @staticmethod
    def _insert(
        conn: Connection,
        connector_id: str,
        kind: str,
        name: str,
        config: Optional[dict],
        state: Optional[dict],
        enabled: bool,
    ) -> dict:
        now = _now()
        values = {
            "id": connector_id,
            "kind": kind,
            "name": name,
            "enabled": bool(enabled),
            "config": dict(config or {}),
            "state": dict(state or {}),
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(sa_insert(connectors).values(**values))
        return values

    def _update(self, connector_id: str, conn: Optional[Connection], change) -> dict:
        with self._writer(connector_id, conn) as c:
            row = self._require(c, connector_id)
            values = {**change(row), "updated_at": _now()}
            c.execute(sa_update(connectors).where(connectors.c.id == connector_id).values(**values))
            return {**row, **values}

    @staticmethod
    def _store_secrets(conn: Connection, connector_id: str, secrets: dict) -> None:
        if not secrets:
            conn.execute(
                sa_delete(connector_secrets).where(connector_secrets.c.connector_id == connector_id)
            )
            return
        now = _now()
        updated = conn.execute(
            sa_update(connector_secrets)
            .where(connector_secrets.c.connector_id == connector_id)
            .values(secrets=secrets, updated_at=now)
        )
        if updated.rowcount == 0:
            conn.execute(
                sa_insert(connector_secrets).values(
                    connector_id=connector_id, secrets=secrets, updated_at=now
                )
            )
