"""A CLI round that has made its tool calls is not left waiting on a reply that won't end.

The host runs the calls a ``claude -p`` round writes only once the CLI closes the round
with its ``result`` event. On 2026-10-19 an Automation round wrote ``update_run_task`` and
``email_search_messages``, then kept streaming for the whole 300 s ceiling without that
event; the stall watchdog never fired because output never stopped, both calls were
marked failed and no mail was read. A well-formed call ends the model's useful work for
the round — anything it writes after is commentary or an invented result — so once the
newest parsed call is ``grace`` seconds old the round is cut and its calls are run.

The clock restarts at every parsed call (a reply with several calls keeps them all) and
never runs while the scanner is inside an open ``<tool_call>`` block (a long call is not
cut in half). What streamed after the last call is tallied, so a cut or a timeout says
what the CLI was doing instead of leaving the minutes unexplained.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Optional

from backend.ai.models.cli_live_session import env_seconds

GRACE_ENV = "ONW_CLAUDE_CLI_CALL_GRACE"
GRACE_DEFAULT = 30.0
_PREVIEW_CHARS = 160


def grace_seconds() -> float:
    return env_seconds(GRACE_ENV, GRACE_DEFAULT, 1.0)


class PostCallWatch:
    """Tracks what one CLI round streams after its newest parsed tool call."""

    def __init__(self, grace: float) -> None:
        self.grace = grace
        self.cut = False
        self.calls = 0
        self.last_call_at: Optional[float] = None
        self.started_at = time.monotonic()
        self._reset_tally()

    def _reset_tally(self) -> None:
        self.kinds: Counter[str] = Counter()
        self.text_chars = 0
        self.thinking_chars = 0
        self.preview = ""

    def call_parsed(self) -> None:
        self.calls += 1
        self.last_call_at = time.monotonic()
        self._reset_tally()

    def saw(self, event: dict) -> None:
        kind = event.get("type") or "?"
        inner = event.get("event") or {}
        if kind == "stream_event":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                self.text_chars += len(text)
                if len(self.preview) < _PREVIEW_CHARS:
                    self.preview = (self.preview + text)[:_PREVIEW_CHARS]
                kind = "text_delta"
            elif delta.get("type") == "thinking_delta":
                self.thinking_chars += len(delta.get("thinking") or "")
                kind = "thinking_delta"
            else:
                kind = inner.get("type") or kind
        elif event.get("subtype"):
            kind = f"{kind}:{event['subtype']}"
        self.kinds[kind] += 1

    def remaining(self, mid_block: bool) -> Optional[float]:
        """Seconds left before the round should be cut; None while no cut can apply."""
        if self.last_call_at is None or mid_block:
            return None
        return self.last_call_at + self.grace - time.monotonic()

    def summary(self) -> str:
        now = time.monotonic()
        if self.last_call_at is None:
            calls, since = "no call parsed", "round start"
        else:
            calls = f"{self.calls} call(s), last {now - self.last_call_at:.0f}s ago"
            since = "last call"
        kinds = ", ".join(f"{k}={n}" for k, n in self.kinds.most_common(6)) or "nothing"
        return (
            f"round {now - self.started_at:.0f}s, {calls}; since {since}: {kinds}; "
            f"text={self.text_chars} chars, thinking={self.thinking_chars} chars; "
            f"text began: {self.preview!r}"
        )
