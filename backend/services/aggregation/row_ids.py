"""When a bank's transaction id can be trusted to name one row (ADR 2026-11-13, Amendment 20).

A row with a bank id is keyed on it; one without is keyed on its content
(``providers.derived_ref``). Two ways an id lied, both seen in other open-source importers
(Actual Budget's per-bank fixes, Sure):

* **A placeholder.** Some banks fill the id with ``NOTPROVIDED`` or similar on every row. Keyed
  on it, the first row booked and every later one was refused as already booked. A placeholder
  is no id: the row is keyed on its content.
* **One id on different rows.** Some banks hand the same id to several transactions. Within one
  read, rows that share an id but differ in date, amount or wording are keyed on their content
  instead. Rows that share an id AND their content are the same row listed twice (a bank that
  repeats a page): one is kept.

Either way the row keeps the id ref as an alias, which ``connector_fetch._booked_under_alias``
honours only for a booked row of the same date and amount — so the one row an earlier version
booked under that id is still known, and no other row hides behind it. Every id-keyed row is
also known by its content hash when no other row in the read shares it, so a row keyed on its
content while its id was shared is still known once it is listed alone."""

from __future__ import annotations

import re

from backend.services.aggregation.providers import DERIVED_PREFIX

_PLACEHOLDERS = frozenset(
    {"NOTPROVIDED", "NOT PROVIDED", "NONREF", "NONE", "NULL", "N/A", "NA", "UNDEFINED", "UNKNOWN"}
)
_FILLER = re.compile(r"^[0\-\s.]*$")


def real_id(value: object) -> str:
    """The id, or "" when it is empty or a placeholder (``NOTPROVIDED``, all zeros …)."""
    text = str(value or "").strip()
    if not text or text.upper() in _PLACEHOLDERS or _FILLER.match(text):
        return ""
    return text


def _ref(row: dict) -> str:
    return str(row["external_refs"][0]["ref"])


def settle(rows: list[dict]) -> list[dict]:
    """One account's rows from one read, each carrying ``_content_hash`` (popped here): the rows
    to keep, with shared ids re-keyed on content and content-hash aliases added. Pending and
    booked rows are judged apart — a pending row and its posted self often share an id."""
    groups: dict[tuple[str, bool], list[dict]] = {}
    for row in rows:
        ref = _ref(row)
        if not ref.startswith(DERIVED_PREFIX):
            groups.setdefault((ref, bool(row.get("pending"))), []).append(row)
    dropped: set[int] = set()
    for (ref, _pending), group in groups.items():
        if len(group) < 2:
            continue
        distinct = len({r.get("_content_hash") or "" for r in group}) > 1
        seen: set[str] = set()
        for row in group:
            content = row.get("_content_hash") or ""
            if content in seen:
                dropped.add(id(row))  # the same row listed again
                continue
            seen.add(content)
            if distinct and content:
                row["external_refs"][0]["ref"] = content
                row["ref_aliases"] = [*row.get("ref_aliases", []), ref]
    kept = [r for r in rows if id(r) not in dropped]
    counts: dict[str, int] = {}
    for row in kept:
        content = row.get("_content_hash") or ""
        counts[content] = counts.get(content, 0) + 1
    for row in kept:
        content = row.pop("_content_hash", None)
        if content and content != _ref(row) and counts[content] == 1:
            row["ref_aliases"] = [*row.get("ref_aliases", []), content]
    for row in rows:
        row.pop("_content_hash", None)
    return kept
