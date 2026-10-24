"""
Wealth portal — FastAPI backend.

Usage (dev):
    pip install -r requirements.txt
    uvicorn backend.main:app --reload --port 8000

Frontend dev server (separate terminal):
    cd frontend && npm run dev   →  http://localhost:5174

Production (after npm run build):
    uvicorn backend.main:app --port 8000   →  http://localhost:8080

Alternatively, `python3 -m backend.main` runs uvicorn honouring the HOST/PORT
env vars (defaults 127.0.0.1:8080). DATA_DIR / CACHE_DIR / CORS_ORIGINS are also
env-configurable — see backend/core/config.py and .env.example.
"""

import asyncio
import json
import logging
import logging.config
import math
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# Always load from the project root regardless of where uvicorn is invoked
if os.environ.get("ONW_DESKTOP_MODE") != "1":
    load_dotenv(Path(__file__).parent.parent / ".env")

# Before ANY SSL context exists: give every stdlib TLS caller a populated trust store
# (core.pinned_http.ensure_default_trust_store — the one fix for an interpreter whose
# CA bundle was never installed). Reads .env, so it must follow load_dotenv.
from backend.core.pinned_http import ensure_default_trust_store

ensure_default_trust_store()

# Route all backend.* logs through the uvicorn logger so they share its
# StreamHandler and format. Must run after uvicorn calls dictConfig, so
# we do it once in lifespan rather than at module level.
_log = logging.getLogger("backend")

# Held open for the process lifetime by the ONE worker that owns the scheduler (see lifespan) —
# module-level so the fd (and thus the advisory lock) is never garbage-collected.
_SCHEDULER_LOCK_HANDLE = None


def _raise_fd_limit(target: int = 4096) -> None:
    """Raise this process's open-file-descriptor ceiling as high as the OS allows.

    macOS/launchd defaults a freshly spawned process to a soft RLIMIT_NOFILE of
    256 — far too low for this app's concurrency (RSS pollers on a 60-90s
    cadence, SSE chat streams, and background sweeps all touching the
    filesystem/network concurrently). Hitting that ceiling surfaces as a
    transient EMFILE ("Too many open files") mid-read, which JsonStore used to
    misdiagnose as data corruption (fixed separately). Raising the limit here
    reduces how often that ceiling gets hit at all. Best-effort: `resource` is
    POSIX-only, and the hard limit may itself sit below `target`.
    """
    try:
        import resource
    except ImportError:
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    new_soft = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if new_soft <= soft:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
    except (ValueError, OSError):
        _log.warning("could not raise RLIMIT_NOFILE from %d toward %d", soft, new_soft)


_raise_fd_limit()

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api._deps import require_auth
from backend.api.account_linking_api import router as account_linking_router
from backend.api.actions_api import router as actions_router
from backend.api.activity import router as activity_router
from backend.api.alerts import router as alerts_router
from backend.api.alpaca_api import router as alpaca_router
from backend.api.artifacts import router as artifacts_router
from backend.api.assets_api import router as assets_router
from backend.api.auth import router as auth_router
from backend.api.automations import router as automations_router
from backend.api.backup import router as backup_router
from backend.api.briefing import router as briefing_router
from backend.api.budgets import router as budgets_router
from backend.api.builder import router as builder_router
from backend.api.capture import router as capture_router
from backend.api.cashflow import router as cashflow_router
from backend.api.channels import router as channels_router
from backend.api.chat import router as chat_router
from backend.api.chat_pairing_api import router as chat_pairing_router
from backend.api.chat_suggestions import router as chat_suggestions_router
from backend.api.checkpoints import router as checkpoints_router
from backend.api.commitments_api import router as commitments_router
from backend.api.config import router as config_router
from backend.api.connector_secrets import router as connector_secrets_router
from backend.api.connectors_api import router as connectors_router
from backend.api.cost import router as cost_router
from backend.api.daily_total_value import router as daily_total_value_router
from backend.api.dashboard import router as dashboard_router
from backend.api.data_events_api import router as data_events_router
from backend.api.demo import router as demo_router
from backend.api.demo_lockdown import refuse_on_demo
from backend.api.demo_visitor import DemoVisitorMiddleware, bind_visitor
from backend.api.desktop_api import router as desktop_router
from backend.api.email_ingest_api import router as email_ingest_router
from backend.api.enable_banking_api import router as enable_banking_router
from backend.api.entity_types import router as entity_types_router
from backend.api.equity import router as equity_router
from backend.api.extensions import router as extensions_router
from backend.api.favicon import router as favicon_router
from backend.api.fire import router as fire_router
from backend.api.fixed_income import router as fixed_income_router
from backend.api.forecast_scorecard import router as forecast_scorecard_router
from backend.api.ibkr_api import router as ibkr_router
from backend.api.import_api import router as import_router
from backend.api.import_runs_api import router as import_runs_router
from backend.api.inbox import router as inbox_router
from backend.api.income import router as income_router
from backend.api.instruments import router as instruments_router
from backend.api.integrations import router as integrations_router
from backend.api.intel_assets import router as intel_assets_router
from backend.api.intel_digest import router as intel_digest_router
from backend.api.intel_latency import router as intel_latency_router
from backend.api.intel_signals import router as intel_signals_router
from backend.api.intel_watch import router as intel_watch_router
from backend.api.investments import router as investments_router
from backend.api.investments_xray import router as investments_xray_router
from backend.api.ledger import router as ledger_router
from backend.api.logs import router as logs_router
from backend.api.lots import router as lots_router
from backend.api.lunch_flow_api import router as lunch_flow_router
from backend.api.lunch_money_api import router as lunch_money_router
from backend.api.market_data_api import router as market_data_router
from backend.api.market_data_qa_api import router as market_data_qa_router
from backend.api.market_ticker import router as market_ticker_router
from backend.api.matches import router as matches_router
from backend.api.mcp_client_api import router as mcp_client_router
from backend.api.mcp_connect_api import router as mcp_connect_router
from backend.api.mcp_oauth_api import router as mcp_oauth_router
from backend.api.memory import router as memory_router
from backend.api.mentionables import router as mentionables_router
from backend.api.mentions_resolve import router as mentions_resolve_router
from backend.api.model_telemetry import router as model_telemetry_router
from backend.api.models import router as model_config_router
from backend.api.move import router as move_router
from backend.api.networth_api import router as networth_api_router
from backend.api.networth_history import router as networth_history_router
from backend.api.news_sources import router as news_sources_router
from backend.api.notification_log import router as notification_log_router
from backend.api.options import router as options_router
from backend.api.payees import router as payees_router
from backend.api.plaid_api import router as plaid_router
from backend.api.plan_outlook import router as plan_outlook_router
from backend.api.plan_state import router as plan_state_router
from backend.api.planning import router as planning_router
from backend.api.portability import router as portability_router
from backend.api.portfolio import router as portfolio_router
from backend.api.portfolio_drivers import router as portfolio_drivers_router
from backend.api.portfolio_history_api import router as portfolio_history_router
from backend.api.preferred_models import router as preferred_models_router
from backend.api.prices import router as prices_router
from backend.api.problem_reports import router as problem_reports_router
from backend.api.product_docs import router as product_docs_router
from backend.api.profile import router as profile_router
from backend.api.rebalance import router as rebalance_router
from backend.api.receipts import router as receipts_router
from backend.api.recurring import router as recurring_router
from backend.api.reports import router as reports_router
from backend.api.returns import router as returns_router
from backend.api.search_config_api import router as search_config_router
from backend.api.setup import router as setup_router
from backend.api.shortcuts import router as shortcuts_router
from backend.api.simplefin_api import router as simplefin_router
from backend.api.skills import router as skills_router
from backend.api.sql_query import router as sql_query_router
from backend.api.statement_parse_api import router as statement_parse_router
from backend.api.terminal import router as terminal_router
from backend.api.tool_permissions import router as tool_permissions_router
from backend.api.upload import router as upload_router
from backend.api.vault import router as vault_router
from backend.api.watchlist import router as watchlist_router
from backend.api.ynab_api import router as ynab_router
from backend.api.yodlee_api import router as yodlee_router
from backend.core.redact import redact_secrets
from backend.mcp.server import router as mcp_router


def _headless_user_messages(prompt: str) -> list[dict]:
    """The user message for an unattended (scheduled/agent) turn. The key MUST be
    ``content`` — that's what ``assistant.run`` reads (same as the web-chat path); a
    ``text`` key sends a contentless message and the provider 400s, which silently broke
    EVERY headless turn (agent run-now, scheduled cadences, prompt_lucius). Kept as a
    tiny module-level helper so it's unit-testable without booting the lifespan."""
    return [{"role": "user", "content": prompt}]


async def _start_connector(name: str, start) -> None:
    """Start an optional chat connector without ever failing the boot. Connectors reach out
    to third-party services in the background; an unreachable service or a config-read error
    must degrade gracefully (logged) — never abort startup. The connectors themselves retry
    their network connection in the background, so this only guards the synchronous spawn."""
    try:
        await start()
    except Exception:
        _log.exception("%s connector failed to start (non-fatal)", name)


async def _headless_turn_runner(
    prompt: str, *, allowed_tools=None, model=None, lean_prompt=False
) -> str:
    """The headless-turn runner registered on the ``core.headless_turn`` seam (below, in
    ``lifespan``) so a service can run one Lucius turn WITHOUT ``services`` importing ``ai``
    (P2-SK2 §B.4). Returns the assistant's text output.

    The live caller is ``services.self_harness``, reached from an Automation executor. (It was
    also the named-agent path until Agents were retired on 2026-08-02.)

    THE TURN IS MARKED UNATTENDED (``set_unattended``) — nobody is in the loop to answer a
    prompt or tap a confirm card. That is a plain fact about the caller, and the features that
    genuinely need it read it directly (``run_script``'s instance-level unattended switch and
    ``run_code``'s interactive-only rule). It is NOT a permission ceiling. The external-MCP
    offer was listed here once and does NOT read it: an external tool's permission level
    decides whether it fires, identically on every surface.

    Kept as a module-level function (like ``_headless_user_messages`` above) so it is
    unit-testable without booting the lifespan.

    allowed_tools = the EXPLICIT tool offer for this turn. Three values, three meanings:
      * a non-empty list → offer exactly those tools;
      * ``[]`` (self-harness's distillation pass) → offer NOTHING, a pure completion. ``[] or X``
        would wrongly widen it back, so the None test below is explicit;
      * ``None`` → the same default offer an interactive chat turn gets: the CORE set plus
        whatever the model loads for itself via ``find_tools``.

    model = per-caller override; None → the instance default (ModelConfigStore, the same source
    as interactive chat).

    lean_prompt = use the short system prompt instead of the full conversational one — right
    for a zero-tool completion that only has to return text.
    """
    import uuid

    from backend.ai.assistant import clear_session
    from backend.ai.assistant import run as _assistant_run
    from backend.ai.prompts import build_lean_system_prompt, build_system_prompt
    from backend.ai.tools import _skill_scope
    from backend.core.cost_context import clear_cost_surface, set_cost_surface
    from backend.core.unattended import reset_unattended, set_unattended
    from backend.db.model_config_store import ModelConfigStore

    # Snapshot BEFORE narrowing (finding 2): this runner is usually the top-level owner, but
    # it can be NESTED — a caller already inside a narrowed turn awaits it in the SAME async
    # context (no forked contextvars.Context). An unconditional ``clear_scope()`` in the
    # finally would wipe the OUTER, still-running turn's narrowed scope to None, silently
    # WIDENING it for the rest of that turn. ``restore_scope(snap)`` is correct for BOTH: at
    # the top level ``snap`` is the all-None/0 default, so it is byte-identical to
    # ``clear_scope()``; when nested it puts the caller's scope back.
    _snap = _skill_scope.snapshot_scope()
    if allowed_tools is not None:
        _skill_scope.set_offer_scope(allowed_tools)
    run_model = model or ModelConfigStore().resolved()["model"]
    short_id = str(uuid.uuid4())[:8]
    session_id = f"headless-{short_id}"
    set_cost_surface("scheduled")
    # Nobody is watching a scheduled run — see core.unattended. Reset in the finally below.
    _unattended_token = set_unattended(True)
    parts: list[str] = []
    try:
        async for event in _assistant_run(
            session_id=session_id,
            new_messages=_headless_user_messages(prompt),
            system=(
                build_lean_system_prompt(allowed_tools=allowed_tools)
                if lean_prompt
                else build_system_prompt()
            ),
            model=run_model,
        ):
            if event.get("type") == "text_delta":
                parts.append(event.get("text", ""))
    finally:
        _skill_scope.restore_scope(_snap)
        clear_cost_surface()
        reset_unattended(_unattended_token)
        clear_session(session_id)
    return "".join(parts).strip()


def _log_auth_first_run_notice() -> None:
    """First-run auth notice (RFC P3-AUTH §6). If the instance has no password and no
    explicitly-configured API token, local access is open and remote is refused. The generated
    API token is NEVER logged (app.log is readable through the LogViewer and the
    get_recent_logs tool); the owner reads it from Settings. AUTH_PASSWORD only seeds the
    stored hash, so say it is ignored from now on — never "remove it": the published
    docker-compose.yml refuses to start without it.
    """
    try:
        from backend.core.config import get_settings as _get_settings
        from backend.db.auth_store import AuthStore

        _auth_settings = _get_settings()
        if _auth_settings.auth_enabled and os.environ.get("ONW_DESKTOP_MODE") != "1":
            _auth_store = AuthStore()
            if not _auth_store.is_configured():
                _log.warning(
                    "Auth not configured. Local (loopback) access is open; remote access is "
                    "refused. Set a password to expose this instance. The API token is not "
                    "logged: read it in Settings."
                )
            if _auth_settings.auth_password:
                _log.warning(
                    "AUTH_PASSWORD only sets the password while none is stored; once one is, "
                    "it is ignored — change the password in Settings → Security. Docker "
                    "Compose needs it kept set; elsewhere you can drop it from .env."
                )
    except Exception:
        _log.exception("auth first-run notice failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── One instance per working folder. BEFORE the migration below, because migrating a
    # database another instance is serving is the worst version of this problem. A sibling
    # uvicorn worker of THIS instance is fine and expected (see core.instance_lock); a second
    # instance on the same folder refuses to boot rather than silently interleaving caches,
    # checkpoints and /rewind with the one already running.
    from backend.core.config import get_settings as _settings_for_lock
    from backend.core.instance_lock import acquire as _acquire_instance
    from backend.core.paths import DATA_DIR as _lock_dir

    _acquire_instance(
        _lock_dir.parent,
        _settings_for_lock().port,
        instance_id=os.environ.get("ONW_DESKTOP_INSTANCE_ID"),
    )

    # ── DB schema: fail-closed (spec 2026-08-05 D3). A migration failure RAISES
    # and the app does NOT boot — deliberately unlike the legacy "non-fatal"
    # JSON boot fixes below, which are being absorbed into Alembic revisions.
    from backend.db.migrate import ensure_migrated

    ensure_migrated(allow_live_instance=True)

    # ── A shell in the browser runs as this user: make this process unreadable to it, and drop
    # the secrets Settings has already absorbed from the live environment. AFTER Settings is
    # built (they are read once, at construction) and BEFORE the app serves a request, so before
    # anything can spawn a terminal. After the migration gate: only the instance claim precedes it.
    from backend.core.config import gateway_active
    from backend.core.config import get_settings as _hardening_settings
    from backend.core.process_hardening import forget_env, restrict_process_inspection

    if gateway_active() or _hardening_settings().terminal_enabled:
        restrict_process_inspection()
        forget_env(
            (
                "ONW_GATEWAY_TOKEN",
                "AUTH_PASSWORD",
                "AUTH_PASSWORD_HASH",
                "AUTH_API_TOKEN",
                "AUTH_SESSION_KEY",
            )
        )

    from backend.services.portability import sweep_stale_exports

    sweep_stale_exports()

    # Boot sweep (B20): the parse-progress registry is in-process and ephemeral — a restart
    # (crash, `kill -9`, redeploy) mid-parse leaves no live entry to ask, so any statement
    # still stamped `parsing` from before THIS boot can never actually finish. Flip it to
    # `interrupted` rather than let it read as a ghost "still working" forever.
    from backend.db.statement_store import StatementStore

    try:
        _interrupted = StatementStore().sweep_interrupted_parses()
        if _interrupted:
            _log.info("marked %d in-flight statement parse(s) interrupted on boot", _interrupted)
    except Exception:
        _log.exception("interrupted-parse boot sweep failed (non-fatal)")

    # Boot sweep for orphaned atomic-write temp files (storage plan 1, task 11): a
    # crash between temp-write and os.replace in core.io.atomic_write_text leaves a
    # `*.tmp-*` sibling behind. The live install accumulated ~1.1 GB of these before
    # the write path itself was hardened to clean up on its own failure — this sweep
    # is the backstop for whatever already accumulated (and any store not yet on the
    # hardened path). Runs every boot; only removes files older than the horizon so
    # an atomic write genuinely in flight is never touched.
    from backend.core.io import sweep_stale_tmp_files
    from backend.core.paths import DATA_DIR

    _swept = sweep_stale_tmp_files(DATA_DIR)
    if _swept:
        _log.info("swept %d stale atomic-write temp files from %s", _swept, DATA_DIR)

    # Copy uvicorn's StreamHandler to backend so all backend.* child loggers
    # share uvicorn's output stream and format. uvicorn puts its handler on
    # the "uvicorn" logger (not "uvicorn.error"), so that's what we read here.
    uv_logger = logging.getLogger("uvicorn")
    _log.handlers = list(uv_logger.handlers)
    _log.setLevel(logging.INFO)
    _log.propagate = False

    # Also mirror all backend.* records to a rotating file so the operator
    # (and the AI Logs Viewer) can read them without tailing stdout.
    from backend.core.logging import add_file_handler

    add_file_handler()

    import backend.ai.checkpoint  # noqa: F401 — registers the pre-write hook (RFC P1-A4)
    import backend.ai.tools  # noqa: F401 — registers the tool-name provider on the core seam (P2-SK1)
    from backend.connectors.telegram import bot as telegram_bot
    from backend.db.skill_store import SkillStore
    from backend.scheduler import start, stop

    # Seed the built-in starter skills ONLY on a fresh instance (empty/absent
    # SKILLS_DIR) — so a user who deletes a starter doesn't get it resurrected on
    # the next boot (RFC P2-SK1 §2.2). seed_starters() itself is idempotent by id.
    try:
        _skill_store = SkillStore()
        if not _skill_store.all():
            inserted = _skill_store.seed_starters()
            if inserted:
                _log.info("seeded %d starter skill(s)", inserted)
    except Exception:
        _log.exception("starter-skill seeding failed (non-fatal)")

    # A starter the owner never edited still carries whatever body it was first seeded with;
    # replace a SUPERSEDED built-in body with the current one (edited copies are untouched).
    try:
        refreshed = SkillStore().refresh_superseded_starters()
        if refreshed:
            _log.info("refreshed superseded starter skill(s): %s", ", ".join(refreshed))
    except Exception:
        _log.exception("starter-skill refresh failed (non-fatal)")

    # A starter shipped after this install was seeded is installed once (a deleted one stays gone).
    try:
        added = SkillStore().install_added_starters()
        if added:
            _log.info("installed new starter skill(s): %s", ", ".join(added))
    except Exception:
        _log.exception("new starter-skill install failed (non-fatal)")

    # A starter that no longer ships is deleted while still unedited — unless an Automation's
    # text names it, since the run would then ask for a skill that is gone.
    try:
        from backend.db.automation_store import AutomationStore
        from backend.db.skill_store import retired_starter_ids

        named = " ".join(
            f"{a.get('name', '')} {a.get('description', '')} {a.get('instructions', '')}"
            for a in AutomationStore().all()
        )
        keep = frozenset(sid for sid in retired_starter_ids() if sid in named)
        retired = SkillStore().retire_dropped_starters(keep=keep)
        if retired:
            _log.info("retired dropped starter skill(s): %s", ", ".join(retired))
    except Exception:
        _log.exception("starter-skill retirement failed (non-fatal)")

    # Lots left behind by a position removed before 2026-10-01 (the removal didn't take its
    # lots with it) still count as held in the history and returns; repair the exact signature.
    try:
        from backend.services.lot_integrity import (
            repair_orphan_lots,
            repair_unvested_placeholder_lots,
        )

        repair_orphan_lots()
        repair_unvested_placeholder_lots()
    except Exception:
        _log.exception("orphan-lot repair failed (non-fatal)")

    # A position's figures follow its records (ADR 2026-10-07); drift can only enter now
    # through a raw write or a restored older backup — re-derive it and say so. After the lot
    # repairs above, so a removed orphan lot is already gone from the records.
    try:
        from backend.services.position_figures import reconcile

        reconcile()
    except Exception:
        _log.exception("position-figure reconcile failed (non-fatal)")

    # A starter folder can ship more than SKILL.md (a report template): write those files into
    # each installed, unedited copy — after the refresh above, so a refreshed body gets them.
    try:
        from backend.services.starter_skill_files import sync_installed_starter_files

        sync_installed_starter_files()
    except Exception:
        _log.exception("starter-skill file sync failed (non-fatal)")

    # Ensure the managed skill-creator body + reference bundle are committed under one exclusive
    # transaction. Runs every boot, including instances created before this starter existed.
    # The installer raises on a partial write (leaving the skill disabled); startup alone catches
    # and logs so one damaged optional bundle cannot prevent the application from booting.
    try:
        from backend.services.skill_creator_bundle import ensure_skill_creator_installed

        ensure_skill_creator_installed()
    except Exception:
        _log.exception("skill-creator install failed (non-fatal)")

    # Ensure the statement-import built-in skill is ALWAYS present (seed if absent, refresh
    # body on version bump). Like skill-creator: managed builtin, runs every boot, best-effort.
    try:
        from backend.services.statement_import_bundle import ensure_statement_import_installed

        ensure_statement_import_installed()
    except Exception:
        _log.exception("statement-import install failed (non-fatal)")

    # NOTE: four one-time data migrations used to run here (embedded price alerts → AlertStore,
    # the "default" → "my_holdings" watchlist rename, retiring auto model switching, and the
    # per-kind payee-category backfill). All were removed 2026-08-28: each rewrote a data shape
    # that only ever existed in the owner's own pre-existing book, so on any install of THIS
    # repo they were four no-op passes over an empty database on every single boot. The ADRs
    # behind them (0014, 0018, 2026-08-06, 2026-07-10) still record the decisions.

    # Seed the starter categories and groups on a genuinely fresh instance.
    # Idempotent: only runs when categories.json is absent or empty. CategoryStore is the sole
    # source of truth for categories. The legacy profile fields
    # (custom_categories/deleted_builtins/hidden_categories/category_color_overrides) were
    # COPIED into the store by that migration and NEVER DELETED — this comment used to claim
    # they "are gone", which is why nobody re-checked for two years' worth of changes while
    # `get_profile` kept serving 1,696 bytes of retired-slug-keyed data to every session. They
    # are now stripped on read (services.profile._DEAD_CATEGORY_FIELDS) and removed from
    # storage by a one-shot migration since removed (not a
    # boot migration). Do not re-assert a state here without grepping for it first.
    try:
        from backend.services.categories import ensure_builtins_seeded as _seed_builtin_categories

        _seed_builtin_categories()
    except Exception:
        _log.exception("category seed failed (non-fatal)")

    # One-time idempotent migration: drop the "uncategorized" category sink. Route through the
    # tested delete_category choke point (reassign→None) so every reference is cleared exactly
    # as an ordinary category deletion: top-level AND split-line category="uncategorized" → None,
    # budgets/targets/assignments/groups scrubbed, category-only rules deleted (rules with extra
    # actions disabled), and the store row removed so it can never be re-seeded. Safe to re-run:
    # if the row is already gone, nothing happens.
    try:
        from backend.db.category_store import CategoryStore as _CSMig
        from backend.services import categories as _cat_mig

        if _CSMig().get("uncategorized") is not None:
            _log.info("migrating: removing 'uncategorized' category sink")
            _res = _cat_mig.delete_category("uncategorized", reassign=True, reassign_to=None)
            _log.info(
                "  migration complete: 'uncategorized' removed (reassigned %d txn(s) to None)",
                _res.get("reassigned", 0),
            )
    except Exception:
        _log.exception("uncategorized→None migration failed (non-fatal)")

    # First-boot envelope seed (fresh installs only): mint one 1:1 envelope per currently-
    # budgeted category so a fresh book's budget is immediately usable. A no-op once
    # envelopes.json is non-empty (docs/adr/2026-07-05-envelope-id-native.md — money is
    # envelope-id native end to end now; there is no category-keyed representation left to
    # resync on boot).
    try:
        from backend.services.envelopes import ensure_envelopes_migrated as _ensure_envelopes

        _env_res = _ensure_envelopes()
        if _env_res.get("migrated"):
            _log.info(
                "envelope seed: %d envelope(s) minted for a fresh book",
                _env_res.get("envelope_count", 0),
            )
    except Exception:
        _log.exception("envelope seed failed (non-fatal)")

    # Credit-card payment-envelope sync (ADR 2026-07-09-ynab-engine-credit-card-payment-
    # envelopes.md): ensures the pinned "Credit Card Payments" group + one envelope per on-budget
    # credit card. Payment envelopes are the ONLY credit-card engine (the "signed" mode was removed
    # 2026-07-10), so this always runs at boot. Idempotent, non-fatal.
    try:
        from backend.services.planning import sync_card_payment_envelopes as _sync_card_pay

        _card_res = _sync_card_pay()
        if _card_res.get("synced") or _card_res.get("removed"):
            _log.info(
                "card-payment envelope sync: synced=%d removed=%d",
                _card_res.get("synced", 0),
                _card_res.get("removed", 0),
            )
    except Exception:
        _log.exception("card-payment envelope sync failed (non-fatal)")

    # News source registry seed (INT9 Phase A, .claude/rfcs/news-source-management.md): mints
    # one row per builtin regulatory/outlet/GDELT/resolver-policy source not already in the
    # store. Idempotent — never touches an existing row (a user's disabled builtin survives),
    # and a code table's new entry simply appears as a fresh id on a later boot.
    try:
        from backend.services.news_sources import ensure_builtin_sources as _ensure_sources

        _src_res = _ensure_sources()
        if _src_res.get("seeded"):
            _log.info("news source seed: %d builtin source(s) added", _src_res["seeded"])
    except Exception:
        _log.exception("news source seed failed (non-fatal)")

    # Register the headless-turn runner (module-level ``_headless_turn_runner`` above) on the
    # core seam so a service can run one Lucius turn WITHOUT importing ai (P2-SK2 §B.4).
    # ``services.self_harness`` is the live caller, reached from an Automation executor.
    from backend.core.headless_turn import register_runner

    register_runner(_headless_turn_runner)

    # Automation Lucius runs are durable conversations. Register the AI-layer adapter on
    # the dependency-inverted core/service seams only after the AI package is initialized.
    from backend.ai.automation_runner_adapter import (
        register_automation_adapters,
        unregister_automation_adapters,
    )

    register_automation_adapters()

    # Boot recovery for server-owned turns (concurrent-conversations RFC Phase 3), modelled
    # on recover_automation_runs just above: a conversation whose last_turn_status is still
    # "running" in a FRESH process was interrupted by a crash (a clean shutdown rewrites it
    # to "cancelled" first — see turns.shutdown() in this module's post-yield block below).
    from backend.ai.turns import recover_interrupted_turns

    recover_interrupted_turns()

    # An import run still "running" belonged to the process that just died.
    from backend.services import import_runs

    import_runs.recover_interrupted()

    # Register the significance-screen consumer on the core.news_arrivals seam (INT5,
    # news-ingestion-latency.md §4) — monitoring.collect.gather_rss/gather_matched emit newly-
    # inserted evidence rows here on every RSS/hot-tier fetch; this is the volume-independent
    # "one material headline is enough" fast path. Idempotent registration (last one wins).
    try:
        from backend.monitoring.news_screen import register as _register_news_screen

        _register_news_screen()
    except Exception:
        _log.exception("news_screen consumer registration failed (non-fatal)")

    _log.info("backend started — logging active")

    _log_auth_first_run_notice()

    # Scheduler in ONE worker only. Under uvicorn --workers every worker runs this lifespan, but
    # the periodic jobs (price checks, RSS polls, AI news screens, digests) must fire ONCE — not
    # per worker, or N workers means N× the background load and AI cost. The first worker to grab
    # an advisory lock owns the scheduler; the rest skip it. Single-worker: that one worker always
    # wins, so behaviour is unchanged.
    global _SCHEDULER_LOCK_HANDLE
    try:
        from backend.core.file_lock import lock_exclusive
        from backend.core.paths import DATA_DIR

        _SCHEDULER_LOCK_HANDLE = open(DATA_DIR / "scheduler.lock", "a+", encoding="utf-8")
        lock_exclusive(_SCHEDULER_LOCK_HANDLE)
        start()
        _log.info("scheduler: started (this worker owns the scheduler lock)")
    except (OSError, BlockingIOError):
        if _SCHEDULER_LOCK_HANDLE is not None:
            _SCHEDULER_LOCK_HANDLE.close()
            _SCHEDULER_LOCK_HANDLE = None
        _log.info("scheduler: another worker owns it — periodic jobs skipped in this worker")
    # Register the always-on in-app notification channel up front. It registers as a side
    # effect of importing services.notify (the bell-icon feed); without this it would only
    # appear after the first notify() call, so /api/notify-channels (the "Send to" picker)
    # would miss it on a fresh boot.
    import backend.services.notify  # noqa: F401 — registers the "in_app" channel
    from backend.services import keep_awake as _keep_awake

    _keep_awake.apply_saved()

    # Loop-lag watchdog. Runs in every worker; see core.loop_watch for why measuring beat
    # reading code here. Held on the app so shutdown can cancel it.
    from backend.core.loop_watch import watch_event_loop

    app.state.loop_watch_task = asyncio.create_task(watch_event_loop(), name="loop_watch")

    # One-time bulk backfill (Phase 3 — ADR 0019): populate the rebuildable price cache for
    # all currently-held instruments so the returns engine has historical depth from day one.
    # Guarded by a marker in CACHE_DIR (checkpoint-excluded) so it only runs once per
    # successfully-completed backfill sweep; re-running is safe (idempotent upsert-skip)
    # but has non-trivial network cost. A daemon thread keeps startup non-blocking;
    # individual failures are logged inside backfill_held_instruments and never propagate here.
    # Skipped under ONW_QA_MODE — QA/isolated runs must not hit external APIs.
    try:
        from backend.core.config import get_settings as _get_settings

        if not _get_settings().onw_qa_mode:
            import threading as _threading

            from backend.core.paths import CACHE_DIR as _CACHE_DIR

            _backfill_marker = _CACHE_DIR / "price_backfill_v1.marker"
            if not _backfill_marker.exists():

                def _run_bulk_backfill() -> None:
                    try:
                        from backend.services.prices import backfill_held_instruments

                        results = backfill_held_instruments()
                        total = sum(results.values())
                        _log.info(
                            "price backfill complete: %d instrument(s), %d new point(s) written",
                            len(results),
                            total,
                        )
                        _backfill_marker.touch()
                    except Exception:
                        _log.exception("price backfill failed (non-fatal)")

                _t = _threading.Thread(
                    target=_run_bulk_backfill, daemon=True, name="price-backfill"
                )
                _t.start()
    except Exception:
        _log.exception("price backfill startup hook failed (non-fatal)")

    # Seed benchmark-roster instruments (SPY/VWRA/QQQ/ACWI/BTC) so the returns/risk engine
    # can resolve them.  The instrument records are created synchronously (fast — just a
    # find_or_create per entry); price history is backfilled in a daemon thread so startup
    # stays non-blocking.  Both steps are idempotent and skipped under ONW_QA_MODE.
    try:
        from backend.core.config import get_settings as _get_settings

        if not _get_settings().onw_qa_mode:
            from backend.services.benchmark_seed import seed_benchmark_instruments

            _bench_result = seed_benchmark_instruments()
            if _bench_result.get("created"):
                _log.info(
                    "benchmark instruments seeded: %d created, %d already present",
                    _bench_result["created"],
                    _bench_result.get("skipped", 0),
                )

            import threading as _threading

            def _run_benchmark_backfill() -> None:
                try:
                    from backend.services.benchmark_seed import backfill_benchmark_prices

                    results = backfill_benchmark_prices()
                    total = sum(results.values())
                    _log.info(
                        "benchmark price backfill complete: %d instrument(s), %d new point(s)",
                        len(results),
                        total,
                    )
                except Exception:
                    _log.exception("benchmark price backfill failed (non-fatal)")

            _bt = _threading.Thread(
                target=_run_benchmark_backfill, daemon=True, name="benchmark-price-backfill"
            )
            _bt.start()
    except Exception:
        _log.exception("benchmark instrument seeding failed (non-fatal)")

    await _start_connector("telegram", telegram_bot.start)
    # Register the Telegram notification channel on the core.channels seam (only when
    # the connector boots) so the Notifier can fan out to Telegram (P2-SK2 §B.5). The
    # channel's notify() no-ops until the bot is actually connected, so it is safe to
    # register even while the connector is still retrying its connection in the background.
    import backend.connectors.telegram.channel  # noqa: F401 — registers the "telegram" channel

    # Register the enabled outbound notification channels (email-out + webhook)
    # on the core.channels seam so the Notifier can fan out to them (only when
    # configured+enabled).
    from backend.connectors.email_out.channel import register_if_active as _register_email_channel
    from backend.connectors.webhook.channel import register_if_active as _register_webhook_channel

    _register_email_channel()
    _register_webhook_channel()

    # Outbound external-MCP client (P3-MCP-CLIENT): register the manager on the
    # core.mcp_external seam so the interactive assistant can offer/dispatch external
    # tools without ai importing backend.mcp. Best-effort connect (per-server isolated).
    #
    # NON-BLOCKING boot (ADR 2026-08-22-external-mcp-stdio-connections-persist-for-the-app-
    # lifetime): `connect_enabled()` now also establishes each stdio server's APP-LIFETIME
    # pooled connection (not just an ephemeral discovery handshake), so a slow/unreachable/
    # misconfigured server must never delay the app becoming ready to serve requests. It is
    # scheduled as a background task and NOT awaited here — `yield` runs immediately. The
    # task reference is kept (module-scope-adjacent local var closed over below) so it isn't
    # garbage-collected mid-flight, and given a bounded wait at shutdown.
    from backend.mcp.client_manager import register_on_seam, teardown_seam

    _mcp_client_manager = register_on_seam()

    def _run_mcp_boot_connect() -> None:
        try:
            _mcp_client_manager.connect_enabled()
        except Exception:
            _log.exception("external mcp connect failed (non-fatal)")

    _mcp_boot_connect_task = asyncio.create_task(
        asyncio.to_thread(_run_mcp_boot_connect), name="mcp-boot-connect"
    )

    # A self-updating CLI can change how tool use is routed without erroring. Probe the
    # configured one once per installed version, in the background — never blocks boot, and
    # skipped under QA/isolated runs, which must not spend a real subscription's turns.
    from backend.core.config import get_settings as _cli_probe_settings

    if not _cli_probe_settings().onw_qa_mode:
        from backend.ai.cli_probe import check_on_startup as _cli_self_check

        app.state.cli_self_check_task = asyncio.create_task(
            _cli_self_check(), name="cli-self-check"
        )

    # GET /api/events streams never end on their own; this lets them close on SIGTERM/SIGINT
    # instead of holding uvicorn's graceful shutdown open (see core.data_events).
    from backend.core.data_events import install_shutdown_watch, mark_closing

    install_shutdown_watch()

    yield
    mark_closing()
    # Cancel every in-flight server-owned turn (concurrent-conversations RFC Phase 3) BEFORE
    # tearing down the Automation adapters — a graceful shutdown journals "cancelled" for
    # each one, so recover_interrupted_turns() has nothing stale to correct on the next boot.
    from backend.ai.turns import shutdown as shutdown_turns

    await shutdown_turns()
    unregister_automation_adapters()
    await telegram_bot.stop()
    _keep_awake.release()
    # Release the working-folder claim last, so the folder stays owned for the whole of a
    # graceful shutdown rather than freeing up while turns are still being journalled.
    from backend.core.instance_lock import release as _release_instance

    stop()
    if _SCHEDULER_LOCK_HANDLE is not None:
        _SCHEDULER_LOCK_HANDLE.close()
        _SCHEDULER_LOCK_HANDLE = None
    # Bounded wait on the boot-connect task: cancelling it wouldn't actually interrupt a
    # blocking spawn already running in its worker thread (asyncio.to_thread cancellation is
    # cooperative-only), so this just gives it a short window to finish naturally before
    # close_all_pooled_connections (inside teardown_seam) closes whatever it managed to
    # register. Never blocks shutdown indefinitely — a straggler thread finishing after the
    # timeout just registers a connection nothing will read again, no worse than any other
    # crash-path leak this same backstop already documents.
    try:
        await asyncio.wait_for(asyncio.shield(_mcp_boot_connect_task), timeout=5)
    except Exception:  # noqa: BLE001 — best-effort wait, see the comment above
        pass
    teardown_seam()
    _release_instance()


app = FastAPI(
    title="Open Net Worth API",
    lifespan=lifespan,
    # app-wide auth gate (RFC P3-AUTH §4), then the public-demo lockdown (inert off-demo)
    dependencies=[Depends(require_auth), Depends(bind_visitor), Depends(refuse_on_demo)],
    # FastAPI registers these routes OUTSIDE the app-wide dependency, so they would serve the
    # full route map to anyone who can reach the port — and /docs would shadow the product
    # docs mount below. Off, not gated: nothing in the app consumes the schema.
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


from backend.api.fx_errors import missing_fx_response
from backend.core.errors import MissingFxRateError

app.add_exception_handler(MissingFxRateError, missing_fx_response)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    # The SPA mount answers unmatched POSTs with 405. QA-only routes must be
    # indistinguishable from absent routes outside the QA process, including
    # non-GET methods, so stop them before that catch-all mount sees the path.
    if not _settings.onw_qa_mode and request.url.path.startswith("/api/qa/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    try:
        response = await call_next(request)
    except Exception:
        ms = (time.monotonic() - start) * 1000
        _log.exception("%s %s → 500  (%.0fms)", request.method, request.url.path, ms)
        raise
    ms = (time.monotonic() - start) * 1000
    if response.status_code >= 500:
        _log.error(
            "%s %s → %d  (%.0fms)", request.method, request.url.path, response.status_code, ms
        )
    else:
        _log.info(
            "%s %s → %d  (%.0fms)", request.method, request.url.path, response.status_code, ms
        )
    return response


# A 422 is raised BEFORE the route runs, so structured diagnostics are recorded here.
# Valid JSON is secret-masked and truncated. Malformed input is never logged verbatim:
# without a parse tree there is no reliable way to distinguish credentials from ordinary
# fields. The response shape remains FastAPI's default, modulo `_json_safe_errors` below.
_MAX_LOGGED_BODY = 2000


def _json_safe_errors(value):
    """Recursively replace a NaN/Infinity float in a pydantic error tree with its repr.

    A field declared ``Field(allow_inf_nan=False)`` (owned-assets money fields, security
    review H1) correctly REJECTS a literal ``NaN``/``Infinity`` in the request body — but
    pydantic's error dict echoes the raw offending value back as `error["input"]`, and
    Starlette's ``JSONResponse.render`` calls ``json.dumps(..., allow_nan=False)``: it
    raises a bare ``ValueError`` on a real (non-string) float nan/inf, turning what should
    be a clean 422 into an unhandled 500. Every other value passes through unchanged —
    this is not a general sanitizer, only the one shape ``json.dumps`` cannot encode."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: _json_safe_errors(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_errors(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001 — diagnostics must never mask the 422 itself
        raw = b""
    try:
        rendered = json.dumps(redact_secrets(json.loads(raw)), ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — malformed bodies may contain unredactable credentials
        rendered = "<malformed JSON omitted>"
    # loc/type/msg only: the errors also echo `input`, which would re-leak a value
    # the body redaction just masked.
    where = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()]
    _log.warning(
        "%s %s → 422  %s  body=%s%s",
        request.method,
        request.url.path,
        "; ".join(where) or "<no detail>",
        rendered[:_MAX_LOGGED_BODY],
        "…" if len(rendered) > _MAX_LOGGED_BODY else "",
    )
    errors = _json_safe_errors(exc.errors())
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


from backend.core.config import get_settings
from backend.core.shipped_assets import DOCS_SITE_DIST_DIR, FRONTEND_DIST_DIR
from backend.core.spa_csp import spa_csp
from backend.core.version import AGENT_CONTRACTS, APP_VERSION
from backend.services.mentions.entity_types import entity_types

_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,  # default: dev origins 5174 + 8080 (CORS_ORIGINS env override)
    allow_methods=["*"],
    allow_headers=["*"],
)


from starlette.middleware.gzip import GZipMiddleware


class _SelectiveGZip(GZipMiddleware):
    """GZip everything except the live event stream. A transcript response runs multi-MB of
    highly-compressible JSON and was measured going over the wire RAW (3.0MB → ~250KB
    gzipped) — a phone pays for every byte on every open. SSE stays uncompressed so no
    proxy/browser buffering can delay a live turn's events."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").endswith("/live"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_SelectiveGZip, minimum_size=1024)


from backend.core import req_timing as _req_timing


class _RequestTimingMiddleware:
    """Pure-ASGI per-request phase profiler (owner-requested, always on). Emits ONE line per
    request telling you WHERE the time went:

        PERF-REQ GET /api/commitments total=3877ms db=1180ms(48q 94208rows) compute=2697ms

    ``db`` is time inside the SQLite/PG driver (recorded by engine.py's cursor events), ``q`` is
    how many statements ran, ``rows`` is how many rows they returned, and ``compute`` is
    everything else (Python folds, FX, serialization). A request that loads the whole ledger
    shows up immediately as a huge ``q``/``rows`` — the "query, don't load-all" smell. Pure ASGI
    (not BaseHTTPMiddleware) so the timing ContextVar propagates cleanly into the sync-endpoint
    threadpool where the DB calls happen."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        stats = _req_timing.start_request()
        start = time.monotonic()

        async def _send(message):
            if message["type"] == "http.response.start":
                total_ms = (time.monotonic() - start) * 1000
                db_ms = stats["db_ms"]
                _log.info(
                    "PERF-REQ %s %s total=%.0fms db=%.0fms(%dq) compute=%.0fms",
                    scope.get("method", "?"),
                    scope.get("path", "?"),
                    total_ms,
                    db_ms,
                    stats["db_queries"],
                    max(total_ms - db_ms, 0.0),
                )
            await send(message)

        await self.app(scope, receive, _send)


app.add_middleware(_RequestTimingMiddleware)
# A public demo's visitor identity cookie (inert off-demo); outside the timing middleware so the
# cookie is on the request every inner layer sees.
app.add_middleware(DemoVisitorMiddleware)

from backend.api.gateway import GatewayTokenMiddleware
from backend.core.config import gateway_active

# Added LAST so it is outermost: the gateway token is checked before routing and the static mounts.
app.add_middleware(GatewayTokenMiddleware)

app.include_router(auth_router)
app.include_router(prices_router)
app.include_router(market_ticker_router)
app.include_router(portfolio_router)
app.include_router(portfolio_drivers_router)
app.include_router(instruments_router)
app.include_router(favicon_router)
app.include_router(actions_router)
app.include_router(briefing_router)
app.include_router(cashflow_router)
app.include_router(commitments_router)
app.include_router(dashboard_router)
app.include_router(budgets_router)
app.include_router(fire_router)
app.include_router(chat_router)
app.include_router(data_events_router)
app.include_router(desktop_router)
app.include_router(chat_suggestions_router)
app.include_router(capture_router)
app.include_router(shortcuts_router)
app.include_router(mcp_connect_router)
app.include_router(upload_router)
app.include_router(import_router)
# statement_parse_router MUST come after import_router — its {statement_id}/preview template
# matches literal siblings (csv/preview, preview) that import_router registers first; see
# statement_parse_api.py's own module docstring.
app.include_router(statement_parse_router)
app.include_router(profile_router)
app.include_router(ledger_router)
app.include_router(receipts_router)
app.include_router(lots_router)
app.include_router(fixed_income_router)
app.include_router(options_router)
app.include_router(notification_log_router)
app.include_router(intel_assets_router)
app.include_router(intel_digest_router)
app.include_router(intel_watch_router)
app.include_router(intel_signals_router)
app.include_router(intel_latency_router)
app.include_router(news_sources_router)
app.include_router(recurring_router)
app.include_router(watchlist_router)
app.include_router(alerts_router)
app.include_router(email_ingest_router)
app.include_router(chat_pairing_router)
app.include_router(search_config_router)
app.include_router(simplefin_router)
app.include_router(plaid_router)
app.include_router(lunch_flow_router)
app.include_router(enable_banking_router)
app.include_router(alpaca_router)
app.include_router(artifacts_router)
app.include_router(vault_router)
app.include_router(assets_router)
app.include_router(automations_router)
app.include_router(ibkr_router)
app.include_router(yodlee_router)
app.include_router(lunch_money_router)
app.include_router(ynab_router)
app.include_router(import_runs_router)
app.include_router(account_linking_router)
app.include_router(integrations_router)
app.include_router(setup_router)
app.include_router(networth_history_router)
app.include_router(daily_total_value_router)
app.include_router(networth_api_router)
app.include_router(portfolio_history_router)
app.include_router(preferred_models_router)
app.include_router(cost_router)
app.include_router(model_telemetry_router)
app.include_router(backup_router)
app.include_router(builder_router)
app.include_router(checkpoints_router)
app.include_router(skills_router)
app.include_router(returns_router)
app.include_router(rebalance_router)
app.include_router(reports_router)
app.include_router(forecast_scorecard_router)
app.include_router(payees_router)
app.include_router(matches_router)
app.include_router(market_data_router)
if _settings.onw_qa_mode:
    app.include_router(market_data_qa_router)
app.include_router(memory_router)
app.include_router(equity_router)
app.include_router(income_router)
app.include_router(activity_router)
app.include_router(inbox_router)
app.include_router(plan_state_router)
app.include_router(plan_outlook_router)
app.include_router(planning_router)
app.include_router(portability_router)
app.include_router(move_router)
app.include_router(investments_router)
app.include_router(investments_xray_router)
app.include_router(mcp_router)
app.include_router(mcp_client_router)
app.include_router(mcp_oauth_router)
app.include_router(config_router)
app.include_router(sql_query_router)
app.include_router(extensions_router)
app.include_router(mentionables_router)
app.include_router(entity_types_router)
app.include_router(mentions_resolve_router)
app.include_router(channels_router)
app.include_router(model_config_router)
# as connector_id="x/permissions" (and `/api/connectors/x/secret/y` likewise). Starlette
# matches in registration order, so every nested connector route must come BEFORE it. Pinned by
# tests/test_tool_permissions_routes.py::test_permissions_route_wins_over_the_connector_path_route
# and tests/test_connector_secrets.py::test_secret_route_wins_over_the_connector_path_route.
app.include_router(tool_permissions_router)
app.include_router(connector_secrets_router)
app.include_router(connectors_router)
app.include_router(demo_router)
app.include_router(logs_router)
app.include_router(problem_reports_router)
app.include_router(product_docs_router)
app.include_router(terminal_router)


@app.get("/api/health")
async def health():
    """Liveness probe — exempt from auth (for reverse proxies / uptime checks). ``gateway`` is
    true only while gateway mode is active, so a proxy can tell an app that really enforces its
    token from one that is merely up. ``contracts`` are the versioned surfaces a peer (portal,
    desktop) checks before it talks to this instance."""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "gateway": gateway_active(),
        "contracts": AGENT_CONTRACTS,
    }


@app.post("/api/intel/scan")
async def trigger_intel_scan():
    """Manually trigger an intelligence scan (for the 'Scan now' button)."""
    from fastapi.responses import JSONResponse

    from backend.monitoring.intel_agent import IntelScanIncomplete, IntelScanPending, run_intel_scan

    try:
        updated = await run_intel_scan(raise_on_incomplete=True)
    except IntelScanPending:
        return JSONResponse(status_code=202, content={"ok": False, "pending": True})
    except IntelScanIncomplete as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "stories_updated": updated}


@app.post("/api/intel/scan/opportunities")
async def trigger_opportunity_scan():
    """Scan only for market opportunities — faster than full scan."""
    from backend.monitoring.intel_agent import run_opportunity_scan

    updated = await run_opportunity_scan()
    return {"ok": True, "signals_updated": updated}


def _shell_with_entity_types(response: FileResponse) -> HTMLResponse:
    """The app shell with the entity-type registry embedded as inline JSON.

    The client seeds its chip icons/labels/tones from this before first render
    (`readEmbeddedDescriptors`, frontend/src/lucius/onwDataSource.ts) — the backend
    descriptors are the ONE source; there is no frontend copy to drift. Schema only, no
    user data, so the unauthenticated shell may carry it. `<` is escaped so no descriptor
    string can close the script element. ADR
    `2026-10-05-entity-types-ship-with-the-page-not-the-bundle`.
    """
    payload = json.dumps(entity_types(), ensure_ascii=False, separators=(",", ":"))
    tag = (
        '<script type="application/json" id="onw-entity-types">'
        + payload.replace("<", "\\u003c")
        + "</script>"
    )
    html = Path(response.path).read_text(encoding="utf-8").replace("</head>", tag + "</head>", 1)
    headers = {
        k: v
        for k, v in response.headers.items()
        if k not in ("content-length", "etag", "last-modified", "accept-ranges")
    }
    return HTMLResponse(html, status_code=response.status_code, headers=headers)


class _SPAStaticFiles(StaticFiles):
    """Serves the built frontend, falling back to index.html for any unmatched GET path.

    The SPA uses browser (path) history, not a hash router, so a deep link or a refresh
    on a client-side route (e.g. /quote/KSTL.DE) is a real request this mount must
    answer — the client-side router only takes over once index.html has loaded. A
    genuinely missing /api/* path is left alone (real 404, not the HTML shell) since a
    Mount bypasses the app-wide auth dependency entirely and must not swallow API errors.

    ``docs/`` is excluded for the same reason, learned the hard way: when the embedded docs
    build is absent its mount is never registered, so /docs/... reached this catch-all and
    was answered with the app's own index.html — the Help modal's iframe then rendered a
    complete second copy of the application inside itself, ticker and chat panel and all.
    A missing docs build must look missing (404), never like a page that loaded.

    ``assets/`` is excluded for a related reason: every build content-hashes its JS/CSS chunk
    filenames and deletes the previous build's files outright (Vite's default `emptyOutDir`).
    A browser tab left open across a redeploy still references the OLD hashed filename on its
    next lazy `import()` — without this carve-out that 404 was masked as a 200 `text/html`
    response (this index.html shell), which a module loader then rejects with an opaque
    "'text/html' is not a valid JavaScript MIME type" instead of a clean, recognizable 404 that
    `lazyChunk.ts`'s auto-reload can actually detect and self-heal from.
    """

    _NEVER_SHELL = ("api/", "docs/", "assets/")

    def is_not_modified(self, response_headers, request_headers) -> bool:
        # The shell's body is rewritten per response (`_shell_with_entity_types`), so the
        # file's ETag/mtime cannot validate it: a 304 would replay a body without the embed.
        if response_headers.get("content-type", "").startswith("text/html"):
            return False
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith(self._NEVER_SHELL):
                response = await super().get_response("index.html", scope)
            else:
                raise
        if isinstance(response, FileResponse) and Path(response.path).name == "index.html":
            response = _shell_with_entity_types(response)
        response = _with_cache_policy(response, path)
        if response.headers.get("content-type", "").startswith("text/html"):
            _purge_stale_cache_once(response, scope)
            portal = (scope.get("state") or {}).get("gateway_portal_url")
            response.headers["Content-Security-Policy"] = spa_csp(portal)
        return response


# The product docs — the SAME Astro/Starlight site published at docs.opennetworth.ai, built
# a second time with a /docs/ base (`npm run build:embed`) and served from here so the in-app
# Help modal can show it in an iframe. One reader, one stylesheet: the app used to carry its
# own React docs reader whose CSS had to be hand-synced against the site's every time either
# moved. Unauthenticated on purpose — it is byte-for-byte the same public documentation
# anyone can read on the web, and it carries no instance data.
#
# MUST be mounted BEFORE the SPA catch-all below: mounts match in registration order, and
# `/` swallows every remaining path (serving index.html for anything it cannot find), so a
# later /docs mount would never be reached.
# Bumped BY HAND to force every browser to drop its cached copy of this origin exactly once.
# Only needed to escape a cache this server itself poisoned before it sent any policy at all;
# with `_with_cache_policy` in place there should never be a reason to bump it again.
_CACHE_EPOCH = "2026-09-03-2"
_CACHE_EPOCH_COOKIE = "onw_cache_epoch"


def _purge_stale_cache_once(response, scope) -> None:
    """Evict a cache this server poisoned, once per browser.

    Correct `Cache-Control` fixes every FUTURE response, but it cannot reach an entry a
    browser already stored under no policy — that entry keeps being served until its
    heuristic freshness expires, and for the app shell that meant every tab kept booting a
    build whose hashed bundle had been deleted. There is no way to invalidate it from the
    server except to tell the browser to clear the origin, so: send `Clear-Site-Data` once,
    remember that we did with a cookie, and never send it again.

    Deliberately NOT unconditional — `Clear-Site-Data` on every load would throw away the
    immutable-asset caching this same module just set up, on every navigation.
    """
    cookies = ""
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            cookies = value.decode("latin-1")
            break
    if f"{_CACHE_EPOCH_COOKIE}={_CACHE_EPOCH}" in cookies:
        return
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers.append(
        "set-cookie",
        f"{_CACHE_EPOCH_COOKIE}={_CACHE_EPOCH}; Path=/; Max-Age=31536000; SameSite=Lax",
    )


def _with_cache_policy(response, path: str):
    """The cache policy every hashed-asset SPA needs, applied to both static mounts.

    Starlette's StaticFiles sends only `etag`/`last-modified` and NO `Cache-Control`, which
    leaves the browser free to cache HEURISTICALLY — and it does. For a build whose asset
    filenames carry content hashes, that is actively wrong: a rebuild mints new
    `index-<hash>.js` / `_astro/<name>-<hash>.css` names and DELETES the old ones, so a
    browser holding a cached copy of the HTML keeps loading a build that no longer exists.

    Two ways that bit, both on 2026-08-25: the docs rendered completely unstyled (cached HTML
    pointing at a stylesheet that now 404s), and — the one that made every subsequent fix look
    like it did nothing — the APP's own `index.html` was cached, so a rebuilt frontend never
    reached the browser at all, in any tab. Neither looks like a caching problem from the
    outside, which is exactly why it cost hours.

    So: HTML revalidates on every load (cheap — the ETag makes it a 304), and the
    content-hashed assets it names are immutable and cached for a year.
    """
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/html"):
        # `no-store`, not `no-cache`. Both force a fresh copy, but `no-cache` still lets the
        # browser STORE the document — and a stored docs page is the whole failure: it names
        # `_astro/<name>.<hash>.css` files that the next docs build deletes, so replaying it
        # renders the site with no stylesheet at all. Refusing to store it means there is
        # never a version-mismatched pair to replay. The cost is one small local HTML fetch
        # per navigation; the hashed CSS/JS it points at stay cached for a year below.
        response.headers["Cache-Control"] = "no-store"
    elif "_astro/" in path or "/assets/" in path or path.startswith("assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


class _DocsStaticFiles(StaticFiles):
    """The embedded docs, with the cache policy a hashed-asset static site needs.

    Starlette's StaticFiles sends only `etag`/`last-modified` and no `Cache-Control`, which
    leaves the browser free to HEURISTICALLY cache — and it does. That is fine for content
    whose URL never changes, and wrong for this site: every rebuild mints new
    `_astro/<name>.<hash>.css` filenames and DELETES the old ones. A browser holding a
    cached copy of the HTML therefore asks for a stylesheet that no longer exists, gets a
    404, and renders the documentation completely unstyled — raw headings, giant logo, blue
    underlined links (owner, 2026-08-25). Nothing about it looks like a cache problem.

    So: HTML revalidates on every load (cheap — the ETag makes it a 304), and the hashed
    assets it points at are immutable by construction and cached for a year.
    """

    async def get_response(self, path, scope):
        return _with_cache_policy(await super().get_response(path, scope), path)


# Both directories resolve to the repo copy in a checkout/Docker and to backend/_bundled/
# inside an installed wheel (core/shipped_assets.py).
if DOCS_SITE_DIST_DIR.is_dir():
    app.mount("/docs", _DocsStaticFiles(directory=DOCS_SITE_DIST_DIR, html=True), name="docs")

# Serve built frontend in production
if FRONTEND_DIST_DIR.is_dir():
    app.mount("/", _SPAStaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="static")


if __name__ == "__main__":
    # Optional entrypoint: `python3 -m backend.main` runs uvicorn with the
    # env-configurable HOST/PORT (defaults 127.0.0.1:8080 — same as the
    # documented `uvicorn backend.main:app --port 8080`). The existing
    # `uvicorn backend.main:app ...` CLI usage is unaffected.
    import uvicorn

    uvicorn.run(app, host=_settings.host, port=_settings.port)
