"""Lunch Money aggregation adapter — imports transactions from a Lunch Money
(https://lunchmoney.app) budgeting account into the ledger.

Lunch Money is a third-party budgeting/transaction-aggregation SaaS: the user links their
OWN banks/cards/crypto INSIDE Lunch Money (via Plaid or manual entry), and Lunch Money
exposes the resulting unified transaction list over a simple Bearer-token REST API — no
OAuth, no DCR, a single long-lived personal access token the user mints in Lunch Money's
own Settings → Developers, closely matching SimpleFIN's shape (a static credential, not a
hosted consent flow).

**API base v2, migrated 2026-08-17** (see the ADR addendum below): ``https://api.
lunchmoney.dev/v2``. Lunch Money's v1 API is legacy; v2 is NOT backwards compatible —
different base URL, a FIXED sign convention (no more ``debit_as_negative``), several field
renames, and transactions arrive DEHYDRATED (no more inline institution/account names).
Every difference is called out at its own call site below; the full list is recorded in
``docs/adr/2026-08-16-lunch-money-connector-scope.md``'s 2026-08-17 addendum.

This module is the PURE protocol (``fetch_transactions``, ``fetch_account_names``) +
normalize (``fetch_window``) — READ-ONLY, called from a Lucius turn via
``lunch_money_fetch_transactions`` (RFC ``connectors-as-data-sources``). There is no
adapter class, no auto-created account, and no scheduled sync any more — the
provider-account → app-account link lives on ``Account.external_ids``, and booking happens
through ``add_transactions`` in the SAME turn that fetched, never here. The 120-day rolling
window is gone too: the fetch tool passes an explicit ``[since, before]`` window every call
(default the provider's ``lookback_days``), so the caller decides how far back to look.

**Scope: transactions only** (see ``docs/adr/2026-08-16-lunch-money-connector-scope.md``).
Lunch Money's ``/manual_accounts`` (formerly ``/assets``, its own manually-tracked
net-worth items) is fetched ONLY for account display names (see ``fetch_account_names``
below) — never for its stored ``balance``, which stays unsynced: a raw STORED balance with
no transaction history, and this app's balances are computed from the ledger, never stored
(CLAUDE.md's core invariant). ``/plaid_accounts`` is fetched for the same names-only reason.
Neither endpoint's transaction/balance data is ever imported.

**Category mapping: NOT attempted.** Every sibling adapter (SimpleFIN/Plaid/Yodlee/IBKR)
leaves categorization to the app's own rules/Automations, and Lunch Money's
``category_id`` is the user's OWN free-form taxonomy inside Lunch Money — this app's
``category`` is an OPAQUE id (``cat_<12hex>``, CLAUDE.md) that RAISES on an unknown value,
never silently coerced. Imported transactions land with ``category=None`` (Uncategorized),
exactly like every other connector. v2's ``tag_ids`` (replacing v1's inline ``tags``
array) is likewise never read — no sibling adapter maps provider tags either.

**Group parents are skipped; split parents no longer even arrive.** v2 excludes a
split-parent row from ``GET /transactions`` by default (its real per-split amounts already
live in separate ``split_parent_id``-carrying child rows, returned as ordinary leaf
transactions) — the adapter never passes ``include_split_parents``, so it never sees one.
A GROUP parent is NOT excluded by default: its own ``amount``/``currency`` still
"represent the totalled amount of transactions bearing this transaction's id as their
group_parent_id" (Lunch Money's docs, v2 wording), with its real children as separate rows
in the same feed. Importing both would double-count the same money movement. Only leaf
transactions are booked; ``is_split_parent`` is still checked defensively alongside
``is_group_parent`` in case a server ever returns one anyway (matches the codebase's
belt-and-suspenders style elsewhere). This assumption is UNVERIFIED against a real account (ADR
``docs/adr/2026-08-16-lunch-money-connector-scope.md`` §3) — if it is ever wrong for some
account configuration, the skip stops preventing double-counting and starts silently
DELETING money instead (the parent, the only row carrying the total, is dropped with
nothing to replace it). ``LunchMoneyAdapter.group_parent_skipped`` counts these skips
SEPARATELY from the generic ``skipped`` bucket (surfaced in ``stats``/the sync result +
logged) so a mismatch against children actually landing in the ledger is at least
observable, even though it can't be fully verified without a real account. ``map()`` also
runs a cheaper, always-on detector for the OPPOSITE failure direction — the
``is_group_parent``/``is_split_parent`` fields silently no longer matching anything (e.g. a
field rename), which would let a full-amount parent row through as an ordinary duplicate
alongside its own children (a silent 2x double-count instead of a deletion): any row
carrying a non-null ``group_parent_id``/``split_parent_id`` proves a parent SHOULD have
been skipped, so if that count is nonzero while the matching per-type skip count is zero, a
warning fires naming the mismatch.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.lunch_money_config_store import LunchMoneyConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

API_BASE = "https://api.lunchmoney.dev/v2"
_HTTP_TIMEOUT = 30.0
# Hard cap on a Lunch Money response body we will read into memory (DoS guard — mirrors
# simplefin.py/ibkr.py's own bound). A page of 1000 transactions is a small JSON document,
# so 8 MiB is generous.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# Lunch Money's documented v1 max/default page size for GET /transactions; v2's migration
# guide doesn't call out a changed ceiling and this value still round-trips cleanly against
# the live API's query-parameter validation (verified 2026-08-17 — no real access token
# exists in this environment to confirm the true server-side cap beyond that, so the
# previously-verified-safe value is kept rather than guessed higher).
_PAGE_LIMIT = 1000
# Guard against a pathological never-terminating pagination loop (mirrors plaid.py's
# _MAX_FETCH_PAGES) — 50 pages * 1000 rows is far more than one fetch window could ever hold.
_MAX_FETCH_PAGES = 50

SOURCE_ID = "lunch_money"
EXTERNAL_SOURCE = "lunch_money"


# ── protocol ───────────────────────────────────────────────────────────────────────


def _safe_get(store: LunchMoneyConfigStore, path: str, params: dict) -> httpx.Response:
    """GET wrapper for the Lunch Money API that NEVER lets a raw exception escape
    (mirrors ``simplefin._safe_get``) and bounds the response body at
    ``_MAX_RESPONSE_BYTES``. Raises ``ValidationError`` when no token is saved,
    ``ExternalServiceError`` (status code / exception class only — no URL, no token) on a
    transport/HTTP failure OR any other unexpected exception raised while building/sending
    the request (e.g. a non-ASCII token raising ``UnicodeEncodeError`` out of httpx's own
    header encoding — not an ``httpx.HTTPError`` subclass, so it would otherwise sail past
    the two httpx-specific clauses below and reach ``last_error``/logs unsanitized).

    v2 uses proper 4XX status codes for every error (verified live 2026-08-17 — an invalid
    token returns ``401`` with a JSON error body, never a v1-style ``200 OK`` with the
    error hidden in the body). That means ``raise_for_status()`` below now catches every
    Lunch Money error case on its own — under v1 a ``200``-with-error-body response would
    have sailed past this check, been parsed as JSON, and silently looked like an empty
    transaction list to the caller. v2 closes that gap structurally."""
    token = store.access_token()
    if not token:
        raise ValidationError("Lunch Money is not configured (no access token)")
    try:
        resp = httpx.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"Lunch Money HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"Lunch Money request failed ({type(exc).__name__})") from None
    except Exception as exc:
        raise ExternalServiceError(f"Lunch Money request failed ({type(exc).__name__})") from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"Lunch Money response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    return resp


def fetch_transactions(
    store: LunchMoneyConfigStore, *, start_date: str, end_date: str
) -> list[dict]:
    """Page through ``GET /transactions`` for ``[start_date, end_date]``. Returns the flat
    list of transaction dicts across every page. Raises ``ValidationError``/
    ``ExternalServiceError`` on an auth/transport failure (the orchestrator catches it).

    No ``debit_as_negative`` param — v2 removed it entirely and FIXED the sign convention
    instead (positive = debit/money-out, negative = credit/money-in, regardless of any
    per-user setting — see ``_txn_to_draft``'s docstring for the mapping this requires).
    ``include_group_children`` is asked for: by default Lunch Money returns a transaction
    group as its parent alone, and its children are the real money movements, each in its
    own account. The other defaults (split parents excluded, their children included,
    pending excluded) already match what this adapter wants."""
    out: list[dict] = []
    offset = 0
    for _ in range(_MAX_FETCH_PAGES):
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "include_group_children": "true",
        }
        resp = _safe_get(store, "/transactions", params)
        data = resp.json() or {}
        page = data.get("transactions") or []
        out.extend(page)
        has_more = data.get("has_more")
        if has_more is None:
            has_more = len(page) >= _PAGE_LIMIT
        if not has_more:
            break
        offset += len(page)
    return out


def _format_account_name(acct: dict) -> str:
    name = str(acct.get("display_name") or acct.get("name") or "Lunch Money account").strip()
    institution = str(acct.get("institution_name") or "").strip()
    return f"{institution} · {name}".strip(" ·") if institution else name


def fetch_account_names(store: LunchMoneyConfigStore) -> dict[str, str]:
    """Build a ``"plaid:<id>"``/``"asset:<id>"`` → display name lookup from ``GET
    /plaid_accounts`` + ``GET /manual_accounts``.

    v2 transactions arrive DEHYDRATED — ``asset_institution_name``/``asset_name``/
    ``asset_display_name``/``plaid_account_name``/``plaid_account_display_name`` (and
    ``institution_name``) are all GONE from the transaction object (verified live against
    the v2 mock server 2026-08-17: none of those keys appear on a single transaction in a
    19-row real sample). Under v1 those hydrated fields rode along on every transaction for
    free; v2 requires a separate call per account-listing endpoint instead — both are small,
    unpaginated lists (never more than a handful of linked accounts), so this is ONE extra
    call pair per sync, not per transaction.

    Best-effort: a failure fetching either endpoint is logged and that endpoint's names are
    simply absent from the result (``_resolve_account`` falls back to a generic name) — it
    must never abort the whole sync. A real auth failure already surfaces from
    ``fetch_transactions`` (called first, and NOT best-effort), so by the time this runs the
    token is known-good; a failure here means only these two specific calls had trouble.

    "Never abort the whole sync" means never letting ANY exception escape this function's own
    boundary, not just ``ValidationError``/``ExternalServiceError`` — ``resp.json()`` can raise
    ``json.JSONDecodeError`` on a malformed/empty body (e.g. a proxy maintenance page), and the
    per-account ``.get()`` calls below can raise ``AttributeError`` if the body parses to
    something other than the expected ``dict`` (e.g. a bare JSON array) or an account entry
    isn't itself a ``dict`` (e.g. a string). Any of those escaping used to propagate all the way
    up through ``fetch()`` and get the ENTIRE sync classified ``"error"`` with zero transactions
    imported — even though ``/transactions`` had already succeeded (security-review finding
    M1). The shape guards below degrade a malformed body / bad entry to "no names from this
    endpoint" instead, and the broad ``except`` is the backstop for anything they don't catch."""
    names: dict[str, str] = {}
    for path, list_key, prefix in (
        ("/plaid_accounts", "plaid_accounts", "plaid"),
        ("/manual_accounts", "manual_accounts", "asset"),
    ):
        try:
            resp = _safe_get(store, path, {})
            body = resp.json()
            if not isinstance(body, dict):
                logger.warning(
                    "Lunch Money: %s returned a non-object body for account naming; skipping",
                    path,
                )
                continue
            for acct in body.get(list_key) or []:
                if not isinstance(acct, dict):
                    continue
                acct_id = acct.get("id")
                if acct_id is not None:
                    names[f"{prefix}:{acct_id}"] = _format_account_name(acct)
        except Exception as exc:
            logger.warning("Lunch Money: could not fetch %s for account naming: %s", path, exc)
    return names


# ── mapping helpers ──────────────────────────────────────────────────────────────────


def _account_key(lm_txn: dict) -> str | None:
    """The Lunch Money account this transaction belongs to, as a stable key — either a
    Plaid-linked account (``plaid:<id>``) or a manually-tracked account (``asset:<id>``,
    kept as our OWN internal prefix even though v2 renamed the underlying field to
    ``manual_account_id`` — so an ``account_map`` linked under the v1 build keeps resolving
    unchanged after this migration, no data rewrite needed). A transaction carrying neither
    has nowhere sensible to book (an unassigned row Lunch Money's own UI would flag) — the
    caller skips it rather than guessing."""
    plaid_id = lm_txn.get("plaid_account_id")
    if plaid_id:
        return f"plaid:{plaid_id}"
    manual_id = lm_txn.get("manual_account_id")  # v2 rename of v1's `asset_id`
    if manual_id:
        return f"asset:{manual_id}"
    return None


def _txn_id(lm_txn: dict) -> str:
    return str(lm_txn.get("id") or "").strip()


def _parents_with_children(txns: list[dict]) -> set:
    """Ids of the group/split parents whose children are in this feed. Only those parents
    are skipped — the children carry the money. A parent whose children did not come back
    is booked itself; skipping it lost the whole group's money."""
    out: set = set()
    for t in txns:
        for key in ("group_parent_id", "split_parent_id"):
            if t.get(key) is not None:
                out.add(t[key])
    return out


def _txn_to_row(lm_txn: dict, account_key: str, currency_hint: str) -> dict | None:
    """One Lunch Money transaction → the common normalized row shape (RFC §A step 3).

    **Sign convention (v2, FIXED — verified live 2026-08-17):** "positive amount values
    always indicate debit transactions and negative amount values always indicate credit
    transactions, regardless of the user's debits_as_negative setting" (Lunch Money's own
    migration guide) — a Lunch-Money-specific inversion, not the shared SimpleFIN/Plaid
    convention (positive = money-in) every sibling provider uses. The common row's
    ``amount`` is always "+ = money in" (this app's own convention), so the raw v2 sign is
    FLIPPED here — getting this backwards would silently flip the kind of every booked
    transaction; see the dedicated sign-convention regression test in
    ``tests/test_lunch_money_service.py``.

    ``external_ref`` is the bare numeric Lunch Money transaction id — globally unique, so
    no account-scoping prefix is needed (unlike SimpleFIN/Plaid's per-account ids). Returns
    None for an unparseable/non-finite/zero amount. A group/split parent reaches here only
    when the feed holds none of its children — then it is the only record of the money."""
    try:
        raw_amount = float(lm_txn.get("amount"))
    except (TypeError, ValueError):
        return None
    # float() happily parses "NaN"/"Infinity"/"1e400" (the last overflows to inf) without
    # raising — none of that is money. NaN in particular defeats the `== 0` guard below
    # (NaN never equals anything, itself included), so an unguarded row would flow straight
    # into a draft and poison every downstream sum it touches.
    if not math.isfinite(raw_amount):
        return None
    if raw_amount == 0:
        return None
    amount = -raw_amount  # v2: positive=debit/negative=credit → flip to +=money-in

    raw_date = str(lm_txn.get("date") or "").strip()
    try:
        date_str = datetime.fromisoformat(raw_date).date().isoformat()
    except ValueError:
        date_str = datetime.now(timezone.utc).date().isoformat()

    # v2 dropped `display_name` from the transaction object entirely (confirmed against a
    # live 19-row sample, 2026-08-17) — `payee` (Lunch Money's own cleaned-up label) is now
    # the best first choice, falling back to `original_name` (the raw bank-provided text).
    description = (lm_txn.get("payee") or lm_txn.get("original_name") or "").strip()
    currency = (lm_txn.get("currency") or currency_hint).upper()
    txn_id = _txn_id(lm_txn)
    ref = txn_id or derived_ref(
        account_key, date_str, amount, currency, description or "Lunch Money"
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": lm_txn,
        "provider_account_id": account_key,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }


def fetch_window(
    store: LunchMoneyConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Lunch Money's provider-side windowed fetch (RFC §B) — ``start_date``/``end_date`` are
    server-side params (the 120-day rolling window is gone; the caller's ``[since,
    before]`` is passed straight through). ``account`` filters client-side (the
    ``/transactions`` endpoint has no per-account request filter). Always
    ``complete=True``: ``fetch_transactions`` itself pages to exhaustion or
    ``_MAX_FETCH_PAGES``, matching every sibling provider's own safety cap.

    Group/split-parent detection (module docstring) runs here too: a row whose
    ``is_group_parent``/``is_split_parent`` is set is dropped (its real children carry the
    same money as separate rows), and the same mismatch-detector warning fires if that
    detection appears to have silently broken."""
    account_names = {} if accounts_only else fetch_account_names(store)
    if accounts_only:
        # A single-day window minimizes (never eliminates) the transaction rows an
        # accounts-only caller pays for; the account NAME lookup itself needs no window.
        txns = fetch_transactions(store, start_date=before, end_date=before)
        account_names = fetch_account_names(store)
    else:
        txns = fetch_transactions(store, start_date=since, end_date=before)

    base_currency = ledger_service.base_currency()
    seen_keys: dict[str, str] = {}  # account_key -> currency hint, for the accounts[] block
    rows: list[dict] = []
    group_parent_skipped = 0
    group_child_rows_seen = 0
    split_child_rows_seen = 0
    group_parent_rows_skipped = 0
    split_parent_rows_skipped = 0
    parents_with_children = _parents_with_children(txns)
    for lm_txn in txns:
        account_key = _account_key(lm_txn)
        if account_key:
            seen_keys.setdefault(account_key, (lm_txn.get("currency") or base_currency).upper())
        if account and account_key != account:
            continue
        if lm_txn.get("group_parent_id") is not None:
            group_child_rows_seen += 1
        if lm_txn.get("split_parent_id") is not None:
            split_child_rows_seen += 1
        is_group_parent = bool(lm_txn.get("is_group_parent"))
        is_split_parent = bool(lm_txn.get("is_split_parent"))
        if (is_group_parent or is_split_parent) and lm_txn.get("id") in parents_with_children:
            if is_group_parent:
                group_parent_rows_skipped += 1
            if is_split_parent:
                split_parent_rows_skipped += 1
            group_parent_skipped += 1
            continue
        if not account_key or accounts_only:
            continue
        row = _txn_to_row(lm_txn, account_key, base_currency)
        if row is not None:
            rows.append(row)

    warnings: list[str] = []
    if group_parent_skipped:
        logger.info(
            "lunch_money fetch: skipped %d group/split parent row(s) — expecting their "
            "children in the same feed",
            group_parent_skipped,
        )
    if group_child_rows_seen and not group_parent_rows_skipped:
        caution = (
            f"saw {group_child_rows_seen} row(s) with a non-null group_parent_id but skipped "
            "ZERO is_group_parent rows — group-parent detection may be broken; each child's "
            "parent total may be booked as an ordinary duplicate (double-counted)"
        )
        logger.warning("lunch_money fetch: %s", caution)
        warnings.append(caution)
    if split_child_rows_seen and not split_parent_rows_skipped:
        caution = (
            f"saw {split_child_rows_seen} row(s) with a non-null split_parent_id but skipped "
            "ZERO is_split_parent rows — split-parent detection may be broken; each child's "
            "parent total may be booked as an ordinary duplicate (double-counted)"
        )
        logger.warning("lunch_money fetch: %s", caution)
        warnings.append(caution)

    accounts = [
        {
            "provider_account_id": key,
            "name": account_names.get(key) or f"Lunch Money account ({key})",
            "mask": None,
            "currency": currency,
            "balance": None,
        }
        for key, currency in seen_keys.items()
        if not account or key == account
    ]
    return FetchWindow(accounts=accounts, rows=rows, complete=True, warnings=warnings)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Lunch Money."""
    if isinstance(exc, (ExternalServiceError, ValidationError)):
        msg = str(exc).lower()
        if "http 401" in msg or "http 403" in msg:
            return "auth_error"
        if "http 429" in msg:
            return "rate_limited"
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
