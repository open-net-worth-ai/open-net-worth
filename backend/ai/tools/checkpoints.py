"""Turn-checkpoint (rollback) tools."""

from __future__ import annotations

import json
import logging
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_no_required

logger = logging.getLogger(__name__)


class ListCheckpointsInput(ToolInput):
    limit: Optional[int] = Field(
        default=None, description="Max checkpoints to return (default 20, up to the 50 kept)."
    )


@tool(
    "list_checkpoints",
    description=(
        "List recent turn checkpoints. Every time you (the assistant) make a data "
        "change in a conversation turn, a checkpoint of the prior state is captured "
        "automatically. Use this to find a checkpoint_id when the user wants to undo "
        "a previous change ('undo that', 'revert what you just did', 'go back to before X'). "
        "Each entry: id, message_preview (what the user asked that turn), created_at, "
        "files (which data files changed), status. Active checkpoints are capped at 50 total "
        "(oldest pruned automatically) — limit=50 always reaches every one that still exists."
    ),
    input_schema=schema_of_no_required(ListCheckpointsInput, tool_name="list_checkpoints"),
)
async def list_checkpoints(input_data: dict):
    from backend.ai import checkpoint as ckpt

    limit = int(input_data.get("limit") or 20)
    cps = ckpt.list_checkpoints(limit=limit)
    total = ckpt.count_checkpoints()
    # Attach a short impact preview to each so the assistant can describe the undo,
    # including cross-session changes (e.g. Telegram) a restore would also unwind.
    for cp in cps:
        try:
            pv = ckpt.preview(cp["id"])
            cp["preview"] = pv["changes"]
            cp["cross_session_changes"] = pv.get("cross_session_changes", [])
        except Exception:
            cp["preview"] = []
            cp["cross_session_changes"] = []
    payload: dict = {
        "ok": True,
        "checkpoints": cps,
        "showing": len(cps),
        "total": total,
    }
    if len(cps) < total:
        payload["note"] = (
            f"Showing the {len(cps)} most recent of {total} checkpoints kept "
            f"(the store retains at most 50 active). Raise limit up to {total} to see the rest."
        )
    return json.dumps(payload, ensure_ascii=False), None


class RestoreCheckpointInput(ToolInput):
    checkpoint_id: str = Field(
        description="UUID of the checkpoint to restore. Get it from list_checkpoints."
    )
    mode: Optional[Literal["data", "conversation", "both"]] = Field(
        default=None, description="What to restore. Default 'data' (revert data files only)."
    )


@tool(
    "restore_checkpoint",
    description=(
        "Roll the system's DATA back to a checkpoint — undoing the changes made in "
        "that turn (and every turn after it). This is the safe way to undo edits, "
        "including bulk deletes. The restore is itself reversible (a pre-restore "
        "snapshot is taken automatically).\n\n"
        "MANDATORY: before calling this, call list_checkpoints to find the right id, "
        "then tell the user exactly what restoring will do (use the impact summary — "
        "e.g. 'this will bring back 147 transactions and revert your Main Checking balance').\n\n"
        "mode: 'data' (default — revert data, keep the chat), 'conversation' (rewind "
        "the chat only, keep data), or 'both' (revert data and rewind the chat)."
    ),
    input_schema=schema_of(RestoreCheckpointInput, tool_name="restore_checkpoint"),
    mutates=("*",),
)
async def restore_checkpoint(input_data: dict):
    from backend.ai import checkpoint as ckpt

    checkpoint_id = input_data.get("checkpoint_id", "")
    mode = input_data.get("mode") or "data"
    try:
        result = ckpt.restore(checkpoint_id, mode=mode)
    except FileNotFoundError:
        return json.dumps({"ok": False, "error": "Checkpoint not found"}), None
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}), None
    except RuntimeError as e:
        return json.dumps({"ok": False, "error": str(e)}), None
    logger.info(
        "tool restore_checkpoint: id=%s mode=%s files=%d",
        checkpoint_id,
        mode,
        len(result.get("restored_files", [])),
    )
    return json.dumps({"ok": True, **result}, ensure_ascii=False), None
