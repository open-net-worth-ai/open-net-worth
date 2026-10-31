"""Main-content extraction that CANNOT take the server down with it.

``trafilatura`` parses HTML through ``lxml``, which is a wrapper around ``libxml2`` — native
C, not Python. A memory fault there does not raise; the allocator calls ``abort()`` and the
whole interpreter dies mid-request. No ``except`` can intercept that, which is why the
caller's own ``except Exception:  # a parser bug must never break the fetch`` guard was
powerless against it:

    signal: SIGABRT · "Abort trap: 6"
      abort ← malloc_report ← find_zone_and_free
      lxml/etree ... _fixHtmlDictNames ← _parseUnicodeDoc ← _parseDoc

That is not a hypothetical failure mode. It killed the public demo twice on 2026-08-25 —
once at 01:32 and again at 11:37, both while researching an asset, both reached through a
DuckDuckGo result page. Each death was silent (a crashed process writes no shutdown log) and
lasted until a human noticed: ~11 hours between them.

So the parse runs in a CHILD process. If libxml2 aborts, it kills something disposable and
this returns ``None``; the caller falls back to the raw text exactly as it already does for
an unextractable page. The isolation is the point — a native crash is contained by process
boundaries or it is not contained at all.

The untrusted HTML crosses to the child as DATA on stdin, never as code and never through
argv or the environment: the child program is a fixed literal below.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys

_log = logging.getLogger(__name__)

# Whole-call ceiling. A parse that has not finished by now is not going to produce anything
# the caller still wants, and the caller's own fallback (raw text) is always available. Also
# bounds the OTHER native failure mode a subprocess contains but an exception handler does
# not: a C-level infinite loop, which no Python timeout could interrupt in-process.
_EXTRACT_TIMEOUT_S = 30

# The child program, as a literal. Reads {"html","url"} from stdin, writes {"text"} to
# stdout. Deliberately tiny: everything it needs arrives as data, so there is no format
# string, no argv interpolation, and nothing an attacker-supplied page can influence except
# the bytes being parsed.
_CHILD = r"""
import json, sys

payload = json.loads(sys.stdin.read())
try:
    import trafilatura

    text = trafilatura.extract(
        payload["html"],
        output_format="markdown",
        favor_recall=True,
        url=payload["url"] or None,
    )
except Exception:
    # A Python-level parser bug still degrades to "no extraction", same as before. The
    # native aborts this whole module exists for never reach here — they kill the process.
    text = None

sys.stdout.write(json.dumps({"text": text}))
"""


def extract_main_content(html: str, url: str, timeout_s: float = _EXTRACT_TIMEOUT_S) -> str | None:
    """Strip nav/ads/boilerplate down to the main content, in a child process.

    Returns the extracted markdown, or ``None`` for every failure — an unextractable page, a
    timeout, or the child dying outright. The caller treats all three the same way (fall back
    to the raw text), so there is nothing to gain from distinguishing them in the return
    value; the log line below is where the difference is recorded.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            input=json.dumps({"html": html, "url": url}),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        _log.warning("html extraction timed out after %ss (url=%s)", timeout_s, url)
        return None
    except Exception:  # noqa: BLE001 — spawning must never break the fetch either
        _log.warning("could not spawn the html extraction child (url=%s)", url, exc_info=True)
        return None

    if proc.returncode != 0:
        # A NEGATIVE returncode is a signal, and it is the case this module was built for:
        # -6 is SIGABRT, i.e. libxml2 aborted and would have taken the server with it.
        # Logged loudly because it is a real crash that just happened to be survivable.
        _log.warning(
            "html extraction child exited %s (url=%s) — falling back to raw text%s",
            proc.returncode,
            url,
            " [NATIVE CRASH — contained]" if proc.returncode < 0 else "",
        )
        return None

    try:
        text = json.loads(proc.stdout).get("text")
    except (ValueError, AttributeError):
        _log.warning("html extraction child returned unreadable output (url=%s)", url)
        return None

    return text if isinstance(text, str) else None


_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_META_ATTR = re.compile(r"""(property|name|content)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_IMAGE_KEYS = (
    "og:image",
    "og:image:secure_url",
    "og:image:url",
    "twitter:image",
    "twitter:image:src",
)


def extract_og_image(html: str) -> str | None:
    """The article's social-card image (og:image first, twitter:image as the fallback) as an
    ABSOLUTE http(s) URL, else None — for the Stories & Analysis cards (owner ask 2026-09-22:
    Perplexity-style cards need a picture; the page is already fetched for its text). Pure
    regex over the <meta> tags — no DOM, no network; a relative or data: URL is not usable."""
    if not html:
        return None
    found: dict[str, str] = {}
    for tag in _META_TAG.findall(html[:200_000]):
        attrs: dict[str, str] = {}
        for k, dq, sq in _META_ATTR.findall(tag):
            attrs[k.lower()] = (dq or sq or "").strip()
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _IMAGE_KEYS and attrs.get("content") and key not in found:
            found[key] = attrs["content"]
    for key in _IMAGE_KEYS:
        url = _html_unescape(found.get(key, ""))
        if url.startswith(("http://", "https://")) and not _looks_like_site_logo(url):
            return url
    return None


def _looks_like_site_logo(url: str) -> bool:
    """A page without a story picture falls back to the site's brand mark as og:image
    (Yahoo's yahoo-finance-default-logo.png, a site-logo.svg) — not a card image."""
    tail = url.rsplit("/", 1)[-1].split("?", 1)[0].lower()
    return "logo" in tail or "default" in tail or tail.endswith(".svg")


def _html_unescape(value: str) -> str:
    import html as _html

    return _html.unescape(value)
