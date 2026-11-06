"""Shared untrusted-text sanitization + tokenization for LLM-facing prompt content.

``sanitize_untrusted_text`` NFKC-normalizes FIRST (security L1: collapses lookalike/compatibility
characters — e.g. fullwidth "＞＞＞" or "－－－" — to their ASCII forms BEFORE the collapse passes
below run on them, so a lookalike can't smuggle a 3+ run past the collapse only to normalize back
into a real delimiter downstream), then strips ASCII control chars, then collapses dash-runs (which
could otherwise forge this repo's "--- BEGIN/END ... ---" data-block delimiters, letting untrusted
text escape its frame — see analyst.py's prompt-injection hardening) AND ">"-runs (INT12:
analyst.py's material_news trigger marker, ">>> NEW (this alert's trigger, published ...):", is OUR
OWN rendered prefix — collapsing any 3+ run of ">" means untrusted evidence text can never forge
that exact 3-char sequence to impersonate/duplicate the marker). Used anywhere user-authored or
third-party text rides a prompt: analyst.py/analyst_prompt.py (news snippets, investor-profile
notes, prior-signal-summary delta context), news_screen.py (headlines), and topic_analyst.py
(watchlist topic notes).

``tokenize``/``content_tokens`` are the ONE shared normalized-token-set implementation for the
same-story/same-cluster overlap checks used by both news_screen.py (clustering + the
cluster-fingerprint cooldown) and analyst_prompt.py (trigger/background dedup) — previously each
module carried its own near-identical `_TOKEN_RE`/`_tokens` copy.
"""

from __future__ import annotations

import re
import unicodedata

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DASH_RE = re.compile(r"-{3,}")
_GT_RE = re.compile(r">{3,}")


def sanitize_untrusted_text(value: str | None, max_len: int) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _CTRL_RE.sub("", text)
    text = _DASH_RE.sub("-", text)
    text = _GT_RE.sub(">", text)
    return text[:max_len]


# Every text that reaches a phone goes through ``strip_unknown_links``, so no sender can forget a
# link form. A markdown link of ANY scheme keeps its text, a bare URL goes, and a bare domain is
# defused: Telegram links one and fetches it for a preview with no click, so
# ``acme-filings.com/r?v=<value>`` would otherwise be a leak.
_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(([^)]*)\)"
    r"|(?P<url>(?:[a-z][a-z0-9+.-]*://|www\.)[^\s)\]]+)"
    r"|(?P<host>\b\d{1,3}(?:\.\d{1,3}){3}\b|(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,})"
    r"(?P<path>[/?#:]\S+)?",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_EXCHANGE_TICKER_RE = re.compile(r"[A-Z0-9]{1,6}\.([A-Z]{1,3})")
_EXCHANGE_SUFFIXES = frozenset(
    "AS AX BO BR CO DE F HE HK KQ KS L LS MC MI MX NS NZ OL PA SA SI SS ST SW SZ T TO TW V VI WA".split()
)
_COMMON_TLDS = frozenset(
    "ai app au biz ca cc club cn co com de dev eu fr gg in info io jp link live me mov net news "
    "online org pro ru shop site store tech top tv uk us xyz zip".split()
)
_HANDLE_RE = re.compile(r"(?<![\w@.])@[A-Za-z_]\w*")
_BELOW_RE = re.compile(r"\s*<\s*(?=[\d$€£¥])")
_ABOVE_RE = re.compile(r"\s*>\s*(?=[\d$€£¥])")


def _without_markup(text: str) -> str:
    """NFKC first so a lookalike can't pass as ordinary text, and angle brackets go BEFORE the link
    checks: removed last, ``http<://evil<.com`` was rebuilt into a live link after they had run."""
    text = unicodedata.normalize("NFKC", text).replace("\u3002", ".")
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = _CTRL_RE.sub("", text).replace("<=", "≤").replace(">=", "≥")
    text = _BELOW_RE.sub(" below ", text)
    text = _ABOVE_RE.sub(" above ", text)
    return text.replace("<", "").replace(">", "")


def _defuse_host(host: str, path: str | None) -> str:
    """A ticker survives. A domain with a path is reduced to its name, dropping whatever the path
    carried. With no path, a familiar domain is reduced too (``reuters.com`` reads as ``reuters``),
    and anything else gets spaces, so a missed space in prose (``J.P.Morgan``, ``rose.Margins``)
    stays readable and no longer links."""
    if _IPV4_RE.fullmatch(host):
        return ""
    ticker = _EXCHANGE_TICKER_RE.fullmatch(host)
    if ticker and not path and ticker.group(1) in _EXCHANGE_SUFFIXES:
        return host
    labels = host.split(".")
    if path or labels[-1].lower() in _COMMON_TLDS:
        return labels[0]
    return ". ".join(labels)


def strip_unknown_links(message: str, allowed_urls: set[str]) -> str:
    """The message with every link that was not given to the model removed, and no markup. Links
    from the items' own server-built sources survive."""

    def _keep(match: re.Match) -> str:
        if match.group("host"):
            return _defuse_host(match.group("host"), match.group("path"))
        if match.group("url"):
            bare = match.group("url")
            return bare if bare.rstrip(".,;:!?") in allowed_urls else ""
        return match.group(0) if match.group(2).strip() in allowed_urls else match.group(1)

    return _HANDLE_RE.sub("", _LINK_RE.sub(_keep, _without_markup(message))).strip()


def plain_line(value: object, max_words: int) -> str:
    """One line of push text with no link, handle or emphasis, and a hard word limit."""
    text = strip_unknown_links(str(value or ""), set()).replace("*", "").replace("`", "")
    return " ".join(text.split()[:max_words])


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small, deliberately conservative stopword set for overlap comparisons (INT12 SF-2): filler
# words a headline's own grammar contributes regardless of what actually happened, so they
# shouldn't count toward "these are the same story." NOT a general-purpose NLP stopword list —
# just enough to stop trivial false-positives.
_OVERLAP_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "it",
        "its",
        "as",
        "by",
        "not",
        "no",
        "vs",
        "new",
        "says",
        "said",
        "say",
        "report",
        "reports",
        "reported",
        "news",
        "amid",
        "over",
        "into",
        "after",
        "before",
        "up",
        "down",
        "out",
    }
)


def tokenize(text: str) -> set[str]:
    """Lowercased alnum-run token set, no stemming — the raw building block for overlap checks."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def content_tokens(text: str, *, subject_name: str = "") -> set[str]:
    """``tokenize`` minus a small stopword set and the subject's own name tokens.

    Without this, two headlines that share only the subject's name + grammatical filler — e.g.
    "Apple Q3 miss" vs "Apple Q3 beat" — can hit a 0.6 overlap threshold on filler alone and be
    wrongly treated as the same story/cluster even though they report OPPOSITE developments.
    Accepted residual risk: this is a cheap heuristic, not semantic understanding — two SHORT
    headlines describing genuinely different specific developments can still occasionally share
    enough real content words to clear the threshold; the fix narrows the false-positive surface,
    it doesn't eliminate it.
    """
    toks = tokenize(text) - _OVERLAP_STOPWORDS
    if subject_name:
        toks -= tokenize(subject_name)
    return toks
