"""Memory API — the global toggle, plus reading and forgetting stored facts.

GET    /api/memory/settings   → {"enabled", "search_chats", "sensitive_topics"}
PUT    /api/memory/settings   body: any of those keys → updated settings
GET    /api/memory/facts      → {"facts": [...], "total": n}; a row carries state, section, topic,
                                retired_reason, retired_at, expires_at, last_used_at
PATCH  /api/memory/facts/{id} body {"content": str} → the edited fact
DELETE /api/memory/facts/{id} → {"ok": bool}
DELETE /api/memory/facts      → {"ok": true, "deleted": n}
DELETE /api/memory/topics     ?section=&topic= → {"ok": true, "deleted": n}
POST   /api/memory/instruct   body {"instruction": str, "topic"?: str}
                              → {"added", "updated", "removed", "rejected"}
GET    /api/memory/import/prompt  → {"prompt"}: what to run in the other assistant
POST   /api/memory/import/preview body {"text": str} → {"items"}; writes nothing
POST   /api/memory/import/apply   body {"items": [...]} → {"applied", "rejected"}
GET    /api/memory/export     → memory-YYYY-MM-DD.zip, one Markdown file per topic

Import and export work while memory is OFF: they are explicit user actions.

Thin: validate → store (or the one ai.memory_instruct call) → shape response.

Forgetting is deliberately reachable while memory is OFF: the toggle stops extraction and
injection, but a user who turned memory off and now wants the already-stored facts GONE must
not have to turn it back on to do that.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from backend.ai.memory import MemoryDisabledError
from backend.ai.memory_export import build_memory_export
from backend.ai.memory_import import EXPORT_PROMPT, plan_import
from backend.ai.memory_import_apply import MAX_ITEMS, apply_import
from backend.ai.memory_instruct import apply_instruction, delete_topic, edit_fact
from backend.ai.memory_write import apply_memory_change
from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore

router = APIRouter()


class MemorySettingsPatch(BaseModel):
    enabled: bool | None = None
    search_chats: bool | None = None
    sensitive_topics: bool | None = None


class MemoryInstruction(BaseModel):
    instruction: str
    topic: str | None = None


class MemoryImportPreview(BaseModel):
    text: str


class MemoryImportApply(BaseModel):
    items: list[Any] = Field(max_length=MAX_ITEMS)


class MemoryFactEdit(BaseModel):
    content: str


@router.get("/api/memory/settings")
def get_memory_settings():
    return MemorySettingsStore().settings()


@router.put("/api/memory/settings")
def put_memory_settings(body: MemorySettingsPatch):
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="Name a setting to change.")
    return MemorySettingsStore().update(patch)


@router.get("/api/memory/facts")
def list_memory_facts():
    """Every stored fact, newest-updated first. Bounded by the store's own size — memory is
    a distilled fact list, not a log, so there is no window to page through."""
    facts = MemoryStore().all_facts()
    return {"facts": facts, "total": len(facts)}


@router.patch("/api/memory/facts/{fact_id}")
def edit_memory_fact(fact_id: str, body: MemoryFactEdit):
    try:
        return edit_fact(fact_id, body.content)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.delete("/api/memory/facts/{fact_id}")
def forget_memory_fact(fact_id: str):
    removed = apply_memory_change({"remove": [fact_id]}, source="explicit", strict=False)["removed"]
    if not removed:
        raise HTTPException(status_code=404, detail="unknown memory")
    return {"ok": True, "id": fact_id}


@router.delete("/api/memory/facts")
def forget_all_memory_facts():
    ids = [f["id"] for f in MemoryStore().all_facts() if f.get("id")]
    removed = apply_memory_change({"remove": ids}, source="explicit", strict=False)["removed"]
    return {"ok": True, "deleted": removed}


@router.delete("/api/memory/topics")
def forget_memory_topic(section: str, topic: str):
    """Forget a whole topic. Like forgetting one fact, it works while memory is off."""
    try:
        deleted = delete_topic(section, topic)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return {"ok": True, "deleted": deleted}


@router.post("/api/memory/instruct")
async def instruct_memory(body: MemoryInstruction):
    """The memory dialog's "Tell Lucius what to change": add, rewrite or forget facts from one
    typed sentence. Unlike forgetting, it needs memory ON — it writes."""
    try:
        return await apply_instruction(body.instruction, body.topic)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except MemoryDisabledError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.get("/api/memory/import/prompt")
def memory_import_prompt():
    return {"prompt": EXPORT_PROMPT}


@router.post("/api/memory/import/preview")
async def preview_memory_import(body: MemoryImportPreview):
    """Sort a pasted export into items to review. Writes nothing."""
    try:
        return {"items": await plan_import(body.text)}
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.post("/api/memory/import/apply")
def apply_memory_import(body: MemoryImportApply):
    """Write the items the user kept, re-validating each one."""
    return apply_import(body.items)


@router.get("/api/memory/export")
def export_memory():
    filename = f"memory-{user_today().isoformat()}.zip"
    return Response(
        content=build_memory_export(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
