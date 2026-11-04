"""How long each step of a spoken turn took, for the log (no words, only seconds): nothing else
says where the wait goes between the owner stopping and the first sound (owner, 2026-11-04)."""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class TurnTiming:
    def __init__(self) -> None:
        self._stopped: float | None = None
        self._was_speaking = False

    def user_state(self, state: str) -> None:
        if state == "speaking":
            self._stopped, self._was_speaking = None, True
        elif self._was_speaking:
            self._stopped, self._was_speaking = time.monotonic(), False

    def _since(self) -> str:
        return "?" if self._stopped is None else f"{time.monotonic() - self._stopped:.2f}s"

    def handed(self) -> None:
        log.info("voice timing: handed to Lucius %s after they stopped", self._since())

    def agent_speaking(self) -> None:
        if self._stopped is not None:
            log.info("voice timing: first sound %s after they stopped", self._since())
            self._stopped = None

    def metrics(self, event) -> None:
        m = getattr(event, "metrics", None)
        kind = getattr(m, "type", "")
        if kind == "eou_metrics":
            log.info(
                "voice timing: end of turn %.2fs (transcript %.2fs)",
                m.end_of_utterance_delay,
                m.transcription_delay,
            )
        elif kind == "llm_metrics":
            log.info(
                "voice timing: front model first token %.2fs, whole reply %.2fs, %d prompt tokens",
                m.ttft,
                m.duration,
                m.prompt_tokens,
            )
        elif kind == "tts_metrics":
            log.info("voice timing: voice first audio %.2fs", m.ttfb)
