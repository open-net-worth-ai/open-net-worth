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
_MAX_LINE_CHARS = 1000
_MAX_PASSES = 4
_MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]]*)\]\(((?:[^()]|\([^()]*\))*)\)")
_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_BELOW_RE = re.compile(r"(?<!\s)\s{0,8}<\s{0,8}(?=[\w$€£¥])")
_ABOVE_RE = re.compile(r"(?<![\s=-])\s{0,8}>\s{0,8}(?=[\w$€£¥])")
_STAR_RE = re.compile(r"(?<!\d)\*|\*(?!\d)")
_UNDERSCORE_RE = re.compile(r"(?<![^\W_])_|_(?![^\W_])")
_HANDLE_RE = re.compile(r"(?<!@)@+[^\W\d]\w*")
_DOUBLE_DOLLAR_RE = re.compile(r"\${2,}")
_SPACES_RE = re.compile(r"[ \t]{2,}")
_TOKEN_SPLIT_RE = re.compile(r"(\s+)")
# A dot joining text to at least two more characters, starting with a letter in any script. A
# digit after the dot is a number (3.5) and one letter is an abbreviation (U.S., e.g., BRK.B).
_JOINING_DOT_RE = re.compile(r"[^\s.]\.[^\W\d_][^\s.]")
_SPLITTABLE_DOT_RE = re.compile(r"\.(?=[^\W\d_][^\s.])")
_PATH_CHAR_RE = re.compile(r"[/?#:=]")
_PATH_CHAR_THEN_TEXT_RE = re.compile(r"([/?#:=])(?=\S)")
# A real address only: 0-255, no leading zeros. Dot-grouped money (1.200.000.000) is not one.
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"(?<![\d.]){_OCTET}(?:\.{_OCTET}){{3}}(?![\d.])")
_EXCHANGE_TICKER_RE = re.compile(r"[A-Z0-9&-]{1,12}\.([A-Z]{1,3})")
_EXCHANGE_SUFFIXES = frozenset(
    "AS AX BK BO BR CO DE F HE HK HM IS JK JO KL KQ KS L LS MC MI MX NS NZ OL PA SA SI SS ST SW SZ "
    "T TA TO TW V VI VN WA".split()
)
_READS_AS_A_NAME_TLDS = frozenset({"com", "net", "org"})
_LEADING_PUNCTUATION = "([{\"'$"
_TRAILING_PUNCTUATION = ".,;:!?)]}\"'"
# Telegram deletes these before it looks for links, so a shape broken by one here is whole there.
_DELETED_BY_TELEGRAM = frozenset("  ‪‫‬‭‮̳̿̊")


def _visible_chars(text: str) -> str:
    """Without format characters, what Telegram deletes, or a combining mark on anything but a letter
    (``evil.́com``), which would hide the dot from every check below."""
    kept: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char in _DELETED_BY_TELEGRAM or category == "Cf":
            continue
        if category.startswith("M") and not (kept and kept[-1].isalpha()):
            continue
        kept.append(char)
    return "".join(kept)


def _without_markup(text: str, max_chars: int) -> str:
    """NFKC first so a lookalike can't pass as ordinary text, and brackets go BEFORE the link checks:
    removed last, ``http<://evil<.com`` was rebuilt into a live link after they had run."""
    text = unicodedata.normalize("NFKC", text[:max_chars])[:max_chars].replace("。", ".")
    text = _TAG_RE.sub("", _CTRL_RE.sub("", _visible_chars(text)).replace("\x00", ""))
    text = text.replace("<=", "≤").replace(">=", "≥").replace("->", "→").replace("=>", "⇒")
    text = _ABOVE_RE.sub(" above ", _BELOW_RE.sub(" below ", text))
    return text.replace("<", "").replace(">", "")


def _spaced(text: str) -> str:
    """No longer a link, and nothing deleted: prose with a missed space stays whole."""
    return _PATH_CHAR_THEN_TEXT_RE.sub(r"\1 ", _SPLITTABLE_DOT_RE.sub(". ", text))


def _defuse_token(token: str) -> str:
    """One whitespace-free token, with no way left to read as a link. A ticker survives. A token that
    starts with a domain and has a path is reduced to the domain's name, dropping what the path
    carried. A familiar domain alone reads as its name. Anything else joined by a dot gets spaces,
    because it is more often prose with a missed space (``J.P.Morgan``, ``股价下跌。分析师``), and text
    with no spaces in a language written without them must not lose everything after the first
    separator."""
    core = token.rstrip(_TRAILING_PUNCTUATION)
    tail = token[len(core) :]
    if "://" in core or core.lower().lstrip("([").startswith("www."):
        return tail
    address = _IPV4_RE.search(core)
    if address:
        return core[: address.start()] + tail
    if not _JOINING_DOT_RE.search(core):
        return token
    bare = core.lstrip(_LEADING_PUNCTUATION)
    lead = core[: len(core) - len(bare)]
    host, *path = _PATH_CHAR_RE.split(bare, maxsplit=1)
    ticker = _EXCHANGE_TICKER_RE.fullmatch(host)
    if ticker and ticker.group(1) in _EXCHANGE_SUFFIXES:
        return lead + host + (_spaced(bare[len(host) :]) + tail)
    if not _JOINING_DOT_RE.search(host):
        return lead + _spaced(bare) + tail
    labels = host.split(".")
    if path or labels[-1].lower() in _READS_AS_A_NAME_TLDS:
        return lead + labels[0].lstrip("/") + tail
    return lead + ". ".join(labels) + tail


def _one_pass(text: str, allowed_urls: set[str], hold) -> str:
    def _markdown(match: re.Match) -> str:
        """Only the given target is trusted: its text still goes through the cleaner, so a label
        can't show one address over a link to another."""
        if match.group(2) not in allowed_urls:
            return match.group(1)
        label = " ".join(strip_unknown_links(match.group(1), set()).split()) or "source"
        return hold(f"[{label}]({match.group(2)})")

    def _allowed_token(token: str) -> str:
        core = token.rstrip(_TRAILING_PUNCTUATION)
        return hold(core) + token[len(core) :] if core in allowed_urls else token

    text = _MARKDOWN_LINK_RE.sub(_markdown, text)
    tokens = _TOKEN_SPLIT_RE.split(text)
    tokens[::2] = [_allowed_token(token) for token in tokens[::2]]
    text = "".join(tokens)
    text = _STAR_RE.sub("", text).replace("`", "").replace("~~", "")
    text = _DOUBLE_DOLLAR_RE.sub("$", _UNDERSCORE_RE.sub("", text))
    tokens = _TOKEN_SPLIT_RE.split(text)
    tokens[::2] = [_defuse_token(token) for token in tokens[::2]]
    return _HANDLE_RE.sub("", "".join(tokens))


def strip_unknown_links(
    message: str, allowed_urls: set[str], *, max_chars: int = _MAX_INPUT_CHARS
) -> str:
    """The message with every link it was not given removed, and no markup or emphasis. It repeats
    until nothing changes, because removing one thing can reveal another. Links from the items' own
    server-built sources are held out of every pass, so they survive unaltered."""
    kept: list[str] = []

    def hold(link: str) -> str:
        kept.append(link)
        return f"\x00{len(kept) - 1}\x00"

    text = _without_markup(message, max_chars)
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
    """One line of push text with no link, handle or emphasis, and hard limits on words and length."""
    text = strip_unknown_links(str(value or ""), set(), max_chars=2 * _MAX_LINE_CHARS)
    return " ".join(text.split()[:max_words])[:_MAX_LINE_CHARS]


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
