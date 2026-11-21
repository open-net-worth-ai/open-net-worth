"""Preview enrichment — a SIDE-EFFECT-FREE dry-run of the write choke's categorization
(RFC D5), so the preview shows the payee/category a row WILL get instead of "Uncategorized".

Mirrors the commit-time precedence exactly (``ledger.create_transaction``):
    explicit > rule > payee-default (per kind) > LLM hint (✦, never auto-applied) > None

Verified pure call chain (see RFC Background):
- ``payee_registry.resolve_payee_id(…, create=False)`` — detects an existing merchant, never
  mints one.
- ``apply_transaction_automations(draft, automations=preloaded)`` — pure by contract
  bump hint is dropped, so no rule counter moves.
- ``payee_registry.default_category_for`` — pure read.
The write-side learning (``note_usage``/``learn_category``) is simply never called here."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _resolve_hint(hint: str | None, category_rows: list[dict]) -> str | None:
    """An LLM ``category_hint`` counts ONLY when it resolves to a category the user already
    has — the model may suggest, never invent taxonomy."""
    if not hint:
        return None
    try:
        from backend.services import categories as categories_service

        return categories_service.resolve_category_ref(hint, rows=category_rows)
    except Exception:
        return None


def _preview_automations(draft: dict, payee_id: str | None, automation_set: list[dict]):
    """The write's own pass: with ``transaction_rules`` on it runs after payee resolution, so the
    resolved payee is on the row; a split that does not validate is dropped, as the write would."""
    from backend.services.automations import rule_engine
    from backend.services.automations.transaction_events import apply_transaction_automations

    if not automation_set or not rule_engine.rules_enabled():
        return apply_transaction_automations(draft, automation_set)
    if payee_id:
        draft["payee_id"] = payee_id
    from backend.services import ledger

    previewed, matches, _skipped = rule_engine.apply_rules(
        draft,
        automation_set,
        finalize=ledger._apply_splits,
        exists=ledger._rule_ref_exists(None),
    )
    return previewed, matches


def enrich_drafts(drafts: list[dict]) -> list[dict]:
    """Annotate each draft with the dry-run verdict:
    ``{payee_id, payee_name, category, category_source}`` where category_source ∈
    ``automation | payee | hint`` (None when the row would land Uncategorized). Reads stores ONCE for
    the batch; never writes, never mints, never bumps."""
    from backend.services import payee_registry
    from backend.services.automations.transaction_events import active_transaction_transforms
    from backend.services.categories import all_category_rows

    try:
        automation_set = active_transaction_transforms()
    except Exception:
        automation_set = []
    try:
        category_rows = all_category_rows()
    except Exception:
        category_rows = []

    out: list[dict] = []
    for draft in drafts:
        verdict: dict = {
            "payee_id": None,
            "payee_name": None,
            "category": None,
            "category_source": None,
        }
        try:
            # Clean payee first, then the RAW description — it carries the long bank line
            # the book's aliases were learned from (the clean name alone missed it, seen live).
            pid = payee_registry.resolve_payee_id(draft.get("payee") or "", create=False)
            if not pid:
                pid = payee_registry.resolve_payee_id(draft.get("description") or "", create=False)
            if pid:
                payee = payee_registry._store.find_by_id(pid)
                verdict["payee_id"] = pid
                verdict["payee_name"] = (payee or {}).get("name")
            previewed, matches = _preview_automations(dict(draft), pid, automation_set)
            kind = draft.get("kind", "expense")
            if previewed.get("splits"):
                verdict["category"] = "split"
                verdict["category_source"] = "automation"
            elif previewed.get("category"):
                verdict["category"] = previewed["category"]
                verdict["category_source"] = "automation" if matches else "explicit"
            elif pid:
                default = payee_registry.default_category_for(pid, kind)
                if default:
                    verdict["category"] = default
                    verdict["category_source"] = "payee"
            if verdict["category"] is None:
                hinted = _resolve_hint(draft.get("category_hint"), category_rows)
                if hinted:
                    verdict["category"] = hinted
                    verdict["category_source"] = "hint"  # ✦-badged, never auto-applied
        except Exception:  # enrichment is display truth, never a preview-breaker
            logger.exception("statement enrich: dry-run failed for a draft")
        out.append(verdict)
    return out
