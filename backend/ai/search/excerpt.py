"""Query-focused excerpt of a fetched page — the passages that best match the search terms.

Deterministic and model-free: split the page into passages, keep only real prose (a nav bar, a
link list or a script blob is not), score each by the query terms it contains — a term found in
few passages counts for more than one found everywhere — then pack the best passages, in page
order, into a character budget. A page that shares no term with the query yields NO excerpt
rather than its opening lines, so a bot-wall or a boilerplate page never poses as an answer.
"""

from __future__ import annotations

import math
import re

# The most a single page may contribute to a search result.
EXCERPT_MAX_CHARS = 2_500
# A long paragraph is cut at sentence boundaries into windows of at most this size, so one
# matching sentence doesn't drag a whole wall of text into the excerpt.
_PASSAGE_MAX_CHARS = 700
_PASSAGE_MIN_WORDS = 5
_MIN_PROSE_RATIO = 0.6
_MAX_STRUCTURAL_RATIO = 0.08
# A model- or user-written query can be a paragraph; scoring cost grows with terms x passages.
MAX_QUERY_TERMS = 32
_STRUCTURAL_CHARS = frozenset("<>{}[]();=/|\\")
_ELLIPSIS = " […] "

_WORD_RE = re.compile(r"\w+")
_BLOCK_SPLIT_RE = re.compile(r"\n+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_END_RE = re.compile(r"[.!?](?:[\"')\]]*\s|[\"')\]]*$)")

_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it its of on or the this that "
    "to was were what when where which who why will with about latest new news current today".split()
)


def _stem(word: str) -> str:
    """Crude plural fold so 'earnings'/'earning' and 'rates'/'rate' meet. Not linguistics."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def query_terms(query: str) -> list[str]:
    """The distinct, ordered, stemmed content words of ``query`` (stopwords dropped unless the
    query is nothing but stopwords), the first ``MAX_QUERY_TERMS`` of them."""
    words = [w for w in _WORD_RE.findall(query.lower()) if len(w) > 1 or w.isdigit()]
    kept = [w for w in words if w not in _STOPWORDS] or words
    return list(dict.fromkeys(_stem(w) for w in kept))[:MAX_QUERY_TERMS]


def _windows(block: str) -> list[str]:
    """``block`` as-is when short, else packed sentence windows of at most _PASSAGE_MAX_CHARS."""
    if len(block) <= _PASSAGE_MAX_CHARS:
        return [block]
    windows: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT_RE.split(block):
        sentence = sentence[:_PASSAGE_MAX_CHARS]
        if current and len(current) + 1 + len(sentence) > _PASSAGE_MAX_CHARS:
            windows.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        windows.append(current)
    return windows


def _is_prose(passage: str) -> bool:
    """True for sentence-like text: enough words, mostly letters, little markup punctuation,
    and either a sentence ending or a long run of non-Title-Case words (a link list or menu
    has neither)."""
    words = passage.split()
    if len(words) < _PASSAGE_MIN_WORDS:
        return False
    sample = passage[:_PASSAGE_MAX_CHARS]
    if sum(1 for ch in sample if ch.isalpha() or ch.isspace()) / len(sample) < _MIN_PROSE_RATIO:
        return False
    if sum(1 for ch in sample if ch in _STRUCTURAL_CHARS) / len(sample) > _MAX_STRUCTURAL_RATIO:
        return False
    if _SENTENCE_END_RE.search(passage):
        return True
    title_case = sum(1 for w in words if w[:1].isupper())
    return len(words) >= 15 and title_case / len(words) < 0.5


def _passages(text: str) -> list[tuple[int, str]]:
    """``(position, passage)`` for every distinct prose passage of ``text``, in page order."""
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for block in _BLOCK_SPLIT_RE.split(text):
        block = " ".join(block.split())
        for passage in _windows(block) if block else []:
            key = passage.lower()
            if key in seen or not _is_prose(passage):
                continue
            seen.add(key)
            out.append((len(out), passage))
    return out


def build_excerpt(page_text: str, query: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """The passages of ``page_text`` that best answer ``query``, in page order, joined by
    ``[…]`` where the page continues between them; at most ``max_chars`` long. Empty when no
    prose passage contains any query term."""
    terms = query_terms(query)
    passages = _passages(page_text)
    if not terms or not passages:
        return ""

    stems = [{_stem(w) for w in _WORD_RE.findall(p.lower())} for _, p in passages]
    df = {t: sum(1 for s in stems if t in s) for t in terms}
    weight = {t: math.log(1 + len(passages) / n) for t, n in df.items() if n}
    scored = [
        (sum(weight[t] for t in terms if t in s), pos, p) for (pos, p), s in zip(passages, stems)
    ]
    ranked = sorted((r for r in scored if r[0] > 0), key=lambda r: (-r[0], r[1]))

    # Every joint is charged the (longer) ellipsis, so the packed text can never exceed the cap.
    chosen: list[tuple[int, str]] = []
    used = 0
    for _, pos, passage in ranked:
        cost = len(passage) + (len(_ELLIPSIS) if chosen else 0)
        if used + cost <= max_chars:
            chosen.append((pos, passage))
            used += cost
    chosen.sort()

    parts: list[str] = []
    previous = -2
    for pos, passage in chosen:
        if parts:
            parts.append(" " if pos == previous + 1 else _ELLIPSIS)
        parts.append(passage)
        previous = pos
    return "".join(parts)
