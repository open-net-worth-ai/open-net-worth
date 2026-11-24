"""Where a conversation's verbatim window starts, chosen by token pressure (flag
``token_context_window``).

The old window was the last ``MAX_HISTORY_MESSAGES`` messages, advanced in steps of ten. A
message is not a size, so a chat of short messages compacted every ten messages, and the start
moved past what the fold summary covered: every slide after the first dropped ten messages
that neither the window nor the summary held.

Here the window start IS the fold's ``folded_through_idx``: nothing exists between them, so
nothing can fall through. The start moves only when the prompt is over the trigger, and then
far enough down (to the target) that the next move is a long way off: the prompt cache, and a
reused CLI session, survive every ordinary turn. ADR
``2026-11-24-the-context-window-starts-where-the-fold-ends-and-moves-on-token-pressure``.

Pure planning lives here (no I/O beyond the measurement helper); ``assistant`` runs the fold.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

# Compact once the prompt passes this share of the context window, capped in tokens: past
# ~160K a turn costs real money and latency even on cached reads, and long-context recall
# degrades. Hermes defaults to 50%, OpenClaw to min(window - 20K, 85%).
TRIGGER_FRACTION = 0.55
TRIGGER_CAP_TOKENS = 160_000
# ...and cut the window back to this, so ~25% of the window of growth separates two folds.
TARGET_FRACTION = 0.30
TARGET_CAP_TOKENS = 90_000
# Never folded, however large: the last messages, this turn and the two exchanges before it (an Automation's
# own report stays readable when the owner asks about it — live incident 2026-08-09).
MIN_TAIL_MESSAGES = 20
MIN_TAIL_USER_TURNS = 3  # this turn + the two exchanges before it
# A fold must free at least this much, or it is not worth the cache it breaks — on a small
# window the system prompt alone can sit near the trigger, and every turn would fold a little
# (Hermes gates its pruning the same way: proactive_prune_min_reclaim_tokens = 4096).
MIN_RECLAIM_TOKENS = 4_096
MIN_RECLAIM_FRACTION = 0.10
# System prompt + tool schemas before a session has a measured prompt to derive it from.
DEFAULT_OVERHEAD_TOKENS = 25_000
# One summary call reads at most this much conversation text; a longer range folds in steps.
FOLD_STEP_MAX_CHARS = 240_000
APPROX_BYTES_PER_TOKEN = 4


def approx_tokens(messages: list[dict]) -> int:
    """Codex's byte/4 estimate over the serialised messages."""
    if not messages:
        return 0
    text = json.dumps(messages, ensure_ascii=False, default=str)
    return -(-len(text) // APPROX_BYTES_PER_TOKEN)


def _message_tokens(msg: dict) -> int:
    return approx_tokens([msg])


@dataclass(frozen=True)
class Budget:
    window: int
    trigger: int
    target: int


def budget_for(window: int) -> Budget:
    window = max(1, int(window))
    return Budget(
        window=window,
        trigger=min(int(window * TRIGGER_FRACTION), TRIGGER_CAP_TOKENS),
        target=min(int(window * TARGET_FRACTION), TARGET_CAP_TOKENS),
    )


@dataclass(frozen=True)
class Plan:
    """This turn's window. ``fold_to`` is set when the turn must fold up to it first; the
    turn then starts at whatever the fold actually reached (``folded_through_idx``)."""

    start: int
    fold_to: Optional[int]
    prompt_tokens: int
    budget: Budget


def is_turn_boundary(msg: dict) -> bool:
    """A message a window may open on: a user message that is not a tool result. Opening
    there never separates a tool call from its result."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return True


def boundary_digest(history: list[dict], idx: int) -> Optional[str]:
    """Fingerprint of the last message a fold covered. A rewind below the fold and new
    messages after it change this, so a fold that summarises messages that are no longer in
    the conversation is never trusted."""
    if idx <= 0 or idx > len(history):
        return None
    # Role and content only: a reloaded message carries extras (``ts``, a client id) the
    # in-memory copy it was folded from did not, and those must not read as a rewind.
    msg = history[idx - 1]
    raw = json.dumps(
        [msg.get("role"), msg.get("content")], ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def trusted_start(fold_data: Optional[dict], history: list[dict]) -> int:
    """Where the fold ends, if the fold still describes this conversation; else 0."""
    if not fold_data or not fold_data.get("summary"):
        return 0
    idx = fold_data.get("folded_through_idx")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx <= 0 or idx > len(history):
        return 0
    digest = fold_data.get("boundary_digest")
    if digest is not None and digest != boundary_digest(history, idx):
        return 0
    return idx


def tail_floor(history: list[dict], turn_start: int) -> int:
    """The latest index a cut may take: the last ``MIN_TAIL_MESSAGES`` messages, the last
    ``MIN_TAIL_USER_TURNS`` exchanges and this turn are always sent whole."""
    floor = min(len(history) - MIN_TAIL_MESSAGES, turn_start)
    seen = 0
    for i in range(len(history) - 1, -1, -1):
        if is_turn_boundary(history[i]):
            seen += 1
            if seen >= MIN_TAIL_USER_TURNS:
                floor = min(floor, i)
                break
    else:
        floor = min(floor, 0)
    return max(0, floor)


def choose_cut(history: list[dict], start: int, floor: int, fit_tokens: int) -> int:
    """The earliest turn boundary in (start, floor] whose suffix fits ``fit_tokens``. When
    none fits, the latest boundary allowed — the tail floor wins over the target. ``start``
    when there is no boundary to move to."""
    suffix = [0] * (len(history) + 1)
    for i in range(len(history) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + _message_tokens(history[i])
    last_ok = start
    for i in range(start + 1, floor + 1):
        if not is_turn_boundary(history[i]):
            continue
        if suffix[i] <= fit_tokens:
            return i
        last_ok = i
    return last_ok


def plan_window(
    history: list[dict],
    fold_data: Optional[dict],
    *,
    turn_start: int,
    window_tokens: int,
    measured_prompt: Optional[int],
) -> Plan:
    """Decide this turn's window from the conversation and the last measured prompt.

    ``measured_prompt`` is the previous turn's largest request as the provider reported it.
    It sized the window as it was then, so the non-history overhead (system prompt, tools)
    is that figure minus the estimate of what was sent; this turn's prompt is the overhead
    plus the estimate of what would be sent now.
    """
    start = trusted_start(fold_data, history)
    budget = budget_for(window_tokens)
    sent_before = approx_tokens(history[start:turn_start])
    if measured_prompt:
        overhead = max(0, measured_prompt - sent_before)
    else:
        overhead = DEFAULT_OVERHEAD_TOKENS
    prompt = overhead + approx_tokens(history[start:])
    if prompt <= budget.trigger:
        return Plan(start=start, fold_to=None, prompt_tokens=prompt, budget=budget)
    floor = tail_floor(history, turn_start)
    # History may keep at most what fits under the target beside the overhead, and never
    # more than half of what fits under the trigger, so a fold always buys real room.
    fit = max(0, min(budget.target - overhead, (budget.trigger - overhead) // 2))
    cut = choose_cut(history, start, floor, fit)
    reclaim = approx_tokens(history[start:cut])
    worth = max(MIN_RECLAIM_TOKENS, int(budget.window * MIN_RECLAIM_FRACTION))
    fold_to = cut if cut > start and reclaim >= worth else None
    return Plan(start=start, fold_to=fold_to, prompt_tokens=prompt, budget=budget)


def fold_steps(history: list[dict], start: int, fold_to: int, text_of) -> list[int]:
    """Cut points to fold through, in order, so no single summary call reads more than
    ``FOLD_STEP_MAX_CHARS`` of conversation text. Each is a turn boundary; the last is
    ``fold_to``. A failed step leaves the window at the previous one — never past it."""
    steps: list[int] = []
    begin = start
    size = 0
    last_boundary = None
    for i in range(start, fold_to):
        if i > begin and is_turn_boundary(history[i]):
            last_boundary = i
        size += len(text_of(history[i : i + 1]))
        if size > FOLD_STEP_MAX_CHARS and last_boundary is not None and last_boundary > begin:
            steps.append(last_boundary)
            begin = last_boundary
            size = len(text_of(history[begin : i + 1]))
            last_boundary = None
    steps.append(fold_to)
    return steps
