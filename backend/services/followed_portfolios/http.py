"""Outbound fetches for the public filing sources — SSRF-guarded, size-capped, typed errors.

Every URL here is one of three fixed hosts (SEC EDGAR, the House Clerk, OpenFIGI); the path is
built from validated digits or ids, never from free text.
"""

from __future__ import annotations

import json
import logging
import urllib.error

from backend.core.errors import ExternalServiceError
from backend.core.pinned_http import pinned_urlopen

logger = logging.getLogger(__name__)

# SEC's fair-use policy asks for a contact in the User-Agent; the Clerk accepts the same.
USER_AGENT = "OpenNetWorth/1.0 (contact: opennetworth-app@proton.me)"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20


def fetch_bytes(
    url: str,
    *,
    what: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    data: bytes | None = None,
    content_type: str | None = None,
) -> bytes:
    """GET (or POST ``data``) ``url`` and return the body. ``what`` names the source in the
    error a person reads. A body over ``max_bytes`` is refused, never cut."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        resp = pinned_urlopen(url, headers=headers, data=data, timeout=TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("followed portfolios: %s unreachable: %s", what, type(exc).__name__)
        raise ExternalServiceError(f"Could not reach {what}. Try again later.") from exc
    with resp:
        if isinstance(resp, urllib.error.HTTPError) or getattr(resp, "status", 200) >= 400:
            status = getattr(resp, "code", None) or getattr(resp, "status", "?")
            raise ExternalServiceError(f"{what} answered with an error ({status}).")
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ExternalServiceError(f"{what} sent more data than expected.")
    return body


def fetch_json(url: str, *, what: str, **kwargs):
    body = fetch_bytes(url, what=what, **kwargs)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise ExternalServiceError(f"{what} sent a reply that could not be read.") from exc
