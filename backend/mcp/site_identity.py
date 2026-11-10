"""A remote MCP server's display name and icon, read from what the server and its website say
about themselves.

Most servers answer the handshake with a bare machine name (IBKR's is ``ibkr-cpapi-mcp``) and
no icon, so a user-added server otherwise reads as its URL host beside a generic glyph. The
sources, in order:

* the handshake's own ``serverInfo.title`` / ``serverInfo.icons`` (MCP 2025-06-18 / 2025-11-25),
* the website of the endpoint's host, then of each parent domain down to the registrable one
  (``api.ibkr.com`` → ``ibkr.com``): ``og:site_name`` / ``application-name`` / ``<title>``, and
  the page's declared ``<link rel=icon>``,
* ``/favicon.ico`` on those same hosts.

Everything here is a CLAIM the site makes about itself, never a verification — the trust badge
is untouched (only the curated catalog sets it) and the caller records which host it came from
(``source``), so the trust panel can say so. Redirects are followed, each hop SSRF-checked and
DNS-pinned (``pinned_urlopen``, never ``owner_configured``): a homepage that redirects elsewhere
(``ibkr.com`` → ``interactivebrokers.com``) yields that site's identity, attributed to it.

Icons are raster only (PNG/JPEG/GIF/ICO/WEBP), typed by their MAGIC BYTES rather than a
server-chosen ``Content-Type``, and size-capped: an SVG can carry script, and the icon is
served back from our own origin. Never raises — a site that can't be read yields nothing.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Optional

from backend.core.pinned_http import pinned_urlopen
from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

BUDGET_S = 8.0
PER_REQUEST_S = 4.0
MAX_PAGE_BYTES = 512 * 1024
MAX_ICON_BYTES = 128 * 1024
MAX_NAME_CHARS = 60
_HEADERS = {"User-Agent": "OpenNetWorth/1.0 (+site identity)", "Accept-Language": "en"}

# ``co.uk``-shaped public suffixes: a two-letter TLD under a short second level. A heuristic,
# not the public-suffix list — it only stops the parent walk from reaching ``co.uk`` itself.
_SHORT_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne"})
_SEPARATORS = re.compile(r"\s+[|\-–—·•:]\s+")
_GENERIC_SEGMENTS = frozenset(
    {"home", "homepage", "welcome", "official site", "official website", "login", "sign in"}
)
_CORPORATE_SUFFIX = re.compile(r",?\s+(llc|inc\.?|ltd\.?|limited|plc|gmbh|corp\.?|co\.)$", re.I)


@dataclass(frozen=True)
class SiteIdentity:
    name: Optional[str] = None
    icon: Optional[bytes] = None
    icon_type: Optional[str] = None
    # The host the name/icon came from (after redirects) — shown as "from <source>".
    source: Optional[str] = None


def lookup(
    endpoint_url: str,
    *,
    server_title: Optional[str] = None,
    server_icons: Optional[list[str]] = None,
    want_icon: bool = True,
) -> SiteIdentity:
    """The best name + icon for ``endpoint_url``; an empty ``SiteIdentity`` when nothing reads."""
    deadline = time.monotonic() + BUDGET_S
    name = clean_name(server_title) if server_title else None
    source = None
    icon: Optional[tuple[bytes, str]] = None
    if want_icon:
        icon = _first_icon(server_icons or [], deadline)

    hosts = candidate_hosts(endpoint_url)
    page_icons: list[str] = []
    for host in hosts:
        if name and (icon or not want_icon):
            break
        page = _read_page(f"https://{host}/", deadline)
        if page is None:
            continue
        final_url, meta = page
        if not name and meta.name:
            name, source = meta.name, urllib.parse.urlsplit(final_url).hostname
        page_icons.extend(urllib.parse.urljoin(final_url, h) for h in meta.icons)
    if want_icon and icon is None:
        icon = _first_icon(page_icons, deadline)
        if icon is None:
            icon = _first_icon([f"https://{h}/favicon.ico" for h in hosts], deadline)
        if icon is not None and source is None:
            source = hosts[0]  # a website icon with no website name: the endpoint's own host
    return SiteIdentity(
        name=name,
        icon=icon[0] if icon else None,
        icon_type=icon[1] if icon else None,
        source=source,
    )


def candidate_hosts(endpoint_url: str) -> list[str]:
    """The endpoint's host, then each parent down to the registrable domain. Empty for an IP
    literal, a single-label host or ``localhost`` — a local server has no public website."""
    host = (urllib.parse.urlsplit(endpoint_url.strip()).hostname or "").lower().rstrip(".")
    if not host or "." not in host or host == "localhost" or host.endswith(".localhost"):
        return []
    try:
        ipaddress.ip_address(host)
        return []
    except ValueError:
        pass
    labels = host.split(".")
    floor = 3 if len(labels[-1]) == 2 and labels[-2] in _SHORT_SECOND_LEVEL else 2
    hosts = [".".join(labels[i:]) for i in range(0, max(1, len(labels) - floor + 1))]
    if hosts and hosts[0].startswith("www."):
        hosts = hosts[1:] or hosts
    return list(dict.fromkeys(hosts))


def clean_name(raw: Any) -> Optional[str]:
    """A site/page title reduced to the brand: ``Home | Interactive Brokers LLC`` →
    ``Interactive Brokers``. Sanitized and capped; ``None`` when nothing usable is left."""
    if not isinstance(raw, str):
        return None
    text = sanitize_untrusted(" ".join(raw.split()), max_len=300).strip()
    segments = [s.strip() for s in _SEPARATORS.split(text) if s.strip()]
    useful = [s for s in segments if s.lower() not in _GENERIC_SEGMENTS]
    if not useful:
        return None
    brand = _CORPORATE_SUFFIX.sub("", useful[0]).strip()
    return brand[:MAX_NAME_CHARS] or None


def sniff_image_type(data: bytes) -> Optional[str]:
    """The image type from its magic bytes — never from a server-chosen header. ``None`` for
    anything else, SVG included."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class _PageMeta:
    name: Optional[str]
    icons: list[str]


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.icons: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:site_name", "application-name") and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "link" and a.get("href"):
            rels = set(a.get("rel", "").lower().split())
            if rels & {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}:
                if "svg" not in a.get("type", "").lower():
                    self.icons.append((_icon_rank(a.get("sizes", ""), rels), a["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and len(self.title) < 300:
            self.title += data


def _icon_rank(sizes: str, rels: set[str]) -> int:
    """Higher is better: the largest declared size up to 256px; apple-touch icons are 180px
    when undeclared, a bare ``icon`` is assumed 16px."""
    best = 0
    for token in sizes.lower().split():
        w, _, _h = token.partition("x")
        if w.isdigit():
            best = max(best, int(w))
    if not best:
        best = 180 if rels & {"apple-touch-icon", "apple-touch-icon-precomposed"} else 16
    return best if best <= 256 else 256 - best // 100


def _remaining(deadline: float) -> float:
    return min(PER_REQUEST_S, deadline - time.monotonic())


def _fetch(url: str, deadline: float, limit: int) -> Optional[tuple[str, str, bytes]]:
    """``(final_url, content_type, body)`` for a 200, else ``None``. Never raises."""
    timeout = _remaining(deadline)
    if timeout <= 0.2 or urllib.parse.urlsplit(url).scheme != "https":
        return None
    try:
        with pinned_urlopen(url, headers=_HEADERS, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            body = resp.read(limit + 1)
            if len(body) > limit:
                return None
            return resp.geturl(), resp.headers.get("Content-Type", ""), body
    except Exception as exc:  # noqa: BLE001 — a site we can't read simply has no identity
        logger.debug("site identity: %s unreadable (%s)", url, type(exc).__name__)
        return None


def _read_page(url: str, deadline: float) -> Optional[tuple[str, _PageMeta]]:
    got = _fetch(url, deadline, MAX_PAGE_BYTES)
    if got is None or "html" not in got[1].lower():
        return None
    final_url, _ctype, body = got
    parser = _HeadParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — malformed markup yields whatever was parsed so far
        pass
    name = clean_name(parser.meta.get("og:site_name")) or clean_name(
        parser.meta.get("application-name")
    )
    name = name or clean_name(parser.title)
    icons = [href for _rank, href in sorted(parser.icons, key=lambda p: -p[0])]
    return final_url, _PageMeta(name=name, icons=icons[:3])


def _first_icon(urls: list[str], deadline: float) -> Optional[tuple[bytes, str]]:
    for url in urls[:4]:
        data = _data_uri_bytes(url) if url.startswith("data:") else None
        if data is None:
            got = _fetch(url, deadline, MAX_ICON_BYTES)
            data = got[2] if got else None
        kind = sniff_image_type(data) if data else None
        if kind and data and len(data) <= MAX_ICON_BYTES:
            return data, kind
    return None


def _data_uri_bytes(uri: str) -> Optional[bytes]:
    head, sep, payload = uri.partition(",")
    if not sep or ";base64" not in head or len(payload) > MAX_ICON_BYTES * 2:
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError:
        return None
