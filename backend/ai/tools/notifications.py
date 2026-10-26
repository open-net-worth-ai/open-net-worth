"""Lucius notification tools.

A first-class, per-channel pair (``telegram_send_message``/``webhook_send_message``) — the
same shape as connectors.py's per-provider ``<id>_get_status``/``<id>_sync_now`` tools (RFC
.claude/rfcs/2026-08-10-connectors-architecture.md §B3): permissions are NAME-KEYED, so a
single shared ``notify(channels=[...])`` tool (retired 2026-08-20) would have let "always
allow Telegram" silently also allow Webhook, since one tool name can't distinguish which
channel a given call actually targeted. Each named tool also carries ``connector=`` so
Telegram/Webhook show a real entry in their own Connectors tool-permissions list instead of
"no tools yet" for a connector Lucius can genuinely reach. Both dispatch to the same
``services.notify.notify()`` fan-out.

**2026-08-20: renamed verb-first → connector-first** (``send_telegram_message``/
``send_webhook_message`` → ``telegram_send_message``/``webhook_send_message``) to match the
same convention every other built-in connector tool now uses. Single-operator, self-hosted
instance (ADR ``2026-08-20-no-compat-aliases-single-user-instance``): a clean rename, no
alias table entry.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class ChannelMessageInput(ToolInput):
    message: str = Field(
        description=(
            "The message body (sent verbatim). It must make sense on its own — a channel "
            "without a title slot shows the message alone."
        )
    )
    title: Optional[str] = Field(
        default=None,
        description=(
            "Optional subject line, e.g. 'Overdue vest: KSTL'. Shown only where the channel "
            "has a title slot (the in-app feed), never added to the message — so put any "
            "fact the reader needs in the message too."
        ),
    )


class TelegramMessageInput(ChannelMessageInput):
    artifact_id: Optional[str] = Field(
        default=None,
        description=(
            "Attach an artifact you created with create_artifact (its artifact_id) as a "
            "file. The message becomes the file's caption — keep it to a few lines; the "
            "artifact carries the detail."
        ),
    )


async def _send_to_channel(channel: str, input_data: dict) -> tuple[str, None]:
    from backend.core.errors import OpenNetWorthError
    from backend.services.notify import notify as send_notification
    from backend.services.notify import notify_document

    artifact_id = (input_data.get("artifact_id") or "").strip()
    if artifact_id:
        try:
            delivered = await notify_document(
                artifact_id,
                input_data.get("message") or "",
                channels=[channel],
                title=input_data.get("title"),
            )
        except OpenNetWorthError as exc:
            return json.dumps({"error": exc.message or str(exc)}), None
        return json.dumps({"ok": True, "delivered": delivered}, ensure_ascii=False), None

    delivered = await send_notification(
        input_data.get("message") or "",
        channels=[channel],
        title=input_data.get("title"),
    )
    return json.dumps({"ok": True, "delivered": delivered}, ensure_ascii=False), None


@tool(
    "telegram_send_message",
    description=(
        "Send a message to the user's Telegram right now. APPROVE-TIER: confirm with the "
        "user before sending — this reaches them out-of-band. The message is sent verbatim, "
        "so include any currency symbols / formatting yourself. Pass artifact_id to deliver "
        "a create_artifact document (a report) as a file, with the message as its caption."
    ),
    input_schema=schema_of(TelegramMessageInput, tool_name="telegram_send_message"),
    mutates=("notification_log",),
    connector="telegram",
)
async def telegram_send_message(input_data: dict):
    return await _send_to_channel("telegram", input_data)


@tool(
    "webhook_send_message",
    description=(
        "Send a message to the user's configured webhook right now. APPROVE-TIER: confirm "
        "with the user before sending — this reaches them out-of-band. The message is sent "
        "verbatim, so include any currency symbols / formatting yourself."
    ),
    input_schema=schema_of(ChannelMessageInput, tool_name="webhook_send_message"),
    mutates=("notification_log",),
    connector="webhook",
)
async def webhook_send_message(input_data: dict):
    return await _send_to_channel("webhook", input_data)
