"""The icon the consent page shows for the app asking to connect, fetched from the app's own domain.

The host is the VERIFIED client host the consent request already carries (the CIMD document's
host, or an owner-made client's redirect host), never something the page sends, so an app can't
pick its picture from someone else's site. Our server fetches it, never the owner's browser:
through the SSRF-guarded, DNS-pinned fetch, no redirects, from the domain's own ``<link rel=icon>``
or ``/favicon.ico``. Only raster images pass, recognised by their bytes, not by what the server
says they are: an SVG can carry script, and this is served back from our own origin.
"""

from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

from backend.services.mcp_oauth.clients import user_agent

logger = logging.getLogger(__name__)

MAX_ICON_BYTES = 256 * 1024
MAX_PAGE_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 5
CACHE_S = 24 * 60 * 60
MISS_CACHE_S = 60 * 60

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_HOST = re.compile(r"^[a-z0-9.-]+(:[0-9]{1,5})?$")

_cache: dict[str, tuple[float, Optional[tuple[bytes, str]]]] = {}


def _sniff(data: bytes) -> Optional[str]:
    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _get(url: str, limit: int, accept: str) -> Optional[bytes]:
    from backend.core.pinned_http import pinned_urlopen

    try:
        resp = pinned_urlopen(
            url,
            headers={"Accept": accept, "User-Agent": user_agent()},
            timeout=FETCH_TIMEOUT_S,
            max_redirects=0,
        )
        with resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None)
            data = resp.read(limit + 1)
    except Exception as exc:  # noqa: BLE001 — a missing icon is the letter, never an error
        logger.info("mcp oauth: icon fetch failed for %s: %s", url, type(exc).__name__)
        return None
    if status != 200 or len(data) > limit:
        return None
    return data


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "")
        if not href or not ({"icon", "apple-touch-icon"} & set(rel)):
            return
        if "svg" in a.get("type", "").lower() or href.lower().split("?")[0].endswith(".svg"):
            return
        size = max((int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))), default=0)
        self.hrefs.append((size, href))


def _candidates(host: str) -> list[str]:
    """The domain's own declared raster icons, largest first, then ``/favicon.ico``. Only URLs on
    the same host: a CDN elsewhere would let the page name any picture on the internet."""
    base = f"https://{host}/"
    found: list[str] = []
    page = _get(base, MAX_PAGE_BYTES, "text/html")
    if page:
        parser = _IconLinks()
        try:
            parser.feed(page.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — a page we can't parse just has no declared icon
            parser.hrefs = []
        for _, href in sorted(parser.hrefs, key=lambda item: -item[0]):
            url = urljoin(base, href)
            parts = urlsplit(url)
            if parts.scheme == "https" and parts.netloc.lower() == host and url not in found:
                found.append(url)
    found.append(base + "favicon.ico")
    return found


def _fetch(host: str) -> Optional[tuple[bytes, str]]:
    for url in _candidates(host)[:4]:
        data = _get(url, MAX_ICON_BYTES, "image/png,image/x-icon,image/*;q=0.8")
        mime = _sniff(data) if data else None
        if data and mime:
            return data, mime
    return None


def icon_for_host(host: str, now: Optional[float] = None) -> Optional[tuple[bytes, str]]:
    """``(bytes, mime)`` for the host's icon, or ``None``. Cached a day (a miss for an hour)."""
    host = (host or "").strip().lower()
    if not _HOST.match(host):
        return None
    clock = time.monotonic() if now is None else now
    cached = _cache.get(host)
    if cached and clock - cached[0] < (CACHE_S if cached[1] else MISS_CACHE_S):
        return cached[1]
    result = _fetch(host)
    _cache[host] = (clock, result)
    return result


def reset_cache_for_tests() -> None:
    _cache.clear()
