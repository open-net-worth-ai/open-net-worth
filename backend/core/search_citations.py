"""What the search tools actually returned during one bounded run, so its message can cite them.

A price alert's search turn ends in text, and a link the model writes there could have been copied
out of a hostile page. So a citation is accepted only if a search tool returned that exact URL
during the run: ``collecting()`` opens a collector on this context, the search tools ``record``
into it, and ``cited`` keeps the URLs the model named that are also in it.

Leaf module: ``ai`` (the tools) and ``monitoring`` (the run) meet here without importing each other.
"""

from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar
from typing import Iterator
from urllib.parse import urlsplit

_collector: ContextVar[dict[str, dict] | None] = ContextVar("search_citations", default=None)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+")
_TRAILING = ".,;:!?"


@contextlib.contextmanager
def collecting() -> Iterator[dict[str, dict]]:
    """A fresh collector for the calls made inside the block. Tool calls dispatched from it, even
    on a task it spawns, add to the same dict (a copied context shares the object)."""
    seen: dict[str, dict] = {}
    token = _collector.set(seen)
    try:
        yield seen
    finally:
        _collector.reset(token)


def record(results: list[dict]) -> None:
    """Called by a search tool with its sanitized results. A no-op outside ``collecting()``."""
    seen = _collector.get()
    if seen is None:
        return
    for item in results or []:
        url = str((item or {}).get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")) and url not in seen:
            seen[url] = {"url": url, "title": str(item.get("title") or "").strip()}


def outlet_of(url: str) -> str:
    """The site a URL belongs to, as a person would name it: the host without ``www.``."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def cited(text: str, seen: dict[str, dict], limit: int = 3) -> list[dict]:
    """The URLs in ``text`` that a search tool really returned, in the order named, as
    ``{url, title, domain, outlet}``. Anything else in the text is dropped."""
    out: list[dict] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING)
        hit = seen.get(url)
        if hit is None or any(c["url"] == url for c in out):
            continue
        host = outlet_of(url)
        out.append({"url": url, "title": hit["title"][:200], "domain": host, "outlet": host})
        if len(out) >= limit:
            break
    return out


def without_urls(text: str) -> str:
    """``text`` with its URLs and a trailing ``Sources:`` line taken out, for the prompt."""
    body = re.split(r"\n\s*Sources?:", text or "", maxsplit=1, flags=re.IGNORECASE)[0]
    flat = " ".join(_URL_RE.sub("", body).replace("()", "").replace("[]", "").split())
    return re.sub(r"\s+([.,;:!?])", r"\1", flat)
