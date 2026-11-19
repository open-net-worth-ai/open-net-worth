"""McpOAuthStore — data access for the inbound MCP OAuth server (flag ``mcp_oauth``).

Four tables (``db/tables.py``): owner-created clients, staged requests/codes, grants and hashed
tokens. Data access only — every decision (PKCE, expiry, rotation, reuse) is made by
``services.mcp_oauth``. Multi-step changes run inside ``transaction()``, which holds all four
tables' locks in the global order (``locked_unit_of_work``) so a refresh and a revoke can't
interleave. Every table is SECRET and checkpoint-excluded, so nothing here fires a write hook.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Connection, delete, insert, select, update

from backend.db.engine import get_engine, locked_unit_of_work
from backend.db.tables import (
    mcp_oauth_clients,
    mcp_oauth_grants,
    mcp_oauth_pending,
    mcp_oauth_tokens,
)

_ALL = (mcp_oauth_clients, mcp_oauth_grants, mcp_oauth_pending, mcp_oauth_tokens)


def _row(result) -> Optional[dict]:
    row = result.mappings().first()
    return dict(row) if row is not None else None


class McpOAuthStore:
    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with locked_unit_of_work(*_ALL) as conn:
            yield conn

    @contextmanager
    def _conn(self, conn: Optional[Connection]) -> Iterator[Connection]:
        if conn is not None:
            yield conn
            return
        with self.transaction() as own:
            yield own

    # ── clients ────────────────────────────────────────────────────────────────
    def insert_client(self, row: dict, conn: Optional[Connection] = None) -> None:
        with self._conn(conn) as c:
            c.execute(insert(mcp_oauth_clients).values(**row))

    def get_client(self, client_id: str) -> Optional[dict]:
        with get_engine().connect() as c:
            return _row(
                c.execute(
                    select(mcp_oauth_clients).where(mcp_oauth_clients.c.client_id == client_id)
                )
            )

    def list_clients(self) -> list[dict]:
        with get_engine().connect() as c:
            rows = c.execute(
                select(mcp_oauth_clients)
                .where(mcp_oauth_clients.c.revoked_at.is_(None))
                .order_by(mcp_oauth_clients.c.created_at)
            )
            return [dict(r) for r in rows.mappings()]

    def revoke_client(self, client_id: str, now: str) -> bool:
        with self.transaction() as c:
            res = c.execute(
                update(mcp_oauth_clients)
                .where(mcp_oauth_clients.c.client_id == client_id)
                .where(mcp_oauth_clients.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            return res.rowcount > 0

    # ── pending requests and codes ─────────────────────────────────────────────
    def insert_pending(self, row: dict, conn: Optional[Connection] = None) -> None:
        with self._conn(conn) as c:
            c.execute(insert(mcp_oauth_pending).values(**row))

    def get_pending(
        self, id_hash: str, kind: str, conn: Optional[Connection] = None
    ) -> Optional[dict]:
        stmt = select(mcp_oauth_pending).where(
            mcp_oauth_pending.c.id_hash == id_hash, mcp_oauth_pending.c.kind == kind
        )
        if conn is not None:
            return _row(conn.execute(stmt))
        with get_engine().connect() as c:
            return _row(c.execute(stmt))

    def mark_pending_used(self, id_hash: str, now: str, conn: Connection) -> None:
        conn.execute(
            update(mcp_oauth_pending)
            .where(mcp_oauth_pending.c.id_hash == id_hash)
            .values(used_at=now)
        )

    def purge_pending(self, before: str, conn: Connection) -> None:
        conn.execute(delete(mcp_oauth_pending).where(mcp_oauth_pending.c.expires_at < before))

    # ── grants ─────────────────────────────────────────────────────────────────
    def insert_grant(self, row: dict, conn: Connection) -> None:
        conn.execute(insert(mcp_oauth_grants).values(**row))

    def get_grant(self, grant_id: str, conn: Optional[Connection] = None) -> Optional[dict]:
        stmt = select(mcp_oauth_grants).where(mcp_oauth_grants.c.grant_id == grant_id)
        if conn is not None:
            return _row(conn.execute(stmt))
        with get_engine().connect() as c:
            return _row(c.execute(stmt))

    def list_connected_grants(self) -> list[dict]:
        """Live grants an app actually redeemed (it holds a token). A consent whose code was never
        exchanged — an abandoned sign-in — is not a connected app."""
        from sqlalchemy import exists

        has_token = exists().where(mcp_oauth_tokens.c.grant_id == mcp_oauth_grants.c.grant_id)
        with get_engine().connect() as c:
            rows = c.execute(
                select(mcp_oauth_grants)
                .where(mcp_oauth_grants.c.revoked_at.is_(None))
                .where(has_token)
                .order_by(mcp_oauth_grants.c.created_at)
            )
            return [dict(r) for r in rows.mappings()]

    def list_live_grants(self) -> list[dict]:
        with get_engine().connect() as c:
            rows = c.execute(
                select(mcp_oauth_grants)
                .where(mcp_oauth_grants.c.revoked_at.is_(None))
                .order_by(mcp_oauth_grants.c.created_at)
            )
            return [dict(r) for r in rows.mappings()]

    def revoke_grant(self, grant_id: str, reason: str, now: str, conn: Connection) -> bool:
        res = conn.execute(
            update(mcp_oauth_grants)
            .where(mcp_oauth_grants.c.grant_id == grant_id)
            .where(mcp_oauth_grants.c.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )
        conn.execute(delete(mcp_oauth_tokens).where(mcp_oauth_tokens.c.grant_id == grant_id))
        return res.rowcount > 0

    def revoke_all(self, reason: str, now: str, conn: Optional[Connection] = None) -> int:
        """Revoke every live grant and delete every token. Safe to call when nothing exists."""
        with self._conn(conn) as c:
            res = c.execute(
                update(mcp_oauth_grants)
                .where(mcp_oauth_grants.c.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason=reason)
            )
            c.execute(delete(mcp_oauth_tokens))
            c.execute(delete(mcp_oauth_pending))
            return res.rowcount

    def revoke_all_if_present(self, reason: str, now: str) -> int:
        """``revoke_all`` for callers that may run against a database migrated before these
        tables existed (``onw reset-password`` on an older book): no table means no grant."""
        from sqlalchemy import inspect

        with get_engine().connect() as c:
            if not inspect(c).has_table(mcp_oauth_grants.name):
                return 0
        return self.revoke_all(reason, now)

    def touch_grant(self, grant_id: str, now: str) -> None:
        with self.transaction() as c:
            c.execute(
                update(mcp_oauth_grants)
                .where(mcp_oauth_grants.c.grant_id == grant_id)
                .values(last_used_at=now)
            )

    # ── tokens ─────────────────────────────────────────────────────────────────
    def insert_token(self, row: dict, conn: Connection) -> None:
        conn.execute(insert(mcp_oauth_tokens).values(**row))

    def get_token(self, token_hash: str, conn: Optional[Connection] = None) -> Optional[dict]:
        stmt = select(mcp_oauth_tokens).where(mcp_oauth_tokens.c.token_hash == token_hash)
        if conn is not None:
            return _row(conn.execute(stmt))
        with get_engine().connect() as c:
            return _row(c.execute(stmt))

    def mark_rotated(self, token_hash: str, replaced_by: str, now: str, conn: Connection) -> None:
        conn.execute(
            update(mcp_oauth_tokens)
            .where(mcp_oauth_tokens.c.token_hash == token_hash)
            .values(rotated_at=now, replaced_by_hash=replaced_by)
        )

    def delete_token(self, token_hash: str, conn: Connection) -> None:
        conn.execute(delete(mcp_oauth_tokens).where(mcp_oauth_tokens.c.token_hash == token_hash))
