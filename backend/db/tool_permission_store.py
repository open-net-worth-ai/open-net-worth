"""ToolPermissionStore — THE permission table: one level per tool, for every tool in
the app (Connectors redesign §4, design handoff §10; approval model RFC 2026-07-20 §16).

One row per tool in the app, each holding exactly ONE level:

    {"id": "plaid:plaid_sync_now", "connector_id": "plaid", "tool": "plaid_sync_now",
     "level": "always_allow", "source": "seed", "updated_at": iso}

## One store, because two answers to one question is the bug

Until 2026-08-14 there were TWO: this table (three levels, per connector, written by the
Connectors ladder, consulted ONLY by external-MCP dispatch) and `tool_grants` (binary
grant / no grant, per tool name, written by the confirm card + Settings › Permissions,
consulted for everything else). Both answered "may this tool run"; they could disagree,
and nothing told the user which had won. `tool_grants` is now ABSORBED here and deleted
(revision 0040) — a stored `scope: "always"` grant became a `level: "always_allow"` row,
and an absent grant was left absent because absence is not a decision. See ADR
`2026-08-14-one-permission-store-for-every-tool`.

So `connector_id` is the tool's OWNER, not necessarily a connector:

  * a tool carrying `@tool(connector="<id>")` -> that id (`plaid`, `web_search:tavily`);
  * an external-MCP tool -> `mcp:<server_id>` (`services.connectors`' own namespacing);
  * everything else -> the reserved bucket `services.tool_permissions.CORE_CONNECTOR_ID`
    (`"core"`) — `add_transactions`, the ~160 unscoped write tools (`email_send_message` is
    NOT one of these — it is `connector="email"`-owned, see `services.tool_permissions`).

The composite key is what keeps a decision from leaking sideways. Two MCP servers may
each advertise a tool called `search`; `plaid_sync_now` and `ibkr_sync_now` are distinct
rows under distinct owners. `tool_grants`' tool-name-only key could express neither —
which is why THIS table survived the merge and that one did not (and why
`ai/tools/connectors.py` split the shared `connector_sync` name apart in the first
place: one name meant one grant covering every provider).

## The shape, and why it is the whole design

Section 10 of the handoff names the storage shape as the thing to get right: *per
connector, per tool, with the group aggregate DERIVED and never written*. This table
holds exactly one level per (owner, tool) pair and has nowhere to put a group
level — "Custom" is computed where it is displayed (a group whose tools disagree) and
can therefore never drift from the tools underneath it, because there is no second
record to drift.

A group-level pick is `set_levels()`: a deliberately LOSSY stamp of one level onto
every named tool, in ONE transaction. Lossy is the specified behaviour ("this is the
'make this whole group behave' control"), and one transaction is what keeps it honest —
a half-applied stamp would leave the group non-uniform, i.e. reading *Custom*, which is
precisely the state the user just asked to leave.

## Every tool has a row, and a row remembers whether it is still the seed

A tool appears -> it is SEEDED (`sync`, called by `services.tool_permissions.sync_seeds` at boot
for the registry's tools and for an external MCP server's discovered tools) with a meaningful
level: a read runs, a change or a send asks (`services.tool_permissions.seed_level`), and an
external MCP tool asks. There is no "undecided" state on any screen, and one value per tool is
the whole model (ADR `2026-10-11-permissions-are-one-value-per-tool`).

`source` is `"seed"` or `"user"` and is never shown. A SEED row follows a changed seed rule on
the next boot sync and is removed when its tool no longer exists; a USER row (anything the owner
set, or any row without the marker) is never rewritten by a sync. Reset puts the seed value back
and the row becomes `"seed"` again (`set_many`). A row is written with `"user"` unless the caller
says otherwise, so a stray write cannot masquerade as a default.

`always_allow` is a decision; `blocked` is a decision; `needs_approval` ("Ask") is the seed for
a change. Dispatch still falls back to the seed rule for a registry tool that has no row yet (a
test, a tool added at runtime, a freshly wiped table before the next sync) so a missing row is
never read as permission and never as a refusal.

## Ids

`id` is the deterministic surrogate `f"{connector_id}:{tool}"` — `budget_assignments`'
precedent (`f"{book_id}:{month}:{category}"`), for the same reason: a keyed relation's
pk composed of the things that identify it makes "set this pair's level" a plain upsert
and makes two rows for one pair structurally impossible. Both components are kept in
the payload too, so nothing has to parse the key; and a tool name is a Python
identifier while a connector id never contains "/", so the pair is recoverable from the
id alone via one `rsplit(":", 1)` if it ever needs to be. The reserved YOLO row
(`services.tool_permissions.YOLO_TOOL`, `"core:*"`) rides the same shape — a real tool
can never collide with it, because tool names are identifiers.

## The two flags every store must answer

**Checkpoint-EXCLUDED** (`ai.checkpoint._policy._EXCLUDED_TABLES`) — the absorbed
`tool_grants`' reasoning, load-bearing rather than tidy: `restore_checkpoint` is a
MODEL-CALLABLE tool carrying `mutates=("*",)`, so a checkpoint-INCLUDED permission table
would mean *user blocks a tool -> the model restores any earlier checkpoint -> the block
is gone*. That is privilege escalation through an unrelated tool, no malice required.
A checkpoint undoes what Lucius did to your DATA; a permission is not something Lucius
did, it is something the owner decided. Accepted cost, stated plainly: a level SURVIVES
a `/rewind`, so undoing a rash session leaves the permission changed in it standing.

**NON_RESTORABLE** (`core.sensitive.NON_RESTORABLE_TABLES`) — the same argument against
a disaster-recovery restore: revoke `always_allow` -> restore a backup taken before the
revocation -> the tool is auto-firing again. `services.backup` captures this table's
live rows across the `onw.db` swap instead.

**NOT portable, NOT secret.** Absent from `core.portable.PORTABLE_FILES` and from
`db.portable_bridge.BRIDGED`: "I trust THIS install to let Lucius place orders
unattended" is instance-local trust and must not travel to another machine inside a
backup. It holds no credential, so it is not a `SECRET_TABLES` member and needs no
chmod-600-equivalent; security-relevant but not confidential. `/api/reset` DOES wipe it
(`portable_bridge._wipe_tool_permissions`) — "reset means reset"; the caller re-seeds
(`services.portability`), so every tool is back at its seed value.

All three classifications are inherited UNCHANGED from both merged predecessors: they
were identical (that is stated verbatim in `tool_grants`' own retired docstring), which
is one more sign these were always one store wearing two names.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import tool_permissions

logger = logging.getLogger(__name__)

# The three stored levels. There is deliberately no fourth: "Custom" is a DERIVED group
# label (a group whose tools disagree), computed for display and never written — see the
# module docstring. Defined here, beside the table, exactly as `tool_grant_store`'s
# VALID_SCOPES is; `services.tool_permissions` re-exports it as the caller-facing name.
LEVELS: tuple[str, ...] = ("always_allow", "needs_approval", "blocked")

# Who last wrote a row. "seed" = the app's default, which a sync may move; "user" = a decision.
SEED = "seed"
USER = "user"
SOURCES: tuple[str, ...] = (SEED, USER)

PermissionKey = tuple[str, str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def permission_key(connector_id: str, tool: str) -> str:
    """This pair's row id — see the module docstring's Ids section."""
    return f"{connector_id}:{tool}"


class ToolPermissionStore(PayloadTableStore):
    """Flat list of permission rows, one per (connector, tool) pair."""

    def __init__(self, engine=None):
        super().__init__(tool_permissions, engine=engine)

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_row(self, connector_id: str, tool: str) -> Optional[dict]:
        """This pair's whole row, or ``None`` when there is no row (a tool not yet seeded)."""
        return self.find_by_id(permission_key(connector_id, tool))

    def get_level(self, connector_id: str, tool: str) -> Optional[str]:
        """This pair's STORED level, or ``None`` when it has no row.

        ``None`` is not "denied" and not "allowed" — it is *not seeded yet*, and the caller
        (`services.tool_permissions.stored_tool_level`) decides what that means: a registry
        tool takes its seed rule, an external MCP tool takes `DEFAULT_LEVEL`.
        """
        row = self.get_row(connector_id, tool)
        return row.get("level") if row else None

    def for_connector(self, connector_id: str) -> list[dict]:
        """Every stored row for one connector, in insertion order.

        A Python-side equality filter over `all()` (`PayloadTableStore.query`'s
        documented shape — no JSON-path SQL). This table is bounded by
        connectors x tools (a few hundred rows), and the hot path (one tool's level, on
        dispatch) is a primary-key lookup, not this scan.
        """
        return self.query(connector_id=connector_id)

    # ── Writes ────────────────────────────────────────────────────────────────

    def set_level(self, connector_id: str, tool: str, level: str, *, source: str = USER) -> dict:
        """Record ONE tool's level. Re-setting the same pair REPLACES its row."""
        with self._use_conn(None) as conn:
            return self._stamp(conn, connector_id, tool, level, source)

    def set_levels(
        self, connector_id: str, tools: list[str], level: str, *, source: str = USER
    ) -> list[dict]:
        """Stamp ``level`` onto every named tool in ONE transaction — the group control.

        Deliberately lossy: an existing per-tool override is overwritten, not merged.
        Atomic because a half-applied stamp is a non-uniform group, i.e. the *Custom*
        state the user just acted to leave.

        Duplicate names in ``tools`` collapse onto one row (same key, upserted twice) —
        harmless, and cheaper than making every caller de-duplicate a list it assembled
        from a UI group.
        """
        if not tools:
            return []
        with self._use_conn(None) as conn:
            return [self._stamp(conn, connector_id, tool, level, source) for tool in tools]

    def set_many(self, rows: Mapping[PermissionKey, tuple[str, str]]) -> None:
        """Write ``{(owner, tool): (level, source)}`` unconditionally, in ONE transaction.

        The Reset path: it overwrites a user row with the seed value and marks it ``"seed"``
        again, which `sync` deliberately never does.
        """
        if not rows:
            return
        with self._use_conn(None) as conn:
            for (connector_id, tool), (level, source) in rows.items():
                self._stamp(conn, connector_id, tool, level, source)

    def sync(
        self,
        desired: Mapping[PermissionKey, tuple[str, str]],
        *,
        prune: Sequence[PermissionKey] = (),
    ) -> dict[str, int]:
        """Bring the table in line with the seed rule, touching ONLY what is still a seed.

        ``desired`` is ``{(owner, tool): (level, source)}``. A pair with no row is inserted. A
        pair whose row is still a ``"seed"`` follows the rule: rewritten when its level (or
        source) differs, left byte-identical otherwise — so a second run changes nothing and no
        ``updated_at`` moves. A USER row (including a row with no ``source`` at all) is never
        touched: a sync cannot undo a decision.

        ``prune`` lists pairs whose tool no longer exists; each is deleted only if its row is
        still a seed — a decision about a retired tool is kept, so the owner can see and Reset it.

        One transaction, so a crash leaves either the old seeds or the new ones. Returns
        ``{"added", "updated", "pruned"}`` for the boot log.
        """
        counts = {"added": 0, "updated": 0, "pruned": 0}
        with self._use_conn(None) as conn:
            existing = {r.get("id"): r for r in self.all(conn=conn)}
            for (connector_id, tool), (level, source) in desired.items():
                row = existing.get(permission_key(connector_id, tool))
                if row is None:
                    self._stamp(conn, connector_id, tool, level, source)
                    counts["added"] += 1
                elif row.get("source") == SEED and (row.get("level") != level or source != SEED):
                    self._stamp(conn, connector_id, tool, level, source)
                    counts["updated"] += 1
            for connector_id, tool in prune:
                key = permission_key(connector_id, tool)
                row = existing.get(key)
                if row is not None and row.get("source") == SEED:
                    self.delete(key, conn=conn)
                    counts["pruned"] += 1
        return counts

    def clear_level(self, connector_id: str, tool: str) -> bool:
        """Delete ONE pair's row; True when a row was actually removed.

        Not "reset": a tool with no row falls back to its seed rule at dispatch, but nothing
        lists it until the next sync. Used for a stale row (its tool no longer exists).
        """
        removed = self.delete(permission_key(connector_id, tool))
        if removed:
            logger.info("tool permission cleared: %s/%s", connector_id, tool)
        return removed

    def clear_connector(self, connector_id: str) -> int:
        """Drop every stored row for one connector; returns how many were removed.

        The caller is an external MCP server being removed — its tools are gone, and a
        re-added server of the same id must not inherit the levels of the one it replaced.

        Reads INSIDE its own transaction (``all(conn=...)``) rather than through
        ``for_connector``'s lock-free read: a row written for this connector between a
        read-then-delete pair would otherwise survive the clear and be the one thing left
        claiming a decision the user just erased.
        """
        with self._use_conn(None) as conn:
            rows = [r for r in self.all(conn=conn) if r.get("connector_id") == connector_id]
            for row in rows:
                self.delete(row["id"], conn=conn)
        if rows:
            logger.info("connector permissions cleared: %s (%d rows)", connector_id, len(rows))
        return len(rows)

    def reseed_connector_level(self, connector_id: str, level: str, to_level: str) -> int:
        """Rewrite every row of one connector that sits AT ``level`` to ``to_level`` as a seed.

        For the one caller that must withdraw a grant without touching refusals
        (`services.tool_permissions.revoke_allowed_tools` — an MCP server whose endpoint was
        edited): ``always_allow`` goes back to the seed, ``blocked`` and ``needs_approval`` stay.
        Same in-transaction read-then-write as `clear_connector`, for the same reason.
        """
        with self._use_conn(None) as conn:
            rows = [
                r
                for r in self.all(conn=conn)
                if r.get("connector_id") == connector_id and r.get("level") == level
            ]
            for row in rows:
                self._stamp(conn, connector_id, row["tool"], to_level, SEED)
        if rows:
            logger.info(
                "connector permissions at level %s reseeded to %s: %s (%d rows)",
                level,
                to_level,
                connector_id,
                len(rows),
            )
        return len(rows)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _stamp(self, conn, connector_id: str, tool: str, level: str, source: str) -> dict:
        """Upsert one pair's row THROUGH the caller's open transaction.

        Validates the level and source here as well as in `services.tool_permissions` — belt
        over the service's own check (`ToolGrantStore.grant`'s identical precedent for an
        unknown scope), because this is the only write path into the table and an
        unrecognised level stored here would be read back as a level nothing knows how to
        enforce.
        """
        if level not in LEVELS:
            raise ValueError(f"unknown permission level {level!r} — expected one of {LEVELS}")
        if source not in SOURCES:
            raise ValueError(f"unknown permission source {source!r} — expected one of {SOURCES}")
        row = {
            "id": permission_key(connector_id, tool),
            "connector_id": connector_id,
            "tool": tool,
            "level": level,
            "source": source,
            "updated_at": _now(),
        }
        self.upsert(row, conn=conn)
        return row
