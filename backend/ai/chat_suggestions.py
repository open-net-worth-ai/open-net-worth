"""Follow-up chat suggestions for CONNECTORS — the 2-4 tappable replies a user would send next,
based on the last exchange. This is the connector-reachable twin of ``api/chat_suggestions.py``'s
FOLLOW-UP mode: connectors import ``ai`` (never ``api``, per the dependency rule), so the generation
lives here. Best-effort: any failure returns ``[]`` and the caller simply sends no suggestions.

Deliberately follow-up ONLY (no STARTER mode) — a connector reply always has a last exchange to
base suggestions on, and STARTER needs the portfolio snapshot which lives behind ``api``. The
FOLLOW-UP system prompt is duplicated from ``api/chat_suggestions.py`` on purpose: unifying them
would require moving the whole thing (incl. the api-bound STARTER path) here, out of scope for this
change — kept semantically in sync by hand.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from backend.ai import assistant, skill_runtime
from backend.ai.structured import complete_model
from backend.ai.tool_text import VOICE_CALL_NOTE_OPENINGS, is_harness_note
from backend.core.chat_context_markers import HARNESS_NOTE_OPEN
from backend.core.untrusted import is_screen_context_block

logger = logging.getLogger(__name__)

_FOLLOWUP_SYSTEM = """\
You are Lucius, a personal wealth manager. Based on the last exchange, propose what \
the client would tap to send next. These are buttons that send the text verbatim, so \
their whole job is to save the client from typing.
FIRST decide which case you're in:
A) Your reply ENDS BY ASKING THE CLIENT SOMETHING — a decision, a choice between \
   options, or a yes/no. Then propose the ANSWERS they'd give, NOT new questions.
   If YOUR question asked more than one thing at once, each suggestion should answer \
   ALL of it together in one reply — never split one compound question across several \
   buttons, each covering only part of it.
B) Your reply did NOT ask the client anything (you answered/explained). Then propose \
   follow-up QUESTIONS they'd naturally ask next — dig deeper, the next step, or an \
   adjacent angle. Do not repeat what you already answered.
Rules for both:
- 2-4 items. Short — under 10 words each. No numbering, no quotes.
- Each must be a COMPLETE, direct, sendable message — the button sends it VERBATIM the \
  instant it's tapped, with no chance to edit first. NEVER a sentence fragment, a \
  trailing ellipsis, or a fill-in-the-blank template ("My name is…", "I think…") — \
  there is nothing after the tap to complete it with.
- One exception to guessing a plausible answer: never guess a fact only the client \
  knows that has no small set of plausible answers — their OWN NAME, any amount (a \
  balance, what they owe, income, spending) or a date. A suggested one is simply \
  invented ("My name is Alex", "Balance is about S$1,200") and would submit FALSE \
  information the instant it's tapped; offer the answers that carry none instead \
  ("I'll send a screenshot", "Skip that for now"). Naming a bank, broker, card or \
  category is fine — a wrong one is simply not tapped — as long as it fits where the \
  client is (their home currency is given with the exchange): DBS or OCBC for a \
  Singapore client, never Chase. Everything else with a small, sensible answer set \
  stays fair game to guess.
"""


def home_currency_line() -> str:
    """Where the client is, for suggestions that name a bank or a shop. Without it a
    Singapore client was offered "Chase / Bank of America / Wells Fargo" (QA 2026-10-13)."""
    try:
        from backend.core.fx import base_currency

        return f"The client's home currency is {base_currency()}."
    except Exception:  # noqa: BLE001 — a nicety; never fail the suggestions over it
        return ""


ONBOARDING_SKILL_ID = "onboarding-new-user"

# The onboarding skill's fixed first menu ("what would you like to do first"). The five answers
# are the five branches the skill follows, so they are the same every time and cost no model call:
# a generated set came back late or empty on a first run and left only grey placeholder bars
# under a message that says "Pick one below" (persona QA round 9).
ONBOARDING_MENU_CHOICES = (
    "Show me where my money goes",
    "Show me everything I have in one place",
    "Help me set up a budget",
    "Check whether my money will last",
    "Keep an eye on my investments",
)

# What the menu message names, in the skill's words, one stem group per branch. The model may
# drop a parenthetical, so four of the five identify it; a budget-style question or any later
# "Pick one below" in the same skill names one or two.
_MENU_TOPICS = (
    ("spending", "money goes"),
    ("net worth", "one place"),
    ("budget",),
    ("retire", "will last"),
    ("invest",),
)
_MENU_TOPICS_NEEDED = 4


def onboarding_menu_choices(history: list[dict], assistant_text: str) -> list[str]:
    """The fixed first-menu chips when ``assistant_text`` is the onboarding skill's "what first?"
    message, else ``[]``. Needs the skill ACTIVE in the conversation and the reply to name the
    branches, so a later option question inside onboarding gets generated answers as usual."""
    if not any(
        r.skill_id == ONBOARDING_SKILL_ID for r in skill_runtime.active_invocations(history)
    ):
        return []
    text = assistant_text.lower()
    named = sum(any(stem in text for stem in topic) for topic in _MENU_TOPICS)
    if named < _MENU_TOPICS_NEEDED:
        return []
    return list(ONBOARDING_MENU_CHOICES)


class _Suggestions(BaseModel):
    suggestions: list[str] = Field(default_factory=list)


def _suggestions_model() -> str:
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().task_model_override("suggestions") or ""
    except Exception:  # noqa: BLE001
        return ""


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _real_client_text(content) -> str:
    """The client's own words from a user message's ``content`` — never harness-appended
    context. Mirrors ``ai/assistant.py``'s own transcript-reconstruction filtering (its
    ``_extract_user_text`` is module-private, so re-implemented here rather than importing
    a leading-underscore helper across a module boundary): a persisted ``<turn_context>``
    snapshot, a resolved @-mention block, and a screen-context snapshot are all MODEL
    context ``api/chat._merge_mention_context`` appends onto the user turn — never
    something the client typed.

    Unlike ``_block_text`` (which flattens EVERY block, turn_context included, into one
    string BEFORE any filtering can run — the exact bug this exists to avoid: a bare
    ``__ONBOARDING_INIT__`` block merged with its trailing ``<turn_context>`` block no
    longer string-matches ``is_harness_note``'s exact-equality check), this filters block
    by block, THEN joins."""
    if isinstance(content, str):
        return "" if is_harness_note(content) else content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        t = block.get("text", "")
        if t.startswith("<turn_context>"):
            continue
        if t.lstrip().startswith("«mention:"):
            continue
        if is_screen_context_block(t):
            continue
        if is_harness_note(t):
            continue
        parts.append(t)
    return "".join(parts).strip()


def _last_exchange(history: list[dict]) -> Optional[tuple[str, str]]:
    """(last user question, last assistant reply) from history, or None.

    The bug this guards against (live, 2026-08-25): on a brand-new onboarding session,
    Lucius speaks first — but the Messages API requires the transcript to START with a
    ``role: "user"`` turn, so the app satisfies that with a HIDDEN trigger message. That
    turn 0 is not one but TWO harness-authored entries: a bare ``__ONBOARDING_INIT__``
    block (structural — makes the turn valid at all) immediately followed by a
    ``<turn_context>`` block, and a SEPARATE skill-invocation entry carrying the entire
    onboarding-new-user skill body (content — the ``_onw_skill`` marker other transcript
    readers already know to skip, see ``ai/assistant.py``'s own reconstruction). Neither
    is the client's own words. Without this fix that content got quoted back to the
    suggestions model as "The client asked: [500 words of internal instructions]" — noisy
    enough to bury the very next rule (never guess the client's name) in front of it.
    """
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "assistant":
            a = _block_text(msg.get("content"))
            if a.strip():
                u = ""
                for j in range(i - 1, -1, -1):
                    if history[j].get("_onw_skill"):
                        continue  # a skill invocation, never the client's own words
                    if history[j].get("role") == "user":
                        ut = _real_client_text(history[j].get("content"))
                        if ut:
                            u = ut
                            break
                return u, a
    return None


def _spoken_on_a_call(history: list) -> bool:
    """Whether the newest thing the owner said came through a voice call: the call marks each
    message with a harness note, which the owner's own words can't forge."""
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("text")]
        if isinstance(content, list) and not texts:
            continue  # a tool's result, not something they said
        return any(
            f"{HARNESS_NOTE_OPEN}{opening}" in text
            for text in texts
            for opening in VOICE_CALL_NOTE_OPENINGS
        )
    return False


async def followup_suggestions(session_id: str) -> list[str]:
    """2-4 tappable follow-up messages for the session's last exchange. ``[]`` if there's no
    session/history or generation fails. The CALLER gates on the per-connector enable toggle."""
    if not session_id:
        return []
    try:
        history = assistant._load_session(session_id) or assistant.SESSIONS.get(session_id)
        if not history:
            return []
        exchange = _last_exchange(history)
        if not exchange:
            return []
        user_q, assistant_a = exchange
        if menu := onboarding_menu_choices(history, assistant_a):
            return menu
        # Nobody taps a chip mid-conversation out loud: a model call for nothing (owner, 2026-11-06).
        if _spoken_on_a_call(history):
            return []
        prompt = (
            f"The client asked:\n{user_q[:1500] or '(opening message)'}\n\n"
            f"You replied:\n{assistant_a[:3000]}\n\n"
            f"{home_currency_line()}\n"
            "Decide case A or B, then propose what the client would tap to send next."
        )
        json_system = (
            f"{_FOLLOWUP_SYSTEM}\n\nReturn ONLY a JSON object of the form "
            '{"suggestions": ["...", "..."]} — 2-4 short replies in the client\'s '
            "first-person voice. No prose, no code fence."
        )
        result = await complete_model(
            prompt,
            _Suggestions,
            system=json_system,
            model=_suggestions_model(),
            max_tokens=400,
            source="chat_suggestions",
            effort="none",  # short tappable replies — reasoning adds latency + cost, no value
        )
        if result is None:
            return []
        return [s.strip() for s in result.suggestions if s.strip()][:4]
    except Exception as exc:  # noqa: BLE001 — suggestions are a nicety, never fail the turn
        logger.debug("followup_suggestions failed: %s", type(exc).__name__)
        return []
