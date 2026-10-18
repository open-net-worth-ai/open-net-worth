"""Canonical persisted Automation and AutomationRun shapes.

These models describe repository rows only. Trigger validation and schedule math
live in :mod:`backend.services.automations.schedules`, preserving the models
leaf-layer contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import Field

from .base import ONBaseModel


class AutomationStatus(str, Enum):
    active = "active"
    paused = "paused"
    completed = "completed"


class AutomationExecution(ONBaseModel):
    """Every Automation is one Lucius turn; behavior lives in its instructions."""

    kind: Literal["lucius"] = "lucius"


class AutomationPermission(ONBaseModel):
    src: str
    desc: str
    capability: Optional[str] = None
    tools: list[str] = Field(default_factory=list)


class Automation(ONBaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str
    status: AutomationStatus = AutomationStatus.active
    trigger: dict
    trigger_revision: str
    execution: AutomationExecution = Field(default_factory=AutomationExecution)
    # Defaults to the mode that still ASKS. An automation is the one surface where nobody is
    # watching, and it is also the surface most likely to be reading untrusted input (an inbox,
    # a feed, a fetched page) — so an unspecified permission mode must not silently grant
    # unattended write and outbound-send authority to whatever that text says. `api/automations.py`
    # already defaults its create body to "manual"; this is the model-level fallback for every
    # other construction path (AI-tool creation, a stored row missing the field).
    permission_mode: str = "manual"
    always_allowed: list[AutomationPermission] = Field(default_factory=list)
    # Websites fetch_page may open without asking in this Automation's runs (owner-set only).
    fetch_hosts: list[str] = Field(default_factory=list)
    model: str = ""
    effort: str = "medium"
    max_runs: Optional[int] = None
    run_count: int = 0
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    created_at: str
    updated_at: str
    provenance: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)


class AutomationRunStatus(str, Enum):
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"


class AutomationRun(ONBaseModel):
    id: str
    automation_id: str
    status: AutomationRunStatus = AutomationRunStatus.running
    session_id: Optional[str] = None
    trigger: dict = Field(default_factory=dict)
    execution: dict = Field(default_factory=dict)
    model: str = ""
    effort: str = "medium"
    started_at: str
    finished_at: Optional[str] = None
    summary: str = ""
    error: Optional[str] = None
    steps: list[dict] = Field(default_factory=list)
    proposed_changes: list[dict] = Field(default_factory=list)
    pending_tool_ids: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    files: list[Any] = Field(default_factory=list)
    working_dir: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    stats: dict = Field(default_factory=dict)
