"""PairingStore — pending chat-channel pairing requests (chat-pairing access policy).

When a chat channel runs the ``pairing`` access policy and an UNKNOWN sender messages the bot,
the seam (``connectors.chat.run.handle_inbound``) records a pending request here and replies the
sender a short **pairing CODE** ("you're not approved yet — ask the operator to approve code
ABC123"). The operator approves it in-app (``POST /api/chat-pairing/approve``) → that sender id
is appended to the channel's allowlist and the pending row is cleared. An unapproved sender's
message NEVER drives a real Lucius turn — only the code reply.

Shape: a flat keyed dict in ``chat_pairing.json``, keyed ``"<connector>:<sender_id>"`` so a
repeated message from the same unknown sender REFRESHES (doesn't pile up) — one pending row per
(connector, sender). Each row: ``{connector, sender_id, code, requested_at, sender_label}``.

It is RUNTIME state (a request queue), not user data: held in the ``chat_pairing`` table (WAVE
5D2b, JSON->DB — was ``chat_pairing.json``, written atomically + ``chmod 600`` as defence in
depth — a pairing code is a short-lived approval token a co-tenant shouldn't read; the table
holds no bearer credential, so it is NOT ``core.sensitive.SECRET_TABLES``), CAPPED (oldest
dropped on overflow so an unknown-sender flood can't grow it unbounded), and
**checkpoint-EXCLUDED** (see ``ai.checkpoint._policy._EXCLUDED_TABLES``) + **NON_RESTORABLE**
(``core.sensitive.NON_RESTORABLE_TABLES`` — an AI-turn restore must not resurrect a stale
pairing request the operator already approved/rejected past). Data-only repository
(import-linter: ``db`` may import ``core``/``models`` only).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import chat_pairing

MAX_PENDING = 500  # cap so an unknown-sender flood can't grow the table unbounded

# Pairing-code alphabet: unambiguous uppercase + digits (no 0/O/1/I/L) — short, human-readable,
# spoken/typed across to the operator. NOT a secret credential (the operator approval is the
# real gate); it just disambiguates which pending sender to approve.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def _key(connector: str, sender_id: str) -> str:
    return f"{connector}:{sender_id}"


class PairingStore(PayloadTableStore):
    def __init__(self, engine=None):
        # id_field documentation only — every method below is hand-rolled against
        # _row/_use_conn directly (mirrors SkillStateStore's own comment): a pairing
        # entry's key is "<connector>:<sender_id>", never a field inside its own payload.
        super().__init__(chat_pairing, id_field="id", engine=engine)

    # ── raw read / write (WAVE 5D2b: DB-backed; was atomic_write_text + chmod 600) ─────

    def _read_raw(self) -> dict:
        with self._eng().connect() as conn:
            return {
                r.id: r.payload
                for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            }

    def _write_raw(self, items: dict) -> None:
        """Whole-dict replace, in ONE transaction — mirrors
        ``SkillStateStore.replace_all_keyed``'s wipe-and-reinsert shape exactly (each
        entry's id is the DICT KEY — "<connector>:<sender_id>" — never a field inside its
        own payload). Non-atomic ACROSS separate ``_read_raw``/``_write_raw`` calls, same as
        the pre-migration file store (no cross-call lock there either — only this one
        replace itself is transactional)."""
        with self._use_conn(None) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            hook_rows = list(existing_map.items())
            hook_rows += [(k, None) for k in items if k not in existing_map]
            self._fire_hook(conn, hook_rows)
            conn.execute(self._table.delete())
            if items:
                conn.execute(
                    sa_insert(self._table),
                    [self._insert_row(i, k, v) for i, (k, v) in enumerate(items.items())],
                )

    # ── API ────────────────────────────────────────────────────────────────────

    def record(self, connector: str, sender_id: str, *, sender_label: str = "") -> dict:
        """Record (or REFRESH) a pending pairing request for ``(connector, sender_id)``.

        Idempotent per sender: a repeated message from the same unknown sender returns the SAME
        existing code (so the operator sees one stable code, and the sender isn't given a new code
        every message) — only ``requested_at``/``sender_label`` are refreshed. A brand-new sender
        gets a freshly minted code. Returns the row (incl. ``code``)."""
        items = self._read_raw()
        key = _key(connector, sender_id)
        existing = items.get(key)
        code = (
            existing["code"]
            if isinstance(existing, dict) and existing.get("code")
            else _make_code()
        )
        # Cap: if we're about to add a NEW row over the cap, drop the oldest first.
        if key not in items and len(items) >= MAX_PENDING:
            oldest = sorted(items.items(), key=lambda kv: kv[1].get("requested_at", ""))
            for k, _ in oldest[: len(items) - MAX_PENDING + 1]:
                items.pop(k, None)
        row = {
            "connector": connector,
            "sender_id": str(sender_id),
            "code": code,
            "requested_at": _now(),
            "sender_label": str(sender_label or ""),
        }
        items[key] = row
        self._write_raw(items)
        return row

    def list_pending(self, connector: Optional[str] = None) -> list[dict]:
        """All pending requests (optionally filtered to one ``connector``), newest first."""
        rows = list(self._read_raw().values())
        if connector:
            rows = [r for r in rows if r.get("connector") == connector]
        return sorted(rows, key=lambda r: r.get("requested_at", ""), reverse=True)

    def get(self, connector: str, sender_id: str) -> Optional[dict]:
        """The pending row for ``(connector, sender_id)`` or ``None``."""
        return self._read_raw().get(_key(connector, sender_id))

    def clear(self, connector: str) -> int:
        """Drop every pending row for ``connector`` (it was removed — a re-added bot must not
        show the old bot's requests). Returns how many were dropped."""
        items = self._read_raw()
        keep = {k: v for k, v in items.items() if v.get("connector") != connector}
        dropped = len(items) - len(keep)
        if dropped:
            self._write_raw(keep)
        return dropped

    def remove(self, connector: str, sender_id: str) -> bool:
        """Drop the pending row for ``(connector, sender_id)`` (after approve/reject). Returns
        True if a row was removed."""
        items = self._read_raw()
        if items.pop(_key(connector, sender_id), None) is None:
            return False
        self._write_raw(items)
        return True
