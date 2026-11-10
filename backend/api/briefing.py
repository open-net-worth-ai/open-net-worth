"""
GET /api/briefing?refresh=false

Generates a daily morning note from a wealth manager perspective.
Focuses on: news affecting specific holdings, FX moves, near-term events.
Does NOT repeat FIRE projections, total NW, or static allocations —
those are already shown in other widgets on the Overview tab.

Uses web_search to pull current news (up to 3 searches).
Cached 24h in briefing_cache.json. ?refresh=true forces regeneration.

Response: { note: str, chat_suggestion: str, generated_at: ISO str, is_cached: bool }
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.ai.assistant import get_model
from backend.ai.structured import complete_model
from backend.api.profile import read_profile
from backend.services import briefing_book_state as book_state
from backend.services import briefing_context as facts
from backend.services import investments_book as investments_book_service
from backend.services import ledger as ledger_service
from backend.services import portfolio_rows as portfolio_rows_service
from backend.services import thesis_judgment
from backend.services.fx_cache import load_fx_cache

router = APIRouter()
logger = logging.getLogger(__name__)

from backend.core.user_time import user_now, user_today
from backend.paths import BRIEFING_CACHE_JSON as CACHE_FILE

# max_tokens is the hard "don't generate more than this" guard. The bug: it sat at 1000,
# below what a full note+actions+chat_suggestion JSON needs from a more thorough model —
# every recent claude-sonnet-5 call hit the ceiling exactly (output=1000 every time),
# truncating mid-JSON -> unparseable -> the visible "couldn't read" fallback on every load.
# Same failure class as the asset_brief/RKLB incident (backend/monitoring/asset_brief.py):
# raised the ceiling AND told the model its budget in the prompt so it self-limits under it.
_BRIEFING_MAX_TOKENS = 2000


class _BriefingOutput(BaseModel):
    """The daily-briefing shape. ``note`` is required non-empty — a briefing with no note is
    not usable, so the structured layer reasks and, if still empty, returns None (the caller
    then degrades to a visible error rather than shipping an empty/raw body). ``actions`` and
    ``chat_suggestion`` are optional and default to empty."""

    note: str = Field(min_length=1)
    actions: list[str] = Field(default_factory=list)
    chat_suggestion: str = ""


def _daypart(dt: datetime) -> tuple:
    """Bucket a moment into (date, part) where part is night|morning|afternoon|evening.

    The briefing is cached until the day-part changes, so a user who reopens the
    app later in the day gets a genuinely refreshed note (not the same text all
    day), while a single morning visit costs just one generation. ``night``
    (22:00-05:00) exists so a 04:41 note is never greeted "Good morning".
    """
    h = dt.hour
    if h < 5 or h >= 22:
        part = "night"
    elif h < 12:
        part = "morning"
    elif h < 17:
        part = "afternoon"
    else:
        part = "evening"
    return (dt.date(), part)


_SYSTEM = """\
You are Lucius — a highly competent, trusted personal wealth manager. \
You are writing a brief daily note to your client. You know their portfolio \
intimately and have just reviewed today's relevant news on their behalf.

Persona: composed, clear, slightly formal but never cold. Think Lucius Fox — \
the advisor who quietly keeps everything running and only surfaces what actually matters. \
You serve your client; you don't perform for them.

Tone and style:
- Do not start with "Hey". Open with the most important thing, or a short orienting line. \
  A time-of-day greeting ("Good morning") is allowed ONLY when it matches the local time \
  given in the context (morning, afternoon or evening); at night open with the point, no \
  greeting. Keep it natural, never formulaic.
- Write every money amount exactly as the context writes it (e.g. S$12,508, US$300). Never \
  switch notation, such as "SGD 12,508".
- 2–3 short paragraphs. No bullet points, no section headers, no emojis.
- Clear and concise. Every sentence earns its place.
- Professional but personal — you know their situation, reference it specifically.
- Slightly servant-like: "I wanted to flag...", "Worth your attention...", \
  "I'd suggest considering...", "For your awareness..."
- Always translate market moves into their impact on the client's actual positions \
  in their base currency.
- Each holding line gives the base-currency value and, for foreign holdings, the \
  native amount (e.g. "native: EUR 138,587"). If you quote a native-currency figure, \
  use the native amount — never reuse the base-currency number with a foreign label. \
  The base value and the native amount are NOT equal.

What to cover (only what's genuinely relevant today):
- News this week about companies or assets they hold — with specific numbers
- FX moves that affect their holdings (only currencies they are actually exposed to)
- What falls due in the next 1–2 weeks and anything OVERDUE, from the UPCOMING section. \
  Never say nothing is due or overdue when that section lists items; say how many are \
  overdue and name the nearest or most important.
- Notable spending — only if the SPENDING PULSE shows something worth a mention (something \
  it lists as overspent or over its cap, an unusually large charge, or a heads-up on what was \
  recently auto-logged). Quote its figures as written and never call anything "over budget" \
  that it does not list as such. Frame it as awareness, not nagging; skip it entirely if \
  nothing stands out.
- Money about to land (INCOMING in the UPCOMING section): name it with its date, e.g. \
  "your salary arrives on the 28th". It changes how tight the days before it are, so \
  never write the next weeks as if only outgoings exist when income is listed.
- One thing you'd flag or recommend

Grounding rules:
- NEEDS ATTENTION is the Dashboard's own flag list. When it lists flags, never write that \
  everything is in order, fine or on track, and never leave its first flag unmentioned: \
  say what it says, with its figures and months as written.
- BOOK HISTORY says how much history the book holds. Describe a period only as far back as \
  it goes ("since you started", "today"), never "the last three days" or a trend over a \
  span the book does not cover. Never tell the client to import a statement or log a \
  transaction that BOOK HISTORY or SPENDING PULSE shows is already there, and never hedge \
  with "if you haven't already".
- Never narrate an input you were not given. If the context has no figure for something \
  (Ready to Assign, a due date, a price), leave the topic out; do not write that it is \
  "not available", "not shown" or "isn't available to me". Ready to Assign exists only \
  under a zero-based budget, and only the CASH AVAILABLE TO SPEND section may state it.

What to omit from the note:
- Instructions. Never write a sentence that reads like a direction to yourself, such as \
  "Everything else is in hand in one sentence:" - say the thing itself.
- Their FIRE number, total NW, and allocation are already on screen — do not repeat them
- Skip anything generic or not specific to their actual situation
- Do not pad the note — a short, precise note is better than a long one
- Do not embed action items in the note text — those go in actions[]

Adapt to the user's financial stage — this is critical:

1. NO ACCOUNTS, NO POSITIONS (brand new user):
   - Briefly acknowledge the fresh start, invite them to add their first account or position
   - Keep it warm and concise — do not fill space with generic wealth-management advice

2. ACCOUNTS ONLY (has bank/credit card accounts, no investments yet):
   - Write a cash management note: credit card utilisation, cash buffer adequacy, spending health
   - Do NOT comment on stock markets or investment news — it's not relevant to them
   - Surface practical observations: e.g. liabilities as % of liquid assets, whether cash buffer \
     covers 3–6 months expenses, whether any card balances look high
   - IMPORTANT — credit card balances: a balance does NOT mean the user is paying interest. \
     It could simply be in-cycle charges not yet due. Do not tell them to "pay now" or assume \
     revolving debt. Instead frame it as: the balance exists and the key habit is paying in \
     full each cycle. Name a due date ONLY when the card's account line gives one; when it \
     says "payment due date: NOT recorded", never mention a due date at all (not "by the due \
     date" either). Only flag urgency if there is actual evidence of revolving or overdue debt.
   - Whether their cash can cover a card balance is answered ONLY by the CASH AVAILABLE TO \
     SPEND section (Ready to Assign), never by account balances: money already assigned to \
     envelopes is spoken for. If that section says every dollar is assigned, never say cash \
     can absorb the card. If there is no such section, say nothing about Ready to Assign \
     or about whether cash can cover a card: this book may not use that method, and a \
     missing figure is never something to narrate.
   - Suggest actions around budgeting, tracking spending, or starting to invest if appropriate

3. HAS INVESTMENT POSITIONS:
   - Focus on news affecting their specific holdings, FX moves, upcoming actions
   - Reference their account balances if relevant (cash available to deploy is the CASH \
     AVAILABLE TO SPEND section's Ready to Assign, when present)

actions[]: 2–3 concrete, short imperatives specific to what is in the context. \
Match the user's stage: onboarding steps for new users, cash/debt actions for accounts-only users, \
portfolio actions for investors. Each must be something the client can do in the app or with \
their money today. Never advise taking on or keeping up debt ("keep using the card for \
everyday spending"), above all for a client whose cash is short: point at paying down, \
covering or trimming instead.

chat_suggestion: a question the client would naturally want to ask after reading, \
written in their voice ("Should I...", "What's your view on...", "How should I...").

OUTPUT FORMAT — respond with a SINGLE JSON object and NOTHING else (no prose, no markdown \
fences around it). The object has exactly these keys:
{
  "note": "<the 2-3 paragraph morning note — no action items here>",
  "actions": ["<short imperative action item>", "..."],
  "chat_suggestion": "<one question the client would want to ask Lucius>"
}
"""


def _spending_block(profile: dict, base_ccy: str) -> list[str]:
    """Compact spending PULSE (signal, not a transaction log): this month against the book's
    budget, a few outlier charges, and what was recently logged/auto-captured. Returns a list
    of context lines (empty if there's nothing worth surfacing).

    The budget part is the Budget screen's own month view for the home method
    (``briefing_context.budget_lines``) — the briefing keeps no budget arithmetic of its own, so
    it cannot quote a monthly figure or an "over budget" category the screen doesn't show."""
    try:
        txns_all = ledger_service.transactions.all()
    except Exception:
        return []
    today = user_today()
    prefix = today.strftime("%Y-%m")
    rates = load_fx_cache()
    # Periods are described by how old the book actually is: "over 14d" or "the last three
    # days" on a book that began yesterday is a claim about days that are not in it.
    age = book_state.history_age_days(today, txns_all)
    recent_days, recent_phrase = book_state.recent_window(age)
    spend_days = today.day if age is None else min(today.day, age + 1)

    def amt(t):
        # canonical recompute into the current base (never a stale stored base_amount)
        return ledger_service.txn_base_amount(t, base_ccy, rates)

    month_txns = [
        t
        for t in txns_all
        if not t.get("deleted")
        and t.get("kind") == "expense"
        and (t.get("date") or "").startswith(prefix)
    ]
    lines: list[str] = []
    if age is not None:
        lines += book_state.uncategorized_lines(base_ccy)

    if month_txns:
        total = sum(amt(t) for t in month_txns)
        lines += facts.budget_lines(prefix, base_ccy) or [
            f"  Month-to-date spend: {facts.money(total, base_ccy)} over {spend_days}d "
            "(no budget set up)"
        ]

        # Outlier charges — the few large ones worth noticing
        outliers = [
            t
            for t in sorted(month_txns, key=amt, reverse=True)[:3]
            if amt(t) >= max(total * 0.08, 100)
        ]
        if outliers:
            lines.append(
                "  Largest charges: "
                + "; ".join(
                    f"{facts.money(amt(t), base_ccy)} "
                    f"{(t.get('description') or t.get('category') or '').strip()}"
                    for t in outliers
                )
            )

    # Recently logged — keeps the user aware of what got captured, esp. via email
    cutoff = (today - timedelta(days=recent_days - 1)).isoformat()
    recent = [
        t
        for t in txns_all
        if not t.get("deleted")
        and t.get("kind") in ("expense", "income")
        and (t.get("date") or "") >= cutoff
    ]
    if recent:
        spent_recent = sum(amt(t) for t in recent if t.get("kind") == "expense")
        via_email = sum(1 for t in recent if t.get("source") == "email")
        note = (
            f"  Recently logged: {len(recent)} transaction{'' if len(recent) == 1 else 's'} "
            f"{recent_phrase} ({facts.money(spent_recent, base_ccy)} spend)"
        )
        if via_email:
            note += f", {via_email} auto-captured from email"
        lines.append(note)

    return lines


def _availability_lines(ym: str, base_ccy: str, card_owed: float) -> list[str]:
    try:
        return facts.cash_availability_lines(ym, base_ccy, card_owed)
    except Exception:
        logger.warning("briefing cash availability unavailable", exc_info=True)
        return []


def _invested_book(base: str) -> dict:
    """The Investments screen's book, valued at last-known prices (no provider round trip on a
    briefing load): owned market holdings only, one row per name across accounts."""
    projection = portfolio_rows_service.projected_rows(base, live=False)
    return investments_book_service.investments_book(base, projection=projection)


def _card_due_note(account: dict) -> str:
    """A card's payment due date AS RECORDED, or the plain fact that none is. The note once
    told a user to settle a card "by the due date" when the book had no due date for it."""
    if account.get("type") != "credit":
        return ""
    if account.get("payment_due_date"):
        return f", payment due {account['payment_due_date']}"
    if account.get("payment_due_day"):
        return f", payment due on day {account['payment_due_day']} of each month"
    return ", payment due date: NOT recorded"


def _attention_section(base_ccy: str) -> Optional[str]:
    try:
        lines = book_state.attention_lines(base_ccy)
    except Exception:
        logger.warning("briefing attention flags unavailable", exc_info=True)
        return None
    return (
        "\nNEEDS ATTENTION (the Dashboard's own flag list, in its order; the same flags the "
        "client sees on screen):\n" + chr(10).join(lines)
    )


def _thesis_section(theses: tuple[dict, dict]) -> Optional[str]:
    judgments, names = theses
    if not judgments:
        return None
    return (
        "\nYOUR INVESTMENT THESES (the client's own reasons and price levels — any line about "
        "one of these tickers that says what to do cites its thesis):\n"
        + chr(10).join(facts.thesis_lines(judgments, names))
        + "\n"
        + thesis_judgment.THESIS_RULE
    )


def _build_context(total_nw: float, book: Optional[dict] = None) -> str:
    now = user_now()
    today = now.strftime("%A, %d %B %Y")
    time_of_day = _daypart(now)[1]
    today_ym = user_today().strftime("%Y-%m")
    fx = load_fx_cache()
    profile = read_profile()
    base_ccy = (profile.get("base_currency") or "USD").upper()

    # The owned, market-valued holdings exactly as Investments shows them.
    book = book if book is not None else _invested_book(base_ccy)
    holdings_lines = facts.holdings_lines(book, base_ccy)
    movers_lines = facts.movers_lines(book)
    exposed_currencies = {
        (r["currency"] or base_ccy).upper()
        for r in book["merged_rows"]
        if (r["currency"] or base_ccy).upper() != base_ccy
    }

    # Only show FX rates for currencies the portfolio is actually exposed to
    fx_lines = []
    for ccy in sorted(exposed_currencies):
        rate = fx.get(ccy)
        if rate and rate > 0:
            fx_lines.append(f"  {ccy}/{base_ccy}: {rate:.6g}")
    fx_block = "\n".join(fx_lines) if fx_lines else "  (no foreign currency exposure)"

    # Accounts summary — always included so Lucius knows the cash/debt picture
    account_lines: list[str] = []
    card_owed = 0.0
    txns: list[dict] = []
    try:
        accts = ledger_service.accounts.query_accounts()
        txns = ledger_service.transactions.all()
        cutoffs = ledger_service.cutoffs_by_account(accts, txns)
        for a in accts:
            bal = ledger_service.account_balance(a, txns, cutoffs[a["id"]])
            bal_b = ledger_service.to_base(bal, a.get("currency", base_ccy), base_ccy)
            kind = "asset" if a.get("kind") == "asset" else "liability"
            account_ccy = a.get("currency", "")
            if a.get("type") == "credit" and bal_b < 0:
                card_owed -= bal_b
            account_lines.append(
                f"  {a.get('name')} [{kind}, {a.get('type', '').replace('_', ' ')}] "
                f"— {facts.money(bal_b, base_ccy)} (native: {account_ccy} {round(bal):,})"
                + _card_due_note(a)
            )
    except Exception:
        pass

    upcoming_lines = facts.upcoming_lines(user_today(), base_ccy, fx)

    notes = profile.get("notes", "")
    prefs = profile.get("briefing_preferences") or {}
    spending_lines = _spending_block(profile, base_ccy) if prefs.get("spending", True) else []

    # Signal what stage this user is at so Lucius picks the right note style
    if not account_lines and not holdings_lines:
        user_stage = "NEW USER — no accounts and no investment positions set up yet"
    elif not holdings_lines:
        user_stage = "ACCOUNTS ONLY — has bank/credit accounts but no investment positions yet"
    else:
        user_stage = "INVESTOR — has investment positions (and possibly accounts)"

    # Assemble only the sections the user has left enabled (advisory toggles).
    sections: list[str] = [
        f"Today: {today}, {now.strftime('%H:%M')} local time ({time_of_day})",
        f"Base currency: {base_ccy}",
        f"User stage: {user_stage}",
    ]
    if prefs.get("accounts", True):
        sections.append("\nACCOUNTS:\n" + (chr(10).join(account_lines) or "  (none set up yet)"))
        availability = _availability_lines(today_ym, base_ccy, card_owed)
        if availability:
            sections.append(
                "\nCASH AVAILABLE TO SPEND (account balances above include money already "
                "assigned to envelopes; this section is the only authority on what cash is free):\n"
                + chr(10).join(availability)
            )
    if prefs.get("holdings_news", True):
        sections.append(
            "\nKEY INVESTMENT HOLDINGS (search for news on these):\n"
            + (chr(10).join(holdings_lines) or "  (none — no market-priced positions)")
        )
        if movers_lines:
            sections.append(
                "\nTODAY'S MOVES ACROSS ALL HOLDINGS (the full ranking — the weakest or "
                "strongest holding is named HERE, not by scanning the list above, which "
                "shows only the largest positions):\n" + chr(10).join(movers_lines)
            )
        thesis_section = _thesis_section(facts.thesis_judgments(book))
        if thesis_section:
            sections.append(thesis_section)
    if prefs.get("fx", True):
        sections.append(
            "\nCURRENT FX RATES (for holdings with foreign currency exposure):\n" + fx_block
        )
    if spending_lines:
        sections.append(
            "\nSPENDING PULSE (surface only what's notable — overspent envelopes or caps, "
            "outliers, or recent activity worth flagging; do not list routine purchases):\n"
            + chr(10).join(spending_lines)
        )
    if prefs.get("upcoming_actions", True):
        sections.append(
            "\nUPCOMING ACTIONS AND OVERDUE ITEMS (next 30 days: the same count and total the Dashboard and Commitments show):\n"
            + (chr(10).join(upcoming_lines) or "  None due and nothing overdue")
        )

    custom_focus = (prefs.get("custom_focus") or "").strip()
    if custom_focus:
        sections.append(
            "\nCLIENT'S STANDING INSTRUCTIONS FOR THIS BRIEFING (honour these — they "
            "told you what they care about):\n  " + custom_focus
        )

    sections.append(
        "\nBOOK HISTORY (how much of the client's life this book holds; describe periods only "
        "as far back as this goes):\n" + chr(10).join(book_state.history_lines(user_today(), txns))
    )
    attention = _attention_section(base_ccy)
    if attention:
        sections.append(attention)

    sections.append(f"\nPROFILE NOTES (for context):\n{notes or '  None'}")
    # What Lucius remembers about the client — the same block a chat turn gets. Without it the
    # briefing never knew the name they asked to be called or the preferences they'd stated.
    from backend.ai.prompts import memory_block

    remembered = memory_block()
    if remembered:
        sections.append(
            remembered
            + "\nUse it like a chat turn would: address them by the name they asked to be "
            "called if one is remembered, and let their stated preferences shape what you "
            "cover and how — without reciting any of it."
        )
    sections.append(
        f"\nTotal NW: {facts.money(total_nw, base_ccy)} (already shown on screen — do not repeat this)"
    )
    return "\n".join(sections) + "\n"


# The activity fingerprint (an import, a new book's first transactions, a Needs-attention flag)
# is checked at most this often: an import lands in a burst and a flag can flap with the day's
# balances, and every regeneration is a model call. The structure fingerprint is not debounced.
_ACTIVITY_DEBOUNCE = timedelta(seconds=120)


def _load_cache(
    fingerprint: Optional[str] = None, activity: Optional[str] = None
) -> Optional[dict]:
    """The cached note, while it is still true. Fresh only within the same day-part AND, when a
    ``fingerprint`` of the book's current structure is given, only while the book still has the
    structure the note was written against (``facts.book_fingerprint``). An ``activity``
    fingerprint (``book_state.activity_fingerprint``) makes it stale too, but only once the note
    is older than ``_ACTIVITY_DEBOUNCE``."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Invalidate old format caches
        if "note" not in data:
            return None
        generated_at = datetime.fromisoformat(data["generated_at"])
        # Fresh only within the same day-part — reopening later in the day regenerates.
        if _daypart(generated_at) != _daypart(user_now()):
            return None
        # A material change to the book (a card or envelope added, categories linked, money
        # assigned) makes the note about a book that no longer exists.
        if fingerprint is not None and data.get("book_fingerprint") != fingerprint:
            return None
        if (
            activity is not None
            and data.get("activity_fingerprint") != activity
            and user_now() - generated_at >= _ACTIVITY_DEBOUNCE
        ):
            return None
        return data
    except Exception:
        return None


def _without_fingerprint(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("book_fingerprint", "activity_fingerprint")}


def _current_fingerprint() -> Optional[str]:
    try:
        return facts.book_fingerprint(user_today().strftime("%Y-%m"))
    except Exception:
        logger.warning("briefing book fingerprint unavailable", exc_info=True)
        return None


def _current_activity() -> Optional[str]:
    try:
        return book_state.activity_fingerprint(ledger_service.base_currency())
    except Exception:
        logger.warning("briefing activity fingerprint unavailable", exc_info=True)
        return None


def _save_cache(
    payload: dict, fingerprint: Optional[str] = None, activity: Optional[str] = None
) -> dict:
    data = {**payload, "generated_at": user_now().isoformat()}
    if fingerprint is not None:
        data["book_fingerprint"] = fingerprint
    if activity is not None:
        data["activity_fingerprint"] = activity
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    return data


async def _fetch_news(queries: list[str]) -> str:
    """Live-search the briefing's holding/FX queries and return a combined text block.

    Routes through the shared evidence collector: it ALWAYS live-searches (top-up — never
    cache-only, so the briefing reflects the latest), then returns today's hits unioned with the
    intel scan's still-valid accumulated holdings news (shared ``scan:intel`` cache) so a valid
    recent story doesn't vanish. Degrades to an empty block when no search provider is configured —
    the rest of the briefing still renders.
    """
    from backend.monitoring.collect import gather_evidence

    try:
        rows = await gather_evidence(queries, ["scan:intel"], kind="news", max_results=4, limit=12)
    except Exception as exc:
        logger.warning("briefing news unavailable: %s", type(exc).__name__)
        return ""
    return "\n\n".join(
        f"  [{r.get('title', '')}] {r.get('content', '')[:200]}" for r in rows if r.get("title")
    )


def _briefing_model() -> str:
    """Task-assigned model (Settings > AI Model > Task models) or the active chat model."""
    from backend.db.model_config_store import ModelConfigStore

    return ModelConfigStore().task_model_override("briefing") or get_model()


async def _generate_briefing(total_nw: float) -> dict:
    profile = read_profile()
    base_ccy = (profile.get("base_currency") or "SGD").upper()
    # OFF THE LOOP: the book and the context fold every position, account and transaction
    # synchronously, which would otherwise hold the whole event loop for the request.
    book = await asyncio.to_thread(_invested_book, base_ccy)
    context = await asyncio.to_thread(_build_context, total_nw, book)
    prompt = context

    prefs = profile.get("briefing_preferences") or {}
    market_rows = sorted(book["merged_rows"], key=lambda r: r["value"], reverse=True)
    # Pre-fetch news only when the user has market-exposed holdings, and skip it entirely if
    # they turned holdings news off (saves the searches).
    if market_rows and prefs.get("holdings_news", True):
        queries: list[str] = []
        seen_labels: set[str] = set()
        for r in market_rows[:3]:
            label = r["name"] or r["symbol"]
            if label and label not in seen_labels:
                # Use full label not ticker to avoid ambiguous searches (a bare ticker can resolve to the wrong company)
                queries.append(f"{label} stock news this week")
                seen_labels.add(label)
        # Add FX news for exposed currencies
        exposed = {
            r["currency"] for r in market_rows if r["currency"] and r["currency"] != base_ccy
        }
        for ccy in sorted(exposed)[:1]:
            queries.append(f"{ccy} {base_ccy} exchange rate this week")

        news_block = await _fetch_news(queries)
        prompt = f"{context}\n\nCURRENT NEWS:\n{news_block}"

    prompt += (
        f"\n\nYour ENTIRE JSON response must fit within a {_BRIEFING_MAX_TOKENS}-token output "
        "budget. Keep the note, actions, and chat_suggestion concise enough that the JSON is "
        "always complete and never cut off mid-object."
    )

    # Route the completion through the LLM-provider seam so the briefing runs on whatever
    # provider/model the instance has configured (anthropic OR openai_compatible) — no
    # hardcoded model id, no raw SDK client. get_model() resolves the active chat model
    # (store → env → default); the provider records its own cost-tracking entry.
    # Structured-output layer: derives a JSON schema from ``_BriefingOutput``, forces the
    # provider's structured/json_object mode + reasoning-off, and validates the reply against
    # the SAME model (reasking once on a validation failure). Returns a validated instance or
    # None — never raw/unparseable output. ``complete_model`` doesn't raise for a bad reply,
    # but ``complete`` underneath still raises ``LLMUnavailable`` when no provider is
    # configured, so keep the guard so the route degrades instead of 500-ing.
    try:
        result = await complete_model(
            prompt,
            _BriefingOutput,
            system=_SYSTEM,
            model=_briefing_model(),
            max_tokens=_BRIEFING_MAX_TOKENS,
            source="briefing",
            effort="none",  # bounded narration of already-computed figures — no reasoning
        )
    except Exception as exc:  # LLMUnavailable or a provider/network error — never 500 the route
        logger.warning("briefing generation failed: %s", type(exc).__name__)
        return {
            "note": "Couldn't reach the AI to generate your briefing. "
            "Check your AI provider in Settings, then refresh.",
            "chat_suggestion": "",
            "error": True,
        }

    if result is not None:
        return {
            "note": result.note,
            "actions": result.actions,
            "chat_suggestion": result.chat_suggestion,
        }
    # No usable structured briefing even after a reask (the seam already logged the reason) —
    # degrade to a visible error rather than shipping an empty/raw body. The route leaves this
    # uncached so the next load retries.
    logger.warning("briefing reply unusable — no valid structured output; skipping")
    return {
        "note": "The AI returned a briefing we couldn't read. Try refreshing.",
        "chat_suggestion": "",
        "error": True,
    }


@router.get("/api/briefing")
async def get_briefing(
    refresh: bool = Query(False),
    current_nw: Optional[float] = Query(None),
):
    # Taken BEFORE the note is written: a change landing mid-generation then marks it stale
    # rather than being silently folded into a fingerprint the note never saw.
    fingerprint = await asyncio.to_thread(_current_fingerprint)
    activity = await asyncio.to_thread(_current_activity)
    if not refresh:
        cached = _load_cache(fingerprint, activity)
        if cached:
            return {**_without_fingerprint(cached), "is_cached": True}

    if current_nw is None:
        try:
            # Canonical add-everything fold (accounts + market), base-normalised —
            # not a market-only sum anchored to the FX pivot.
            # OFF THE LOOP: net_worth() folds every account, position and FX rate
            # synchronously. Same shape as the evidence read further down this path — an
            # async endpoint calling straight into heavy sync work holds the whole loop,
            # which is what left /runs, /live and /portfolio all pending at once.
            nw = await asyncio.to_thread(ledger_service.net_worth)
            current_nw = nw.get("net_worth", 0.0)
        except Exception:
            current_nw = 0.0

    payload = await _generate_briefing(current_nw)
    # Don't persist a failed briefing — caching it would pin the error for the whole
    # day-part; leaving it uncached lets the next load (or a refresh) retry.
    if payload.get("error"):
        return {**payload, "is_cached": False}
    data = _save_cache(payload, fingerprint, activity)
    return {**_without_fingerprint(data), "is_cached": False}
