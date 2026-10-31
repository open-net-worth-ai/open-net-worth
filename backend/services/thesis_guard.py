"""Thesis guard — advice about a ticker held to the user's investment thesis, after the model.

Each surface tells its model the thesis (``thesis_judgment.THESIS_RULE``); this makes the outcome
deterministic. Sell advice survives only when the price is at or below the stop, or when the advice
itself says the thesis broke ("thesis broken: …" — the model's reading of ``breaks_if`` against the
news, which no rule can check); trim advice survives at or above the trim level. A contradicting
line becomes the thesis's own advice, cited. With no thesis every function returns its input —
ADR ``2026-10-31-advice-on-a-ticker-is-judged-against-its-thesis``.
"""

from __future__ import annotations

import re

from backend.services.thesis_judgment import cite_line, thesis_action

_SELL_RE = re.compile(
    r"\b(sell|selling|sold off your|exit|exiting|get out|cut (?:your )?(?:losses|the position|"
    r"exposure)|cut it|close (?:out )?(?:the|your) position|dump|reduce (?:the |your )?"
    r"(?:position|exposure|stake)|trim(?!\s+level)|trimming|take (?:some )?profits?|lock in (?:gains|profits?)"
    r"|stop (?:out|loss)|bail)\b",
    re.IGNORECASE,
)
_TRIM_RE = re.compile(
    r"\b(trim(?!\s+level)|trimming|take (?:some )?profits?|lock in (?:gains|profits?))\b", re.I
)
_BROKEN_RE = re.compile(r"\bthesis\b[^.]{0,40}\b(broke|broken|breaks|break)\b", re.IGNORECASE)
_NEGATED_RE = re.compile(
    r"\b(?:no|not|never|don'?t|do not|no need to|nothing to|isn'?t|is not|without)\b[^.]{0,30}$",
    re.IGNORECASE,
)


def is_sell_advice(text: str) -> bool:
    """True when ``text`` advises selling/trimming — a negated mention ("no need to sell") is not."""
    for m in _SELL_RE.finditer(text or ""):
        if not _NEGATED_RE.search(text[: m.start()]):
            return True
    return False


def _allowed(text: str, j: dict) -> bool:
    """Sell advice is allowed past the stop, trim advice at the trim level, and either when the
    advice itself says the thesis broke (a ``breaks_if`` the model saw happen in the news)."""
    if not is_sell_advice(text) or j["sell_ok"] or _BROKEN_RE.search(text):
        return True
    return j["trim_ok"] and bool(_TRIM_RE.search(text)) and not _sell_only(text)


def _sell_only(text: str) -> bool:
    stripped = _TRIM_RE.sub("", text)
    return is_sell_advice(stripped)


def _cites(text: str) -> bool:
    return "thesis" in (text or "").lower()


def guard_actions(actions: list[str] | None, j: dict | None) -> list[str]:
    """``actions`` with sell advice the thesis does not support replaced by the thesis's own
    advice, and the thesis cited in the FIRST action (callers that keep one keep the citation).
    Unchanged with no thesis."""
    if not j:
        return list(actions or [])
    actions = [a for a in (actions or []) if isinstance(a, str) and a.strip()]
    if not actions:
        return []
    kept = [a for a in actions if _allowed(a, j)]
    if len(kept) < len(actions):
        return [thesis_action(j)]
    if any(_cites(a) for a in kept):
        return kept
    return [f"{kept[0].rstrip(' .')}. {cite_line(j)}", *kept[1:]]


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def guard_text(text: str, j: dict | None, *, cite: bool = True) -> str:
    """Prose with every sentence that gives unsupported sell advice dropped, and (``cite``) the
    thesis line appended when the prose does not already cite it. Unchanged with no thesis."""
    if not j or not text:
        return text
    out_lines = []
    for line in text.split("\n"):
        sentences = _SENTENCE_RE.split(line)
        kept = [s for s in sentences if _allowed(s, j)]
        if len(kept) == len(sentences):
            out_lines.append(line)
        elif kept:
            out_lines.append(" ".join(kept))
    out = "\n".join(out_lines).strip()
    if len(out) < len(text.strip()) and not _cites(out):
        out = f"{out}\n\n{thesis_action(j)}".strip()
    elif cite and not _cites(out):
        out = f"{out}\n\n{cite_line(j)}".strip()
    return out


def _mentions(text: str, ticker: str, name: str) -> bool:
    if re.search(rf"(?<![\w$]){re.escape(ticker)}(?!\w)", text):
        return True
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) is not None


def _blocking(text: str, judgments: dict[str, dict], names: dict[str, str]) -> dict | None:
    """The first named ticker whose thesis ``text``'s advice contradicts, or None."""
    for ticker, j in judgments.items():
        if _mentions(text, ticker, names.get(ticker, "")) and not _allowed(text, j):
            return j
    return None


def guard_book_actions(
    actions: list[str] | None, judgments: dict[str, dict], names: dict[str, str] | None = None
) -> list[str]:
    """``guard_actions`` for advice that may name several tickers (the daily briefing): an action
    contradicting a named ticker's thesis becomes that thesis's advice; an action naming a
    ticker with a thesis cites it. Unchanged with no theses."""
    if not judgments:
        return list(actions or [])
    names = names or {}
    out: list[str] = []
    for action in actions or []:
        blocked = _blocking(action, judgments, names)
        named = [j for t, j in judgments.items() if _mentions(action, t, names.get(t, ""))]
        if blocked:
            action = thesis_action(blocked)
        elif named and not _cites(action):
            action = f"{action.rstrip(' .')}. {cite_line(named[0])}"
        if action not in out:
            out.append(action)
    return out


def guard_book_text(
    text: str, judgments: dict[str, dict], names: dict[str, str] | None = None
) -> str:
    """Prose that may name several tickers, with every sentence whose sell advice contradicts a
    named ticker's thesis replaced by that thesis's advice. Unchanged with no theses."""
    if not judgments or not text:
        return text
    names = names or {}
    out_lines = []
    for line in text.split("\n"):
        sentences = []
        for sentence in _SENTENCE_RE.split(line):
            blocked = _blocking(sentence, judgments, names)
            sentences.append(thesis_action(blocked) if blocked else sentence)
        out_lines.append(" ".join(sentences))
    return "\n".join(out_lines)
