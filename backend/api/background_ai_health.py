"""GET /api/ai/background-health — how each background AI task has been doing lately."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import background_ai_health as background_ai_health_service

router = APIRouter()


@router.get("/api/ai/background-health")
def get_background_ai_health():
    return background_ai_health_service.background_ai_health()
