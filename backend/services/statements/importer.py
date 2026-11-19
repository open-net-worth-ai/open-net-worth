"""Bridge: unified parser output → the ONE import core (drafts → preview/commit choke).

``_row_to_draft``/``rows_to_results`` are the draft mappers the decision pipeline
(``statements/decisions.py``) builds on; ``preview_bytes`` / ``commit_bytes`` remain the
``import_transactions`` tool's blind preview/commit facade (the REST endpoints now route
through the decision pipeline instead — ADR 2026-07-17 import-one-pipeline-unification).
Draft semantics carry the P0 decisions forward: the RAW descriptor stays the description
(cross-source match signal), the clean payee rides as a ``payee`` NAME the write choke
resolves, the bank reference keys the dedup id, and display-only keys never reach the
choke."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from backend.core.errors import ConflictError, MissingFxRateError, ValidationError
from backend.core.fx import known_rate
from backend.core.txn_sources import STATEMENT_NAMESPACE
from backend.services import ledger as ledger_service
from backend.services.import_common import (
    PreviewResult,
    RowResult,
    build_commit,
    build_preview,
    disambiguate_identical_rows,
    flag_duplicates,
    resolve_account,
)
from backend.services.statements.canonical import CanonicalRow, ParseResult
from backend.services.statements.parser import parse_statement_bytes

# The model must be at least this confident before a row is offered; below it the row is a
# skip (P1) / a "needs your eyes" review item (P2 preview) — never silently booked.
MIN_CONFIDENCE = 0.6

# Preview-display keys that must not become stored transaction columns (the write choke
# spreads **data). ``payee`` is NOT here — the choke consumes it into payee_id.
# ``routing_automation_id`` is the routed-transfer provenance hint — read by
# decision_commit for the match-count bump, never stored. ``lucius_skip`` is the
# Lucius-pass default-skip marker (SI-68) — read by decision_commit's action ladder.
# ``policy_suppressed`` is the match-policy twin of it (RFC 2026-07-29 §D5): the owner's notes
# removed or redirected this row's engine match, so it default-skips instead of default-creating.
_DISPLAY_ONLY_DRAFT_KEYS = (
    "reference",
    "import_description",
    "category_hint",
    "routing_automation_id",
    "lucius_skip",
    "policy_suppressed",
    "match_rejected",
    "match_rejected_ids",
)

logger = logging.getLogger(__name__)


def external_ref(
    account_id: str,
    date: str,
    note: str,
    amount: float,
    inflow: bool,
    reference: str | None,
) -> str:
    """Deterministic row identity: account + date + signed amount."""
    signed = amount if inflow else -amount
    ident = "|".join([account_id, date, f"{signed:.2f}"])
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


# ── Import routing Automations ("this is a transfer to X, always") ────────────────────
# A transaction Automation whose action carries ``route_to_account`` reshapes a statement row
# into a two-leg TRANSFER at draft birth — direction-aware (money-out row → statement
# account → routed account; money-in row → the reverse). This is the ONLY place routing
# applies: the create choke skips routing transforms wholesale (label-only
# invariant), so a routed draft passes the choke like any other transfer.


@dataclass
class ImportRouting:
    """One Automations+accounts read for the whole file."""

    automations: list[dict]
    accounts: dict[str, dict]


def import_routing() -> ImportRouting | None:
    """The active routing Automations + account universe, or None when none exists
    (the common case — zero per-row overhead). Best-effort: a failed store read never
    blocks a parse (format-memory precedent)."""
    try:
        from backend.services.automations.transaction_events import active_transaction_transforms

        automations = [
            row
            for row in active_transaction_transforms()
            if ((row.get("execution") or {}).get("set") or {}).get("route_to_account")
        ]
        if not automations:
            return None
        return ImportRouting(
            automations=automations,
            accounts={a["id"]: a for a in ledger_service.accounts.all()},
        )
    except Exception:  # noqa: BLE001 — routing is an enhancement, never a parse-breaker
        logger.exception("import routing: Automation lookup failed")
        return None


def lacks_route_rate(currency: str, target: dict) -> bool:
    """True when ``target`` holds another currency and no rate converts this row into it. The
    ledger's conversion raises on an unknown pair, so a route never reaches it: one row with no
    rate must stay a plain row, not fail the whole review."""
    target_ccy = (target.get("currency") or currency).upper()
    return target_ccy != currency.upper() and known_rate(currency, target_ccy) is None


def unconverted_leg(draft: dict) -> str | None:
    """The side (``"from"``/``"to"``) of a routed transfer draft still stated in the statement's
    currency while its account holds another. Its figure is not a fact until the choke converts
    it, and even then only an estimate at the latest rate, so nothing may match on it or write
    it onto a booked transfer. None for any other draft, an FX pair's native legs included."""
    if draft.get("kind") != "transfer":
        return None
    for side in ("from", "to"):
        account = ledger_service.accounts.find_by_id(draft.get(f"{side}_account_id") or "")
        held = ((account or {}).get("currency") or "").upper()
        stated = (draft.get(f"{side}_currency") or "").upper()
        if held and stated and held != stated:
            return side
    return None


def statement_leg_only(draft: dict) -> dict:
    """A routed transfer draft as a Link may apply it: the statement speaks only for its own
    account, so the unconverted leg's figure is dropped and the booked transfer keeps its own."""
    side = unconverted_leg(draft)
    return {**draft, f"{side}_amount": None} if side else draft


def routed_other_leg(draft: dict) -> dict | None:
    """What a cross-currency routed draft would book on the other account, resolved the way the
    write resolves it so the review shows that figure. None when the draft has no such leg."""
    side = unconverted_leg(draft)
    if side is None:
        return None
    try:
        resolved, _meta = ledger_service.resolve_transaction_row(
            dict(draft), mint_payee=False, skip_payee_resolve=True, automations=[]
        )
    except (ValidationError, ConflictError, MissingFxRateError):
        logger.warning("import routing: could not resolve the converted leg", exc_info=True)
        return None
    account = ledger_service.accounts.find_by_id(resolved[f"{side}_account_id"]) or {}
    return {
        "account_id": resolved[f"{side}_account_id"],
        "account_name": account.get("name"),
        "amount": resolved[f"{side}_amount"],
        "currency": resolved[f"{side}_currency"],
    }


def route_transfer_draft(
    draft: dict,
    row: CanonicalRow,
    account_id: str,
    currency: str,
    target: dict | None,
    provenance_id: str | None,
) -> dict | None:
    """The direction-aware two-leg transfer reshape of a plain single-leg draft, for Lucius-pass
    ``route_transfer`` ops (SI-68) — the review's Type picker included. Honest fallbacks — None
    (the plain draft stands) when the target account is gone, archived, the statement-side
    account itself, or holds a currency with no known rate.

    Both legs are stated in the statement's currency: the choke converts the other account's
    leg into its own currency at write time (see ``unconverted_leg``), the same shape
    ``routed_transfer.routed_transfer_draft`` books for an explicit decision."""
    if target is None or target.get("archived") or target["id"] == account_id:
        return None
    if lacks_route_rate(currency, target):
        return None
    outbound = row.direction == "OUT"
    transfer = {
        "date": draft["date"],
        "kind": "transfer",
        "description": draft["description"],
        "source": draft["source"],
        # The plain draft's id verbatim — dedup identity is unchanged by routing, so a
        # file imported before the rule existed still L0-dedups after it.
        "external_refs": [dict(e) for e in (draft.get("external_refs") or [])],
        "reference": row.reference,  # display-only (already inside the external_ref)
        "routing_automation_id": provenance_id,
        "from_account_id": account_id if outbound else target["id"],
        "from_amount": row.amount,
        "from_currency": currency,
        "to_account_id": target["id"] if outbound else account_id,
        "to_amount": row.amount,
        "to_currency": currency,
    }
    if draft.get("category"):
        transfer["category"] = draft["category"]
    if draft.get("tags"):
        transfer["tags"] = list(draft["tags"])
    return transfer


def _apply_routing(
    draft: dict, row: CanonicalRow, account_id: str, currency: str, routing: ImportRouting
) -> dict | None:
    """Return the first valid Automation-routed transfer, before leg validation."""
    from backend.services.automations.transaction_events import (
        route_statement_draft_with_automations,
    )

    transfer, matches = route_statement_draft_with_automations(draft, routing.automations)
    if not matches:
        return None
    match = matches[0]
    automation = next(item for item in routing.automations if item.get("id") == match.automation_id)
    action = (automation.get("execution") or {}).get("set") or {}
    target = routing.accounts.get(action.get("route_to_account") or "")
    if (
        target is None
        or target.get("archived")
        or target["id"] == account_id
        or (target.get("currency") or "").upper() != currency
    ):
        return None
    transfer["routing_automation_id"] = match.automation_id
    return transfer


def _statement_refs(account_id: str, row: CanonicalRow, note: str, inflow: bool) -> list[dict]:
    """The one origin pair a statement row carries: its deterministic id."""
    primary = external_ref(account_id, row.date, note, row.amount, inflow, row.reference)
    return [{"source": STATEMENT_NAMESPACE, "ref": primary}]


def statement_row_refs(row: CanonicalRow, account_id: str) -> list[dict]:
    """The (un-disambiguated) ``external_refs`` a draft for this row would carry: its
    format-native id when the adapter minted one, else the fabricated deterministic id.

    Pulled out of ``_row_to_draft`` so ``ledger_statement_links._record_refs`` can derive a
    cached statement row's refs through this SAME function, then run them through the SAME
    ``disambiguate_identical_rows`` the draft pipeline runs — one source of truth, so the
    index never disagrees with what a commit actually wrote (a repeat row's suffixed ref used
    to have no match in an index that only ever computed the bare form)."""
    inflow = row.direction == "IN"
    note = row.description or row.payee or "Imported transaction"
    return [dict(e) for e in row.external_refs] or _statement_refs(account_id, row, note, inflow)


def _row_to_draft(
    row: CanonicalRow,
    account_id: str,
    currency: str,
    prefix: str,
    category_rows: list[dict] | None = None,
    routing: ImportRouting | None = None,
) -> dict:
    inflow = row.direction == "IN"
    note = row.description or row.payee or "Imported transaction"
    draft: dict = {
        "date": row.date,
        "description": note,
        # Sheet-shaped sources share the csv namespace; every other format keeps the SAME
        # source stamp its legacy importer used (ofx_import/qif_import/mt940_import/…).
        # ONE writer value for every file format (RFC 2026-08-02). The format is already in
        # external_refs[].source — stamping it here too produced "csv_import" on xlsx and
        # disagreed with decisions.py for every other format.
        "source": "import",
        # A format-native id (OFX FITID & friends, minted by the adapter) wins — byte-identical
        # to the legacy importers' ids, so cross-path re-imports still hard-dedup.
        "external_refs": statement_row_refs(row, account_id),
        "payee": row.payee,  # clean name → resolved into payee_id at the choke, then dropped
        "reference": row.reference,  # display-only (already inside the external_ref)
        "category_hint": row.category_hint,  # preview-only, ✦-badged, never auto-applied
    }
    if row.category_ref:
        # The file's own declared category (QIF `L`, a mapped CSV column) — resolved against
        # the live store, applied as an explicit category; unresolvable → dropped (Uncategorized),
        # never invented (category-opaque-ids invariant).
        from backend.services.categories import resolve_category_ref

        resolved_cat = resolve_category_ref(row.category_ref, rows=category_rows)
        if resolved_cat:
            draft["category"] = resolved_cat
    if row.original_amount is not None and (row.original_currency or "").upper() != currency:
        # A "foreign" charge in the account's own currency is conversion noise, not a fact.
        draft |= {
            "original_amount": row.original_amount,
            "original_currency": row.original_currency,
        }
    leg = (
        {
            "kind": "income",
            "to_account_id": account_id,
            "to_amount": row.amount,
            "to_currency": currency,
        }
        if inflow
        else {
            "kind": "expense",
            "from_account_id": account_id,
            "from_amount": row.amount,
            "from_currency": currency,
        }
    )
    draft |= leg
    if routing is not None and account_id:
        routed = _apply_routing(draft, row, account_id, currency, routing)
        if routed is not None:
            return routed
    return draft


def category_rows_for(rows) -> list[dict] | None:
    """One category-store read for the whole file when any row carries a source-declared
    category (QIF `L`, a mapped CSV column) — None otherwise (no read at all)."""
    if not any(getattr(r, "category_ref", None) for r in rows):
        return None
    from backend.services.categories import all_category_rows

    return all_category_rows()


def rows_to_results(result: ParseResult, account_id: str | None, prefix: str) -> list[RowResult]:
    """Map canonical rows → RowResults (draft | skip), then run the shared intra-file
    disambiguation + exact-id duplicate flagging. Reads the ledger, writes NOTHING."""
    resolved = resolve_account(account_id, None)
    acct = ledger_service.accounts.find_by_id(resolved)
    currency = ((acct or {}).get("currency") or ledger_service.base_currency()).upper()
    category_rows = category_rows_for(result.rows)
    routing = import_routing()
    results: list[RowResult] = []
    for i, row in enumerate(result.rows, start=1):
        if row.skip:
            results.append(RowResult(line=i, skip_reason=row.skip_reason or "unreadable row"))
            continue
        if row.confidence < MIN_CONFIDENCE:
            reason = "low confidence"
            if row.notes:
                reason += f" ({row.notes[0]})"
            results.append(RowResult(line=i, skip_reason=reason))
            continue
        results.append(
            RowResult(
                line=i,
                draft=_row_to_draft(
                    row, resolved, currency, prefix, category_rows=category_rows, routing=routing
                ),
            )
        )
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


def strip_display_only(draft: dict) -> dict:
    for key in _DISPLAY_ONLY_DRAFT_KEYS:
        draft.pop(key, None)
    return draft


def preview_bytes(
    content: bytes, fmt: str, account_id: str | None = None, *, schema: dict | None = None
) -> PreviewResult:
    """Dry-run: parse (cheapest tier) + guards + map + report. Writes nothing."""
    result = parse_statement_bytes(content, fmt, schema=schema)
    results = rows_to_results(result, account_id, prefix=fmt)
    return build_preview([], len(result.rows), results)


def commit_bytes(
    content: bytes, fmt: str, account_id: str | None = None, *, schema: dict | None = None
) -> dict:
    """Parse + map + persist the non-duplicate drafts through the ledger choke. Idempotent."""
    result = parse_statement_bytes(content, fmt, schema=schema)
    results = rows_to_results(result, account_id, prefix=fmt)
    for r in results:
        if r.draft:
            strip_display_only(r.draft)
    committed = build_commit(len(result.rows), results)
    _stamp_profile_account(result, account_id, committed)
    return committed


def _stamp_profile_account(result: ParseResult, account_id: str | None, committed: dict) -> None:
    """After a successful commit, remember which account this source imports into — the
    auto-pick hint for the next import of the same format. Best-effort, never blocks."""
    prof = (result.meta.extra or {}).get("format_profile")
    if not prof or not account_id or not committed.get("created"):
        return
    try:
        from backend.db.statement_format_store import StatementFormatStore

        StatementFormatStore().record_use(prof["id"], account_ids=[account_id])
    except Exception:  # noqa: BLE001 — memory is an optimization
        pass
