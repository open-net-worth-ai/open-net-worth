"""The provider-agnostic FETCH service — RFC ``connectors-as-data-sources`` §A.

``fetch(provider_key, ...)`` is the one function every ``<provider>_fetch_transactions`` /
``alpaca_fetch_positions`` / ``ibkr_fetch_activity`` tool calls. It knows nothing about any
ONE provider's wire protocol (that lives in ``services.aggregation.<provider>.fetch_window``)
— it only orchestrates the eleven steps every provider shares: the dormant gate, the window
default, sanitizing provider strings, stashing raw evidence, resolving the provider-account
link, filtering already-booked rows, detecting a vanished row, warming the FX cache, and
recording status. Booking a row is never this module's job — the tool hands the model's
own ``add_transactions``/position-tool call the drafts this returns, and the LEDGER CHOKE
(not this service) is what makes a re-fetch idempotent.

``provider_accounts(provider_key)`` is the lighter sibling ``api/connectors_api.py``'s
``GET /api/connectors/{id}/accounts`` route lazily imports (RFC §D) — the account list
only, no transaction fetch, so opening the Connectors tab's link picker never pays for a
full transaction pull.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math stays inside the ledger choke (``add_transactions``);
this module only shapes what a Lucius turn sees before it decides to call that choke.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from backend.core.errors import ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.core.user_time import user_today
from backend.models.account import AccountType
from backend.services import ledger as ledger_service
from backend.services.aggregation import providers
from backend.services.aggregation.providers import FetchWindow, Provider, UnknownProvider

logger = logging.getLogger(__name__)

#: Hard cap on a single fetch — bounded AT the tool/service boundary regardless of what a
#: caller asks for (CLAUDE.md: "Tool results are bounded AT THE TOOL — filter before you cap").
_HARD_MAX_LIMIT = 500
_DEFAULT_LIMIT = 200
# S-F6: a hard ceiling on how many rows ONE fetch call will stash/dedup-check/diff, applied
# BEFORE any of that work — independent of (and much larger than) `limit`/`_HARD_MAX_LIMIT`
# above, which only bounds what is RETURNED. Without this, a wide date window against a
# provider with a large history turns one call into thousands of individual
# snapshot-store writes (each its own DB transaction + checkpoint hook) and a
# proportionally expensive note_missing/dedup pass. Hitting it marks the window incomplete
# — the same signal a paginated provider's own truncation already produces — never a
# silent drop.
_MAX_FETCH_ROWS = 5000

# Text fields a provider supplies that reach the model — sanitized on every row/account
# (RFC §A step 4, AC9). Identity fields (external_refs/provider_account_id) are NOT in this
# list: they are structural keys the ledger dedup floor and the snapshot store key off of,
# never rendered as prose, and must stay byte-stable across re-fetches of the same row.
_ROW_TEXT_FIELDS = ("description", "memo", "currency")
_ACCOUNT_TEXT_FIELDS = ("name", "mask", "currency", "institution")
_POSITION_TEXT_FIELDS = ("symbol", "currency")
# Cap on any single string leaf inside a stashed `raw` payload (S-F3) — generous enough for
# a real provider field, small enough that a hostile/malformed response can't balloon a
# snapshot row. Mirrors sanitize_untrusted's own default cap order of magnitude.
_RAW_LEAF_MAX_LEN = 2000
_RAW_MAX_DEPTH = 8
# Cap on an identity field (provider_account_id / external_refs[].ref) — S-F4, mirrors
# email_snapshots.py capping raw_message_id the same way. Never sanitized (RFC §A step 4's
# own carve-out: these are structural keys, not prose), only bounded.
_ID_MAX_LEN = 512


def _resolve_provider(provider_key: str) -> Provider:
    try:
        return providers.resolve([provider_key])[0]
    except UnknownProvider:
        raise


def _window_defaults(
    provider: Provider, since: Optional[str], before: Optional[str]
) -> tuple[str, str]:
    today = user_today()
    resolved_before = before or today.isoformat()
    resolved_since = since or (today - timedelta(days=provider.lookback_days)).isoformat()
    return resolved_since, resolved_before


def _cap_identity_fields(out: dict) -> None:
    """Cap (never sanitize — these are structural keys, RFC §A step 4's own carve-out)
    ``provider_account_id``/``external_refs[].ref`` at ``_ID_MAX_LEN`` (S-F4; mirrors
    ``email_snapshots.py`` capping ``raw_message_id`` the same way) — a hostile/malformed
    provider response must not be able to balloon an identity key the dedup floor and the
    snapshot store index on."""
    pid = out.get("provider_account_id")
    if isinstance(pid, str) and len(pid) > _ID_MAX_LEN:
        out["provider_account_id"] = pid[:_ID_MAX_LEN]
    refs = out.get("external_refs")
    if isinstance(refs, list):
        capped = []
        for entry in refs:
            if isinstance(entry, dict) and isinstance(entry.get("ref"), str):
                entry = {**entry, "ref": entry["ref"][:_ID_MAX_LEN]}
            capped.append(entry)
        out["external_refs"] = capped


def _sanitize_row(row: dict) -> dict:
    out = dict(row)
    for field in _ROW_TEXT_FIELDS:
        value = out.get(field)
        if isinstance(value, str):
            out[field] = sanitize_untrusted(value)
    _cap_identity_fields(out)
    return out


def _known_account_type(value: object) -> str | None:
    """A provider's suggested app account type, kept only if it is one of the seven."""
    return value if isinstance(value, str) and value in AccountType.__members__ else None


def _sanitize_account(account: dict) -> dict:
    out = dict(account)
    for field in _ACCOUNT_TEXT_FIELDS:
        value = out.get(field)
        if isinstance(value, str):
            out[field] = sanitize_untrusted(value)
    _cap_identity_fields(out)
    return out


def _sanitize_extra(extra: dict) -> dict:
    """Sanitize the broker-only ``extra`` channel's own text fields (S-F4) —
    ``extra["positions"][*].symbol``/``.currency``. ``broker_fetch.py`` copies this dict's
    ``positions`` list through to the model largely verbatim, so those two fields are
    exactly as untrusted as any row/account text field this module already defangs;
    ``cash``/``buying_power`` are provider-formatted numeric strings, not freeform prose,
    and are left alone. A no-op for a bank fetch, whose ``window.extra`` is always empty."""
    if not extra:
        return extra
    out = dict(extra)
    positions = out.get("positions")
    if isinstance(positions, list):
        sanitized = []
        for pos in positions:
            if not isinstance(pos, dict):
                sanitized.append(pos)
                continue
            clean = dict(pos)
            for field in _POSITION_TEXT_FIELDS:
                value = clean.get(field)
                if isinstance(value, str):
                    clean[field] = sanitize_untrusted(value)
            _cap_identity_fields(clean)
            sanitized.append(clean)
        out["positions"] = sanitized
    return out


def _sanitize_raw(value: object, *, _depth: int = 0) -> object:
    """Recursively sanitize every STRING LEAF of a provider's raw evidence dict (S-F3) —
    ``raw`` is stashed unsanitized-and-unbounded today, but it is readable by Lucius via
    ``run_sql_query`` against the ``connector_snapshots`` table, so it is exactly as
    untrusted as any other provider-supplied string this module already defangs (RFC §A
    step 4, AC9). Caps each string at ``_RAW_LEAF_MAX_LEN`` and depth at a small constant
    (a provider response is a flat-ish JSON object; a pathological nesting bomb must not
    turn one row into an expensive walk). Dict keys are left alone — they are a fixed,
    provider-documented vocabulary, never freeform prose."""
    if _depth > _RAW_MAX_DEPTH:
        return None
    if isinstance(value, str):
        return sanitize_untrusted(value, max_len=_RAW_LEAF_MAX_LEN)
    if isinstance(value, dict):
        return {k: _sanitize_raw(v, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_raw(v, _depth=_depth + 1) for v in value]
    return value


def _row_ref(row: dict) -> str:
    refs = row.get("external_refs") or []
    return str(refs[0]["ref"]) if refs and isinstance(refs[0], dict) else ""


def _resolve_account_ref(account: str, accounts: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """Resolve a caller-supplied ``account`` — a provider account id OR a NAME, resolved
    server-side (RFC §A's own promise) — against the provider's own account list. Id match
    wins outright; otherwise a case-insensitive name match. Returns
    ``(resolved_provider_account_id, None)`` on a clean match, or ``(None, error_message)``
    on ambiguity or no match — always naming the real candidates, never a silent guess."""
    ids = {a.get("provider_account_id") for a in accounts if a.get("provider_account_id")}
    if account in ids:
        return account, None
    needle = account.strip().lower()
    name_matches = [
        a
        for a in accounts
        if a.get("provider_account_id") and str(a.get("name") or "").strip().lower() == needle
    ]
    if len(name_matches) == 1:
        return name_matches[0]["provider_account_id"], None
    if len(name_matches) > 1:
        candidates = ", ".join(sorted(a["provider_account_id"] for a in name_matches))
        return None, (
            f"'{account}' matches more than one account by name: {candidates}. "
            "Use the provider_account_id instead."
        )
    valid = ", ".join(sorted(ids)) or "(none — no accounts visible to this connector)"
    return None, f"no account matches '{account}'. Valid provider account ids: {valid}"


def _linked_accounts_by_provider_id(provider_key: str) -> dict[str, dict]:
    """``{provider_account_id: {name, mask, linked_account_id}}`` from
    ``Account.external_ids`` — reuses ``services.accounts.linked_accounts_for_source`` (L1)
    rather than re-deriving the same scan, so every caller of that data (this service's
    ``fetch()``, ``provider_accounts()``'s union/fallback) agrees on one definition."""
    from backend.services import accounts as accounts_service

    return {
        a["provider_account_id"]: a
        for a in accounts_service.linked_accounts_for_source(provider_key)
        if a.get("provider_account_id")
    }


def _linked_account_ids(provider_key: str) -> dict[str, str]:
    """``{provider_account_id: linked app account id}`` — the narrow projection ``fetch()``
    needs onto each fetched row/account."""
    return {
        pid: info["linked_account_id"]
        for pid, info in _linked_accounts_by_provider_id(provider_key).items()
    }


def _linked_only_accounts(linked: dict[str, dict]) -> list[dict]:
    """The LINKED-only account view (``currency``/``balance`` always ``None`` — no live
    provider call backs them) — what ``provider_accounts()`` returns when the provider call
    itself can't run (dormant/disabled) or failed, so a link made before the provider went
    quiet still shows up in the picker instead of vanishing (C-B2)."""
    return [
        _sanitize_account(
            {
                "provider_account_id": pid,
                "name": info.get("name") or "",
                "mask": info.get("mask") or "",
                "currency": None,
                "balance": None,
            }
        )
        | {
            "account_type": None,
            "institution": None,
            "linked_account_id": info.get("linked_account_id"),
        }
        for pid, info in linked.items()
    ]


def _warm_fx(currencies: set[str]) -> None:
    """Best-effort FX warm-up for the currencies this fetch returned (RFC §A step 9) — the
    real live-rate fetch (``services.prices.fetch_live``, which refreshes ``fx_cache.json``),
    not the old ingestion orchestrator's ``ctx.fx_warm`` seam (verified in-lane: every bank
    adapter's ``fx_warm`` was ``getattr(adapter, "fx_warm", lambda _c: None)`` — a permanent
    no-op, since no adapter ever defined the attribute; only this module now actually calls a
    live-fetch path). An unresolved pair now refuses conversion at the booking choke;
    this warm-up lets a normal import proceed without inventing an exchange rate."""
    base = ledger_service.base_currency()
    wanted = sorted(c for c in currencies if c and c != base)
    if not wanted:
        return
    try:
        from backend.services.prices import fetch_live

        fetch_live([], wanted, record_history=False)
    except Exception:
        logger.warning("connector_fetch: FX warm-up failed for %s", wanted, exc_info=True)


def _empty_result(
    status: str,
    error: Optional[str],
    since: str,
    before: str,
    lookback_days: int,
    *,
    complete: bool = True,
) -> dict:
    return {
        "status": status,
        "error": error,
        "window": {
            "since": since,
            "before": before,
            "lookback_days": lookback_days,
            "complete": complete,
        },
        "accounts": [],
        "transactions": [],
        "changes": [],
        "counts": {
            "fetched": 0,
            "already_booked": 0,
            "returned": 0,
            "truncated": False,
            "changes": 0,
        },
    }


def fetch(
    provider_key: str,
    *,
    since: Optional[str] = None,
    before: Optional[str] = None,
    account: Optional[str] = None,
    include_booked: bool = False,
    include_pending: bool = False,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """Fetch + normalize + stash + link + filter one provider's window (RFC §A). NEVER
    raises for a provider-side failure — a dormant connector, an auth/rate/connect error,
    and a genuine transport exception all come back as ``status`` inside the result, exactly
    like the retired ingestion orchestrator's contract. Raises ``UnknownProvider`` only for a
    caller-side bug (an unrecognized ``provider_key`` — every real tool call passes a fixed,
    valid one)."""
    provider = _resolve_provider(provider_key)
    store = provider.store()
    since_resolved, before_resolved = _window_defaults(provider, since, before)
    limit = max(1, min(_HARD_MAX_LIMIT, int(limit or _DEFAULT_LIMIT)))
    offset = max(0, int(offset or 0))

    # ── step 1: dormant gate ────────────────────────────────────────────────────────────
    if not store.is_configured():
        return _empty_result(
            "not_configured", None, since_resolved, before_resolved, provider.lookback_days
        )
    if not store.get().get("enabled"):
        return _empty_result(
            "disabled", None, since_resolved, before_resolved, provider.lookback_days
        )

    # ── step 1b: resolve `account` (provider id OR name, RFC §A) before the real fetch ──
    resolved_account = account
    if account:
        try:
            accounts_probe = provider.fetch_window(
                since=since_resolved, before=before_resolved, account=None, accounts_only=True
            )
        except Exception as exc:  # noqa: BLE001 — classified below, never propagated
            status = provider.classify_error(exc)
            error_text = sanitize_untrusted(str(exc), max_len=300)
            store.record_status(status, error=error_text)
            logger.warning(
                "connector_fetch: %s account lookup failed (%s): %s", provider_key, status, exc
            )
            return _empty_result(
                status, error_text, since_resolved, before_resolved, provider.lookback_days
            )
        resolved_account, account_error = _resolve_account_ref(account, accounts_probe.accounts)
        if account_error is not None:
            return _empty_result(
                "error", account_error, since_resolved, before_resolved, provider.lookback_days
            )

    # ── step 2: fetch ────────────────────────────────────────────────────────────────────
    try:
        window: FetchWindow = provider.fetch_window(
            since=since_resolved, before=before_resolved, account=resolved_account
        )
    except Exception as exc:  # noqa: BLE001 — classified below, never propagated
        status = provider.classify_error(exc)
        # S-F2: a provider's own exception text is UNTRUSTED (it can embed whatever the
        # upstream API/library chose to say) and reaches the model verbatim via this
        # result's `error` field plus the store's `last_error` — sanitize it exactly like
        # any other provider-supplied string (RFC §A step 4), not just cap its length.
        error_text = sanitize_untrusted(str(exc), max_len=300)
        store.record_status(status, error=error_text)
        logger.warning("connector_fetch: %s fetch failed (%s): %s", provider_key, status, exc)
        return _empty_result(
            status, error_text, since_resolved, before_resolved, provider.lookback_days
        )

    # ── steps 3-4: normalize (already done by fetch_window) + sanitize ─────────────────
    accounts = [_sanitize_account(a) for a in window.accounts]
    rows = [_sanitize_row(r) for r in window.rows]
    rows_truncated = len(rows) > _MAX_FETCH_ROWS
    if rows_truncated:
        logger.warning(
            "connector_fetch: %s fetch returned %d rows, over the %d-row cap — truncating",
            provider_key,
            len(rows),
            _MAX_FETCH_ROWS,
        )
        rows = rows[:_MAX_FETCH_ROWS]

    # ── step 5: stash raw evidence + diff ───────────────────────────────────────────────
    from backend.services import connector_snapshots

    snapshot_rows = [
        {
            "ref": _row_ref(r),
            "provider_account_id": r.get("provider_account_id"),
            "raw": _sanitize_raw(r.get("raw")),
            "normalized": {
                "date": r.get("date"),
                "amount": r.get("amount"),
                "currency": r.get("currency"),
                "description": r.get("description"),
                "account": r.get("provider_account_id"),
            },
        }
        for r in rows
        if _row_ref(r)
    ]
    changes = connector_snapshots.stash_rows(provider_key, snapshot_rows)

    # ── step 6: resolve linked_account_id from Account.external_ids ────────────────────
    linked = _linked_account_ids(provider_key)
    for a in accounts:
        a["linked_account_id"] = linked.get(a.get("provider_account_id"))
    for r in rows:
        r["linked_account_id"] = linked.get(r.get("provider_account_id"))

    # ── step 7: filter already-booked rows unless include_booked ───────────────────────
    known = ledger_service.known_external_refs(provider_key)
    seen_refs = {_row_ref(r) for r in rows if _row_ref(r)}
    kept: list[dict] = []
    already_booked = 0
    for r in rows:
        ref = _row_ref(r)
        txn_id = known.get((provider_key, ref))
        if txn_id is not None:
            already_booked += 1
            if not include_booked:
                continue
            r["booked_txn_id"] = txn_id
        if not include_pending and r.get("pending"):
            continue
        kept.append(r)

    fetched_count = len(rows)
    truncated = (offset + limit) < len(kept)
    returned = kept[offset : offset + limit]
    window_complete = window.complete and not truncated and not rows_truncated

    # ── step 8: vanished check — only on a COMPLETE, non-truncated window ──────────────
    if window_complete:
        vanished = connector_snapshots.note_missing(
            provider_key, since_resolved, before_resolved, seen_refs, resolved_account
        )
        changes = changes + vanished

    # ── step 9: FX warm-up ───────────────────────────────────────────────────────────────
    _warm_fx({r.get("currency") for r in rows if r.get("currency")})

    # ── step 10: record status + publish sync event ─────────────────────────────────────
    sanitized_extra = _sanitize_extra(window.extra)
    store.record_status("ok", error=None)
    if window.warnings:
        store.update({"last_caution": "; ".join(window.warnings)})
    if sanitized_extra:
        store.update(sanitized_extra)

    # ── step 11: bound + return (never leaks "raw" — that lives only in the snapshot store) ──
    public_rows = [{k: v for k, v in r.items() if k != "raw"} for r in returned]
    result = {
        "status": "ok",
        "error": None,
        "window": {
            "since": since_resolved,
            "before": before_resolved,
            "lookback_days": provider.lookback_days,
            "complete": window_complete,
        },
        "accounts": accounts,
        "transactions": public_rows,
        "changes": changes,
        # Part of the provider failed while the rest answered (one of several Plaid banks with
        # an expired login, say) — say so, so it is never a silent gap.
        "warnings": [sanitize_untrusted(w, max_len=300) for w in window.warnings],
        "counts": {
            "fetched": fetched_count,
            "already_booked": already_booked,
            "returned": len(public_rows),
            "truncated": truncated,
            "changes": len(changes),
        },
    }
    if sanitized_extra:
        # A broker-only channel (Alpaca/IBKR positions + cash/buying_power) — a bank fetch's
        # window.extra is always empty, so this key never appears in a bank tool's own
        # response. backend/ai/tools/broker_fetch.py is the one reader; it pops this back
        # off before returning to the model, reshaping it into the broker tools' own
        # 'positions' field.
        result["extra"] = sanitized_extra
    return result


def provider_accounts(provider_key: str) -> dict:
    """The account list ONLY, no transaction fetch — ``GET /api/connectors/{id}/accounts``
    (``api/connectors_api.py``, L1) calls this directly. Returns ``{status, provider_label,
    accounts: [{provider_account_id, name, mask, currency, balance, account_type,
    linked_account_id}]}`` — ``account_type`` is the provider's suggested app type, or None.

    A pure READ (C-B2/S-F7): unlike ``fetch()``, this NEVER calls ``store.record_status``
    or publishes a sync event — opening the Connectors tab's link picker must not look like
    a fetch happened, and must not overwrite ``last_status``/``last_synced_at`` a real fetch
    tool call set.

    ALWAYS merges in the LINKED view (``Account.external_ids``, C-B2): an account already
    linked before the provider went quiet (dormant/disabled/errored) still shows up, marked
    from its stored name/mask with ``currency``/``balance`` left ``None`` rather than
    vanishing from the picker just because the live call couldn't run; when the provider
    call succeeds, any linked id it did NOT itself return is unioned in the same way.

    Raises ``ValidationError`` (an ``OpenNetWorthError``, the type the route already catches)
    for an id that isn't one of the nine aggregation providers — the route validates
    ``connector_id`` against the WHOLE connector catalog (email, MCP servers, web-search
    providers, …) before ever calling this, so a non-aggregation id reaching here is a real,
    if unlikely, caller mismatch that must surface as a clean 4xx, never an unhandled 500."""
    try:
        provider = _resolve_provider(provider_key)
    except UnknownProvider as exc:
        raise ValidationError(str(exc)) from exc

    linked = _linked_accounts_by_provider_id(provider_key)
    store = provider.store()
    if not store.is_configured():
        return {
            "status": "not_configured",
            "provider_label": provider.label,
            "accounts": _linked_only_accounts(linked),
        }
    if not store.get().get("enabled"):
        return {
            "status": "disabled",
            "provider_label": provider.label,
            "accounts": _linked_only_accounts(linked),
        }

    today = user_today().isoformat()
    try:
        window = provider.fetch_window(since=today, before=today, account=None, accounts_only=True)
    except Exception as exc:  # noqa: BLE001 — classified, never propagated
        status = provider.classify_error(exc)
        error_text = sanitize_untrusted(str(exc), max_len=300)
        logger.warning(
            "connector_fetch: %s account list failed (%s): %s", provider_key, status, error_text
        )
        return {
            "status": status,
            "provider_label": provider.label,
            "accounts": _linked_only_accounts(linked),
        }

    accounts = []
    seen: set[str] = set()
    for a in window.accounts:
        clean = _sanitize_account(a)
        pid = clean.get("provider_account_id")
        seen.add(pid)
        accounts.append(
            {
                "provider_account_id": pid,
                "name": clean.get("name"),
                "mask": clean.get("mask"),
                "currency": clean.get("currency"),
                "balance": clean.get("balance"),
                "account_type": _known_account_type(clean.get("account_type")),
                "institution": clean.get("institution"),
                "linked_account_id": (linked.get(pid) or {}).get("linked_account_id"),
            }
        )
    accounts.extend(
        a for a in _linked_only_accounts(linked) if a["provider_account_id"] not in seen
    )
    return {"status": "ok", "provider_label": provider.label, "accounts": accounts}
