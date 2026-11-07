"""Which Krisp filter cleans the caller's audio on a call: the Noise cancelling setting.

It runs here, on the audio the worker receives, so no browser or phone changes. Both filters need
LiveKit Cloud, and a filter is never worth a call: whatever goes wrong here, the call goes ahead
without it. LiveKit's plugins register only on the main thread, so they are imported with this
module, which the worker imports first (``worker.py``).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

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


def _isolation() -> Any:
    if krisp is None:
        return _standard()
    # Named, not defaulted: left to itself the plugin also reads a licence from the environment,
    # and a connector reads none. Past LiveKit's allowance it logs the refusal and passes the
    # audio through unfiltered, so the call goes on (krisp_internal's ``_process``).
    return krisp.voice_isolation(auth_provider=krisp.auth.livekit_cloud())


def call_filter(choice: str | None, livekit_url: str | None) -> Any:
    """The filter for one call, or None for none. A voice isolation filter holds that call's
    session, so each call gets its own. Never raises."""
    if choice == "off" or not on_livekit_cloud(livekit_url):
        return None
    try:
        return _isolation() if choice == "isolation" else _standard()
    except Exception:  # noqa: BLE001 — a call is still a call without it
        log.warning(
            "voice: couldn't set up noise cancelling; this call goes without", exc_info=True
        )
        return None
