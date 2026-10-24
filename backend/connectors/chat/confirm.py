"""Shared confirm/apply over chat (RFC unified-chat-channels Slice 1 §1).

Promoted verbatim from the Telegram callback handler — these are already
provider-agnostic: ``pop_pending(change_id)`` pops the in-memory pending change and
``apply_change`` routes it through the existing ``positions_service.apply_position_change``
choke point (checkpoint-covered, undoable). A ``change_id`` is single-use
(``pending_changes.pop``). The provider renders the confirm UI in its own idiom (Telegram
inline keyboard, Slack Block Kit buttons) — both call the ONE shared
``handle_confirm_action`` below.

``handle_confirm_action`` is the generalization of Telegram's ``handle_callback`` apply
path: given a tapped button (``confirm`` | ``discard``) and a ``change_id``, it pops the
pending change, applies or discards it through the SAME choke point, injects a session note,
and returns a ``ConfirmOutcome`` the adapter renders in its own idiom. **NO new write path
to domain data is created** — apply still goes through the service choke point. The caller
MUST re-check its allowlist on the tap BEFORE calling this (the button rides on a separate
inbound request than the original turn).

**IDOR defense (unified-chat-channels).** A pending change is BOUND to its proposer
``(connector, channel_id, sender_id)`` at ``pending_changes.store`` time. ``handle_confirm_action``
takes the TAPPING user's ``(connector, channel_id, sender_id)`` and refuses to apply unless
they MATCH the bound proposer — so User B in a shared workspace can NEVER tap-and-apply a
change User A proposed. A mismatch returns ``ignored`` and leaves the change in place (it is
``peek``-checked, not popped, so the real proposer can still confirm it). A web/SSE change
stored with no binding (``sender_id is None``) skips the match (the authenticated operator
confirms in their own browser).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.connectors import pending_changes
from backend.connectors.chat import grants
from backend.core.ratelimit import SlidingWindowLimiter

log = logging.getLogger("backend.connectors.chat.confirm")

# Per-(connector, sender) cap on confirm-button taps (HIGH — the interactivity apply path).
# A confirm tap reaches the position-change choke point, so an attacker spamming taps (or a
# misbehaving client) is a write-amplification / DoS vector distinct from the inbound-turn
# limiter in chat.run. In-memory, resets on restart; over-cap → the route silently drops the
# tap (no apply). Shared by the Slack + Discord interactivity routes.
_CONFIRMS_PER_WINDOW = 20
_CONFIRM_WINDOW_SECONDS = 60.0
_confirm_limiter = SlidingWindowLimiter(_CONFIRMS_PER_WINDOW, _CONFIRM_WINDOW_SECONDS)


def reset_confirm_rate_limit() -> None:
    """Drop all per-sender confirm-tap counters (test isolation / restart)."""
    _confirm_limiter.clear()


def confirm_rate_limit_ok(connector: str, sender_id: str) -> bool:
    """Whether ``sender_id`` may make ANOTHER confirm tap on ``connector`` right now.

    Records the attempt when allowed; returns False (and records nothing) when over the cap so
    the caller can drop the tap. Keyed on ``(connector, sender_id)`` so one user's burst never
    throttles another's confirms."""
    key = f"{connector}:{sender_id}"
    allowed, _retry = _confirm_limiter.check(key)
    if not allowed:
        return False
    _confirm_limiter.record(key)
    return True


# ── Tool-permission actions (RFC 2026-07-20 approval-and-review-model §13.1) ───────────────
#
# A permission card offers FOUR choices, not two, and a channel must not be weaker than web
# (ADR 2026-07-19-confirm-gate-surface-matrix). ``confirm`` and ``discard`` stay exactly as
# they were — an ordinary (non-permission) card is untouched by any of this.
#
# The two allow actions differ ONLY in how long the approval lasts; both apply the change
# identically. That is the model: a grant names a tool and nothing else, so SCOPE is the
# only knob (§13.3b). The per-session tier was removed on 2026-08-02.
_GRANT_ACTIONS = {
    "allow_once": "once",
    "allow_always": "always",
}


@dataclass
class ChannelTarget:
    """Where a resumed turn should render, and who tapped.

    Carried (rather than passed as three loose parameters through four functions) so a
    caller either supplies a complete channel target or none at all. ``None`` means the
    web/SSE path, whose own resolve endpoint already owns rendering — see
    ``_resolve_via_pending_tool_use``.
    """

    adapter: object
    channel_id: str
    sender_id: Optional[str] = None


@dataclass
class ConfirmOutcome:
    """The result of a confirm/discard button tap, for the adapter to render.

    ``status`` is one of ``applied`` | ``discarded`` | ``stale`` | ``failed`` |
    ``ignored``. ``message`` is a short human acknowledgement (e.g. "✓ Applied — added
    BTC"); the adapter shows it. ``error`` carries the apply failure detail when
    ``status == "failed"``.
    """

    status: str
    message: str
    error: Optional[str] = None


_BUDGET_TYPES = frozenset(
    {
        "assign",
        "move_money",
        "set_target",
        "group_create",
        "group_rename",
        "group_reorder",
        "group_delete",
        "move_category",
        "reorder_categories",
        "category_create",
        "category_rename",
        "category_delete",
    }
)

# The ledger confirm gate's own change types (``services.ledger_preview``). Membership-only
# now (RFC 2026-07-22): resolving no longer needs to look up which tool redeems a ctype — the
# staged ``PendingPlanStore`` entry already names its own tool — so the dict is kept as the
# "is this a ledger confirm card" set, not a lookup table.
_LEDGER_CONFIRM_TOOLS = frozenset({"txn_create", "txn_bulk", "txn_update"})


async def _resolve_via_pending_tool_use(
    change: dict,
    session_id: str,
    decision: str,
    *,
    target: Optional["ChannelTarget"] = None,
) -> dict:
    """Redeem (or deny) a staged AWAITING-APPROVAL tool_use (RFC 2026-07-22) through the SAME
    server-side resolve path the web resolve endpoint uses (``assistant.resolve_pending_tool_use``)
    — never re-deriving the write from ``change``, so a card showing $12.40 cannot commit
    $1,240 regardless of what the tap carries. This is what makes a Telegram/Slack/Discord
    "yes" resolve identically to a web tap: the REAL result lands on the SAME ``tool_use_id``
    the paused turn is waiting on, and the turn resumes — not a fresh, unrelated dispatch.

    Drains the resumed continuation to natural completion (never abandoning the generator
    mid-iteration — see the module note on why) so history/checkpoint/memory hooks run
    exactly as they would for a web resolve, but returns only the REDEEMED call's own result
    (the first ``tool_done`` event). The model's post-approval commentary from the resumed
    turn is not (yet) forwarded as a second connector message — a deliberate, documented scope
    limit (RFC 2026-07-22's connector mirror covers correctness: the write lands, the
    transcript stays valid, nothing double-books — not yet a second push of the model's
    follow-up text). ``handle_confirm_action``'s own ``ConfirmOutcome.message`` is what the
    tapper sees, built from the STAGED card's own label exactly as before.
    """
    import json as _json

    from backend.ai import assistant
    from backend.ai.prompts import build_system_prompt

    tool_use_id = change.get("tool_use_id")
    if not tool_use_id:
        raise RuntimeError("confirm card carries no tool_use_id — cannot apply")

    if target is not None:
        # A CHAT surface: hand the resumed turn to the connector's own renderer, which
        # binds the cost surface (keeping the confirm gate armed for the rest of the turn)
        # and delivers prose / further permission cards to the channel. See
        # ``processor.pump_channel_turn`` for the two defects this closes.
        from backend.connectors.processor import resume_pending_tool_use

        return await resume_pending_tool_use(
            target.adapter,
            target.channel_id,
            session_id,
            tool_use_id,
            decision,
            sender_id=target.sender_id,
        )

    system = build_system_prompt()
    result: Optional[dict] = None
    async for event in assistant.resolve_pending_tool_use(
        session_id, tool_use_id, decision, system
    ):
        etype = event.get("type")
        if etype == "error":
            raise RuntimeError(event.get("message") or "could not resolve the pending approval")
        if etype == "tool_done" and result is None:
            text = event.get("result")
            try:
                result = _json.loads(text) if isinstance(text, str) else (text or {})
            except Exception:
                result = {"ok": True}
        # Keep draining every remaining event (the resumed model turn) rather than stopping
        # the instant `result` is captured — the generator holds the session lock for its
        # whole lifetime, and abandoning it mid-iteration would leave that lock (and
        # `_run_turn`'s own live provider stream) torn down uncleanly instead of via its own
        # normal completion/finally path.
    if result is None:
        raise RuntimeError("resolving the pending approval produced no result")
    return result


async def _apply_ledger_confirm(
    ctype: str, change: dict, session_id: str, *, target: Optional["ChannelTarget"] = None
) -> dict:
    """Redeem a staged ledger-confirm-gate pending call — the ledger-specific entry point
    into ``_resolve_via_pending_tool_use`` (RFC 2026-07-22)."""
    return await _resolve_via_pending_tool_use(change, session_id, "approve", target=target)


# The propose_*/apply_* pair collapse's change types (harness reliability program,
# 2026-07-19 — ai/tools/{lots,equity,options}.py) that had NO branch in ``apply_change``
# before this fix: propose_vest / propose_vest_undo / propose_open_option /
# propose_close_option / propose_8b_import's confirm cards were UNAPPLIABLE over a chat
# connector — a tap fell through to the ``else`` branch below (``positions_service
# .apply_position_change``, which only recognizes "add"/"update"/"remove") and failed with
# "unknown change type" (a pre-existing gap, not introduced by the merge — these ctypes
# were never wired here even when they were separate propose_*/apply_* tool pairs).
# ``lot_acquire``/``lot_dispose``/``lot_delete``/``record_income``/the position types keep
# their EXISTING direct-dispatch branches below unchanged: ``change`` there is already the
# server-resolved write (built by the tool's own preview step, never model-reconstructed),
# so hand-dispatching straight to the service is already safe and this fix doesn't need to
# touch it.
_CONFIRM_TOKEN_TOOLS = {
    "lot_dispose_undo": "undo_lot_disposals",
    "vest_apply": "record_vest",
    "vest_undo": "undo_vest",
    "8b_import_commit": "import_8b",
    "open_option": "open_option",
    "close_option": "close_option",
}


async def _apply_via_confirm_token(tool_name: str, change: dict) -> dict:
    """Redeem a staged propose_*/apply_* merge plan through its OWN tool — the generalized
    twin of ``_apply_ledger_confirm`` for ``_CONFIRM_TOKEN_TOOLS``'s change types. Also
    re-runs that tool's own outcome re-check against what the card promised, which the
    direct-dispatch branches below skip."""
    from backend.ai.tools import execute_tool
    from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY

    token = change.get(CONFIRM_TOKEN_KEY)
    if not token:
        raise RuntimeError(f"{tool_name} confirm card carries no confirm token — cannot apply")
    text, _ = await execute_tool(tool_name, {CONFIRM_TOKEN_KEY: token}, tool_use_id=token)
    import json as _json

    return _json.loads(text) if isinstance(text, str) else (text or {})


async def apply_change(
    change: dict, session_id: str, *, target: Optional["ChannelTarget"] = None
) -> None:
    """Apply a confirmed change through the appropriate service choke point. Raises on error.

    ``target`` names the chat channel to render the RESUMED turn on, for the three change
    types that resolve a paused ``tool_use``. Omit it on the web/SSE path."""

    ctype = change.get("type", "")

    # ONE rule, checked before any display type: a card that carries a ``tool_use_id`` is a
    # PAUSED tool_use, and the only correct way to apply it is to resolve that id — the
    # server pops the args it staged and runs THOSE. Never re-derive a write from this
    # dict: it arrived from a client, and a card showing one thing must not commit another.
    #
    # This replaced five per-type branches (the three ledger types, ``tool_permission``, the
    # six ``_CONFIRM_TOKEN_TOOLS`` redemptions, and direct service dispatch for the
    # position/lot/income types) that all ended up here anyway or, worse, hand-dispatched
    # the client's dict. Every gated tool stages the same way now, so every one applies the
    # same way.
    if change.get("tool_use_id"):
        result = await _resolve_via_pending_tool_use(change, session_id, "approve", target=target)
    else:
        # A card with no ``tool_use_id`` staged nothing, so there is nothing the server can
        # run that the user actually saw. Refusing is the only safe answer: re-deriving a
        # write from this dict is exactly the reconstruct-the-arguments path the universal
        # staging replaced.
        raise RuntimeError(f"unknown change type: {ctype or '(none)'} — nothing was applied")

    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
        # The ledger tools' own refusal shapes don't all carry an "error" key (an
        # already-recorded / suspected-duplicate abort uses "error_note"/"message" instead —
        # ``ok: False`` is the one field every refusal shares), so checking only "error" here
        # would silently report a non-write as "applied".
        raise RuntimeError(
            result.get("error")
            or result.get("error_note")
            or result.get("message")
            or "the change could not be applied"
        )


def _ledger_change_label(ctype: str, change: dict) -> str:
    """Short label for a ledger confirm ack. ``services.ledger_preview`` returns these FLAT
    (no ``display`` wrapper, unlike the position/agent changes below), so this reads the
    resolved fields directly off ``change``."""
    if ctype == "txn_bulk":
        return f"booked {change.get('count', 0)} transactions"
    if ctype == "txn_update":
        return f"updated transaction {change.get('transaction_id', '')}".strip()
    leg = change.get("from") or change.get("to") or {}
    amount, currency = leg.get("amount"), leg.get("currency") or ""
    category = (change.get("category") or {}).get("label", "Uncategorized")
    if amount is not None:
        return f"booked {currency} {amount:,.2f} • {category}".strip(" •")
    return f"booked transaction • {category}"


def change_label(change: dict) -> str:
    """A short human label for a confirm/discard acknowledgement (e.g. 'added BTC')."""
    ctype = change.get("type", "change")
    if ctype in _LEDGER_CONFIRM_TOOLS:
        return _ledger_change_label(ctype, change)
    d = change.get("display", {})
    # "contract"/"summary" cover the option and 8B-import cards, whose display has no
    # bare name/symbol field (2026-07-19 propose_*/apply_* merge — see _CONFIRM_TOKEN_TOOLS).
    label = d.get("name") or d.get("symbol") or d.get("contract") or d.get("summary") or "position"
    verbs = {
        "update": "updated",
        "add": "added",
        "remove": "removed",
        "lot_acquire": "recorded lot for",
        "lot_dispose": "disposed lot for",
        "lot_delete": "deleted lot for",
        "lot_update": "corrected lot for",
        "lot_dispose_undo": "reversed the disposal of",
        "vest_apply": "vested",
        "vest_undo": "reversed the vest of",
        "open_option": "opened option:",
        "close_option": "closed option:",
        "8b_import_commit": "imported Appendix 8B:",
    }
    return f"{verbs.get(ctype, ctype)} {label}"


def inject_session_note(session_id: str, note: str) -> None:
    """Append a system note to session history so Lucius has confirmation context.

    WRAPPED as a harness note: it rides on a ``role: "user"`` entry (the model must read
    it as turn context), but it is not the owner speaking. Unwrapped, every confirm /
    discard / deny on a chat channel drew a "[System: …]" bubble attributed to them, and
    one such note was what a conversation got NAMED after — see
    ``ai.tool_text.is_harness_note`` and ``tests/test_proactive_alert_history.py``. The
    marker is read-only bookkeeping for the renderer; the model sees the note either way.
    """
    from backend.ai import assistant
    from backend.ai.tool_text import harness_note

    _, history = assistant.get_or_create_session(session_id)
    history.append({"role": "user", "content": harness_note(f"[System: {note}]")})
    assistant._save_session(session_id, history)


def pop_pending(change_id: str) -> Optional[dict]:
    """Pop a pending change (single-use). Returns ``{session_id, change, …proposer}`` or ``None``."""
    return pending_changes.pop(change_id)


def _proposer_matches(
    entry: dict,
    *,
    connector: Optional[str],
    channel_id: Optional[str],
    sender_id: Optional[str],
) -> bool:
    """Whether the tapping ``(connector, sender_id)`` may apply this pending change (IDOR).

    The load-bearing identity is ``(connector, sender_id)`` — the SAME sender identity the
    allowlist checks. ``channel_id`` is deliberately NOT part of the equality: an untrusted
    Slack connector posts the confirm card to the proposer's DM (a different channel than the
    reply channel the change was bound to), so requiring the channel to match would break the
    proposer's own confirm. The strong boundary is sender identity — User B can never apply a
    change User A proposed because their ``sender_id`` differs.

    An UNBOUND change (``sender_id is None`` on the stored entry — the web/SSE path) skips the
    check (the authenticated operator confirms in their own browser). ``channel_id`` is kept
    in the signature for callers/logging but does not gate."""
    if entry.get("sender_id") is None:
        return True  # unbound (web/SSE) — no chat-identity check
    return entry.get("connector") == connector and entry.get("sender_id") == sender_id


def _resolved_inside_the_turn(change: dict, target: Optional["ChannelTarget"]) -> bool:
    """Whether the model already SAW this decision land, inside its own resumed turn.

    True when the card resolves a paused ``tool_use`` (RFC 2026-07-22) AND a chat target was
    supplied — i.e. ``processor.resume_pending_tool_use`` ran, the real ``tool_result`` was
    appended to the transcript on the id the model was waiting on, and the model answered it.

    Callers use this to SKIP the "[System: …]" session note. That note exists to tell the
    model what happened on a path where nothing else would — a discard, a deny with no
    tool_use, an ordinary card applied straight through a service. On the resumed-turn path
    it is redundant, and worse: it is appended AFTER the turn's own reply, so the transcript
    ends on an unanswered user-role message. The model then treats it as the outstanding
    request — a bare "Hello" got answered with *"This confirms the SGD 7.00 Corner Soup
    Stall payment already logged…"* on the owner's live instance (2026-08-23).
    """
    return bool(change.get("tool_use_id")) and target is not None


async def handle_confirm_action(
    action: str,
    change_id: str,
    *,
    connector: Optional[str] = None,
    channel_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    adapter: object = None,
) -> ConfirmOutcome:
    """Apply or discard a pending change identified by ``change_id`` (the shared confirm path).

    ``action`` is the tapped button. Two vocabularies share this entry point:

      * an ORDINARY card — ``confirm`` (apply) or ``discard``, unchanged;
      * a PERMISSION card (one carrying ``grant_tool``; RFC 2026-07-20 §13.1) —
        ``allow_once`` / ``allow_always`` (both apply the change and differ only in how long
        the approval persists) or ``deny`` (applies nothing and feeds
        the model an explicit human-decision refusal rather than a generic discard).

    A permission action on a card with no ``grant_tool`` returns ``ignored``. This is the
    verbatim
    logic of Telegram's ``handle_callback`` apply path, promoted so any adapter (Telegram
    inline keyboard, Slack Block Kit) reuses ONE path to the
    ``positions_service.apply_position_change`` choke point — no per-provider write path.

    ``connector`` / ``channel_id`` / ``sender_id`` identify the TAPPING user. The change is
    applied only when they MATCH the proposer the change was bound to (IDOR defense); a
    mismatch returns ``ignored`` and leaves the change in place (peek-checked, not popped) for
    the real proposer. Omit them (the web/SSE path) for an unbound change.

    Returns a :class:`ConfirmOutcome`:
      * ``stale``    — the change was already processed / unknown id (single-use; nothing done);
      * ``ignored``  — an unrecognized ``action``, OR the tapper is not the proposer (nothing done);
      * ``applied``  — applied through the choke point + a session note injected;
      * ``failed``   — the apply raised; ``error`` carries the detail (the change is consumed);
      * ``discarded``— the user discarded it + a session note injected.

    The CALLER is responsible for re-checking its allowlist on the tap before calling this —
    a button tap is a fresh inbound request, so trust must be re-verified (it is not implied
    by the original turn).
    """
    # Local import to avoid a module-load cycle (processor → confirm → processor).
    from backend.connectors.processor import format_change_summary

    # IDOR check FIRST (peek, don't pop): a mismatched tap must NOT consume the change so the
    # real proposer can still confirm it.
    pending = pending_changes.peek(change_id)
    if pending is None:
        return ConfirmOutcome("stale", "This change has already been processed.")
    if not _proposer_matches(
        pending, connector=connector, channel_id=channel_id, sender_id=sender_id
    ):
        log.warning(
            "confirm refused: tapper (%s/%s/%s) != proposer for change %s",
            connector,
            channel_id,
            sender_id,
            change_id,
        )
        return ConfirmOutcome("ignored", "")  # leave the change in place for the proposer

    entry = pop_pending(change_id)
    if entry is None:
        return ConfirmOutcome("stale", "This change has already been processed.")

    change = entry["change"]
    session_id = entry["session_id"]
    summary = format_change_summary(change)
    label = change_label(change)
    # Built only once the tap has been proven to come from the proposer: a resumed turn
    # renders to this channel, so an unverified target would be a way to redirect one
    # conversation's output into another.
    target = (
        ChannelTarget(adapter, str(channel_id), sender_id)
        if adapter is not None and channel_id is not None
        else None
    )

    if action == "confirm":
        try:
            await apply_change(change, session_id, target=target)
        except Exception as exc:  # apply failed — the change is consumed (single-use)
            log.exception("Failed to apply change %s", change_id)
            return ConfirmOutcome("failed", f"Failed to apply: {exc}", error=str(exc))
        if not _resolved_inside_the_turn(change, target):
            try:
                inject_session_note(session_id, f"User confirmed and applied via chat: {summary}")
            except Exception:  # best-effort context note — never fail the apply over it
                log.exception("Failed to inject confirm session note for %s", change_id)
        return ConfirmOutcome("applied", f"✓ Applied — {label}")

    if action in _GRANT_ACTIONS:
        return await _handle_grant_action(
            _GRANT_ACTIONS[action], change, change_id, session_id, summary, label, target=target
        )

    if action == "deny":
        return await _handle_deny_action(change, session_id, target=target)

    if action == "discard":
        try:
            inject_session_note(
                session_id, f"User discarded the proposed change via chat: {summary}"
            )
        except Exception:
            log.exception("Failed to inject discard session note for %s", change_id)
        return ConfirmOutcome("discarded", f"✗ Discarded — {label}")

    return ConfirmOutcome("ignored", "")


def _grant_tool_of(change: dict) -> str:
    """The canonical tool name a permission card is gating, or "" for an ordinary card.

    Mirrors the frontend contract (``ProposedChange.grant_tool``): the key's PRESENCE is what
    makes a card a permission card. Deliberately not ``change["tool"]`` — that key already
    means external-MCP's display-only tool name, and keying a grant off it would let an
    external server's tool name silently become a granted permission."""
    tool = change.get("grant_tool")
    return tool if isinstance(tool, str) else ""


async def _handle_grant_action(
    scope: str,
    change: dict,
    change_id: str,
    session_id: str,
    summary: str,
    label: str,
    *,
    target: Optional["ChannelTarget"] = None,
) -> ConfirmOutcome:
    """Record the grant, THEN apply the change — never the reverse.

    Order is the security property, matching the web card: the grant is written from a
    human-authenticated tap before the model is told anything, so the model can never escalate
    its own permission. A FAILED grant does not cancel the apply (the user approved this call
    and the card showed exactly what it does) but is reported in the acknowledgement, so a
    permission the user believes they granted is never silently absent."""
    tool_name = _grant_tool_of(change)
    if not tool_name:
        # A grant action on a card that is not gating a tool — nothing to grant against.
        # Treated as ``ignored`` rather than applied: acting on a malformed instruction is
        # exactly the class of bug a permission system must not have.
        log.warning("grant action %r on a card with no grant_tool (change %s)", scope, change_id)
        return ConfirmOutcome("ignored", "")

    stored = grants.record_grant(tool_name, scope, session_id=session_id)

    try:
        await apply_change(change, session_id, target=target)
    except Exception as exc:  # apply failed — the change is consumed (single-use)
        log.exception("Failed to apply granted change %s", change_id)
        return ConfirmOutcome("failed", f"Failed to apply: {exc}", error=str(exc))
    if not _resolved_inside_the_turn(change, target):
        try:
            inject_session_note(session_id, f"User confirmed and applied via chat: {summary}")
        except Exception:  # best-effort context note — never fail the apply over it
            log.exception("Failed to inject confirm session note for %s", change_id)

    ack = grants.grant_ack(tool_name, scope, stored=stored)
    return ConfirmOutcome("applied", f"{ack}\n{label}")


async def _handle_deny_action(
    change: dict, session_id: str, *, target: Optional["ChannelTarget"] = None
) -> ConfirmOutcome:
    """A Deny tap. NOTHING is applied and nothing is granted.

    The model is told through a session note carrying the S2-shaped refusal
    (``grants.denial_message``) rather than a bare "discarded" — a denial is a HUMAN DECISION,
    and reporting it as a generic discard is what lets the model retry or confabulate a reason
    the app cannot do the thing (§15.6).

    A ``tool_use_id``-carrying card (RFC 2026-07-22 — the ledger tools + every generic
    dispatch-gated ``tool_permission`` card) must ALSO resolve the paused tool_use itself
    (denied, not merely discarded) — without this the tool_use would stay dangling forever,
    since nothing else in the connector confirm flow ever touches it. Best-effort: a failed
    resolve still tells the user "Denied" (their tap was honoured) and is logged, never
    raised — the session note below is the fallback that keeps the model informed either way.
    """
    tool_name = _grant_tool_of(change)
    tool_use_id = change.get("tool_use_id")
    if tool_use_id:
        try:
            await _resolve_via_pending_tool_use(change, session_id, "deny", target=target)
        except Exception:
            log.exception("Failed to resolve denied tool_use %s", tool_use_id)
    if not _resolved_inside_the_turn(change, target):
        try:
            inject_session_note(session_id, grants.denial_message(tool_name or "that tool"))
        except Exception:
            log.exception("Failed to inject denial session note")
    return ConfirmOutcome("discarded", f"✗ Denied — {tool_name or 'change not applied'}")
