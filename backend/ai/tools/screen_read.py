"""``read_screen`` — look at the owner's open app now, on a turn that has no snapshot or an old one.

The request and its answer live in ``services.screen_read``; this head decides who may ask and
says plainly when there is nothing to see, so the model never guesses at a screen.
"""

from __future__ import annotations

import json
import logging

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of

logger = logging.getLogger(__name__)

# The owner in front of the app, typing or on a call. Never a Telegram sender (a stranger must
# not read the owner's open screen by asking), an Automation or inbound MCP. A channel's
# conversation is refused by name too: approving its card on the web resumes it as chat:web.
_SCREEN_SURFACES = frozenset({"chat:web", "chat:voice"})

_NOTES = {
    "no_screen": (
        "Nothing to read: Lucius or the call screen is filling the display. They can open the "
        "screen they mean, or fold the call away, and ask again."
    ),
    "no_app": "No open, visible app is showing this conversation.",
}


class ReadScreenInput(ToolInput):
    """Schema-of-record for ``read_screen`` (see ``schema_from_model``)."""


def _result(status: str, **extra) -> tuple[str, None]:
    return json.dumps({"status": status, **extra}, ensure_ascii=False), None


def _unavailable(session_id: str, status: str) -> tuple[str, None]:
    from backend.ai import assistant

    # As on any other turn with no screen: nothing from an earlier look stays invocable.
    assistant.upsert_session_meta(session_id, screen_handles=[], screen_commands=[])
    return _result(status, note=f"{_NOTES[status]} Tell them; do not guess what is on screen.")


@tool(
    "read_screen",
    description=(
        "Read what the user has on screen in the app right now. Call it when they refer to their "
        "screen and this turn has no '[Currently on screen' block (always so on a voice call), or "
        "when the screen may have changed since their message. Returns the screen as untrusted "
        "data, plus any handles for get_screen_detail. If it can't see the screen it says why: "
        "tell the user, never guess."
    ),
    input_schema=schema_of(ReadScreenInput, tool_name="read_screen"),
)
async def read_screen(input_data: dict):
    from backend.ai import assistant
    from backend.ai.tools._extra_groups_scope import add_extra_names
    from backend.core.audit_context import get_audit_scope
    from backend.core.chat_session import get_session_id, is_run_session
    from backend.services import screen_context, screen_read

    session_id = get_session_id()
    scope = get_audit_scope()
    if (
        not session_id
        or is_run_session(session_id)
        or assistant.session_channel(session_id)
        or scope is None
        or scope.surface not in _SCREEN_SURFACES
    ):
        return _result("no_screen", note="This turn has no screen to read. Do not guess at one.")
    if not screen_read.consume_read():
        return _result(
            "limit",
            note=f"Screen read {screen_read.MAX_READS_PER_TURN} times this turn already.",
        )

    reply = await screen_read.request_snapshot(session_id)
    logger.info("read_screen: %s", reply.status if reply else "no_app")
    if reply is None:
        return _unavailable(session_id, "no_app")
    if reply.status != screen_read.STATUS_OK:
        return _unavailable(session_id, reply.status)
    block = screen_context.format_screen_context(reply.snapshot)
    if not block:
        return _unavailable(session_id, "no_screen")

    handles, commands = screen_context.session_manifests(reply.snapshot)
    assistant.upsert_session_meta(session_id, screen_handles=handles, screen_commands=commands)
    # Into this turn's scope too: the offer is seeded once at turn start, and a later find_tools
    # persists that scope, which would drop a load only the session meta knew about.
    add_extra_names(assistant.load_screen_tools(session_id, reply.snapshot.title))
    return _result("ok", screen=block)
