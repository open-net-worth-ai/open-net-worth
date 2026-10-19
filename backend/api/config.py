"""Instance branding / white-label config API (P3-ON / ON3).

Thin: validate input → call ``AppConfigStore`` → shape the response. No financial
logic. Branding is INSTANCE config (app name + accent + logo URL), not user data —
it does not travel in a portability bundle and is not checkpointed.

``GET /api/config`` is intentionally in the auth-exempt set (``_deps._EXEMPT``) so
the login screen / auth splash can brand themselves BEFORE the user authenticates.
It exposes ONLY public, non-secret branding (name / accent hex / logo URL) — no
secrets, no user data — so the exemption is the minimal exposure needed. The write
(``PUT /api/config``) stays auth-gated like any mutation.

Also exposes GET/PUT ``/api/config/script-skills`` — the instance-level kill switch
for script-bundling skills (S4). Human-only (require_auth via the app-wide dependency);
no Lucius tool may write this flag. Default OFF — all script execution is blocked until
the operator explicitly enables it here. Unattended runs have a second instance-level switch.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.app_config_store import (
    CODE_EXEC_ENABLED_FLAG,
    NOTIFY_UNATTENDED_WRITE_FLAG,
    SCRIPT_SKILLS_FLAG,
    AppConfigStore,
    set_sql_query_enabled,
    sql_query_enabled,
)

router = APIRouter()

# A valid accent is a 6-digit hex color (#rrggbb). Empty "" clears it back to the
# built-in --accent token.
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ConfigBody(BaseModel):
    app_name: Optional[str] = None
    accent_color: Optional[str] = None
    logo_url: Optional[str] = None


class ScriptSkillsEnabledPatch(BaseModel):
    enabled: bool


@router.get("/api/config")
def get_config():
    """Public branding for the SPA — read once at boot. Auth-exempt (see module doc)."""
    return AppConfigStore().get()


@router.put("/api/config")
def put_config(body: ConfigBody):
    """Update instance branding. ``accent_color`` must be a ``#rrggbb`` hex (or "" to
    clear); a bad accent → HTTP 422. Auth-gated."""
    changes = body.model_dump(exclude_none=True)
    accent = changes.get("accent_color")
    if accent is not None:
        accent = accent.strip()
        if accent and not _HEX_RE.match(accent):
            raise HTTPException(
                status_code=422,
                detail="accent_color must be a hex color like #3b86dd (or empty to reset)",
            )
        changes["accent_color"] = accent
    return AppConfigStore().update(changes)


@router.get("/api/config/script-skills")
def get_script_skills_enabled():
    """The instance-level script-execution kill switch (S4). Default ``false`` — when off,
    no bundled Python script reaches the sandbox subprocess, regardless of per-skill flags.
    Auth-gated; never exposed via the public branding GET /api/config."""
    return {"enabled": AppConfigStore().get_flag(SCRIPT_SKILLS_FLAG, default=False)}


@router.put("/api/config/script-skills")
def put_script_skills_enabled(body: ScriptSkillsEnabledPatch):
    """Turn the instance-level script-skills kill switch on or off (human-only, auth-gated).
    When off, the ``run_script`` tool gate refuses every invocation before reaching the sandbox.
    Turning this off instantly blocks all scripts without touching any other state."""
    value = AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, body.enabled)
    return {"enabled": value}


@router.get("/api/config/notify-unattended-write")
def get_notify_unattended_write():
    """Whether a routine unattended agent write (inside its envelope) surfaces a Signals row
    + outbound notify() ping. Default ``true``. Auth-gated; never on the public branding
    GET /api/config. Does not affect the agent's own end-of-run notify() summary, nor
    surface_unattended_refusal (a write refused outside its envelope always notifies)."""
    return {"enabled": AppConfigStore().get_flag(NOTIFY_UNATTENDED_WRITE_FLAG, default=True)}


@router.put("/api/config/notify-unattended-write")
def put_notify_unattended_write(body: ScriptSkillsEnabledPatch):
    """Turn the unattended-write notify ping on or off (human-only, auth-gated; no Lucius
    tool may write this flag). When off, a routine unattended write neither logs a Signals
    row nor sends an outbound push — only the agent's own end-of-run summary remains."""
    value = AppConfigStore().set_flag(NOTIFY_UNATTENDED_WRITE_FLAG, body.enabled)
    return {"enabled": value}


# ── Ad-hoc code execution (RFC ad-hoc-code-execution) ─────────────────────────────────────
# ONE instance flag, the kill switch: human-only (auth-gated via the app-wide require_auth
# dependency), and no Lucius tool may write it. Whether the owner is asked before a script runs is
# the ordinary permission value on ``run_code`` (Settings → Permissions), not a flag here. Never
# exposed via the auth-exempt branding GET /api/config.


class CodeExecConfigPatch(BaseModel):
    enabled: Optional[bool] = None  # the kill switch


def _code_exec_state() -> dict:
    s = AppConfigStore()
    return {
        "enabled": s.get_flag(CODE_EXEC_ENABLED_FLAG, default=False),
    }


@router.get("/api/config/code-execution")
def get_code_execution_config():
    """The ad-hoc code-execution kill switch (default ``false``). Auth-gated; never on the
    public branding GET /api/config."""
    return _code_exec_state()


@router.put("/api/config/code-execution")
def put_code_execution_config(body: CodeExecConfigPatch):
    """Set the kill switch (human-only, auth-gated). ``enabled=false`` instantly blocks all
    run_code, whatever its permission value."""
    s = AppConfigStore()
    if body.enabled is not None:
        s.set_flag(CODE_EXEC_ENABLED_FLAG, body.enabled)
    return _code_exec_state()


# ── Read-only SQL access (spec 2026-08-23-lucius-sql-access-design) ───────────────────────
# ONE instance flag, human-only and auth-gated exactly like the code-execution pair above —
# no Lucius tool may write it, so the model can never turn its own access back on. Lives HERE
# rather than in api/sql_query.py because this module is the home for every /api/config/*
# route; the query LOG (which is not config) is that router's job.
#
# Default TRUE, unlike code execution — see db/app_config_store.SQL_QUERY_FLAG for why the
# asymmetry is deliberate.


class SqlQueryConfigPatch(BaseModel):
    enabled: bool


@router.get("/api/config/sql-query")
def get_sql_query_config():
    """The read-only-SQL kill switch. Defaults ``true``. Auth-gated; never on the public
    branding GET /api/config."""
    return {"enabled": sql_query_enabled()}


@router.put("/api/config/sql-query")
def put_sql_query_config(body: SqlQueryConfigPatch):
    """Flip the kill switch (human-only, auth-gated). ``enabled=false`` instantly blocks
    ``run_sql_query`` and ``describe_db_schema`` before either reaches the executor."""
    return {"enabled": set_sql_query_enabled(body.enabled)}
