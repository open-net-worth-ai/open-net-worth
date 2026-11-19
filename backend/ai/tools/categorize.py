"""Learned category-suggestion tool (competitor gap #1, L1).

Gives Lucius (and the seeded Bookkeeper agent) a READ over the learned per-row category
suggester (``services/categorize.py``): for each UNCATEGORIZED expense/income transaction
it returns a confidence-ranked suggested category drawn from the user's OWN history of
that payee (corrections + already-categorized rows). It NEVER writes — applying a
suggestion reuses the existing ``bulk_update_transactions`` (the ``categorize`` capability:
set a category on a confirmed id list). So the loop is read → user confirms → existing
write choke point, no new write path.

This complements (does not duplicate) ``get_rule_suggestions``: a payee corrected ≥N times
flows to ``get_rule_suggestions`` to create a PERMANENT rule; this tool handles the unruled
long tail — a payee already covered by an enabled rule is excluded by the service.

A pure read (proposed_change is always None) → on the headless ``_T0`` /
``HEADLESS_ALLOWED_TOOLS`` ceiling. A new module (NOT folded into ai/tools/ledger.py) to
avoid a merge collision with the concurrently-edited ledger tool group, mirroring payees.

Left singular (harness audit 2026-07-19, "make single-entity tools accept batches"):
``get_category_suggestions`` already returns MANY rows per call (one suggestion per
uncategorized transaction, paginated) — it isn't the single-scalar-id antipattern the audit
targets. A ``transaction_ids`` narrowing filter would be a plausible enhancement, but doing it
correctly (filtering BEFORE limit/offset, so a page can't silently miss a requested id) needs
a service-layer change to ``services/categorize.py``'s ``suggest_categories``/
``count_category_suggestions``, which is out of this lane's edit scope (``backend/ai/tools/``
only) — left for whoever owns that service next.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import categorize as categorize_service


class GetCategorySuggestionsInput(ToolInput):
    """Schema-of-record for ``get_category_suggestions`` (see ``schema_from_model``)."""

    min_confidence: Optional[float] = Field(
        default=None, description="Minimum confidence (0-1) a suggestion must clear (default 0.6)."
    )
    limit: Optional[int] = Field(
        default=None, description="Max number of suggestions to return (default 50)."
    )
    offset: Optional[int] = Field(
        default=None,
        description="Zero-based row offset (default 0) — page through further suggestions.",
    )


@tool(
    "get_category_suggestions",
    description=(
        "Suggest a category for each UNCATEGORIZED expense/income transaction, learned from "
        "the user's OWN history of that payee (corrections + already-categorized rows). "
        "Each row carries: transaction_id, payee_key, description, suggested_category, "
        "confidence (0-1), evidence_count, kind. Sorted highest-confidence first. "
        "Pure READ — to apply, call "
        "update_transactions(updates=[{transaction_id, category}, ...]) per group. "
        "Payees covered by an enabled rule are excluded. Prefer get_rule_suggestions for "
        "repeatedly-corrected payees. Returns [] on an empty book. Reports showing/total; "
        "pass offset to page through further suggestions rather than only the top 50."
    ),
    input_schema=schema_of(GetCategorySuggestionsInput, tool_name="get_category_suggestions"),
)
async def get_category_suggestions(input_data: dict):
    kwargs: dict = {}
    mc = input_data.get("min_confidence")
    if mc is not None:
        kwargs["min_confidence"] = float(mc)
    limit = int(input_data.get("limit") or categorize_service.DEFAULT_LIMIT)
    offset = max(0, int(input_data.get("offset") or 0))
    suggestions = categorize_service.suggest_categories(limit=limit, offset=offset, **kwargs)
    total = categorize_service.count_category_suggestions(**kwargs)
    end = offset + len(suggestions)
    payload: dict = {
        "ok": True,
        "suggestions": suggestions,
        "showing": len(suggestions),
        "offset": offset,
        "total": total,
    }
    if end < total:
        payload["note"] = (
            f"Showing suggestions {offset + 1}–{end} of {total}. Page with offset={end}."
        )
    return json.dumps(payload, ensure_ascii=False), None
