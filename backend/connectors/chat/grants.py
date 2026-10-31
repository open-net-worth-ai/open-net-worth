"""Tool-permission grants, as reached from a chat connector.

RFC ``.claude/rfcs/2026-07-20-approval-and-review-model.md`` §13.1 / §16. A write tool
confirms every time until granted; a grant names a TOOL and nothing else and varies only on
SCOPE (``once`` | ``always``). Grants are GLOBAL across surfaces (§16.4) — one
made on web applies on Telegram and vice versa — which is precisely why a connector must be
able to record one, and why this is the same store the web card writes through rather than a
parallel connector-local table. Since 2026-08-14 that store is the ONE permission table
(`services.tool_permissions`), so "always" here and "Always allow" in the Connectors ladder
are literally the same row.

**This module is the ONE seam.** The import is LAZY and a missing/unwritable store degrades
to "the grant did not stick" rather than an exception that eats the user's confirm tap. That degradation is reported to the caller, never swallowed: the tapped call
still proceeds (the user approved it and the card showed exactly what it would do), but the
standing part is announced as failed, because a user who believes they granted a permission
they did not is worse off than one who is asked again.

Deliberately NOT here: the ``once`` scope, which stores nothing at all. It is handled as a
plain confirm by the caller, and ``record_grant`` treats it as a no-op so every scope can
route through one call.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("backend.connectors.chat.grants")

# The two scopes, mirroring `frontend/src/api/grants.ts`'s `GrantScope`. NO tool is excluded
# from either of them (D8/D9 were proposed and withdrawn — §16.3b): a per-tool exception
# would be a BOUND, in a model defined as having none. The per-session tier was removed on
# 2026-08-02 — a card offers Allow once and Always allow, nothing else.
GRANT_SCOPES = frozenset({"once", "always"})


def record_grant(tool_name: str, scope: str, *, session_id: Optional[str] = None) -> bool:
    """Record a standing grant for ``tool_name``. Returns whether it was DURABLY stored.

    ``once`` returns True having stored nothing — it is a real, complete outcome (this call is
    approved and nothing should persist), not a failure. ``always`` returns False when the
    store is unavailable or the write fails, which the caller MUST surface.

    ``session_id`` names the conversation for the caller's own bookkeeping; it is NOT part of
    the stored grant, which is global.

    Never raises: a confirm tap that reached this point represents a decision the user already
    made, and losing it to an ImportError would be a worse outcome than an unsaved grant.
    """
    if scope not in GRANT_SCOPES:
        log.warning("record_grant: unknown scope %r for %s", scope, tool_name)
        return False
    if scope == "once":
        return True
    try:
        # ONE store since 2026-08-14 (ADR `2026-08-14-one-permission-store-for-every-tool`):
        # `ToolGrantStore` is gone, absorbed into the permission table this writes now, so a
        # standing permission granted on Telegram is the same row the Connectors ladder and
        # Settings > Permissions show. The owner is resolved from the tool's `connector=`
        # tag rather than assumed — a per-provider decision must not leak to its siblings.
        #
        # The historical note this replaces is still worth keeping: this module was written
        # against a placeholder store name while the store was built concurrently, so it
        # called `GrantStore` (the EQUITY-COMP store, a different thing sharing the word)
        # and every tap raised AttributeError into the bare except below — a connector user
        # tapped "Always" and the grant silently never persisted.
        from backend.ai.permissions import engine
        from backend.services.tool_permissions import set_tool_permission

        owner, name = engine().permission_key(tool_name)
        set_tool_permission(owner, name, "always_allow")
        return True
    except Exception:  # noqa: BLE001 — see the module note; a tap must never be lost to this
        log.exception("record_grant: could not persist %s grant for %s", scope, tool_name)
        return False


def denial_message(tool_name: str) -> str:
    """The refusal text a Deny tap injects into session history for the model to read.

    **A denial is not a tool failure and must not read like one** (§15.6). The documented
    failure it prevents: when something is out of tool reach this model confabulates a
    mechanism rather than reporting the gap, so a bare "refused" invites either a retry loop
    or an invented explanation of why the app cannot do the thing. Three facts, in order —
    who decided, that it is not a capability limit, and what to do next.

    Kept byte-identical in spirit to the web card's ``denialMessage`` (``ChangePreview.tsx``):
    a channel must not be quietly weaker than web (ADR 2026-07-19-confirm-gate-surface-matrix),
    and that applies to what the MODEL is told just as much as to what the user is shown.
    """
    return (
        f"Denied — the user declined to run `{tool_name}`. This is the USER'S DECISION, not "
        f"a tool failure and not a missing capability: the app can do this, they chose not to "
        f"let it happen now. Do not retry this call, do not re-propose it, and do not work "
        f"around it with a different tool. Acknowledge briefly and ask what they would "
        f"prefer, or propose an alternative."
    )


def grant_ack(tool_name: str, scope: str, *, stored: bool) -> str:
    """The short acknowledgement the connector shows the USER after a grant tap.

    States what the grant covers (§13.3b: the tool, unbounded) and where to undo it, because
    the grant table is checkpoint-EXCLUDED and Settings › Permissions is the ONLY revocation
    surface (§16.1) — a permission with no visible state is one the user can only interact
    with by being surprised, which is the failure mode this whole RFC exists to close.
    """
    if scope == "once":
        return f"✓ Allowed once — {tool_name}"
    if not stored:
        return (
            f"✓ Allowed this call — {tool_name}\n"
            f"⚠️ Couldn't save the standing permission; you'll be asked again."
        )
    return (
        f"✓ Always allowed — {tool_name}\n"
        f"Every future call, any amount. Revoke in Settings › Permissions."
    )
