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


# The cleaner for alert text and delivery decider messages. It works token by token in linear time,
# because a blocklist of link shapes kept missing forms and its regexes went quadratic on one long
# word, freezing the event loop. Telegram links a bare domain and can fetch it for a preview with no
# click, so ``acme-filings.com/r?v=<value>`` would be a leak of whatever the model put in it.
_MAX_INPUT_CHARS = 8000
_MAX_PASSES = 4
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_BELOW_RE = re.compile(r"\s*<\s*(?=[\w$€£¥])")
_ABOVE_RE = re.compile(r"\s*>\s*(?=[\w$€£¥])")
_STAR_RE = re.compile(r"(?<!\d)\*|\*(?!\d)")
_UNDERSCORE_RE = re.compile(r"(?<![^\W_])_|_(?![^\W_])")
_HANDLE_RE = re.compile(r"@+[^\W\d]\w*")
_DOUBLE_DOLLAR_RE = re.compile(r"\${2,}")
_SPACES_RE = re.compile(r"[ \t]{2,}")
_TOKEN_SPLIT_RE = re.compile(r"(\s+)")
# A dot joining text to at least two more characters, starting with a letter in any script. A
# digit after the dot is a number (3.5) and one letter is an abbreviation (U.S., e.g., BRK.B).
_JOINING_DOT_RE = re.compile(r"[^\s.]\.[^\W\d_][^\s.]")
_IPV4_RE = re.compile(r"\W*\d{1,3}(?:\.\d{1,3}){3}")
_PATH_RE = re.compile(r"[/?#:=&]")
_EXCHANGE_TICKER_RE = re.compile(r"[A-Z0-9]{1,10}\.([A-Z]{1,3})")
_EXCHANGE_SUFFIXES = frozenset(
    "AS AX BO BR CO DE F HE HK IS JK JO KQ KS L LS MC MI MX NS NZ OL PA SA SI SS ST SW SZ T TO TW "
    "V VI VN WA".split()
)
_READS_AS_A_NAME_TLDS = frozenset({"com", "net", "org"})
_TRAILING_PUNCTUATION = ".,;:!?)]\"'"


def _without_markup(text: str) -> str:
    """NFKC first so a lookalike can't pass as ordinary text, and brackets go BEFORE the link checks:
    removed last, ``http<://evil<.com`` was rebuilt into a live link after they had run."""
    text = unicodedata.normalize("NFKC", text[:_MAX_INPUT_CHARS]).replace("\u3002", ".")
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = _TAG_RE.sub("", _CTRL_RE.sub("", text).replace("\x00", ""))
    text = text.replace("<=", "≤").replace(">=", "≥")
    text = _ABOVE_RE.sub(" above ", _BELOW_RE.sub(" below ", text))
    return text.replace("<", "").replace(">", "")


def _defuse_token(token: str) -> str:
    """One whitespace-free token, with no way left to read as a link. A ticker survives. A domain
    with a path is reduced to its name, dropping whatever the path carried. With no path, a familiar
    domain reads as its name, and anything else gets spaces, so a missed space in prose
    (``J.P.Morgan``, ``rose.Margins``) stays readable and no longer links."""
    core = token.rstrip(_TRAILING_PUNCTUATION)
    tail = token[len(core) :]
    if "://" in core or core.lower().lstrip("([").startswith("www."):
        return tail
    if _IPV4_RE.match(core):
        return tail
    ticker = _EXCHANGE_TICKER_RE.fullmatch(core)
    if (ticker and ticker.group(1) in _EXCHANGE_SUFFIXES) or not _JOINING_DOT_RE.search(core):
        return token
    host, *path = _PATH_RE.split(core, maxsplit=1)
    labels = host.split(".")
    if path or labels[-1].lower() in _READS_AS_A_NAME_TLDS:
        return labels[0].lstrip("/") + tail
    return ". ".join(labels) + tail


def _one_pass(text: str, allowed_urls: set[str], hold) -> str:
    def _markdown(match: re.Match) -> str:
        return hold(match.group(0)) if match.group(2).strip() in allowed_urls else match.group(1)

    def _allowed_token(token: str) -> str:
        core = token.rstrip(_TRAILING_PUNCTUATION)
        return hold(core) + token[len(core) :] if core in allowed_urls else token

    text = _MARKDOWN_LINK_RE.sub(_markdown, text)
    tokens = _TOKEN_SPLIT_RE.split(text)
    tokens[::2] = [_allowed_token(token) for token in tokens[::2]]
    text = "".join(tokens)
    text = _STAR_RE.sub("", text).replace("`", "").replace("~", "")
    text = _DOUBLE_DOLLAR_RE.sub("$", _UNDERSCORE_RE.sub("", text))
    tokens = _TOKEN_SPLIT_RE.split(text)
    tokens[::2] = [_defuse_token(token) for token in tokens[::2]]
    return _HANDLE_RE.sub("", "".join(tokens))


def strip_unknown_links(message: str, allowed_urls: set[str]) -> str:
    """The message with every link it was not given removed, and no markup or emphasis. It repeats
    until nothing changes, because removing one thing can reveal another. Links from the items' own
    server-built sources are held out of every pass, so they survive unaltered."""
    kept: list[str] = []

    def hold(link: str) -> str:
        kept.append(link)
        return f"\x00{len(kept) - 1}\x00"

    text = _without_markup(message)
    for _ in range(_MAX_PASSES):
        cleaned = _one_pass(text, allowed_urls, hold)
        if cleaned == text:
            break
        text = cleaned
    text = _SPACES_RE.sub(" ", text).strip()
    for index, link in enumerate(kept):
        text = text.replace(f"\x00{index}\x00", link)
    return text


def plain_line(value: object, max_words: int) -> str:
    """One line of push text with no link, handle or emphasis, and a hard word limit."""
    return " ".join(strip_unknown_links(str(value or ""), set()).split()[:max_words])


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
