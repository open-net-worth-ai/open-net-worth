"""Shared helpers for mention search providers.

Lifted verbatim from providers.py so both the bespoke holdout providers and
the generic engine share one implementation.  providers.py re-imports these.
"""

from __future__ import annotations

import re

from backend.services.mentions.types import ICONS

_ROW_CAP = 25

_TYPE_SYNONYMS: dict[str, list[str]] = {
    "account": ["account", "accounts"],
    "holding": [
        "holding",
        "holdings",
        "stock",
        "stocks",
        "position",
        "positions",
        "investment",
        "investments",
        "equity",
        "equities",
        "asset",
        "assets",
    ],
    "transaction": [
        "transaction",
        "transactions",
        "txn",
        "txns",
        "payment",
        "payments",
        "spending",
        "spend",
        "expense",
        "expenses",
        "charge",
        "charges",
        "activity",
    ],
    "category": ["category", "categories"],
    "envelope": ["envelope", "envelopes", "pot", "pots"],
    "budget_month": ["budget", "budgets"],
    "payee": ["payee", "payees", "merchant", "merchants", "vendor", "vendors"],
    "automation": [
        "automation",
        "automations",
        "rule",
        "rules",
        "scheduled",
        "recurring",
        "upcoming",
        "subscription",
        "subscriptions",
    ],
    "watchlist": ["watchlist", "watchlists", "watch"],
    "skill": ["skill", "skills", "command", "commands", "action", "actions"],
    "net_worth": ["net", "networth", "worth"],
    "returns": ["return", "returns", "performance"],
    "target": ["target", "targets", "goal", "goals"],
    "allocation_slice": ["allocation", "allocations", "slice", "slices"],
    "instrument": ["instrument", "instruments", "ticker", "tickers", "security", "securities"],
    # "@doc" / "@files" lists what Documents holds (owner: "to mention, we have to search by
    # name, right?").
    "document": ["document", "documents", "docs", "file", "files"],
}

_RE_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm_q(s: str) -> str:
    """Normalize a query to lowercase alphanumeric for synonym matching."""
    return _RE_NON_ALNUM.sub("", s.lower())


_PLURAL_AS_IS = frozenset({"news", "returns"})


def _plural(word: str) -> str:
    if word in _PLURAL_AS_IS:
        return word
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


def _type_names(mention_type: str) -> list[str]:
    """Every word ``mention_type`` answers to: its own name and its registered label, each
    singular and plural, then the hand-kept aliases. Derived, so a newly registered type is
    searchable by name without anyone remembering to list it ("@connector" found nothing
    while 19 types were missing from ``_TYPE_SYNONYMS``)."""
    from backend.services.mentions.registry import DESCRIPTORS

    d = DESCRIPTORS.get(mention_type)
    # Only a registered type answers to its own name — a retired one ("agent") stays silent.
    own = {_norm_q(mention_type), _norm_q(d.label)} - {""} if d else set()
    names = [n for word in sorted(own) for n in (word, _plural(word))]
    return names + _TYPE_SYNONYMS.get(mention_type, [])


def _type_synonym_match(mention_type: str, q: str) -> str | None:
    """Return a hint string if ``q`` (>=3 chars, normalized) equals or is a prefix of any
    name of ``mention_type`` (see ``_type_names``).  Returns ``None`` if none matches."""
    if len(q) < 3:
        return None
    nq = _norm_q(q)
    if len(nq) < 3:
        return None
    for s in _type_names(mention_type):
        if s == nq or s.startswith(nq):
            return s
    return None


def _row(
    type_: str,
    id_: str,
    label: str,
    sublabel: str,
    value: str,
    surface: str = "entity",
    search: str | None = None,
    color: str | None = None,
) -> dict:
    """A normalized mention row.  ``color`` is the entity's own display color (an account's
    user-chosen tint, an envelope/category dot) — optional, client-validated before render."""
    row: dict = {
        "type": type_,
        "id": id_,
        "label": label,
        "sublabel": sublabel,
        "value": value,
        "icon": ICONS.get(type_, "◆"),
        "surface": surface,
    }
    if search is not None:
        row["search"] = search
    if color:
        row["color"] = color
    return row


def _matches(needle: str, *fields: str) -> bool:
    """True if ``needle`` appears in any field, tolerant of punctuation/whitespace."""
    nn = _norm_q(needle)
    return any(needle in (f or "").lower() or (nn != "" and nn in _norm_q(f or "")) for f in fields)
