"""Equity-compensation tools (feat/equity-comp).

Lucius reads grants / vest calendar / vest report and, on an interactive/connector
turn, PREVIEWS a vest confirmation before writing it (a vest moves SHARES, never cash —
FMV lot + locked-position decrement + vest event; no ledger transaction, ADR
2026-09-22-a-vest-books-no-income-transaction):

  * ``list_grants`` / ``get_equity_report`` / ``get_upcoming_vests`` — reads
    (proposed_change = None).
  * ``create_grant`` / ``update_grant`` / ``cancel_grant`` — grant config writes (they
    move unvested value into net worth via the locked position).
  * ``record_vest`` / ``undo_vest`` / ``import_8b`` — ONE tool per write
    (harness reliability program, 2026-07-19 — collapses the former
    ``propose_vest``/``record_vest`` etc. pairs; see ``portfolio.py``'s
    ``remove_positions`` for the full rationale). On a card-capable/attended turn:
    PREVIEWS (``record_vest`` prices the vest via ``preview_vest``, prefilling
    ``fmv_per_unit`` from price history when omitted; ``import_8b`` runs the LLM row
    extraction), returns a ``proposed_change``, writes NOTHING and PAUSES on its own
    ``tool_use_id``. ``assistant.resolve_pending_tool_use`` re-runs it on approval with the
    STORED args (and, for ``import_8b``, the already-extracted payload — never
    re-extracted). An unattended/automated caller writes immediately, no card.

ALL equity logic (four-store atomicity, idempotency, locked↔liquid sync, tax-event
routing) lives in ``services/equity.py`` — this module only marshals I/O and shapes
the confirm card. Respects the dependency rule (ai → services → db → core).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.db.account_store import AccountStore
from backend.services import equity as equity_service
from backend.services import equity_release as equity_release_service
from backend.services.equity_report import equity_report  # noqa: E402


def _match_account_id(name: str) -> str | dict:
    """Resolve an account by name (case-insensitive) from non-archived accounts.

    Returns the account id string on a unique match. Returns a dict
    {"error": "..."} on zero matches or ambiguous matches — callers must return
    that dict as the tool's JSON error.
    """
    candidates = [a for a in AccountStore().all() if not a.get("archived")]
    name_lower = name.lower()
    exact = [a for a in candidates if a.get("name", "").lower() == name_lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(repr(a["name"]) for a in exact)
        return {"error": f"ambiguous — pass account_id from list_accounts (matches: {names})"}
    substr = [a for a in candidates if name_lower in a.get("name", "").lower()]
    if len(substr) == 1:
        return substr[0]["id"]
    if len(substr) > 1:
        names = ", ".join(repr(a["name"]) for a in substr)
        return {"error": f"ambiguous — pass account_id from list_accounts (matches: {names})"}
    return {
        "error": (
            f"no existing account matches {name!r} — pass account_id from list_accounts, or set create_new_account=true after the user confirms creating a new account"
        )
    }


# ── Reads ─────────────────────────────────────────────────────────────────────


class ListGrantsInput(ToolInput):
    """Schema-of-record for ``list_grants`` (see ``schema_from_model``)."""


@tool(
    "list_grants",
    description=(
        "List all equity-compensation grants (RSU/LTIP) with computed fields: symbol, "
        "vested_units, unvested_units, next_vest_date, and per-tranche status "
        "(vested|upcoming|performance_pending). Use this to get grant_id and tranche_id before "
        "proposing a vest or an undo."
    ),
    input_schema=schema_of(ListGrantsInput, tool_name="list_grants"),
)
async def list_grants(input_data: dict):
    grants = equity_service.hydrated_grants()
    return json.dumps({"grants": grants}, ensure_ascii=False), None


class GetEquityReportInput(ToolInput):
    """Schema-of-record for ``get_equity_report`` (see ``schema_from_model``)."""

    year: Optional[int] = Field(
        default=None, description="Tax year (YYYY). Defaults to the current year."
    )


@tool(
    "get_equity_report",
    description=(
        "Per-vest report for a year: per-vest rows (symbol, vest_date, units, fmv_per_unit, "
        "gross_gain in the base currency, tax_event flag) and total_gain_base. Deferred-grant "
        "vests appear in the table but are excluded from the total (a deferred grant is not "
        "taxed at vest). Gross gain is computed from the vest EVENT's own native FMV at current "
        "FX — no vest books a ledger transaction; a vest recorded before 2026-09-22 that still "
        "links one uses that transaction's base amount under the historical-rate lock. "
        "Omit year to default to the current year. The 'years' field lists every year with ≥1 "
        "vest event for the frontend year picker."
    ),
    input_schema=schema_of(GetEquityReportInput, tool_name="get_equity_report"),
)
async def get_equity_report(input_data: dict):
    report = equity_report(year=input_data.get("year"))
    return json.dumps(report, ensure_ascii=False), None


class GetUpcomingVestsInput(ToolInput):
    """Schema-of-record for ``get_upcoming_vests`` (see ``schema_from_model``)."""

    window_days: Optional[int] = Field(
        default=None,
        description=(
            "Return only tranches vesting within this many days from today (0 = today only; "
            "omit for all future vests)."
        ),
    )


@tool(
    "get_upcoming_vests",
    description=(
        "Unvested tranches of active grants sorted by vest_date, annotated with "
        "indicative_value_base (current price × units × FX) and an overdue flag (vest_date is "
        "in the past but the tranche has not been vested yet). Use window_days to limit the "
        "window; omit for all upcoming vests."
    ),
    input_schema=schema_of(GetUpcomingVestsInput, tool_name="get_upcoming_vests"),
)
async def get_upcoming_vests(input_data: dict):
    items = equity_service.upcoming_vests(window_days=input_data.get("window_days"))
    return json.dumps({"items": items}, ensure_ascii=False), None


class ListPendingReleasesInput(ToolInput):
    """Schema-of-record for ``list_pending_releases`` (see ``schema_from_model``)."""

    grant_id: Optional[str] = Field(
        default=None, description="Restrict to one grant (from list_grants). Omit for all."
    )


@tool(
    "list_pending_releases",
    description=(
        "Vests that HAPPENED but whose delivery is not recorded yet. This includes current "
        "release_required grants and legacy vests whose existing lot predates explicit release "
        "tracking. 'overdue' is true ONLY when an expected_release_date was recorded and has "
        "passed; a blank one means unknown, never late. Answers 'what am I waiting on' and "
        "gives the vest_event_id for record_release."
    ),
    input_schema=schema_of(ListPendingReleasesInput, tool_name="list_pending_releases"),
)
async def list_pending_releases(input_data: dict):
    items = equity_release_service.pending_releases(grant_id=input_data.get("grant_id"))
    return json.dumps({"items": items, "showing": len(items), "total": len(items)}), None


# ── Grant config writes (NOT headless) ──────────────────────────────────────


# Tranche field prose, shared by create_grant's and update_grant's tranche items.
_TRANCHE_UNITS_DESC = "Shares vesting on this date. create_grant REFUSES the grant unless these sum to total_units (±0.01); update_grant does NOT re-check that, so an edited schedule can silently disagree. Cost basis is the FMV at each vest, never grant_price_per_unit."
_RELEASE_REQUIRED_DESC = "True when the plan DELIVERS shares some time AFTER they vest (settlement lag, blackout, country delivery rules). Vested units then park in a separate pending position — in net worth but NOT sellable — until record_release. Default false (the common case; leave alone unless the user says delivery is delayed). Money is unaffected either way: income and cost basis are always struck at vest-date FMV. Applies to FUTURE vests only."
_TRANCHE_VEST_DATE_DESC = "ISO YYYY-MM-DD. The tranches list REPLACES the entire schedule — carry each existing tranche's `id` through unchanged, or it is re-minted as a brand-new tranche with vested=false, detaching it from the vest already recorded against it."


# One tranche of create_grant's vesting schedule — a loose (non-ToolInput) nested item,
# since the literal never declared additionalProperties on it; a docstring here would leak
# into the item schema as a "description" key (only ToolInput suppresses that), so this
# class deliberately has none.
class CreateGrantTranche(BaseModel):
    vest_date: str = Field(description="ISO YYYY-MM-DD")
    units: float = Field(description=_TRANCHE_UNITS_DESC)
    kind: Optional[Literal["time", "performance"]] = Field(
        default=None, description="time (default) | performance"
    )
    condition_note: Optional[str] = Field(
        default=None, description="Performance condition text (LTIP only)"
    )


class CreateGrantInput(ToolInput):
    """Schema-of-record for ``create_grant`` (see ``schema_from_model``)."""

    instrument_id: str = Field(
        description="From resolve_instruments — required; pins a live price source."
    )
    total_units: float = Field(description="Total units across all tranches")
    grant_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD")
    grant_type: Optional[Literal["rsu", "ltip"]] = Field(
        default=None, description="RSU (default) or LTIP/performance grant"
    )
    tax_event: Optional[Literal["at_vest", "deferred"]] = Field(
        default=None,
        description=(
            "When the grant is taxed — information only. at_vest (default) or deferred. A vest "
            "books the same thing either way (FMV lot, no ledger transaction); the flag drives "
            "the vest report's taxable total."
        ),
    )
    release_required: Optional[bool] = Field(default=None, description=_RELEASE_REQUIRED_DESC)
    tranches: List[CreateGrantTranche] = Field(
        min_length=1, description="Vesting schedule. Σ units must equal total_units."
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Brokerage/custodian account id (from list_accounts) — preferred path",
    )
    account_name: Optional[str] = Field(
        default=None,
        description=(
            "Resolve an existing account by name (case-insensitive). Prefer account_id from "
            "list_accounts. If the account doesn't exist yet, set create_new_account=true to "
            "create it."
        ),
    )
    create_new_account: Optional[bool] = Field(
        default=None,
        description=(
            "When true AND account_name is given without account_id, create a new account with "
            "that name. Only set when the user has said a new account should be created. Default false — resolve against existing accounts instead."
        ),
    )
    locked_position_id: Optional[str] = Field(
        default=None,
        description=(
            "Adopt an existing locked position instead of creating one (use when you've already "
            "added the position manually)"
        ),
    )
    currency: Optional[str] = Field(
        default=None,
        description="Grant-statement currency (informational; defaults to instrument quote_currency)",
    )
    grant_price_per_unit: Optional[float] = Field(
        default=None,
        description=(
            "Market price per unit on the grant date, in the grant's currency. Informational "
            "only — NOT used for tax (vest-date FMV drives tax) and never the locked position's "
            "cost basis. Store it when the user mentions the price at grant (e.g. 'granted at "
            "€20.36'). Enables 'since-grant performance' display in the UI."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note about the grant (plan name, performance conditions, what the user "
            "was told). Stored on the grant for reference; never parsed and never read by "
            "vesting, tax, or basis. Defaults to empty."
        ),
    )


@tool(
    "create_grant",
    description=(
        "Create an equity-compensation grant (RSU or LTIP) with its vesting schedule. ALWAYS "
        "call resolve_instruments first to get the instrument_id. Provide tranches as "
        "[{vest_date, units, kind?}] — Σ units must equal total_units. An unvested position "
        "(vesting_state='unvested') is created automatically to hold unvested units in net "
        "worth. account_id or account_name is REQUIRED — ask the user which custodian/account "
        "holds the grant; never invent one. Pass account_id (from list_accounts) for an "
        "existing brokerage venue, or account_name to create/reuse one by name. Pass "
        "locked_position_id to adopt an existing locked position instead of creating one (in "
        "that case account is derived from the existing position). grant_type: 'rsu' (default) "
        "| 'ltip'. tax_event: 'at_vest' (default) | 'deferred' — INFORMATION about when the "
        "grant is taxed; a vest writes the same FMV lot either way and never a ledger "
        "transaction (deferred vests are excluded from the vest report's total). Not headless — creates a real locked position and moves unvested "
        "value into net worth. Account = the custodian's securities account (e.g. 'IBKR'), "
        "never a currency wallet."
    ),
    input_schema=schema_of(CreateGrantInput, tool_name="create_grant"),
    mutates=("portfolio",),
)
async def create_grant(input_data: dict):
    account_id = input_data.get("account_id")
    account_name = input_data.get("account_name")
    if account_name and not account_id and not input_data.get("create_new_account"):
        resolved = _match_account_id(account_name)
        if isinstance(resolved, dict):
            return json.dumps({"ok": False, "error": resolved["error"]}), None
        account_id = resolved
        account_name = None
    try:
        result = equity_service.apply_equity_change(
            {
                "type": "grant_create",
                "instrument_id": input_data["instrument_id"],
                "total_units": input_data["total_units"],
                "grant_date": input_data.get("grant_date", ""),
                "grant_type": input_data.get("grant_type", "rsu"),
                "tax_event": input_data.get("tax_event", "at_vest"),
                "release_required": input_data.get("release_required", False),
                "tranches": input_data.get("tranches") or [],
                "account_id": account_id,
                "account_name": account_name,
                "locked_position_id": input_data.get("locked_position_id"),
                "currency": input_data.get("currency", ""),
                "grant_price_per_unit": input_data.get("grant_price_per_unit"),
                "note": input_data.get("note", ""),
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# Same reasoning as CreateGrantTranche — a loose nested item, no docstring.
class UpdateGrantTranche(BaseModel):
    id: Optional[str] = Field(default=None, description="Stable tranche id (preserve when editing)")
    vest_date: str = Field(description=_TRANCHE_VEST_DATE_DESC)
    units: float = Field(description=_TRANCHE_UNITS_DESC)
    kind: Optional[Literal["time", "performance"]] = Field(
        default=None, description="time (default) | performance"
    )
    condition_note: Optional[str] = Field(
        default=None, description="Performance condition text (LTIP only)"
    )


class UpdateGrantInput(ToolInput):
    """Schema-of-record for ``update_grant`` (see ``schema_from_model``)."""

    grant_id: str = Field(description="Grant id from list_grants")
    grant_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD")
    currency: Optional[str] = Field(
        default=None,
        description=(
            "Grant-statement currency (3-letter ISO). Informational — it labels "
            "grant_price_per_unit; vest income and lot basis are booked in the vest's own "
            "fmv_currency, not this. Omit to leave it unchanged."
        ),
    )
    grant_price_per_unit: Optional[float] = Field(
        default=None,
        description=(
            "Market price per unit on the grant date, in the grant's currency. Informational "
            "only — NOT used for tax (vest-date FMV drives tax) and never the locked position's "
            "cost basis. Use this to record the price when the user learns it later."
        ),
    )
    tax_event: Optional[Literal["at_vest", "deferred"]] = Field(
        default=None,
        description=(
            "When the grant is taxed — information only. A vest books the FMV-basis lot and "
            "no ledger transaction whatever this says; the flag decides whether the vest counts "
            "in the vest report's taxable total. Omit to leave it unchanged."
        ),
    )
    release_required: Optional[bool] = Field(default=None, description=_RELEASE_REQUIRED_DESC)
    tranches: Optional[List[UpdateGrantTranche]] = Field(
        default=None,
        description=(
            "REPLACES the entire vesting schedule — pass every tranche you want to keep, each "
            "with its existing `id` preserved, or that tranche's vested/vest_event_id link is "
            "lost and a fresh id is minted. Omit to leave the schedule untouched."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Replacement free-form note on the grant (replaces the whole note, never appends). "
            "Omit to leave it unchanged."
        ),
    )
    account_id: Optional[str] = Field(
        default=None,
        description=(
            "Move the grant and its locked position to this account id (from list_accounts) — "
            "preferred path."
        ),
    )
    account_name: Optional[str] = Field(
        default=None,
        description=(
            "Move the grant and its locked position to an existing account by name "
            "(case-insensitive). Prefer account_id from list_accounts. If the account doesn't "
            "exist yet, set create_new_account=true."
        ),
    )
    create_new_account: Optional[bool] = Field(
        default=None,
        description=(
            "When true AND account_name is given without account_id, create a new account with "
            "that name. Only set when the user has said a new account should be created. Default false — resolve against existing accounts instead."
        ),
    )


@tool(
    "update_grant",
    description=(
        "Update a grant. To record the market price at grant date (the 'grant price'), set "
        "grant_price_per_unit — NEVER store a price in note and NEVER in the locked position's "
        "cost basis. Also edits grant_date, currency, tax_event, note, or replaces the tranche "
        "schedule. Call list_grants first to get the grant_id. Pass account_id (from "
        "list_accounts) or account_name to move the grant and its locked position to a "
        "different custodian in one step — this is the 'move my grant to JP Morgan' operation. "
        "Not headless — may affect the vesting schedule and locked-position sync."
    ),
    input_schema=schema_of(UpdateGrantInput, tool_name="update_grant"),
    mutates=("portfolio",),
)
async def update_grant(input_data: dict):
    account_id = input_data.get("account_id")
    account_name = input_data.get("account_name")
    if account_name and not account_id and not input_data.get("create_new_account"):
        resolved = _match_account_id(account_name)
        if isinstance(resolved, dict):
            return json.dumps({"ok": False, "error": resolved["error"]}), None
        account_id = resolved
        account_name = None
    try:
        change: dict = {"type": "grant_update", "grant_id": input_data["grant_id"]}
        for field in (
            "grant_date",
            "currency",
            "grant_price_per_unit",
            "tax_event",
            "release_required",
            "note",
            "tranches",
        ):
            if field in input_data:
                change[field] = input_data[field]
        if account_id:
            change["account_id"] = account_id
        if account_name:
            change["account_name"] = account_name
        result = equity_service.apply_equity_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


class RecordReleaseInput(ToolInput):
    """Schema-of-record for ``record_release`` (see ``schema_from_model``)."""

    vest_event_id: str = Field(
        description="vest_event id from list_pending_releases (NOT a grant or tranche id)."
    )
    release_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO YYYY-MM-DD the shares landed. Defaults to today; cannot precede the vest date. "
            "Dates the DELIVERY only — the lot keeps the vest date as acquisition."
        ),
    )
    note: Optional[str] = Field(
        default=None, description="Free-form note stored on the vest event. Defaults to empty."
    )


@tool(
    "record_release",
    description=(
        "Record that a vest's shares were DELIVERED. For a current pending vest, units move "
        "onto an FMV lot; for a legacy vest that already has its lot, only the release date is "
        "annotated so shares are never duplicated. Call list_pending_releases first for the "
        "vest_event_id. Writes NO transaction and never alters vest-date basis. Refused if "
        "already released. Not headless."
    ),
    input_schema=schema_of(RecordReleaseInput, tool_name="record_release"),
    mutates=("portfolio",),
)
async def record_release(input_data: dict):
    try:
        result = equity_service.apply_equity_change(
            {
                "type": "release_apply",
                "vest_event_id": input_data["vest_event_id"],
                "release_date": input_data.get("release_date"),
                "note": input_data.get("note", ""),
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


class CancelGrantInput(ToolInput):
    """Schema-of-record for ``cancel_grant`` (see ``schema_from_model``)."""

    grant_id: str = Field(description="Grant id from list_grants")


@tool(
    "cancel_grant",
    description=(
        "Cancel a grant (marks status='cancelled'). Use when an employee leaves or a grant is "
        "forfeited. Its unvested units are forfeited, so they drop to 0 and stop counting as "
        "unvested equity; vests already recorded are untouched. Call list_grants first to get "
        "the grant_id. Not headless."
    ),
    input_schema=schema_of(CancelGrantInput, tool_name="cancel_grant"),
    mutates=("portfolio",),
)
async def cancel_grant(input_data: dict):
    try:
        result = equity_service.apply_equity_change(
            {"type": "grant_cancel", "grant_id": input_data["grant_id"]}
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# ── Propose (approve-tier: return a proposed_change; write in the apply path) ─


def _build_vest_change(input_data: dict) -> tuple[Optional[dict], Optional[str], dict]:
    """Shared preview-build for the ``record_vest`` confirm gate — the former body of
    ``propose_vest``. Called only when previewing or re-verifying a confirm's outcome.

    Returns ``(change, error, resolved_input)``. ``resolved_input`` is ``input_data`` with a
    missing ``fmv_per_unit`` prefilled from price history and a missing ``idempotency_key``
    freshly minted — exactly what ``propose_vest`` used to do — BAKED IN so the eventual
    write uses the SAME values the card showed rather than re-deriving them (or trusting the
    model to echo them back) at confirm/write time.
    """
    grant_id = input_data["grant_id"]
    tranche_id = input_data["tranche_id"]

    # FMV prefill when fmv_per_unit is absent.
    fmv_per_unit = input_data.get("fmv_per_unit")
    fmv_source_note: str | None = None
    if fmv_per_unit is None:
        try:
            prefill = equity_service.fmv_prefill(grant_id, tranche_id)
            if prefill.get("fmv_per_unit") is not None:
                fmv_per_unit = prefill["fmv_per_unit"]
                fmv_source_note = (
                    f"prefilled from price history as of {prefill.get('as_of', 'unknown date')}"
                )
        except OpenNetWorthError:
            pass

    resolved = dict(input_data)
    resolved["fmv_per_unit"] = fmv_per_unit
    if not resolved.get("idempotency_key"):
        resolved["idempotency_key"] = str(uuid.uuid4())

    change = {
        "type": "vest_apply",
        "grant_id": grant_id,
        "tranche_id": tranche_id,
        "fmv_per_unit": fmv_per_unit,
        "fmv_currency": input_data.get("fmv_currency"),
        "vest_date": input_data.get("vest_date"),
        "units": input_data.get("units"),
        "price_paid_per_unit": input_data.get("price_paid_per_unit", 0),
        "units_withheld": input_data.get("units_withheld", 0),
        "note": input_data.get("note", ""),
        "idempotency_key": resolved["idempotency_key"],
    }

    preview = equity_service.preview_vest(change)
    if preview.get("error"):
        return None, preview["error"], resolved

    tax_event = preview.get("tax_event", "at_vest")
    needs_release = bool(preview.get("release_required"))
    what_will_post: list[str] = ["no ledger transaction — a vest delivers shares, not cash"]
    if needs_release:
        what_will_post.append(
            f"{preview.get('units')} units parked as pending release (owned, in net worth, "
            "not sellable) — the FMV-basis lot is struck at release, at this vest date and price"
        )
    else:
        what_will_post.append(
            f"FMV-basis lot: {preview.get('units')} units @ "
            f"{preview.get('fmv_per_unit')} {preview.get('fmv_currency')} on the liquid position"
        )
    what_will_post.append("locked position decremented by vest units (unvested→vested)")
    what_will_post.append("vest event recorded with the tax facts (units, FMV, dates)")

    change["display"] = {
        "symbol": preview.get("symbol"),
        "grant_id": grant_id,
        "tranche_id": tranche_id,
        "vest_date": preview.get("vest_date"),
        "units": preview.get("units"),
        "fmv_per_unit": preview.get("fmv_per_unit"),
        "fmv_currency": preview.get("fmv_currency"),
        "fmv_source": fmv_source_note,
        "price_paid_per_unit": preview.get("price_paid_per_unit"),
        "units_withheld": preview.get("units_withheld"),
        "gross_gain_native": preview.get("gross_gain_native"),
        "gross_gain_base": preview.get("base_amount"),
        "base_currency": preview.get("base_currency"),
        "tax_event": tax_event,
        "tax_event_note": (
            "taxed at vest — counts in the vest report's taxable total (information only; "
            "no transaction is written)"
            if tax_event != "deferred"
            else "tax deferred — excluded from the vest report's taxable total"
        ),
        "what_will_post": what_will_post,
    }
    return change, None, resolved


class ApplyVestInput(ToolInput):
    """Schema-of-record for ``record_vest`` (see ``schema_from_model``)."""

    grant_id: str = Field(description="Grant id from list_grants")
    tranche_id: str = Field(description="Tranche id from list_grants")
    fmv_per_unit: Optional[float] = Field(
        default=None,
        description=(
            "Fair market value per unit at vest. If omitted, prefilled from price_history "
            "at/before the tranche vest_date."
        ),
    )
    fmv_currency: Optional[str] = Field(
        default=None,
        description=(
            "Currency of the FMV (defaults to the instrument's quote_currency). You may enter "
            "the SGD 8B figure or the broker's native-currency figure — base is always "
            "recomputed via FX."
        ),
    )
    vest_date: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD override (defaults to the tranche's vest_date)"
    )
    units: Optional[float] = Field(
        default=None, description="Units to vest (defaults to the full tranche units)"
    )
    price_paid_per_unit: Optional[float] = Field(
        default=None, description="Cost per unit if non-zero (ESOW discount; default 0 for RSU)"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Caller-supplied dedup key. A second call with the SAME key returns the original "
            "vest event and writes nothing (idempotent: true). Pass a stable id from the source "
            "system (broker/plan-admin event id) on any automated or retried flow so a "
            "reprocessed message cannot double-book the vest event and lot. (A repeat "
            "of the same grant+tranche is refused regardless, key or not.)"
        ),
    )
    # The choke has always READ this; until 2026-07-19 no tool OFFERED it, so RSU tax
    # withholding at vest was unreachable from the AI surface even though REST accepted it.
    # Pinned by test_domain_introspect's _CODE_ONLY register, which no longer lists it.
    units_withheld: Optional[float] = Field(
        default=None,
        description="Units withheld for tax at vest (RSU sell-to-cover).",
    )
    expected_release_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO YYYY-MM-DD the shares are EXPECTED to be delivered. Only meaningful when "
            "release_required=true. Never guess — omit unless the user states a date."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note stored on the VEST EVENT record (not on the lot — that gets its "
            "own deterministic 'vest:<grant>:<tranche>' marker). "
            "Defaults to empty."
        ),
    )


@tool(
    "record_vest",
    description=(
        "Record a vest event for a specific tranche. Use list_grants to get grant_id and "
        "tranche_id. When fmv_per_unit is omitted, the most recent prior close from "
        "price_history is prefilled automatically (the card will say 'prefilled from price "
        "history as of YYYY-MM-DD' so the user can override if their broker statement differs). "
        "A vest writes a FMV-basis lot on the liquid position (or parks the units as pending "
        "when the grant needs a separate release) and decrements the locked position. It "
        "writes NO ledger transaction — shares are delivered, not cash; the tax facts live on "
        "the vest event. If the call asks the user first, their answer runs it server-side with exactly the values shown — never call it again and never restate the values. Pass idempotency_key so a retry is a safe no-op."
    ),
    input_schema=schema_of(ApplyVestInput, tool_name="record_vest"),
    mutates=("portfolio", "ledger"),
    # propose_vest is the former FIRST HALF of this merge (harness reliability program,
    # 2026-07-19 — see portfolio.py's remove_positions aliases note for the full
    # rationale). propose_vest used to MINT a fresh idempotency_key into every proposal
    # (its own schema had no such field) and rely on the model copying it into the apply
    # call; the merge does the SAME minting server-side (_build_vest_change) instead of
    # trusting the model to echo it back.
    self_stages=True,
)
async def record_vest(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "record_vest")
        if entry is None:
            return _gate.unknown_token_result("record_vest")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    if gated or promised is not None:
        change, error, resolved = _build_vest_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        input_data = resolved
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # e.g. the FMV prefill or the tranche state could have changed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("record_vest")

    try:
        result = equity_service.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": input_data["grant_id"],
                "tranche_id": input_data["tranche_id"],
                # .get(), not indexing: fmv_per_unit is OPTIONAL on this schema now (the
                # preview path prefills it — see _build_vest_change); a direct/unattended
                # caller that omits it must get the choke's own graceful ValidationError
                # ("fmv_per_unit is required"), never an uncaught KeyError.
                "fmv_per_unit": input_data.get("fmv_per_unit"),
                "fmv_currency": input_data.get("fmv_currency"),
                "vest_date": input_data.get("vest_date"),
                "units": input_data.get("units"),
                "price_paid_per_unit": input_data.get("price_paid_per_unit", 0),
                "units_withheld": input_data.get("units_withheld", 0),
                "expected_release_date": input_data.get("expected_release_date"),
                "idempotency_key": input_data.get("idempotency_key"),
                "note": input_data.get("note", ""),
                "source": "ai",
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


def _build_vest_undo_change(input_data: dict) -> tuple[Optional[dict], Optional[str]]:
    """Shared preview-build for the ``undo_vest`` confirm gate — the former body of
    ``propose_vest_undo`` (minus the ``idempotency_key`` it used to mint:
    ``equity_service._vest_undo`` never reads one — dead metadata, dropped here rather than
    carried forward). Returns ``(change, None)`` or ``(None, error_message)``."""
    vest_event_id = input_data.get("vest_event_id")
    ev = equity_service.vest_events.find_by_id(vest_event_id) if vest_event_id else None
    if not ev:
        return None, "vest event not found — use get_equity_report or list_grants"

    grant = equity_service.grants.find_by_id(ev.get("grant_id", ""))
    inst_id = (grant or {}).get("instrument_id", "")
    inst = equity_service.instruments.find_by_id(inst_id) if inst_id else None
    symbol = (inst or {}).get("symbol") or (inst or {}).get("name") or ""

    will_reverse: list[str] = [
        f"re-increment locked position by {ev.get('units')} units",
        f"delete FMV-basis lot (lot_id={ev.get('lot_id')}) — REFUSED if partially sold",
    ]
    txn_id = ev.get("txn_id")
    if txn_id:
        # Only vests recorded before 2026-09-22 carry one.
        will_reverse.append(f"soft-delete the legacy income txn (txn_id={txn_id})")
    will_reverse.append("flip tranche back to unvested")
    will_reverse.append("delete vest_event record")

    change = {
        "type": "vest_undo",
        "vest_event_id": vest_event_id,
        "display": {
            "symbol": symbol,
            "vest_event_id": vest_event_id,
            "grant_id": ev.get("grant_id"),
            "tranche_id": ev.get("tranche_id"),
            "vest_date": ev.get("date"),
            "units": ev.get("units"),
            "fmv_per_unit": ev.get("fmv_per_unit"),
            "fmv_currency": ev.get("fmv_currency"),
            "gross_gain_native": ev.get("gross_gain_native"),
            "txn_id": txn_id,
            "lot_id": ev.get("lot_id"),
            "will_reverse": will_reverse,
        },
    }
    return change, None


class ApplyVestUndoInput(ToolInput):
    """Schema-of-record for ``undo_vest`` (see ``schema_from_model``)."""

    vest_event_id: Optional[str] = Field(
        default=None,
        description="vest_event id from get_equity_report or list_grants.",
    )


@tool(
    "undo_vest",
    description=(
        "REVERSE a vest event — shows what will be undone before writing anything. Use "
        "get_equity_report or list_grants to find the vest_event_id. The undo reverses: "
        "locked-position re-increment, FMV lot deletion (REFUSED if the lot was partially sold "
        "— dispose/adjust manually first) or pending-units drain, and tranche flip back to "
        "unvested; a vest recorded before 2026-09-22 also gets its legacy income txn "
        "soft-deleted. If the call asks the user first, their answer runs it server-side with exactly the values shown — never call it again and never restate the values."
    ),
    input_schema=schema_of(ApplyVestUndoInput, tool_name="undo_vest"),
    mutates=("portfolio", "ledger"),
    # propose_vest_undo is the former FIRST HALF of this merge — see record_vest's aliases
    # note (identical rationale, harness reliability program 2026-07-19).
    self_stages=True,
)
async def undo_vest(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "undo_vest")
        if entry is None:
            return _gate.unknown_token_result("undo_vest")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    if gated or promised is not None:
        change, error = _build_vest_undo_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        if gated:
            return registry.stage_write(change)
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("undo_vest")

    try:
        result = equity_service.apply_equity_change(
            {"type": "vest_undo", "vest_event_id": input_data["vest_event_id"]}
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# ── Appendix 8B PDF import (L6 — stretch lane) ──────────────────────────────────
# On an interactive/connector turn: preview → confirm card → the server re-runs the tool on
# approval with the already-extracted payload. The user attaches the PDF in chat via
# attachment_ref; the tool resolves the bytes from the attachment store (same mechanism as
# import_transactions).


def _build_8b_import_change(input_data: dict) -> tuple[Optional[dict], Optional[str]]:
    """Shared preview-build for the ``import_8b`` confirm gate — the former body of
    ``propose_8b_import``. Runs the row EXTRACTION (an LLM call — slow and NOT guaranteed
    deterministic on a retry), so unlike every other pair in this lane there is no cheap
    outcome to re-derive at confirm time; the extracted ``payload`` returned here IS staged
    verbatim and a confirm never re-extracts (see ``import_8b``). Returns
    ``(change, None)`` or ``(None, error_message)``."""
    from backend.services.equity_import import EightBImportError, preview_8b_import

    content_bytes = _resolve_pdf_bytes(input_data)
    if isinstance(content_bytes, str):  # error string
        return None, content_bytes

    instrument_id = input_data.get("instrument_id") or None
    document_currency = str(input_data.get("document_currency") or "SGD")

    try:
        preview = preview_8b_import(
            content_bytes,
            instrument_id=instrument_id,
            document_currency=document_currency,
        )
    except EightBImportError as exc:
        return None, str(exc)

    n_grants = len(preview.get("proposed_grants") or [])
    n_vests = len(preview.get("proposed_vests") or [])
    n_unresolved = len(preview.get("unresolved_rows") or [])
    dropped = preview.get("rows_dropped", 0)

    summary_parts = [
        f"{preview.get('rows_extracted', 0)} vest rows extracted from {preview.get('raw_count', 0)} LLM-parsed rows",
    ]
    if dropped:
        summary_parts.append(f"{dropped} rows dropped (low confidence or validation error)")
    if n_grants:
        summary_parts.append(f"{n_grants} new grant(s) proposed")
    if n_vests:
        summary_parts.append(f"{n_vests} vest event(s) proposed")
    if n_unresolved:
        summary_parts.append(
            f"{n_unresolved} row(s) could not be matched to an instrument (use resolve_instruments to find the right instrument_id and re-run)"
        )

    display = {
        "summary": "; ".join(summary_parts),
        "raw_count": preview.get("raw_count"),
        "rows_extracted": preview.get("rows_extracted"),
        "rows_dropped": dropped,
        "proposed_grants": preview.get("proposed_grants"),
        "proposed_vests": preview.get("proposed_vests"),
        "unresolved_rows": preview.get("unresolved_rows"),
        "document_currency": preview.get("document_currency"),
        "file_sha256": preview.get("file_sha256"),
        "note": (
            "Proposed grants include ONLY historical vest rows. If the grant has future "
            "unvested tranches, add them manually after confirming this import."
        ),
    }

    change = {
        "type": "8b_import_commit",
        "payload": preview,
        "display": display,
    }
    return change, None


def _bare_object_schema(schema: dict) -> None:
    """``json_schema_extra`` callable for a free-form dict field whose hand-written
    literal is a BARE ``{"type": "object"}`` — no ``properties``, no
    ``additionalProperties`` at all. A plain ``Dict[str, Any]`` always emits
    ``additionalProperties: true`` (the "bare free-form dict" quirk class,
    ``schema_from_model.py``'s module docstring / ADR `2026-07-19-derive-tool-schemas-
    from-models`), a byte mismatch against that literal. Strips it from both the field's
    own schema and, for an Optional field, every ``anyOf`` branch — Pydantic attaches
    ``json_schema_extra`` to the OUTER ``anyOf[X, null]`` wrapper, not the inner ``X``
    branch, so a plain top-level pop alone would miss it whenever the field is optional."""
    schema.pop("additionalProperties", None)
    for branch in schema.get("anyOf", []):
        if isinstance(branch, dict):
            branch.pop("additionalProperties", None)


class Apply8bImportInput(ToolInput):
    """Schema-of-record for ``import_8b`` (see ``schema_from_model``)."""

    attachment_ref: Optional[str] = Field(
        default=None,
        description=(
            "The ref_id of the Appendix 8B PDF the user attached in chat (from the upload "
            "response). Mutually exclusive with statement_id. Omit both when `payload` is used "
            "instead."
        ),
    )
    statement_id: Optional[str] = Field(
        default=None,
        description=(
            "Statement ID (from list_statements), for an 8B already on file. Mutually "
            "exclusive with attachment_ref."
        ),
    )
    instrument_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional instrument id (from resolve_instruments) to use for all rows in the "
            "document. When omitted, instrument is resolved by fuzzy company-name match against "
            "existing instruments."
        ),
    )
    document_currency: Optional[str] = Field(
        default=None,
        description=(
            "Currency for all FMV values in the document (default 'SGD' — Appendix 8B is always "
            "in Singapore dollars). Change only when the employer's statement uses a different "
            "base currency."
        ),
    )
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "An already-extracted preview result to commit directly (e.g. an unattended flow "
            "with its own pre-extracted payload). The normal interactive flow never passes this "
            "by hand — an answered card re-runs the tool on the stored extraction."
        ),
        json_schema_extra=_bare_object_schema,
    )


@tool(
    "import_8b",
    description=(
        "Import an Appendix 8B ESOW statement from a PDF the user attached in chat. Use "
        "attachment_ref (the ref_id from the uploaded PDF in chat). Optionally pass "
        "instrument_id (from resolve_instruments) to pin the instrument for all rows — "
        "recommended when the company name in the PDF might not match the system instrument "
        "symbol exactly. NEVER auto-create an instrument; call resolve_instruments first if the "
        "company is not yet in the system. It extracts the rows via LLM first. If the call asks the user first, they see the proposed grants + vest events and their answer runs it server-side with exactly those values — never call it again and never restate the values. Creates proposed grants then applies each vest event through "
        "apply_equity_change. Idempotent: re-applying the same payload (same idempotency_key "
        "per vest row) is a safe no-op — the existing vest_event_ids are returned without "
        "duplicate writes."
    ),
    input_schema=schema_of(Apply8bImportInput, tool_name="import_8b"),
    mutates=("portfolio", "ledger"),
    # propose_8b_import is the former FIRST HALF of this merge (harness reliability
    # program, 2026-07-19 — see portfolio.py's remove_positions aliases note for the
    # general rationale). UNLIKE every other pair in this lane, the confirm here does NOT
    # re-verify an "outcome" against a freshly-rebuilt preview — the preview IS an LLM
    # extraction (slow, not guaranteed reproducible), so the STORED payload from the one
    # real extraction is what commits, never re-derived.
    self_stages=True,
)
async def import_8b(input_data: dict):
    from backend.services.equity_import import EightBImportError, commit_8b_import

    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "import_8b")
        if entry is None:
            return _gate.unknown_token_result("import_8b")
        input_data = _gate.staged_args(entry)
    else:
        gated = registry.should_preview_write()

    payload = input_data.get("payload")
    if not isinstance(payload, dict):
        # No payload yet — this is a fresh attachment_ref/statement_id call. Extract now
        # (the one real LLM call); if gated, STAGE the extracted payload (never re-extract
        # on confirm) — otherwise (unattended/automated, no card to show) commit the
        # freshly extracted payload immediately.
        change, error = await asyncio.to_thread(_build_8b_import_change, input_data)
        if error is not None:
            return json.dumps({"ok": False, "error": error}), None
        if gated:
            return registry.stage_write(
                change,
                # The one override (see stage_write): approval must commit the ALREADY
                # extracted rows, never re-run the LLM extraction on the attachment ref.
                args={"payload": change["payload"]},
            )
        payload = change["payload"]

    try:
        result = commit_8b_import(payload)
    except EightBImportError as exc:
        return json.dumps({"ok": False, "error": str(exc)}), None
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


def _resolve_pdf_bytes(input_data: dict) -> bytes | str:
    """Resolve PDF bytes from attachment_ref or statement_id. Returns bytes on success or
    an error string on failure (so the tool can return the error without raising)."""
    ref = (input_data.get("attachment_ref") or "").strip()
    sid = (input_data.get("statement_id") or "").strip()

    if ref and sid:
        return "provide either attachment_ref or statement_id, not both"

    if ref:
        try:
            from backend.services.attachments import get_attachment_import_source, private_refusal

            refusal = private_refusal(ref)
            if refusal:
                return refusal
            src = get_attachment_import_source(ref)
            if src is None:
                from backend.ai.tools.imports import _unimportable_attachment

                return _unimportable_attachment(ref)
            cached_bytes, _ = src
            return cached_bytes
        except Exception as exc:
            return f"failed to read attachment: {exc}"

    if sid:
        try:
            from backend.db.statement_store import StatementStore
            from backend.paths import DATA_DIR

            record = StatementStore().find_by_id(sid)
            if record is None:
                return f"Statement {sid!r} not found — call list_statements."
            fp = record.get("file_path", "")
            if not fp:
                return f"Statement {sid!r} has no file path — re-upload the PDF."
            disk = DATA_DIR / fp
            if not disk.exists():
                return f"Statement {sid!r} file missing from disk — re-upload the PDF."
            return disk.read_bytes()
        except Exception as exc:
            return f"failed to read statement: {exc}"

    return "provide attachment_ref (just uploaded) or statement_id (from list_statements)"
