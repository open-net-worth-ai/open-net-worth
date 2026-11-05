"""POST /api/screen-reads/{id} — an open app's answer to a ``read_screen`` request."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.models.screen import ScreenSnapshot
from backend.services import screen_read

router = APIRouter()


class ScreenReadAnswer(BaseModel):
    status: Literal["ok", "no_screen"]
    rank: int = Field(default=screen_read.RANK_COVERED, ge=0, le=screen_read.RANK_CALL)
    screen: Optional[ScreenSnapshot] = None


# async, so the reply lands on the event loop the waiting turn runs on.
@router.post("/api/screen-reads/{request_id}")
async def answer_screen_read(request_id: str, body: ScreenReadAnswer):
    reply = screen_read.ScreenReply(body.status, body.rank, body.screen)
    return {"accepted": screen_read.submit_reply(request_id, reply)}
