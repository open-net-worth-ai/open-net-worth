"""Feature flags — the owner's view and switch (Settings → Developer → Experimental).

Auth-gated by the app-wide dependency; ``PUT`` is locked on the public demo
(``demo_lockdown._LOCKED``), which always runs the declared defaults. A successful write is
published on ``GET /api/events`` by the service, so a ``useFlag`` read on any open screen
revalidates. No Lucius tool reaches these writes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import feature_flags

router = APIRouter()


class FeatureFlagPatch(BaseModel):
    # Required but nullable: ``null`` resets to the declared default.
    enabled: Optional[bool]


def _raise_http(exc: OpenNetWorthError) -> None:
    raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/feature-flags")
def list_feature_flags():
    return feature_flags.list_flags()


@router.put("/api/feature-flags/{name}")
def put_feature_flag(name: str, body: FeatureFlagPatch):
    try:
        return feature_flags.set_flag(name, body.enabled)
    except OpenNetWorthError as exc:
        _raise_http(exc)
