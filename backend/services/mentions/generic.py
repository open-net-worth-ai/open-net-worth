"""Generic mention search + resolve derived from an EntityDescriptor.

RFC 2026-06-25-lucius-generic-entity-registry §5.

Pure functions; imports db / services / core only (same contract as resolve.py).
The shared row helpers are imported from _common so both the bespoke holdout
providers and this generic engine share one implementation.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.services.mentions._common import (
    _ROW_CAP,
    _matches,
    _row,
    _type_synonym_match,
)
from backend.services.mentions._corpus import corpus
from backend.services.mentions.descriptor import EntityDescriptor
from backend.services.mentions.types import SearchQuery

# The one query a generic ``rows`` is called with on the search path — it is the type's whole
# corpus, memoised across keystrokes (``_corpus``) and filtered here.
_WHOLE_CORPUS = SearchQuery(q="", types=None, surfaces=None, limit=_ROW_CAP, cursor=None)


def _sublabel(d: EntityDescriptor, row: dict) -> str:
    """Build the search-row sublabel for a descriptor row.

    Uses ``d.sublabel_fn`` when provided; otherwise joins the values of all
    ``in_sublabel`` fields, separated by ' • '.
    """
    if d.sublabel_fn is not None:
        return d.sublabel_fn(row)
    parts = [
        str(row.get(key) or "")
        for key, spec in d.fields.items()
        if spec.in_sublabel and row.get(key) is not None
    ]
    return " • ".join(p for p in parts if p)


def make_search(d: EntityDescriptor) -> Callable[[SearchQuery], list[dict]]:
    """Return a search callable derived from the descriptor.

    If ``d.search_override`` is set (multi-surface, singleton) it is returned
    directly.  Otherwise a generic loop is built: iterate ``d.rows(query)``,
    apply synonym + field matching, and cap at ``_ROW_CAP``.
    """
    if d.search_override:
        return d.search_override

    def _search(query: SearchQuery) -> list[dict]:
        q = (query.q or "").strip().lower()
        hint = _type_synonym_match(d.type, q) if q else None
        fields = d.search_fields or (d.label_field,)
        out: list[dict] = []
        source = corpus(d.rows, _WHOLE_CORPUS) if d.rows else []
        for row in source:
            if q and not hint and not _matches(q, *(str(row.get(f) or "") for f in fields)):
                continue
            id_val = str(row.get(d.id_field) or "")
            label = str(row.get(d.label_field) or id_val)
            out.append(_row(d.type, id_val, label, _sublabel(d, row), id_val, search=hint))
            if len(out) >= _ROW_CAP:
                break
        return out

    return _search


def _find_by_id(d: EntityDescriptor, ref: str) -> dict | None:
    """Linear scan of ``d.rows`` to find a row by ``d.id_field``.

    Only used when ``d.find_one`` is not set.  For service-hydrated types with a
    dedicated ``find_one`` (e.g. ``positions_service.hydrated_one``), that is
    preferred to avoid a full scan.
    """
    if d.rows is None:
        return None
    dummy_query = SearchQuery(q="", types=None, surfaces=None, limit=1000, cursor=None)
    for row in d.rows(dummy_query):
        if str(row.get(d.id_field) or "") == ref:
            return row
    return None


def _project(d: EntityDescriptor, row: dict) -> dict:
    """Project ``row`` through ``d.fields``: include only declared keys, omit sensitive ones.

    This is the security boundary for the model-bound resolve path: only keys that
    appear in ``d.fields`` with ``sensitive=False`` reach the output dict.  Sensitive
    fields are omitted AT SOURCE — never loaded into the returned dict.
    """
    out: dict = {}
    for key, spec in d.fields.items():
        if spec.sensitive:
            continue
        val = row.get(key)
        if val is not None:
            out[key] = val
    return out


def make_resolve(d: EntityDescriptor) -> Callable[[str], dict | None]:
    """Return a resolve callable derived from the descriptor.

    If ``d.resolve_override`` is set (singleton / multi-surface) it is returned
    directly.  Otherwise:
    - ``d.find_one(ref)`` locates the row (or ``_find_by_id`` falls back to a scan).
    - ``d.project_fn(row)`` runs if set (FX recompute / FK→name / brief splice).
    - Otherwise ``_project(d, row)`` projects through ``d.fields``.
    """
    if d.resolve_override:
        return d.resolve_override

    def _resolve(ref: str) -> dict | None:
        row = d.find_one(ref) if d.find_one else _find_by_id(d, ref)
        if row is None:
            return None
        if d.project_fn:
            return d.project_fn(row)
        return _project(d, row)

    return _resolve
