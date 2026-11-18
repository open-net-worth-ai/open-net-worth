"""One autopilot review: Lucius reads the paper portfolio, its mandate and the market, places the
trades that keep the mandate, and writes a memo. Run by the scheduler, never by a request.

The review is ONE headless Lucius turn (the ``core.headless_turn`` seam, so ``services`` never
imports ``ai``) with an enforced tool offer — reads and this portfolio's paper tools, nothing
else — and the paper-only guard Automations use (``AutomationApprovalContext`` with
``simulation_only`` and the portfolio bound), so dispatch refuses any real write, connector or
outbound tool, and every paper write outside this portfolio, whatever the model asks for. Its
orders carry the origin ``autopilot``: the paper choke refuses one that breaks the mandate and
refuses one placed outside a review."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.core.automation_runner import (
    AutomationApprovalContext,
    bind_automation_approval,
    reset_automation_approval,
)
from backend.core.headless_turn import get_runner
from backend.services import feature_flags
from backend.services.paper_trading import autopilot, mandate, service

logger = logging.getLogger(__name__)

ORIGIN = "autopilot"
CONTEXT_PREFIX = "autopilot:"
TIMEOUT_S = 600
# Reads, and this portfolio's paper tools. Everything else is outside the offer, and the
# paper-only guard refuses it at dispatch even if the model names it.
TOOLS = (
    "get_paper_portfolios",
    "get_paper_results",
    "get_paper_activity",
    "preview_paper_orders",
    "place_paper_orders",
    "cancel_paper_orders",
    "get_live_prices",
    "get_asset_context",
    "get_asset_technicals",
    "get_asset_fundamentals",
    "get_investment_theses",
    "get_intel_digest",
    "calculate",
)
# The paper writes run without a card: nobody is there to tap one, and the mandate is what the
# owner approved in advance.
_ALLOWED_WRITES = frozenset({"place_paper_orders", "cancel_paper_orders"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prompt(row: dict) -> str:
    a = autopilot.of(row)
    m = row.get("mandate") or {}
    limits = mandate.describe(m) or "none beyond the goal"
    lines = [
        f'You are running the autopilot review of the PAPER portfolio "{row["name"]}" '
        f"(id {row['id']}). It is simulated: pretend money, never the owner's, and nothing "
        "reaches a broker.",
        f"Its mandate goal: {m.get('goal') or '(no goal written)'}",
        f"Its limits: {limits}.",
    ]
    if a["instructions"]:
        lines.append(f"The owner's standing instructions: {a['instructions']}")
    lines += [
        "",
        "Do this:",
        "1. Read the portfolio (get_paper_portfolios with this id): holdings, prices, cash, "
        "open orders, and its mandate status.",
        "2. Every review, before deciding anything, also check: (a) the news on its holdings "
        "(get_intel_digest); (b) the owner's thesis for each holding that has one "
        "(get_investment_theses): is it in its buy zone, near its target, past its stop?; "
        "(c) for any holding that moved more than 3% since the last review or is near a "
        "limit, its technicals and fundamentals (get_asset_technicals, "
        "get_asset_fundamentals). Before any buy, check the name you would buy the same way.",
        "3. Then decide whether to trade. Doing nothing is often right: trade only for a "
        "reason you can state in one sentence, in the goal's terms.",
        "4. Place the paper orders you decide on, sells first, each with a "
        "short Why. Preview a buy if you are unsure it keeps the mandate. A buy that breaks a "
        "limit is refused: never try to break one. Fix any limit that is broken now (a holding "
        "over its cap, cash below its floor, a holding past its stop loss) before anything else.",
        "5. Trade only this portfolio.",
        "",
        "Then reply with the memo the owner reads: 3 to 6 plain sentences on what you checked "
        "(the news, theses and signals that mattered), what you did or why you did nothing, "
        "and what you are watching next. No headings, no lists, no tables.",
    ]
    return "\n".join(lines)


async def review(pid: str) -> dict:
    """Run one claimed review of ``pid`` and log it. Never raises: a failure is a logged run."""
    started = _now()
    row = autopilot.start(pid, started)
    if row is None:
        return {"status": "skipped"}
    runner = get_runner()
    settings = autopilot.of(row)
    record: dict
    if runner is None:
        record = {"status": "failed", "error": "Lucius isn't available on this install."}
    else:
        token = bind_automation_approval(
            AutomationApprovalContext(
                automation_id=f"{CONTEXT_PREFIX}{pid}",
                run_id=f"{CONTEXT_PREFIX}{started.isoformat()}",
                session_id="",
                mode="manual",
                always_allowed_tools=_ALLOWED_WRITES,
                simulation_only=True,
                paper_portfolio_id=pid,
            )
        )
        try:
            memo = await asyncio.wait_for(
                runner(
                    prompt(row),
                    allowed_tools=list(TOOLS),
                    model=settings["model"] or None,
                    lean_prompt=True,
                    enforce_offer=True,
                    effort=settings["effort"],
                ),
                timeout=TIMEOUT_S,
            )
            record = {"status": "done", "memo": memo}
        except asyncio.TimeoutError:
            record = {"status": "failed", "error": "The review took too long and was stopped."}
        except Exception as exc:  # noqa: BLE001 — any failure is a logged run, never a crash
            logger.exception("autopilot review failed for %s", pid)
            record = {"status": "failed", "error": str(exc)[:300] or type(exc).__name__}
        finally:
            reset_automation_approval(token)
    record["orders"] = _orders_since(pid, started)
    record.update(model=settings["model"], effort=settings["effort"])
    autopilot.finish(pid, record)
    return record


def _orders_since(pid: str, started: datetime) -> list[dict]:
    from backend.services.paper_trading import views

    out = []
    for o in service.portfolio_orders(pid):
        if o.get("origin") == ORIGIN and str(o.get("placed_at") or "") >= started.isoformat():
            out.append({"id": o["id"], "label": views.toast(o), "status": o.get("status")})
    return out


async def run_due() -> list[dict]:
    """The scheduler's pass: review every due auto portfolio, one after another."""
    if not feature_flags.enabled("paper_trading"):
        return []
    out = []
    for row in autopilot.due(_now()):
        out.append({"id": row["id"], **(await review(row["id"]))})
    return out
