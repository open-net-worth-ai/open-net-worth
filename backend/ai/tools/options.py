"""Option-contract tools (options-e2e, completing the sys-options-data-model RFC).

Lucius reads the options lens and, on an interactive/connector turn, PREVIEWS opening or
closing a contract before writing it (both MOVE money when a premium cash leg is booked):

  * ``get_options`` / ``get_option_contract`` — reads (proposed_change = None).
  * ``open_option`` / ``close_option`` — ONE tool per write (harness
    reliability program, 2026-07-19 — collapses the former ``propose_open_option``/
    ``open_option`` etc. pairs; see ``portfolio.py``'s ``remove_positions`` for
    the full rationale). On a card-capable/attended turn: PREVIEWS (validates
    underlying/account, dry-runs the choke-point validation, pre-checks the double-book
    guard, computes total premium/CSP collateral or the buy-to-close/sell-to-close cash
    leg), returns a ``proposed_change``, writes NOTHING and PAUSES on its own
    ``tool_use_id``. ``assistant.resolve_pending_tool_use`` re-runs it on approval with the
    STORED args (fully resolved ids) — routes through
    ``options_service.apply_option_change({type:"open"|"close",...})`` (idempotent on
    idempotency_key; double-book guarded; optional premium cash leg via the ledger choke
    point). An unattended/automated caller writes immediately, no card.
  * ``update_option`` / ``cancel_option`` — metadata edit / mistaken-entry delete;
    direct writes (no cash legs), in ``option_writes``.

ALL option math and lifecycle logic live in ``services/options.py`` — this
module only marshals I/O and shapes the confirm card.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError

_OptionType = Literal["call", "put"]
_OptionSide = Literal["long", "short"]


class ApplyOpenOptionInput(ToolInput):
    """Schema-of-record for ``open_option`` (see ``schema_from_model``)."""

    underlying: Optional[str] = Field(
        default=None,
        description="Instrument id or symbol. Alias for underlying_instrument_id — pass either one.",
    )
    underlying_instrument_id: Optional[str] = Field(
        default=None, description="Instrument id or symbol. Required unless `underlying`."
    )
    account_id: Optional[str] = Field(
        default=None, description="Securities account id (or exact name)"
    )
    option_type: Optional[_OptionType] = Field(
        default=None,
        description=(
            "'call' or 'put' — the right the contract confers, independent of `side` (which "
            "says whether the user holds or wrote it). A short put is a cash-secured put; a "
            "short call is a covered call."
        ),
    )
    side: Optional[_OptionSide] = Field(
        default=None, description="short = written/sold (a liability)"
    )
    strike: Optional[float] = Field(
        default=None,
        description=(
            "Strike price per SHARE, in the underlying's quote currency — not multiplied by the "
            "contract size. Drives intrinsic value and, for a CSP, the required collateral "
            "(strike × contracts × multiplier)."
        ),
    )
    expiry: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    contracts: Optional[float] = Field(
        default=None,
        description=(
            "Number of CONTRACTS, not shares — 1 contract at the default multiplier is 100 "
            "shares of exposure. Always positive; `side` carries the direction."
        ),
    )
    multiplier: Optional[int] = Field(default=None, description="Shares/contract, default 100")
    premium_per_share: Optional[float] = Field(
        default=None, description="Per SHARE (OCC): 2.50 on 1 contract = 250 cash"
    )
    premium_currency: Optional[str] = Field(
        default=None,
        description=(
            "3-letter ISO currency of `premium_per_share` and of the booked premium cash leg. "
            "Defaults to the underlying instrument's quote_currency — only pass it when the "
            "contract genuinely settles in a different currency."
        ),
    )
    open_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    strategy_tag: Optional[str] = Field(
        default=None, description="covered_call | csp | long_call | long_put | protective_put | …"
    )
    collateral_account_id: Optional[str] = Field(
        default=None, description="CSP cash wallet / covered-call share account"
    )
    collateral_amount: Optional[float] = Field(
        default=None, description="CSP cash collateral; omit otherwise"
    )
    premium_account_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional wallet for the premium leg (short→income, long→expense); omitted = no "
            "cash leg"
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note stored on the contract (why the trade was put on, the thesis, the "
            "roll it came from). Display only — never read by marks, collateral checks, or the "
            "expiry sweep. Defaults to empty."
        ),
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Caller-supplied dedup key. A second call with the SAME key returns the original "
            "contract and writes nothing — no second premium cash leg. Pass the broker order id "
            "on any automated flow. If you omit it, one is minted server-side and stored with "
            "the staged confirm, so confirming a card twice still cannot double-book."
        ),
    )
    allow_duplicate: Optional[bool] = Field(
        default=None, description="Only if the user said it is a genuine same-signature second fill"
    )


def _resolve_underlying(ref: str) -> dict | str:
    """Accept an instrument id or a symbol; return the instrument dict or an error string."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.find_by_id(ref or "")
    if inst:
        return inst
    up = (ref or "").strip().upper()
    matches = [i for i in store.all() if (i.get("symbol") or "").upper() == up]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return f"unknown underlying {ref!r} — the contract must reference an existing instrument; call resolve_instrument (or get_portfolio) first"
    return f"ambiguous symbol {ref!r} ({len(matches)} instruments) — pass the instrument id"


def _resolve_account(ref: str) -> str | dict:
    """Accept an account id or an exact (case-insensitive) name; id string or {'error': ...}."""
    from backend.db.account_store import AccountStore

    store = AccountStore()
    if store.find_by_id(ref or ""):
        return ref
    name_lower = (ref or "").strip().lower()
    exact = [
        a
        for a in store.all()
        if not a.get("archived") and (a.get("name") or "").lower() == name_lower
    ]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(repr(a["name"]) for a in exact)
        return {
            "error": f"ambiguous account {ref!r} — pass account_id from list_accounts ({names})"
        }
    return {"error": f"unknown account {ref!r} — pass account_id from list_accounts"}


def _resolve_wallet(ref: str, field: str) -> tuple[str, str] | dict:
    """Resolve a cash-wallet ref (id or exact name) to (id, name).

    The ledger's generic income/expense path never validates account existence,
    so an unresolved wallet would book an orphaned cash leg that lands on no
    account's balance — fail CLOSED here instead (security review 2026-07-12)."""
    from backend.db.account_store import AccountStore

    resolved = _resolve_account(ref)
    if isinstance(resolved, dict):
        return {"error": f"{field}: {resolved['error']}"}
    acct = AccountStore().find_by_id(resolved)
    return resolved, (acct.get("name") if acct else resolved) or resolved


def _contract_line(c: dict, symbol: str | None = None) -> str:
    sign = "−" if (c.get("side") or "") == "short" else "+"
    sym = symbol or c.get("underlying_symbol") or c.get("underlying_instrument_id") or "?"
    return (
        f"{sign}{c.get('contracts')} {sym} {c.get('option_type', '').upper()} "
        f"@ {c.get('strike')} exp {c.get('expiry')}"
    )


# ── Reads ─────────────────────────────────────────────────────────────────────


class GetOptionsInput(ToolInput):
    """Schema-of-record for ``get_options`` (see ``schema_from_model``)."""


@tool(
    "get_options",
    description=(
        "Options lens: every open contract valued/signed in base — marks, ITM/OTM, "
        "days-to-expiry, premium, by-strategy split, expiry buckets, CSP encumbered cash, "
        "covered-call coverage. Answers premium/expiry/assignment-risk questions."
    ),
    input_schema=schema_of(GetOptionsInput, tool_name="get_options"),
)
async def get_options(input_data: dict):
    from backend.services import options as options_service

    return json.dumps(options_service.summary(), ensure_ascii=False, default=str), None


class GetOptionContractInput(ToolInput):
    """Schema-of-record for ``get_option_contract`` (see ``schema_from_model``)."""

    contract_id: str = Field(
        description="The option contract to read, by its stored id (from get_options)."
    )


@tool(
    "get_option_contract",
    description=(
        "One hydrated option contract: mark value (signed, base currency), moneyness, intrinsic "
        "per share, days to expiry, premium and collateral figures."
    ),
    input_schema=schema_of(GetOptionContractInput, tool_name="get_option_contract"),
)
async def get_option_contract(input_data: dict):
    from backend.services import options as options_service

    contract = options_service.get_contract(input_data["contract_id"])
    if contract is None:
        return json.dumps({"error": "option contract not found"}), None
    return json.dumps(contract, ensure_ascii=False, default=str), None


# ── Open: propose → apply ─────────────────────────────────────────────────────


def _build_open_option_change(input_data: dict) -> tuple[Optional[dict], Optional[tuple]]:
    """Shared preview-build for the ``open_option`` confirm gate — the former body
    of ``propose_open_option``. Called only when previewing or re-verifying a confirm's
    outcome. Returns ``(change, None)`` — a FULLY RESOLVED change (real account/underlying/
    wallet ids, defaulted premium_currency) plus its ``display`` — or, on a validation
    refusal, ``(None, ready_to_return_tool_result_tuple)`` preserving the exact error
    shape each resolution step already produces."""
    from backend.db.option_contract_store import OptionContractStore
    from backend.services import options as options_service

    inst = _resolve_underlying(
        input_data.get("underlying_instrument_id") or input_data.get("underlying") or ""
    )
    if isinstance(inst, str):
        return None, (json.dumps({"error": inst}), None)
    account = _resolve_account(input_data.get("account_id") or "")
    if isinstance(account, dict):
        return None, (json.dumps(account), None)

    change = {
        k: v
        for k, v in input_data.items()
        if k not in ("underlying", "underlying_instrument_id", _gate.CONFIRM_TOKEN_KEY)
        and v is not None
    }
    change["account_id"] = account
    change["underlying_instrument_id"] = inst["id"]
    change.setdefault("premium_currency", inst.get("quote_currency") or "USD")
    # Mint once if the caller didn't supply one — mirrors propose_open_option's own old
    # unconditional minting, now baked into the STORED args (see open_option) instead
    # of trusting the model to echo it back on the eventual apply/confirm call.
    if not change.get("idempotency_key"):
        change["idempotency_key"] = str(uuid.uuid4())

    wallet_name: str | None = None
    if change.get("premium_account_id"):
        wallet = _resolve_wallet(change["premium_account_id"], "premium_account_id")
        if isinstance(wallet, dict):
            return None, (json.dumps(wallet), None)
        change["premium_account_id"], wallet_name = wallet
    if change.get("collateral_account_id"):
        coll = _resolve_wallet(change["collateral_account_id"], "collateral_account_id")
        if isinstance(coll, dict):
            return None, (json.dumps(coll), None)
        change["collateral_account_id"] = coll[0]

    err = options_service._validate_open(change)
    if err:
        return None, (json.dumps({"error": err}), None)

    total_premium = options_service.total_premium_native(change)
    currency = change["premium_currency"]
    side = change["side"]

    warnings: list[str] = []
    dup = options_service._find_duplicate_open(OptionContractStore(), change)
    if dup:
        warnings.append(
            f"an OPEN contract with this exact signature already exists ({dup['id']}) — apply will refuse unless allow_duplicate=true"
        )

    what_will_post: list[str] = [f"option contract: {_contract_line(change, inst.get('symbol'))}"]
    if change.get("premium_account_id"):
        if side == "short":
            what_will_post.append(
                f"income txn: premium received {total_premium} {currency} → {wallet_name} (no taxability stamp — the write path records facts only)"
            )
        else:
            what_will_post.append(
                f"expense txn: premium paid {total_premium} {currency} from {wallet_name}"
            )
    else:
        what_will_post.append("no cash leg (premium_account_id omitted)")
    collateral = options_service.csp_collateral_native(change)
    if collateral is not None:
        what_will_post.append(
            f"CSP collateral reference: {collateral} "
            f"{currency} (surfaced as encumbered cash — never moved)"
        )

    change["type"] = "open_option"
    change["display"] = {
        "contract": _contract_line(change, inst.get("symbol")),
        "underlying_symbol": inst.get("symbol"),
        "side": side,
        "strategy_tag": change.get("strategy_tag") or "",
        "total_premium": total_premium,
        "premium_currency": currency,
        "warnings": warnings,
        "what_will_post": what_will_post,
    }
    return change, None


@tool(
    "open_option",
    description=(
        "Open an option contract (covered call, CSP, long call/put). Validates, pre-checks the "
        "double-book guard, computes total premium + CSP collateral. If the call asks the user first, their answer runs it server-side with exactly the values shown — never call it again and never restate the values. Pass idempotency_key so a retry is a safe no-op. Optional premium cash leg books through the ledger."
    ),
    input_schema=schema_of(ApplyOpenOptionInput, tool_name="open_option"),
    mutates=("options", "portfolio", "ledger"),
    # propose_open_option is the former FIRST HALF of this merge (harness reliability
    # program, 2026-07-19 — see portfolio.py's remove_positions aliases note for the
    # full rationale). propose_open_option used to MINT a fresh idempotency_key into every
    # proposal (its own schema had no such field) and rely on the model copying it into the
    # apply call; the merge does the SAME minting server-side (_build_open_option_change)
    # instead of trusting the model to echo it back — a caller-supplied key is honored.
    self_stages=True,
)
async def open_option(input_data: dict):
    from backend.services import options as options_service

    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "open_option")
        if entry is None:
            return _gate.unknown_token_result("open_option")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    if gated or promised is not None:
        change, err_result = _build_open_option_change(input_data)
        if err_result is not None:
            return err_result
        # The resolved change (minus type/display) IS the args to replay — the final
        # resolution below then re-confirms already-resolved ids (a cheap no-op).
        input_data = {k: v for k, v in change.items() if k not in ("type", "display")}
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # e.g. another open of the same signature could have landed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("open_option")

    inst = _resolve_underlying(
        input_data.get("underlying_instrument_id") or input_data.get("underlying") or ""
    )
    if isinstance(inst, str):
        return json.dumps({"error": inst}), None
    account = _resolve_account(input_data.get("account_id") or "")
    if isinstance(account, dict):
        return json.dumps(account), None
    change = {
        k: v
        for k, v in input_data.items()
        if k not in ("underlying", "underlying_instrument_id") and v is not None
    }
    change["account_id"] = account
    change["underlying_instrument_id"] = inst["id"]
    for field in ("premium_account_id", "collateral_account_id"):
        if change.get(field):
            wallet = _resolve_wallet(change[field], field)
            if isinstance(wallet, dict):
                return json.dumps(wallet), None
            change[field] = wallet[0]
    change["type"] = "open"
    try:
        result = options_service.apply_option_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# ── Close: propose → apply ────────────────────────────────────────────────────


def _build_close_option_change(input_data: dict) -> tuple[Optional[dict], Optional[tuple]]:
    """Shared preview-build for the ``close_option`` confirm gate — the former body
    of ``propose_close_option``. Called only when previewing or re-verifying a confirm's
    outcome. Returns ``(change, None)`` or, on a refused/not-found contract,
    ``(None, ready_to_return_tool_result_tuple)``."""
    from backend.services import options as options_service

    contract_id = input_data.get("contract_id")
    contract = options_service.get_contract(contract_id) if contract_id else None
    if contract is None:
        return None, (json.dumps({"error": "option contract not found"}), None)
    status = (contract.get("status") or "open").lower()
    if status != "open":
        return None, (
            json.dumps({"error": f"contract status is '{status}' — only open contracts close"}),
            None,
        )

    side = (contract.get("side") or "long").lower()
    what_will_post = [f"contract closed: {_contract_line(contract)}"]
    pps = input_data.get("close_premium_per_share")
    wallet_id = input_data.get("premium_account_id")
    wallet_name: str | None = None
    if wallet_id is not None and pps is not None:
        wallet = _resolve_wallet(wallet_id, "premium_account_id")
        if isinstance(wallet, dict):
            return None, (json.dumps(wallet), None)
        wallet_id, wallet_name = wallet
        total = round(
            float(contract.get("contracts") or 0)
            * int(contract.get("multiplier") or 100)
            * float(pps),
            2,
        )
        currency = (contract.get("premium_currency") or "USD").upper()
        if side == "short":
            what_will_post.append(
                f"expense txn: buy-to-close {total} {currency} from {wallet_name}"
            )
        else:
            what_will_post.append(f"income txn: sell-to-close {total} {currency} → {wallet_name}")
    else:
        what_will_post.append("no cash leg (status flip only)")

    change = {
        "type": "close_option",
        "contract_id": contract_id,
        "closed_at": input_data.get("closed_at"),
        "close_premium_per_share": pps,
        "premium_account_id": wallet_id,
        "display": {
            "contract": _contract_line(contract),
            "side": side,
            "mark_value_base": contract.get("mark_value_base"),
            "what_will_post": what_will_post,
        },
    }
    return change, None


class ApplyCloseOptionInput(ToolInput):
    """Schema-of-record for ``close_option`` (see ``schema_from_model``)."""

    contract_id: Optional[str] = Field(default=None, description="contract_id from get_options.")
    closed_at: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (default today)")
    close_premium_per_share: Optional[float] = Field(
        default=None, description="Buyback / sell-to-close price PER SHARE"
    )
    premium_account_id: Optional[str] = Field(
        default=None, description="Cash wallet the close leg hits; omitted → no cash leg"
    )


@tool(
    "close_option",
    description=(
        "Close an open option contract. Optional close_premium_per_share + premium_account_id "
        "book the cash leg (short → buy-to-close expense; long → sell-to-close income); omitted "
        "= status flip only. If the call asks the user first, their answer runs it server-side with exactly the values shown — never call it again and never restate the values. Optional "
        "cash leg books BEFORE the status flip; re-closing is an idempotent no-op (never "
        "double-books)."
    ),
    input_schema=schema_of(ApplyCloseOptionInput, tool_name="close_option"),
    mutates=("options", "portfolio", "ledger"),
    # propose_close_option is the former FIRST HALF of this merge — see
    # open_option's aliases note (identical rationale, harness reliability program
    # 2026-07-19).
    self_stages=True,
)
async def close_option(input_data: dict):
    from backend.services import options as options_service

    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "close_option")
        if entry is None:
            return _gate.unknown_token_result("close_option")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    if gated or promised is not None:
        change, err_result = _build_close_option_change(input_data)
        if err_result is not None:
            return err_result
        input_data = {k: v for k, v in change.items() if k not in ("type", "display")}
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # the contract's status could have changed (e.g. already closed) since.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("close_option")

    change = {k: v for k, v in input_data.items() if v is not None}
    if change.get("premium_account_id"):
        wallet = _resolve_wallet(change["premium_account_id"], "premium_account_id")
        if isinstance(wallet, dict):
            return json.dumps(wallet), None
        change["premium_account_id"] = wallet[0]
    change["type"] = "close"
    try:
        result = options_service.apply_option_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# ── Metadata edit / mistaken-entry delete ─────────────────────────────────────


class UpdateOptionInput(ToolInput):
    """Schema-of-record for ``update_option`` (see ``schema_from_model``)."""

    contract_id: str = Field(
        description="The option contract to edit, by its stored id (from get_options)."
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Replacement free-form note on the contract (replaces the whole note, never "
            "appends). Omit to leave it unchanged."
        ),
    )
    strategy_tag: Optional[str] = Field(
        default=None,
        description=(
            "Strategy label: covered_call | csp | long_call | long_put | protective_put | …. "
            "NOT inert — 'csp' is what makes the contract report a required cash collateral "
            "(strike × contracts × multiplier); any other tag reports none. Omit to leave it "
            "unchanged."
        ),
    )
    collateral_account_id: Optional[str] = Field(
        default=None,
        description=(
            "Account backing the position (id or exact name): the cash wallet for a CSP, the "
            "account holding the shares for a covered call. Wiring it is what lets a short call "
            "report covered/uncovered instead of 'unknown'. Omit to leave it unchanged."
        ),
    )
    collateral_amount: Optional[float] = Field(
        default=None,
        description=(
            "Cash actually set aside as collateral, in the premium currency. Recorded as stated "
            "— it is not checked against the CSP requirement the contract computes for itself. "
            "Omit to leave it unchanged."
        ),
    )
    open_date: Optional[str] = Field(
        default=None,
        description=(
            "Corrected trade-open date, YYYY-MM-DD. Metadata only — this does NOT re-date or "
            "re-price the premium cash leg already booked. Omit to leave it unchanged."
        ),
    )


@tool(
    "update_option",
    description=(
        "Edit option metadata (note, strategy_tag, collateral linkage, open_date). NEVER edits "
        "economics or derived marks — delete and re-open a mistake instead."
    ),
    input_schema=schema_of(UpdateOptionInput, tool_name="update_option"),
    mutates=("options",),
)
async def update_option(input_data: dict):
    from backend.services import options as options_service

    change = {k: v for k, v in input_data.items() if v is not None}
    if change.get("collateral_account_id"):
        wallet = _resolve_wallet(change["collateral_account_id"], "collateral_account_id")
        if isinstance(wallet, dict):
            return json.dumps(wallet), None
        change["collateral_account_id"] = wallet[0]
    change["type"] = "update"
    try:
        result = options_service.apply_option_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


class CancelOptionInput(ToolInput):
    """Schema-of-record for ``cancel_option`` (see ``schema_from_model``)."""

    contract_id: str = Field(
        description=(
            "The option contract to delete, by its stored id (from get_options). The contract "
            "row is removed outright; any premium cash leg already booked is NOT reversed."
        )
    )
    force: Optional[bool] = Field(
        default=None,
        description=(
            "Default false — only an 'open' contract may be deleted, and a "
            "closed/expired/assigned/exercised one is refused with its status. Pass true to "
            "delete a non-open contract anyway, which discards settled history: do it only for "
            "an entry the user has said was a genuine mistake."
        ),
    )


@tool(
    "cancel_option",
    description=(
        "Delete a MISTAKEN option entry (open only, unless force=true). Books/reverses no cash "
        "leg — a genuine exit goes through close_option."
    ),
    input_schema=schema_of(CancelOptionInput, tool_name="cancel_option"),
    mutates=("options", "portfolio"),
)
async def cancel_option(input_data: dict):
    from backend.services import options as options_service

    change = {
        "type": "delete",
        "contract_id": input_data["contract_id"],
        "force": bool(input_data.get("force")),
    }
    try:
        result = options_service.apply_option_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None
