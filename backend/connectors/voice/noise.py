"""Which Krisp filter cleans the caller's audio on a call: the Noise cancelling setting.

It runs here, on the audio the worker receives, so no browser or phone changes. Both filters need
LiveKit Cloud, and a filter is never worth a call: whatever goes wrong here, the call goes ahead
without it. LiveKit's plugins register only on the main thread, so they are imported with this
module, which the worker imports first (``worker.py``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from livekit import rtc

log = logging.getLogger(__name__)

# How long audio must keep passing through untouched to count as refused: the filter checks with
# LiveKit a few seconds in, and it may pass frames through while it starts.
REFUSED_AFTER_S = 10.0
# How long it must keep filtering to count as working: past that check, and past a passing blink.
WORKING_AFTER_S = 60.0

try:
    from livekit.plugins import noise_cancellation
except Exception:  # noqa: BLE001 — a missing or broken native library, on this computer only
    log.warning("voice: Krisp noise suppression isn't available here", exc_info=True)
    noise_cancellation = None

try:
    from livekit.plugins import krisp
except Exception:  # noqa: BLE001 — a missing or broken native library, on this computer only
    log.warning("voice: Krisp voice isolation isn't available here", exc_info=True)
    krisp = None


def on_livekit_cloud(livekit_url: str | None) -> bool:
    host = (urlsplit(livekit_url or "").hostname or "").lower()
    return host == "livekit.cloud" or host.endswith(".livekit.cloud")


def _standard() -> Any:
    return noise_cancellation.NC() if noise_cancellation is not None else None


class WatchedIsolation(rtc.FrameProcessor[rtc.AudioFrame]):
    """Voice isolation that says whether it's really working. Refused, the plugin only logs it and
    hands back the frame it was given, so the same frame coming back is the one sign there is. The
    call in progress can't change filter: the audio stream fixes that when it opens."""

    def __init__(self, inner: Any, note_working: Callable[[bool], None]) -> None:
        self._inner = inner
        self._note = note_working
        self._credentials = False
        self._passing_since: float | None = None
        self._filtering_since: float | None = None
        self._said: set[bool] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._inner.enabled)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._inner.enabled = value

    def _say_once(self, working: bool) -> None:
        if working in self._said:
            return
        self._said.add(working)
        try:
            self._note(working)
        except Exception:  # noqa: BLE001 — the audio matters more than the report
            log.info("voice: couldn't report how voice isolation is doing", exc_info=True)

    def _process(self, frame: rtc.AudioFrame) -> rtc.AudioFrame:
        processed = self._inner._process(frame)
        if not self._credentials:
            return processed
        now = time.monotonic()
        if processed is frame:
            self._filtering_since = None
            self._passing_since = self._passing_since or now
            if now - self._passing_since >= REFUSED_AFTER_S:
                self._say_once(False)
        else:
            self._passing_since = None
            self._filtering_since = self._filtering_since or now
            if now - self._filtering_since >= WORKING_AFTER_S:
                self._say_once(True)
        return processed

    def _on_credentials_updated(self, *, token: str, url: str) -> None:
        self._credentials = True
        self._inner._on_credentials_updated(token=token, url=url)

    def _on_credentials_cleared(self) -> None:
        self._credentials = False
        self._inner._on_credentials_cleared()

    def _on_stream_info_updated(
        self, *, room_name: str, participant_identity: str, publication_sid: str
    ) -> None:
        self._inner._on_stream_info_updated(
            room_name=room_name,
            participant_identity=participant_identity,
            publication_sid=publication_sid,
        )

    def _on_stream_info_cleared(self) -> None:
        self._inner._on_stream_info_cleared()

    def _close(self) -> None:
        self._inner._close()


def _isolation(note_working: Callable[[bool], None]) -> Any:
    if krisp is None:
        return _standard()
    # Named, not defaulted: left to itself the plugin also reads a licence from the environment,
    # and a connector reads none.
    inner = krisp.voice_isolation(auth_provider=krisp.auth.livekit_cloud())
    return WatchedIsolation(inner, note_working)


def call_filter(
    choice: str | None,
    livekit_url: str | None,
    note_working: Callable[[bool], None] = lambda _working: None,
) -> Any:
    """The filter for one call, or None for none. A voice isolation filter holds that call's
    session, so each call gets its own. Never raises."""
    if choice == "off" or not on_livekit_cloud(livekit_url):
        return None
    try:
        return _isolation(note_working) if choice == "isolation" else _standard()
    except Exception:  # noqa: BLE001 — a call is still a call without it
        log.warning(
            "voice: couldn't set up noise cancelling; this call goes without", exc_info=True
        )
        return None
