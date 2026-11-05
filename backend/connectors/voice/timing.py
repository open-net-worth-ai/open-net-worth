"""How long each step of a spoken turn took, for the log (no words, only seconds): nothing else
says where the wait goes between the owner stopping and the first sound (owner, 2026-11-04)."""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class SpeechLog:
    """Each spoken line's life, for the log (a number, a kind and a length, no words): when it was
    queued, played, and finished or cut off. The chat only shows the order things were written,
    which couldn't explain an answer heard out of order (owner, 2026-11-05). Worker lines carry no
    timestamp of their own, hence the clock in each."""

    def __init__(self) -> None:
        self._count = 0
        self._lines: dict[str, dict] = {}

    def _note(self, what: str) -> None:
        log.info("voice speech: %s %s", time.strftime("%H:%M:%S"), what)

    def created(self, speech) -> None:
        self._count += 1
        line = {"n": self._count, "kind": "front voice", "chars": None}
        line.update(queued=time.monotonic(), started=None, name=f"#{self._count}")
        self._lines[speech.id] = line
        speech.add_done_callback(self._done)

    def label(self, speech, kind: str, chars: int) -> None:
        line = self._lines.get(getattr(speech, "id", None))
        if line is None:
            return
        line["name"] = f"#{line['n']} {kind} ({chars} chars)"
        self._note(f"{line['name']} queued")

    def playing(self, speech) -> None:
        line = self._lines.get(getattr(speech, "id", None))
        if line is None:
            return
        now = time.monotonic()
        if line["started"] is None:
            line["started"] = now
            self._note(f"{line['name']} started, {now - line['queued']:.1f}s after it was queued")
        else:
            self._note(f"{line['name']} playing again, {now - line['started']:.1f}s after it began")

    def resumed(self, resumed: bool) -> None:
        self._note(f"false interruption, the line {'resumed' if resumed else 'did not resume'}")

    def _done(self, speech) -> None:
        line = self._lines.pop(speech.id, None)
        if line is None:
            return
        if line["started"] is None:
            # Back-to-back lines may not change the agent's state, so a missed start proves nothing
            # unless it was also interrupted.
            how = "dropped before it played" if speech.interrupted else "ended, start not seen"
            self._note(f"{line['name']} {how}")
            return
        how = "cut off" if speech.interrupted else "finished"
        self._note(f"{line['name']} {how} after {time.monotonic() - line['started']:.1f}s")


class AnswerTiming:
    """Waiting for Lucius versus Lucius writing: the second part is all that speaking while the
    answer streams could save (owner, 2026-11-05)."""

    def __init__(self) -> None:
        self._asked = time.monotonic()
        self._writing: float | None = None

    def text(self, chunk: str) -> None:
        if self._writing is None and chunk.strip():
            self._writing = time.monotonic()

    def lookup(self) -> None:
        # What he wrote before a lookup is his working, not the answer starting.
        self._writing = None

    def done(self, chars: int) -> None:
        now = time.monotonic()
        if self._writing is None:
            log.info(
                "voice timing: Lucius ended %.2fs after the request, no answer", now - self._asked
            )
            return
        log.info(
            "voice timing: Lucius's answer began %.2fs after the request, written in %.2fs, %d chars",
            self._writing - self._asked,
            now - self._writing,
            chars,
        )


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
