"""Account creation + name → id resolution.

The twin of ``services.categories.resolve_category_ref``, for accounts. Any write path that
may receive a human-readable account NAME instead of an opaque ``acc_<hex>`` id — an LLM
naming the account the user just said out loud, a foreign CSV/QIF account column, a REST
caller working from a UI label — resolves through here so the name the user already
recognizes works exactly like an id does.

Why this is its own module rather than a helper inside ``services.ledger``: the ledger is
not the only choke that accepts an account ref (the statement/QIF/OFX import path resolves
one too), and both must agree on the matching rules — an account resolved one way at import
and another way at the create choke is a wrong balance, not a cosmetic inconsistency.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.core.write_receipt import record_write
from backend.db.account_store import AccountStore
from backend.db.engine import locked_unit_of_work
from backend.db.tables import accounts as accounts_table
from backend.db.tables import institutions as institutions_table
from backend.db.tables import transactions as transactions_table
from backend.db.tables import txn_audit as txn_audit_table

logger = logging.getLogger(__name__)

_MAX_LISTED_CANDIDATES = 6

# Fields the SERVER owns on an account row. A caller-settable opening pointer would hand one
# account another account's Starting Balance row — i.e. another account's balance.
SERVER_OWNED_ACCOUNT_FIELDS = frozenset({"opening_txn_id", "payment_envelope_id"})

# Fields the seven-type taxonomy retired (ADR 2026-09-07-seven-account-types-one-axis):
# `type` alone now derives kind and net-worth band, and `locked`/`on_budget`/`group` never
# fed anything but sidebar presentation. The REST API silently drops these for a smoother
# mid-rollout client (api/ledger.py); the AI tool surface REJECTS them instead — a model
# still sending one is working from stale knowledge and should learn that immediately
# rather than have the call silently no-op on the field it thought it was setting.
DEAD_ACCOUNT_FIELDS = frozenset({"kind", "asset_class", "group", "locked", "on_budget"})
ACCOUNT_COLOR_REMOVED_MESSAGE = "Account colour has been removed and cannot be set."

ACCOUNT_TYPE_IMMUTABLE_MESSAGE = (
    "Account type is set when you create the account and cannot be changed. "
    "Create a new account if it needs a different type."
)
ACCOUNT_CURRENCY_IMMUTABLE_MESSAGE = (
    "Account currency is set when you create the account and cannot be changed. "
    "Create a new account if it needs a different currency."
)
ACCOUNT_PLATFORM_RETIRED_MESSAGE = "Account platform has been renamed to institution."


def assert_no_server_owned_fields(data: dict) -> None:
    """Refuse a caller-supplied patch/create that names a server-owned field."""
    named = sorted(SERVER_OWNED_ACCOUNT_FIELDS & set(data))
    if named:
        raise ValidationError(
            f"{', '.join(named)} is set by the server, not by callers — "
            "use initial_balance / initial_balance_date to move an account's opening."
        )


def assert_no_dead_account_fields(data: dict) -> None:
    """Refuse an AI-tool create/update naming a field the seven-type taxonomy retired."""
    named = sorted(DEAD_ACCOUNT_FIELDS & set(data))
    if named:
        raise ValidationError(
            f"{', '.join(named)} no longer exist — account type alone now determines "
            "kind, net-worth band and budget membership. Omit them."
        )


def assert_no_account_color(data: dict) -> None:
    if "color" in data:
        raise ValidationError(ACCOUNT_COLOR_REMOVED_MESSAGE)


def assert_no_legacy_platform(data: dict) -> None:
    if "platform" in data:
        raise ValidationError(ACCOUNT_PLATFORM_RETIRED_MESSAGE)


def _resolve_institution_patch(data: dict, *, conn) -> dict:
    """Replace a user-facing institution name with its stable stored id."""
    if "institution" not in data:
        return data
    from backend.db.institution_store import InstitutionStore

    patch = dict(data)
    institution = InstitutionStore().resolve_or_create(patch.pop("institution"), conn=conn)
    patch["institution_id"] = institution["id"] if institution else None
    return patch


def _disclose_retired_payment_envelopes(
    sync: dict, own_account_id: str, side_effects: dict
) -> None:
    """Split ``sync_card_payment_envelopes()``'s ``retired`` list by WHICH account's stale
    ``payment_envelope_id`` actually caused each retirement (``planning.py`` stamps this as
    ``account_id`` from a snapshot taken before the sync's own FK-clear step), and write the
    honest halves into ``side_effects``.

    The sync is a GLOBAL, idempotent reconciliation pass — one call can retire a DIFFERENT,
    already-stale envelope as an incidental side effect (e.g. an archived card's envelope that
    never got swept because archiving alone doesn't trigger a sync; some LATER, unrelated
    account write does). Crediting — or worse, silently dropping — that retirement on the
    account this call is actually about would be a false attribution, so it goes under a
    separate key instead of being folded into ``retired_payment_envelopes``, which is reserved
    for retirements THIS account's own write actually caused.

    Never both empty and never dropped: every retirement with a real ``zeroed_assignments``
    lands in exactly one of the two lists, whichever caller triggered the sync."""
    retired = [r for r in sync.get("retired") or [] if r.get("zeroed_assignments")]
    own = [r for r in retired if r.get("account_id") == own_account_id]
    other = [r for r in retired if r.get("account_id") != own_account_id]
    if own:
        side_effects["retired_payment_envelopes"] = [
            {
                "envelope_id": r["envelope_id"],
                "name": r["name"],
                "zeroed_assignments": r["zeroed_assignments"],
                "zeroed_total": round(sum(r["zeroed_assignments"].values()), 2),
            }
            for r in own
        ]
    if other:
        logger.warning(
            "sync_card_payment_envelopes: incidentally retired %d payment envelope(s) "
            "belonging to OTHER account(s) while writing %s: %s",
            len(other),
            own_account_id,
            [(r["envelope_id"], r.get("account_id")) for r in other],
        )
        side_effects["unrelated_retired_payment_envelopes"] = [
            {
                "account_id": r.get("account_id"),
                "envelope_id": r["envelope_id"],
                "name": r["name"],
                "zeroed_assignments": r["zeroed_assignments"],
                "zeroed_total": round(sum(r["zeroed_assignments"].values()), 2),
            }
            for r in other
        ]


def create_account(data: dict, *, side_effects: dict | None = None) -> dict:
    """Create an account AND its Starting Balance row, as one operation.

    ``data`` is the stored account shape plus the two INPUT-ONLY opening parameters
    ``initial_balance`` (signed, native currency) and ``initial_balance_date`` (the tracking
    start). Neither is stored: the account is created with ``opening_txn_id: None``, the row is
    minted through ``ledger.set_account_opening``, and the pointer is patched onto the account.

    ``initial_balance`` is required, and zero is a real assertion: every new account receives a
    Starting Balance row dated today unless the caller supplies another date. The account row,
    opening transaction, audit row and pointer commit in one database transaction.

    Creating a CREDIT account re-runs ``sync_card_payment_envelopes()`` (see below) — that sync
    is a GLOBAL reconciliation pass, so it can incidentally retire a DIFFERENT, already-stale
    envelope (e.g. an archived card's, left behind because archiving alone doesn't trigger a
    sync) in the same pass that mints this new account's own envelope. That retirement zeroes
    real assigned money and is never THIS account's doing, so passing a dict as ``side_effects``
    fills it with ``{"unrelated_retired_payment_envelopes": [...]}`` when it happens (mirroring
    ``update_account``'s disclosure) — omit it and nothing changes for existing callers, but the
    retirement is logged either way so it is never silently dropped (review 2026-09-10)."""
    from backend.services import ledger as ledger_service
    from backend.services.fx_cache import ensure_fx_rate

    assert_no_server_owned_fields(data)
    assert_no_account_color(data)
    assert_no_legacy_platform(data)
    data = dict(data)
    if "initial_balance" not in data:
        raise ValidationError("initial_balance is required (use 0 when the account is empty)")
    try:
        opening = float(data.pop("initial_balance"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("initial_balance must be a finite number") from exc
    if not math.isfinite(opening):
        raise ValidationError("initial_balance must be a finite number")
    opening = round(opening, 2)
    as_of = data.pop("initial_balance_date", None)
    if not as_of:
        as_of = user_today().isoformat()

    if "external_ids" in data:
        raw_ids = data["external_ids"]
        normalized = _normalize_external_ids(raw_ids)
        assert_external_ids_unique(None, normalized)
        data.update(_link_fill(data, raw_ids))
        data["external_ids"] = normalized

    ensure_fx_rate(data.get("currency"), opening)
    with locked_unit_of_work(
        accounts_table, institutions_table, transactions_table, txn_audit_table
    ) as conn:
        data = _resolve_institution_patch(data, conn=conn)
        account = ledger_service.accounts.create({**data, "opening_txn_id": None}, conn=conn)
        result = ledger_service.set_account_opening(account["id"], opening, as_of, conn=conn)
    result = ledger_service.accounts.find_by_id(result["id"]) or result
    # The receipt names what the user did — "1 account added" — and its Starting Balance row,
    # booked through the ledger choke, is folded into it rather than counted as a transaction
    # (onboarding said "6 transactions added" for six accounts; QA 2026-10-12).
    record_write(
        "create",
        result["id"],
        entity_type="account",
        meta={"absorbs": result.get("opening_txn_id")},
    )
    if result.get("type") == "credit":
        # `sync_card_payment_envelopes()` otherwise only runs at boot (main.py) — a card
        # created through the running app never got its payment envelope until the next
        # restart (owner-reported: a fresh Citi Rewards card synced 0 in Credit Card
        # Payments). Idempotent per its own docstring, so calling it here for every new
        # credit account is safe and cheap. It writes `payment_envelope_id` onto the account
        # row directly (svc.accounts.update_by_id) — `result` is a snapshot from BEFORE that
        # write, so re-fetch to hand the caller the row it would get back from a GET anyway.
        from backend.services.planning import sync_card_payment_envelopes

        try:
            sync = sync_card_payment_envelopes()
        except Exception:
            # The account and opening have already committed. Reporting this as a failed create
            # invites a duplicate retry, so surface a fixed, retryable reconciliation warning.
            logger.exception(
                "post-create payment-envelope sync failed for account %s", result["id"]
            )
            if side_effects is not None:
                side_effects["payment_envelope_sync_warning"] = {
                    "code": "payment_envelope_sync_failed",
                    "message": (
                        "Account created, but its credit-card payment envelope could not be "
                        "synced. The app will retry reconciliation on startup."
                    ),
                    "retryable": True,
                }
        else:
            result = ledger_service.accounts.find_by_id(result["id"]) or result
            if side_effects is not None:
                _disclose_retired_payment_envelopes(sync, result["id"], side_effects)
    return result


# ── External account links (RFC connectors-as-data-sources §D) ──────────────────────────────
#
# A provider-account link lives ON the account (``Account.external_ids``), unique per
# ``(source, ref)``, replacing the nine provider config stores' own ``account_map`` blobs
# (backfilled by revision 202609051210). ``connected`` is DERIVED from this list
# (``bool(external_ids)``) at the serializer (``services.ledger.account_with_balance``) —
# never trusted as an independently-stored fact.


def _normalize_external_ids(ids: object) -> list[dict]:
    """Coerce a caller-supplied ``external_ids`` list to the canonical STORED shape —
    ``{source, ref, name, mask, linked_at}``. Extra caller-supplied fields (e.g. a transient
    ``institution``, read by ``_link_fill`` below for the institution fill and never persisted)
    are dropped here, not carried into storage."""
    if not isinstance(ids, list):
        raise ValidationError("external_ids must be a list of {source, ref} objects")
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for entry in ids:
        if not isinstance(entry, dict) or not entry.get("source") or not entry.get("ref"):
            raise ValidationError("each external_ids entry requires a source and a ref")
        out.append(
            {
                "source": entry["source"],
                "ref": entry["ref"],
                "name": entry.get("name") or "",
                "mask": entry.get("mask") or "",
                "linked_at": entry.get("linked_at") or now,
            }
        )
    return out


def assert_external_ids_unique(account_id: str | None, ids: list[dict]) -> None:
    """Refuse an ``external_ids`` write that collides with another account's own link — a
    ``(source, ref)`` pair may be held by at most ONE account (RFC AC5). ``account_id`` is the
    account being written (None on create, nothing to exempt yet); a pair already held BY
    ``account_id`` itself is not a collision (an idempotent re-save)."""
    pairs = {(e.get("source"), e.get("ref")) for e in ids if isinstance(e, dict) and e.get("ref")}
    if not pairs:
        return
    for row in all_account_rows():
        if row.get("id") == account_id:
            continue
        for entry in row.get("external_ids") or []:
            if not isinstance(entry, dict):
                continue
            pair = (entry.get("source"), entry.get("ref"))
            if pair in pairs:
                raise ValidationError(
                    f"external id (source={pair[0]!r}, ref={pair[1]!r}) is already linked to "
                    f"account {row.get('name')!r} (id={row.get('id')})"
                )


def _link_fill(account: dict, raw_ids: list) -> dict:
    """last_four/institution fill from a newly linked entry's ``mask``/``institution`` — an
    EMPTY field only, existing non-empty values are never overwritten (RFC §D "Link-time
    fill"). ``raw_ids`` is the CALLER's un-normalized list — ``institution`` is read here,
    before ``_normalize_external_ids`` drops it."""
    last_four = account.get("last_four") or ""
    institution = account.get("institution") or ""
    for entry in raw_ids or []:
        if not isinstance(entry, dict):
            continue
        if not last_four and entry.get("mask"):
            last_four = entry["mask"]
        if not institution and entry.get("institution"):
            institution = entry["institution"]
    patch = {}
    if last_four and last_four != (account.get("last_four") or ""):
        patch["last_four"] = last_four
    if institution and institution != (account.get("institution") or ""):
        patch["institution"] = institution
    return patch


def update_account(
    account_id: str, changes: dict, *, side_effects: dict | None = None
) -> dict | None:
    """The account PATCH choke shared by REST and Lucius.

    Account type and currency are immutable after creation. They define how the ledger
    classifies and values the account, so both are refused here and at the store boundary.
    """
    from backend.services import ledger as ledger_service

    assert_no_server_owned_fields(changes)
    assert_no_account_color(changes)
    assert_no_legacy_platform(changes)
    existing = ledger_service.accounts.find_by_id(account_id)
    if existing is None:
        return None
    changes = dict(changes)
    if "type" in changes:
        raise ValidationError(ACCOUNT_TYPE_IMMUTABLE_MESSAGE)
    if "currency" in changes:
        raise ValidationError(ACCOUNT_CURRENCY_IMMUTABLE_MESSAGE)
    if "external_ids" in changes:
        raw_ids = changes["external_ids"]
        normalized = _normalize_external_ids(raw_ids)
        assert_external_ids_unique(account_id, normalized)
        changes.update(_link_fill(existing, raw_ids))
        changes["external_ids"] = normalized
    with locked_unit_of_work(accounts_table, institutions_table) as conn:
        changes = _resolve_institution_patch(changes, conn=conn)
        result = ledger_service.accounts.update_by_id(account_id, changes, conn=conn)
    if result is None:
        return None
    return ledger_service.accounts.find_by_id(account_id) or result


def provider_label(source: str | None) -> str:
    """The connector's display name for ``source`` — an ``external_ids``/connector-snapshot
    provider key (e.g. ``"plaid"``) — read from ``services.aggregation.providers.PROVIDERS``
    (L2 owns that table; this only ever reads its ``.label``). Falls back to the raw source
    string for an unknown/blank key so a drawer/list never renders empty."""
    if not source:
        return ""
    try:
        from backend.services.aggregation.providers import PROVIDERS

        provider = PROVIDERS.get(source)
        if provider is not None:
            return provider.label
    except Exception:
        logger.warning("provider_label: lookup failed for %r", source, exc_info=True)
    return source


def link_statuses() -> dict[str, str]:
    """Each connector's state as an account link sees it: ``"connected"`` (switched on) or
    ``"paused"`` (switched off), keyed by connector id. A source with no entry has no connector
    any more — removed, or never set up — and reads as ``"disconnected"`` (``link_status``).
    ONE lean read (``enabled_flags``: id + enabled, no JSON parsed) — it sits on the account
    serializer's path, so a caller with several links reads it once, not per link."""
    from backend.db.connector_store import ConnectorStore

    return {
        row["id"]: "connected" if row["enabled"] else "paused"
        for row in ConnectorStore().enabled_flags()
    }


def link_status(source: str | None, statuses: dict[str, str]) -> str:
    """``source``'s state from a ``link_statuses()`` read — absent means ``"disconnected"``."""
    return statuses.get(source or "", "disconnected")


def linked_accounts_for_source(source: str) -> list[dict]:
    """The ``GET /api/connectors/{id}/accounts`` LINKED block, derived ONLY from
    ``Account.external_ids`` — no live provider balance (``currency``/``balance`` are always
    None here; ``services.connector_fetch.provider_accounts``, once L2 lands it, is what
    actually calls the provider and fills them in)."""
    out = []
    for row in all_account_rows():
        for entry in row.get("external_ids") or []:
            if not isinstance(entry, dict) or entry.get("source") != source:
                continue
            out.append(
                {
                    "provider_account_id": entry.get("ref"),
                    "name": entry.get("name") or "",
                    "mask": entry.get("mask") or "",
                    "currency": None,
                    "balance": None,
                    "linked_account_id": row.get("id"),
                }
            )
    return out


def all_account_rows() -> list[dict]:
    """Every account row (archived included), read from a FRESH ``AccountStore``.

    Callers that own a long-lived store handle should pass THEIR rows to
    ``resolve_account_ref(rows=...)`` rather than relying on this. Two reasons, one of which
    cost a debugging round: (1) it saves a re-read per item in a bulk loop, and (2) — the
    load-bearing one — it keeps ONE store identity per path. ``services.ledger`` validates leg
    existence through its own module-level ``accounts`` instance, and callers/tests
    monkeypatch that instance by convention; a resolver reading a second, independently
    constructed store can disagree with the very check that runs immediately after it."""
    return AccountStore().all()


def _describe(acc: dict) -> str:
    """One candidate, rendered with the fields that actually disambiguate it: the currency
    (the "IBKR USD" vs "IBKR SGD" case), the institution, whether it's archived, and the id the
    caller should send instead."""
    name = (acc.get("name") or "").strip()
    bits = [(acc.get("currency") or "").upper() or "?"]
    institution = (acc.get("institution") or "").strip()
    if institution:
        bits.append(institution)
    if acc.get("archived"):
        bits.append("archived")
    return f"{name!r} ({', '.join(bits)}, id={acc.get('id')})"


def _ambiguous(ref: str, matches: list[dict]) -> ValidationError:
    shown = matches[:_MAX_LISTED_CANDIDATES]
    more = "" if len(matches) <= _MAX_LISTED_CANDIDATES else f" (+{len(matches) - len(shown)} more)"
    return ValidationError(
        f"Account name {ref!r} is ambiguous — {len(matches)} accounts share it: "
        f"{'; '.join(_describe(a) for a in shown)}{more}. "
        "Pass the id of the one you mean."
    )


def resolve_account_ref(ref: str | None, *, rows: list[dict] | None = None) -> str | None:
    """Resolve ``ref`` — an account id OR a name (case-insensitive EXACT match) — to its
    store id. Returns None for a blank ``ref`` or one that matches nothing (the caller owns
    the "does not exist" error, which already names the closest real names via
    ``core.errors.recovery_hint``).

    RAISES ``ValidationError`` when a name matches more than one live account. This is the
    one deliberate divergence from ``resolve_category_ref``, which returns None on ambiguity
    and lets the caller treat it as unknown. For a category, "unresolved" degrades to a
    rejection or to Uncategorized — both safe. For an account, returning None would surface as
    "no such account" for a name that in fact matches two, sending the caller hunting for a
    typo that isn't there; worse, any future caller tempted to "just pick the first" would be
    booking money against the wrong balance. Ambiguity is a distinct, actionable failure and
    is reported as one.

    Matching ladder:
      1. exact id → itself (no scan of names at all; an id is authoritative)
      2. exact name, case-insensitive, among NON-ARCHIVED accounts
      3. exact name, case-insensitive, among archived accounts — reached only when no live
         account claims the name

    Archived accounts DO match (step 3). ``_validate_legs`` has always accepted an archived
    account BY ID (a closed account still owns its transaction history, and a correction
    against it must stay possible), so refusing the same account by name would be an
    arbitrary asymmetry between the two ways of naming the same row.

    An archived account never makes a live one ambiguous (step 2 is checked first and wins
    outright). Archiving is an explicit "this one is out of use" signal from the user, so a
    bare name pointing at the live account is a reading, not a coin flip. Two LIVE accounts
    sharing a name, or two ARCHIVED ones with no live claimant, are genuinely ambiguous and
    raise.

    Matching is EXACT (after strip + casefold) — never fuzzy. A near-miss resolves to None
    and the caller's error names the closest candidates; guessing that "DBS" means "DBS
    Savings" rather than "DBS Multiplier" is not a call this layer gets to make silently.

    ``rows`` lets a caller processing many rows (bulk import) pass a pre-loaded
    ``all_account_rows()`` once instead of re-reading the store per item.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    rows = rows if rows is not None else all_account_rows()
    for row in rows:
        if row.get("id") == ref:
            return ref

    lowered = ref.lower()
    by_name = [r for r in rows if (r.get("name") or "").strip().lower() == lowered]
    if not by_name:
        return None
    live = [r for r in by_name if not r.get("archived")]
    pool = live or by_name
    if len(pool) == 1:
        resolved = pool[0]
        if resolved.get("archived"):
            logger.info(
                "resolve_account_ref: %r resolved to ARCHIVED account %s", ref, resolved.get("id")
            )
        return resolved.get("id")
    raise _ambiguous(ref, pool)


def resolve_venue_ref(ref: str | None, *, rows: list[dict] | None = None) -> str | None:
    """``resolve_account_ref`` widened for a holding's venue: a NAME that no account carries
    falls back to the institution a live account is filed under ("IBKR" -> the one account
    whose institution is IBKR). A name always beats an institution, and two live accounts
    under one institution RAISE naming both — "IBKR" must never quietly pick the USD or the
    SGD one. Exact (casefolded) matches only; None when nothing matches."""
    rows = rows if rows is not None else all_account_rows()
    resolved = resolve_account_ref(ref, rows=rows)
    if resolved:
        return resolved
    wanted = _norm(ref)
    if not wanted:
        return None
    filed = [r for r in rows if not r.get("archived") and _norm(r.get("institution")) == wanted]
    if len(filed) > 1:
        raise _ambiguous(str(ref).strip(), filed)
    return filed[0]["id"] if filed else None


def require_account_id(
    ref: str, *, field: str = "account_id", rows: list[dict] | None = None
) -> str:
    """``resolve_account_ref`` for a WRITE: the stored id of the account ``ref`` names (an id
    or an exact name), or RAISE ``ValidationError`` naming the closest real accounts. A write
    stores what this returns, never ``ref`` itself — so an id field only ever holds an id
    (2026-10-13: a name persisted as an "id" on a sale event, and a stale id silently minted a
    "Brokerage" account). Ambiguity raises from ``resolve_account_ref`` itself."""
    from backend.core.errors import recovery_hint

    rows = rows if rows is not None else all_account_rows()
    account_id = resolve_account_ref(str(ref), rows=rows)
    if not account_id:
        raise ValidationError(
            f"Unknown account {ref!r} for {field}."
            + recovery_hint(
                str(ref), (a.get("name") or "" for a in rows), tool_hint="list_accounts"
            ),
            **{field: ref},
        )
    return account_id


def _norm(value: object) -> str:
    """The shared normalisation for every comparison in this module: strip + lower.

    Kept as one function so the browse filters below and ``resolve_account_ref`` above can
    never drift into two different case rules for the same field.
    """
    return str(value or "").strip().lower()


def filter_accounts(
    rows: list[dict],
    *,
    query: str | None = None,
    kind: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution: str | None = None,
) -> list[dict]:
    """Select account ROWS for a browse/lookup surface (``list_accounts``).

    DELIBERATELY SEPARATE FROM ``resolve_account_ref``, which is authoritative resolution
    for a WRITE: that one is exact-only and RAISES on ambiguity, precisely so nothing ever
    silently books money against "DBS Savings" when the user said "DBS". This function is
    the opposite job — a read-only narrowing that is ALLOWED to be fuzzy because returning
    several candidates is the useful answer, not a hazard. ``query`` is therefore a
    SUBSTRING match, and it must never be reused to pick an account to write to.

    ``query`` matches name OR institution (a user says "chase" for either). The remaining
    parameters are exact, case-insensitive equality. Row selection only — no field
    projection: dropping FIELDS from a money-bearing read is what hid ticker/last_four
    from agents in the 2026-07-09 incident.
    """
    out = rows
    if query:
        needle = _norm(query)
        out = [
            r
            for r in out
            if needle in _norm(r.get("name")) or needle in _norm(r.get("institution"))
        ]
    for field, wanted in (
        ("kind", kind),
        ("type", type),
        ("currency", currency),
        ("institution", institution),
    ):
        if wanted:
            target = _norm(wanted)
            out = [r for r in out if _norm(r.get(field)) == target]
    return out
