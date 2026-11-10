"""Turn checkpoints API — list, preview, restore, delete.

Thin handlers over backend/ai/checkpoint.py. See .claude/rfcs/turn-checkpoints.md.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.ai import checkpoint as ckpt
from backend.core import audit_context

logger = logging.getLogger(__name__)


async def _ui_audit_scope() -> None:
    """A Restore clicked in the chat is the user acting through the UI — the "revert" rows
    it writes to each transaction's Activity are theirs. ``async`` for the same reason as
    ``api.ledger._ui_audit_scope``: a sync dependency's ContextVar never reaches the endpoint."""
    audit_context.set_audit_scope("user", "ui")


router = APIRouter(dependencies=[Depends(_ui_audit_scope)])


@router.get("/api/checkpoints")
def list_checkpoints(
    session_id: Optional[str] = None, limit: int = 50, include_orphaned: bool = False
):
    return ckpt.list_checkpoints(
        session_id=session_id, limit=limit, include_orphaned=include_orphaned
    )


@router.post("/api/checkpoints/{checkpoint_id}/preview")
def preview_checkpoint(checkpoint_id: str):
    try:
        return ckpt.preview(checkpoint_id)
    except FileNotFoundError:
        raise HTTPException(404, "Checkpoint not found")


class RestoreRequest(BaseModel):
    mode: str = "data"  # "data" | "conversation" | "both"


@router.post("/api/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(checkpoint_id: str, body: RestoreRequest):
    try:
        return ckpt.restore(checkpoint_id, mode=body.mode)
    except FileNotFoundError:
        raise HTTPException(404, "Checkpoint not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))  # turn in progress


@router.delete("/api/checkpoints/{checkpoint_id}")
def delete_checkpoint(checkpoint_id: str):
    if not ckpt.delete_checkpoint(checkpoint_id):
        raise HTTPException(404, "Checkpoint not found")
    return {"deleted": checkpoint_id}
