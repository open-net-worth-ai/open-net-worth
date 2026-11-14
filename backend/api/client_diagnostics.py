"""``POST /api/diagnostics/client`` — a device's opt-in event trace, written to the app log.

Behind the app-wide ``require_auth``; locked in the public demo (``api/demo_lockdown.py``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.services import client_diagnostics

router = APIRouter()


class ClientTrace(BaseModel):
    session: str = Field(default="", max_length=40)
    events: list[dict[str, Any]] = Field(
        default_factory=list, max_length=client_diagnostics.MAX_EVENTS
    )


@router.get("/api/diagnostics/client")
def get_client_trace_switch() -> dict[str, bool]:
    return {"enabled": client_diagnostics.enabled_for_all_devices()}


@router.post("/api/diagnostics/client")
def post_client_trace(body: ClientTrace) -> dict[str, int]:
    return {"logged": client_diagnostics.record(body.session, body.events)}
