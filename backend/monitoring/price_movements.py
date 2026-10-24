"""Notable Price Movement — the narration half (LLM) + the background sweep.

``services.price_movements`` owns every number; this module writes the PARAGRAPH: for one
session it selects that session's window of cached evidence (``asset:{id}`` rows published
between the previous session's close and this session's end), asks the model to explain the
move using ONLY that evidence, and stores the paragraph together with server-built sources
for the evidence items the model says it relied on (``analyst_prompt.source_dicts`` — a URL
never comes from the model; ADR 0004 / 2026-08-28). Zero evidence → no call, ``summary``
stays None and the row reads "no notable coverage".

Cadence (the cost bound):
  * a completed session inside the backfill window (``services.price_movements.BACKFILL_DAYS``)
    is narrated ONCE (past tense) — by the 5-minute sweep for held/watched instruments, on
    demand for whatever page the user is looking at (``ON_DEMAND_MAX_CALLS`` per pass); an older
    session is never written up and is shown only if it already has a paragraph; a close
    paragraph written while after-hours still traded is rewritten ONCE more when the extended
    session is over, if an after-hours print exists (the post-market-close milestone);
  * today's LIVE row: the four MILESTONES — pre-market open (first paragraph), the open
    (pre → regular phase change), close, post-market close — are guaranteed in the BACKGROUND
    for held/watched instruments (owner decision 2026-09-22, amending ADR 2026-09-20's
    "live only on demand"); the ≥ ``RENARRATE_PCT_DELTA``-point move and the
    ≥ ``RENARRATE_STALE_MIN``-minutes-with-newer-evidence rewrites stay on demand (someone is
    looking) — never more often than ``NARRATE_MIN_GAP_MIN`` minutes per instrument.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import backend.services.evidence as _evidence_svc
from backend.db.instrument_store import InstrumentStore
from backend.db.price_movement_store import PriceMovementStore
from backend.monitoring.price_movement_prompt import (
    build_prompt,
    check_result,
    evidence_window,
    live_figures_restated,
    select_evidence,
)
from backend.services import price_movements as _svc

logger = logging.getLogger(__name__)

NARRATE_MIN_GAP_MIN = 5
RENARRATE_PCT_DELTA = 1.0
# A COMPLETED session is rewritten when its own numbers changed under it by at least this
# much (a provider gap filled from our recorded close shifts the previous close — KSTL.DE
# Sep 18 read "slipped 0.65%" while the row now said 0.41%). Within the backfill window only.
REWRITE_PCT_DELTA = 0.1
RENARRATE_STALE_MIN = 30
# One on-demand pass may narrate the whole visible page (DEFAULT_LIMIT sessions) — a first
# open of a ticker writes every row up once; after that only new closes cost anything.
ON_DEMAND_MAX_CALLS = 15
# Due rows are independent, so a pass narrates them CONCURRENTLY, this many in flight at once
# (owner decision 2026-09-20: 15) — a page fills in about the time of its slowest single call
# instead of the sum of all of them. The per-pass call cap above matches it, so the limit is
# real rather than capped down by the pass size.
NARRATION_CONCURRENCY = 15
# Stamped on every quiet-day verdict; a verdict from an older window definition is re-checked
# once (v2: the window runs to the NEXT session's open and Mondays start at Friday's close;
# v3: a LIVE row looks back LIVE_LOOKBACK_HOURS for background, 2026-09-22).
EVIDENCE_WINDOW_VERSION = "v3"
# A failed/rejected row retries on this cadence, at most this many times (then it rests until
# a code/provider change — never an unbounded retry loop against a broken provider).
FAILURE_RETRY_MIN = 20
MAX_ATTEMPTS = 6
# Reasoning is requested LOW, not off — a short cited paragraph needs little — because some
# models cannot disable thinking at all: to them effort="none" (OpenRouter's
# `reasoning.enabled=false`) is a no-op and they reason at their DEFAULT level, whereas "low"
# (`reasoning.effort=low`) is normalized to the lowest level each model does support, so the
# thinking is bounded either way (owner decision 2026-09-21). The budget still covers a model
# that reasons anyway: the instance's openrouter:z-ai/glm-5.3-flash ignored the off switch and
# its thinking counts against max_tokens on that transport (owner incident 2026-09-20: cost_log
# showed out=600, then 1200 on the reask, then 2000 — the ENTIRE budget burned on hidden
# reasoning, zero visible text, every row "rejected"). Same finding and same remedy as
# price_monitor._ALERT_NARRATION_MAX_TOKENS. 12000, not 6000: within the first hour after the
# fix the cost_log showed one call at 6671 output tokens — it only finished because the seam's
# reask doubled the budget. A ceiling costs nothing unused; a starved call costs a whole retry.
# Stays under core.llm_seam._MAX_REASK_TOKENS (16000).
NARRATION_MAX_TOKENS = 12000
NARRATION_EFFORT = "low"
RECHECK_HOURS = 2  # a summary-less recent session gets another look this often
RECHECK_DAYS = 2  # …but only while it is this recent
# Max narrations per sweep tick — the concurrency, so a tick fans out ~95 held/watched names
# crossing into pre-market at 04:00 ET in a few ticks rather than ~1.5 hours at 5 a tick.
SWEEP_LLM_CAP = NARRATION_CONCURRENCY
_ON_DEMAND_GAP_SECS = 60
_FULL_CHECK_GAP_SECS = 3600

_inflight: set[str] = set()
# (instrument_id, session_date) pairs whose narration is literally in flight right now — the
# server-side truth behind the timeline's "Updating…" beside a paragraph being rewritten.
_narrating: set[tuple[str, str]] = set()
_last_trigger: dict[str, float] = {}
_last_limit: dict[str, int] = {}
_last_full_check: dict[str, float] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def note_outcome(note: dict | None) -> str | None:
    """The note's last narration outcome. A LEGACY note (written before `narration_status`
    existed) that was checked but holds no paragraph reads as `failed`: those are exactly the
    rows the 2026-09-20 reasoning-budget bug poisoned, and reading them this way is what gets
    them retried — a genuinely quiet day just re-settles as `no_evidence` at zero cost."""
    note = note or {}
    status = note.get("narration_status")
    if status:
        return status
    if note.get("summary"):
        return "ready"
    if note.get("narration_checked_at"):
        return "failed"
    return None


def narration_due(
    row: dict,
    note: dict | None,
    now: datetime | None = None,
    tz_name: str | None = None,
    today: str | None = None,
    evidence_newest: str | None = None,
    *,
    live_milestones_only: bool = False,
) -> bool:
    """Whether this row deserves a (re)narration right now — see the module docstring.
    `today` is the exchange-local day the timeline was composed for (falls back to now in
    `tz_name`); a session older than the backfill window is never written up.
    `evidence_newest` is the arrival time (`first_seen`) of the newest article cached for the
    asset: a "no evidence" verdict older than that is void — articles landed after we looked
    (owner-reproduced 2026-09-20, IBM: a first-open pass judged five sessions quiet seconds
    before the page's own news gather delivered 128 articles)."""
    now = now or _utcnow()
    note = note or {}
    narrated_at = _parse_iso(note.get("narrated_at"))
    checked_at = _parse_iso(note.get("narration_checked_at"))
    if note_outcome(note) == "no_evidence" and row["kind"] != "market_closed":
        if note.get("evidence_window") != EVIDENCE_WINDOW_VERSION:
            return True  # judged under an older, narrower window: look again once
        if (
            checked_at is not None
            and evidence_newest
            and (_parse_iso(evidence_newest) or checked_at) > checked_at
        ):
            return True  # new articles since we looked: re-check now, ahead of every cadence
    if checked_at and (now - checked_at) < timedelta(minutes=NARRATE_MIN_GAP_MIN):
        return False  # a hard floor for every kind: never two calls inside the gap
    if row["kind"] == "session":
        ref_day = datetime.fromisoformat(today or _svc.local_today(tz_name, now)).date()
        session_day = datetime.fromisoformat(row["session_date"]).date()
        in_window = (ref_day - session_day).days <= _svc.BACKFILL_DAYS
        if note.get("narrated_final"):
            # The post-market-close milestone: a close paragraph written while after-hours
            # still traded (`post`) is rewritten ONCE with the final print when the extended
            # session is over (`closed`) — only when there IS an after-hours print, so a venue
            # without extended hours never pays for a paragraph that could not change.
            if (
                in_window
                and note.get("narrated_phase") == "post"
                and row.get("phase") == "closed"
                and (row.get("after_hours") or {}).get("price") is not None
            ):
                return True
            # Otherwise frozen — unless the facts moved under it (see REWRITE_PCT_DELTA).
            last_pct, pct = note.get("narrated_change_pct"), row.get("change_pct")
            return (
                in_window
                and last_pct is not None
                and pct is not None
                and abs(float(pct) - float(last_pct)) >= REWRITE_PCT_DELTA
            )
        if not note.get("summary") and not in_window:
            return False  # outside the backfill window: not ours to write up
        if note.get("summary") or checked_at is None:
            return True  # a live-hours paragraph awaiting its final rewrite, or never tried
        if note_outcome(note) in ("failed", "rejected"):
            # A provider failure or guard rejection is retried on a short cadence, bounded —
            # never left to age out as if the day had been quiet.
            if int(note.get("narration_attempts") or 0) >= MAX_ATTEMPTS:
                return False
            return (now - checked_at) >= timedelta(minutes=FAILURE_RETRY_MIN)
        # Exchange-local day arithmetic, never now.date(): a UTC calendar day rolls at 8pm ET,
        # which would cut the recheck window hours short for every west-of-UTC venue.
        recent = (ref_day - session_day).days <= RECHECK_DAYS
        return recent and (now - checked_at) >= timedelta(hours=RECHECK_HOURS)
    if row["kind"] != "live":
        return False
    if narrated_at is None:
        return True
    # The paragraph's PREMISE moved: written in pre-market, now the regular session is trading
    # (owner-reproduced 2026-09-21, ORCL at 12:35 ET still read "in Monday's pre-market
    # session" — the move had travelled 0.42pt and no newer article had landed, so neither
    # trigger below fired). A legacy paragraph with no phase stamp is rewritten once, then
    # stamped. Inside the 5-minute floor above like every other trigger.
    if note.get("narrated_phase") != row.get("phase"):
        return True
    if live_milestones_only:
        return False  # the background sweep: milestones only, moves and news stay on demand
    last_pct = note.get("narrated_change_pct")
    pct = row.get("change_pct")
    if pct is not None and last_pct is not None and abs(pct - last_pct) >= RENARRATE_PCT_DELTA:
        return True
    stale = (now - narrated_at) >= timedelta(minutes=RENARRATE_STALE_MIN)
    newest = _parse_iso(note.get("newest_evidence_seen"))
    return stale and newest is not None and newest > narrated_at


def _model() -> str:
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().task_model_override("price_monitor") or ""
    except Exception:
        return ""


async def _complete(prompt: str, row: dict | None = None) -> object:
    """One structured narration call. A LIVE row hands the seam a validator: a paragraph that
    restates the exact live figures is reasked ONCE with the reason (cheap — the seam feeds
    it back) rather than rejected here and retried 20 minutes later."""
    from backend.core import llm_seam

    validate = None
    if row is not None and row.get("kind") == "live":

        def validate(obj: object) -> str | None:
            paragraph = str((obj or {}).get("paragraph") or "") if isinstance(obj, dict) else ""
            return live_figures_restated(paragraph, row)

    schema = {
        "type": "object",
        "properties": {
            "paragraph": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down", "flat"]},
            "source_indexes": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["paragraph", "direction", "source_indexes"],
        "additionalProperties": False,
    }
    return await llm_seam.complete_json(
        prompt,
        system=(
            "You write grounded, cited market-wrap paragraphs. Numbers come only from FACTS; "
            "causes and context come only from the news data. Respond with JSON only."
        ),
        model=_model(),
        max_tokens=NARRATION_MAX_TOKENS,
        source="price_movement",
        effort=NARRATION_EFFORT,
        schema=schema,
        validate=validate,
    )


def _evidence_for(
    inst: dict,
    row: dict,
    prev_session_date: str | None,
    tz_name: str | None,
    next_session_date: str | None = None,
):
    rows = _evidence_svc.read_evidence(subjects=[f"asset:{inst['id']}"], fresh_only=True)
    start, end = evidence_window(
        row["session_date"],
        prev_session_date,
        tz_name,
        live=row["kind"] == "live",
        next_session_date=next_session_date,
    )
    picked = select_evidence(rows, start, end)
    newest = max((r.get("first_seen") or r.get("published_at") or "" for r in picked), default=None)
    return picked, newest


def _newest_evidence_arrival(instrument_id: str) -> str | None:
    """`first_seen` of the newest article cached for the asset (None when nothing is cached)."""
    try:
        rows = _evidence_svc.read_evidence(subjects=[f"asset:{instrument_id}"], fresh_only=True)
    except Exception:
        return None
    return (
        max((r.get("first_seen") or r.get("published_at") or "" for r in rows), default=None)
        or None
    )


async def _await_news_gather(instrument_id: str) -> None:
    """Let the page's own news gather land BEFORE the first narration pass judges a session
    quiet — `refresh_asset_brief` holds a per-instrument lock while it collects, and returns
    at once ("fresh"/"rate_limited") when it already ran; same shape as the analyst's 2026-09-03
    fix for the identical race on key_issues. Bounded, best-effort: a slow or failing gather
    never blocks the pass for long (the arrival re-check above catches a late landing)."""
    try:
        from backend.monitoring.asset_brief import refresh_asset_brief

        await asyncio.wait_for(refresh_asset_brief(instrument_id, force=False), timeout=90)
    except Exception:
        logger.debug(
            "price_movements: news gather wait skipped for %s", instrument_id, exc_info=True
        )


def _row_facts(row: dict) -> dict:
    facts = {"prev_close": row.get("prev_close")}
    if row["kind"] == "live":
        facts.update(
            live_price=row.get("price"),
            live_change_pct=row.get("change_pct"),
            live_as_of=row.get("as_of"),
            live_phase=row.get("phase"),
        )
    else:
        facts.update(close=row.get("price"), change_pct=row.get("change_pct"), final=True)
        if row.get("after_hours"):
            facts["after_hours"] = row["after_hours"]
    return facts


async def narrate_row(
    inst: dict,
    row: dict,
    *,
    prev_session_date: str | None,
    market: dict | None,
    currency: str,
    next_session_date: str | None = None,
) -> bool:
    """Narrate ONE row and persist the outcome. Returns True when a paragraph landed. A
    zero-evidence or rejected result still stamps `narration_checked_at` so the row isn't
    retried on every poll."""
    store = PriceMovementStore()
    prior = store.get_note(inst["id"], row["session_date"]) or {}
    now_iso = _utcnow().isoformat()
    note = {
        **prior,
        "instrument_id": inst["id"],
        "session_date": row["session_date"],
        "currency": currency,
        **_row_facts(row),
        "narration_checked_at": now_iso,
        "updated_at": now_iso,
    }
    tz_name = (market or {}).get("timezone")
    evidence, newest = await asyncio.to_thread(
        _evidence_for, inst, row, prev_session_date, tz_name, next_session_date
    )
    if newest:
        note["newest_evidence_seen"] = newest
    attempts = int(prior.get("narration_attempts") or 0)

    def _settle(status: str, error: str | None = None, *, counts: bool) -> bool:
        # Three honest outcomes, never conflated: no_evidence (the day was quiet in OUR cache),
        # failed (the provider gave nothing usable), rejected (the guard refused the answer).
        # Only the last two count as attempts and retry (see narration_due).
        note["narration_status"] = status
        note["evidence_window"] = EVIDENCE_WINDOW_VERSION
        note["narration_error"] = error
        note["narration_attempts"] = attempts + 1 if counts else attempts
        store.upsert_note(note)
        return False

    if not evidence:
        return _settle("no_evidence", counts=False)
    prompt = build_prompt(
        inst, row, evidence, currency=currency, tz_short=(market or {}).get("tz_short")
    )
    try:
        result = await _complete(prompt, row=row)
    except Exception as exc:
        logger.warning(
            "price_movements: narration failed for %s %s (%s: %s)",
            inst.get("symbol"),
            row["session_date"],
            type(exc).__name__,
            str(exc)[:160],
        )
        return _settle("failed", type(exc).__name__, counts=True)
    valid, reason = check_result(result, row, evidence)
    if valid is None:
        status = "failed" if reason == "empty_or_invalid_json" else "rejected"
        logger.info(
            "price_movements: narration %s for %s %s — %s (attempt %d/%d)",
            status,
            inst.get("symbol"),
            row["session_date"],
            reason,
            attempts + 1,
            MAX_ATTEMPTS,
        )
        return _settle(status, reason, counts=True)
    paragraph, sources = valid
    note.update(
        summary=paragraph,
        sources=sources,
        narrated_at=now_iso,
        narrated_change_pct=row.get("change_pct"),
        narrated_phase=row.get("phase"),
        narrated_final=row["kind"] == "session",
        narration_status="ready",
        narration_error=None,
        narration_attempts=attempts,
    )
    store.upsert_note(note)
    return True


def _due_rows(
    instrument_id: str,
    rows: list[dict],
    timeline: dict,
    evidence_newest: str | None,
    *,
    live_milestones_only: bool = False,
) -> list[dict]:
    """The rows of `rows` that `narration_due` says deserve a (re)narration now."""
    store = PriceMovementStore()
    tz_name = (timeline.get("market") or {}).get("timezone")
    return [
        row
        for row in rows
        if narration_due(
            row,
            store.get_note(instrument_id, row["session_date"]),
            tz_name=tz_name,
            today=timeline.get("today"),
            evidence_newest=evidence_newest,
            live_milestones_only=live_milestones_only,
        )
    ]


def rows_being_rewritten(instrument_id: str, timeline: dict) -> set[str]:
    """Session dates whose narration is in flight right now, plus those that already hold a
    paragraph and are due for another (the on-demand pass picks them up within the minute)
    — what the endpoint stamps `rewriting` on, so the client can say "Updating…" beside the
    old text instead of silently showing it (owner 2026-09-21: "when the narrative is
    regenerating there is no way to tell"). A due row WITHOUT a paragraph is `pending`
    already and is not reported here."""
    in_flight = {day for iid, day in _narrating if iid == instrument_id}
    rows = [
        r
        for r in timeline.get("rows") or []
        if r.get("kind") != "market_closed" and r.get("summary")
    ]
    if not rows:
        return in_flight
    try:
        due = _due_rows(instrument_id, rows, timeline, _newest_evidence_arrival(instrument_id))
    except Exception:
        logger.debug("price_movements: due check skipped for %s", instrument_id, exc_info=True)
        due = []
    return in_flight | {r["session_date"] for r in due}


async def ensure_narrations(
    instrument_id: str,
    *,
    max_calls: int = ON_DEMAND_MAX_CALLS,
    closed_only: bool = False,
    live_milestones_only: bool = False,
    limit: int = _svc.DEFAULT_LIMIT,
) -> int:
    """Narrate whatever is due on this instrument's page of `limit` sessions, newest first,
    bounded by `max_calls` model calls per pass (the next poll continues where this stopped)."""
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst or not inst.get("price_source_id"):
        return 0
    timeline = await asyncio.to_thread(_svc.read_timeline, instrument_id, limit)
    if not timeline:
        return 0
    rows = [r for r in timeline["rows"] if r["kind"] != "market_closed"]
    if closed_only:
        rows = [r for r in rows if r["kind"] == "session"]
    # Every bar session, hidden days included — never just the visible rows (a Monday's
    # previous session is Friday even when Friday isn't on the page).
    session_dates = sorted(
        timeline.get("session_dates")
        or [r["session_date"] for r in timeline["rows"] if r["kind"] == "session"]
    )
    evidence_newest = await asyncio.to_thread(_newest_evidence_arrival, instrument_id)
    due: list[tuple[dict, str | None, str | None]] = []
    for row in _due_rows(
        instrument_id, rows, timeline, evidence_newest, live_milestones_only=live_milestones_only
    ):
        if len(due) >= max_calls:
            break
        prev = max((d for d in session_dates if d < row["session_date"]), default=None)
        nxt = min((d for d in session_dates if d > row["session_date"]), default=None)
        due.append((row, prev, nxt))
    if not due:
        return 0
    gate = asyncio.Semaphore(NARRATION_CONCURRENCY)

    async def one(row: dict, prev: str | None, nxt: str | None) -> bool:
        key = (instrument_id, row["session_date"])
        async with gate:
            _narrating.add(key)
            try:
                return await narrate_row(
                    inst,
                    row,
                    prev_session_date=prev,
                    next_session_date=nxt,
                    market=timeline.get("market"),
                    currency=timeline["currency"],
                )
            except Exception:
                # One row's failure never sinks its siblings in the same pass.
                logger.warning(
                    "price_movements: row failed for %s %s",
                    inst.get("symbol"),
                    row["session_date"],
                    exc_info=True,
                )
                return False
            finally:
                _narrating.discard(key)

    results = await asyncio.gather(*(one(row, prev, nxt) for row, prev, nxt in due))
    return sum(1 for ok in results if ok)


async def trigger_on_demand_narration(instrument_id: str, limit: int = _svc.DEFAULT_LIMIT) -> bool:
    """Fire-and-forget entry for the GET endpoint: one in-flight pass per instrument, at most
    once per `_ON_DEMAND_GAP_SECS` — except that a bigger page than seen before runs at once —
    covering the page the caller asked for (`limit`, so scrolling for more backfills the new
    rows too). Returns True when a pass actually ran (tests)."""
    iid = (instrument_id or "").strip()
    now = time.monotonic()
    if not iid or iid in _inflight:
        return False
    # A BIGGER page than any seen for this instrument is a new request (the reader scrolled
    # for more) and bypasses the gap — its fresh rows shouldn't wait a minute to start.
    bigger_page = limit > _last_limit.get(iid, 0)
    if not bigger_page and now - _last_trigger.get(iid, 0.0) < _ON_DEMAND_GAP_SECS:
        return False
    _inflight.add(iid)
    _last_trigger[iid] = now
    _last_limit[iid] = max(limit, _last_limit.get(iid, 0))
    try:
        await _await_news_gather(iid)
        await ensure_narrations(iid, limit=limit)
        return True
    except Exception:
        logger.warning("price_movements: on-demand pass failed for %s", iid, exc_info=True)
        return False
    finally:
        _inflight.discard(iid)


def _candidates() -> list[dict]:
    """Held instruments plus monitor_news watchlist tickers — the set kept warm in the
    background (same scope the analyst's brief precompute uses)."""
    from backend.services.positions import hydrated as hydrated_positions
    from backend.services.watchlist import is_topic, read_watchlist

    seen: set[str] = set()
    out: list[dict] = []
    for r in hydrated_positions():
        iid = r.get("instrument_id")
        if iid and iid not in seen and r.get("ticker"):
            seen.add(iid)
            out.append({"instrument_id": iid})
    for item in read_watchlist().get("items") or []:
        if is_topic(item) or not item.get("monitor_news", True):
            continue
        iid = (item.get("instrument_id") or "").strip()
        if iid and iid not in seen:
            seen.add(iid)
            out.append({"instrument_id": iid})
    return out


def _sweep_quotes(candidates: list[dict]) -> dict:
    """ONE live batch for the whole sweep: held tickers + every candidate's own id. Fetched per
    candidate instead, each watched or crypto name was its own cache key — a full provider fetch
    of the book per candidate every 5 minutes (live server 2026-10-24: ~230/h)."""
    ids = {c["instrument_id"] for c in candidates}
    pids = [i.get("price_source_id") for i in InstrumentStore().all() if i.get("id") in ids]
    tickers = list(dict.fromkeys([*_svc.held_tickers(), *(p for p in pids if p)]))
    return _svc.fetch_quotes(tickers) if tickers else {}


def _observe(instrument_id: str, live: dict | None = None) -> tuple[dict | None, dict | None]:
    inst = InstrumentStore().find_by_id(instrument_id)
    if not inst or not inst.get("price_source_id"):
        return None, None
    market = _svc._market(inst["price_source_id"])
    today = _svc.local_today((market or {}).get("timezone"))
    try:
        live = _svc._live_quote(inst, market, live)
    except Exception:
        live = {}
    return _svc.observe_today(inst, live=live, market=market, today=today), market


def _live_milestone_pending(note: dict | None, state: str) -> bool:
    """A held/watched name whose live observation sits in a phase its paragraph was not written
    for: the first pre-market print with no paragraph yet, or the open under a pre-market one."""
    if not note or state not in ("PRE", "REGULAR"):
        return False
    phase = note.get("live_phase")
    if not phase:
        return False
    return not note.get("summary") or note.get("narrated_phase") != phase


async def run_price_movement_sweep() -> int:
    """Every 5 min for held/watched instruments: record today's observation (cheap, one cached
    batch quote) and narrate what is due in the BACKGROUND — a just-completed session, the
    post-market-close rewrite, and the live milestones (pre-market open, the open). Candidates
    narrate concurrently (``NARRATION_CONCURRENCY``); returns paragraphs landed."""
    now = time.monotonic()
    due: list[str] = []
    candidates = await asyncio.to_thread(_candidates)
    try:
        live = await asyncio.to_thread(_sweep_quotes, candidates)
    except Exception:
        logger.warning("price_movements: sweep quote batch failed", exc_info=True)
        live = None
    for cand in candidates:
        iid = cand["instrument_id"]
        try:
            note, market = await asyncio.to_thread(_observe, iid, live)
        except Exception:
            logger.warning("price_movements: observe failed for %s", iid, exc_info=True)
            continue
        state = (market or {}).get("state") or "UNKNOWN"
        close_pending = bool(note and note.get("final") and not note.get("narrated_final"))
        # The post-market-close rewrite: a close paragraph written while after-hours still
        # traded, now that the extended session is over — gated on an OBSERVED after-hours
        # print, so a day without one never re-reads the timeline every tick for nothing.
        post_close_pending = bool(
            note
            and note.get("narrated_final")
            and note.get("narrated_phase") == "post"
            and note.get("after_hours")
            and state not in ("POST", "REGULAR", "PRE")
        )
        overdue_check = (
            state in _svc._FINAL_STATES
            and now - _last_full_check.get(iid, 0.0) >= _FULL_CHECK_GAP_SECS
        )
        milestone = _live_milestone_pending(note, state)
        # The cap bounds NARRATIONS only — every candidate still gets its cheap observation
        # (after-hours print capture) each tick, even when many closes queue up at 4pm ET.
        if not (close_pending or post_close_pending or overdue_check or milestone):
            continue
        if len(due) >= SWEEP_LLM_CAP:
            continue
        _last_full_check[iid] = now
        due.append(iid)
    if not due:
        return 0
    gate = asyncio.Semaphore(NARRATION_CONCURRENCY)

    async def one(iid: str) -> int:
        async with gate:
            try:
                return await ensure_narrations(
                    iid, max_calls=1, closed_only=False, live_milestones_only=True
                )
            except Exception:
                logger.warning("price_movements: sweep narration failed for %s", iid, exc_info=True)
                return 0

    return sum(await asyncio.gather(*(one(iid) for iid in due)))
