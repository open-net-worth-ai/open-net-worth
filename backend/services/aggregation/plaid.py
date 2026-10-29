"""Plaid aggregation provider — the bank account-aggregation data source for the US/Canada
market (Plaid is the dominant US aggregator).

Plaid (https://plaid.com/docs) is a hosted bank-data aggregator. Its consent flow is
redirect-shaped:

  * **Link a bank** — POST ``/link/token/create`` with ``hosted_link`` → a Plaid **Hosted
    Link** URL the USER opens in a browser to authenticate at their bank (NO frontend
    Link-SDK / npm dependency). On completion Plaid redirects back; the public_token is
    delivered to the configured redirect.
  * **Exchange** — POST ``/item/public_token/exchange`` ``{public_token}`` → an
    ``access_token`` (a bearer credential, write-only) + an ``item_id`` (the linked Item).
  * **List transactions** — POST ``/transactions/get`` ``{access_token, start_date,
    end_date, options: {count, offset}}`` → ``{accounts[], transactions[],
    total_transactions}``, paged via ``count``/``offset`` until every row is fetched. RFC
    ``connectors-as-data-sources`` §B: this REPLACES the cursor-based ``/transactions/sync``
    — an explicit date window is fetched every call, "new" means "no live ledger row
    carries the ref" (never "since the last run"), so there is no cursor to persist. A
    ``pending_transaction_id`` on a posted row is carried through as ``supersedes_ref``
    rather than silently dropped.

This module is the PURE protocol + normalize (``fetch_window``) — READ-ONLY, called from a
Lucius turn via ``plaid_fetch_transactions``:

  * ``create_link_token(redirect)`` — start the consent flow; returns the Hosted Link URL.
  * ``exchange_public_token(public_token)`` — swap the public_token for the access_token
    (+ item_id), persisted as write-only secrets on the config store.

There is no adapter class, no auto-created account, and no scheduled sync any more: the
provider-account → app-account link lives on ``Account.external_ids``, and booking happens
through ``add_transactions`` in the SAME turn that fetched, never here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.core.errors import ValidationError
from backend.db.plaid_config_store import PlaidConfigStore
from backend.models.account import AccountType
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

SOURCE_ID = "plaid"
EXTERNAL_SOURCE = "plaid"

_HTTP_TIMEOUT = 30.0
# Plaid product + the country/language for the link token (US-first; the user re-classifies).
_LINK_PRODUCTS = ["transactions"]
_LINK_COUNTRY_CODES = ["US"]
_LINK_LANGUAGE = "en"
_CLIENT_NAME = "Open Net Worth"
# /transactions/get page size + a guard against a pathological never-terminating page loop.
_PAGE_SIZE = 500
_MAX_FETCH_PAGES = 50


# ── low-level POST helper ────────────────────────────────────────────────────────────


def _post(store: PlaidConfigStore, path: str, payload: dict) -> dict:
    """POST a Plaid JSON-RPC-style request. Plaid auth is the client_id + secret in the body
    (not a header). Raises ``ValidationError`` on a non-2xx / transport failure (the
    orchestrator catches + records it)."""
    client_id = store.client_id()
    secret = store.secret()
    if not client_id or not secret:
        raise ValidationError("Plaid client_id / secret are not configured")
    body = {"client_id": client_id, "secret": secret, **payload}
    try:
        resp = httpx.post(
            f"{store.api_base()}{path}",
            json=body,
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Plaid: {exc}") from exc
    if resp.status_code not in (200, 201):
        # Plaid error bodies carry {error_code, error_message}; surface what's useful.
        detail = ""
        try:
            data = resp.json()
            detail = data.get("error_message") or data.get("error_code") or ""
        except Exception:
            detail = ""
        raise ValidationError(
            f"Plaid {path} failed: HTTP {resp.status_code}" + (f" — {detail}" if detail else "")
        )
    return resp.json() or {}


# ── link token + public-token exchange (the consent flow) ─────────────────────────────


def create_link_token(redirect: str = "") -> dict:
    """Start the bank-consent flow → return a Plaid **Hosted Link** URL the USER opens.

    POSTs ``/link/token/create`` with ``hosted_link`` so the link runs entirely in a browser
    (NO frontend Link-SDK dependency). Returns ``{link, link_token, expiration}``. Requires
    credentials. Raises ``ValidationError`` on a bad config / auth / transport failure."""
    store = PlaidConfigStore()
    payload: dict = {
        "client_name": _CLIENT_NAME,
        "language": _LINK_LANGUAGE,
        "country_codes": _LINK_COUNTRY_CODES,
        "user": {"client_user_id": "open-net-worth"},
        "products": _LINK_PRODUCTS,
        # Hosted Link — Plaid hosts the whole flow; the user just opens this URL.
        "hosted_link": {},
    }
    redirect_url = (redirect or "").strip()
    if redirect_url:
        payload["redirect_uri"] = redirect_url

    data = _post(store, "/link/token/create", payload)
    link = (data.get("hosted_link_url") or "").strip()
    link_token = (data.get("link_token") or "").strip()
    if not link:
        raise ValidationError("Plaid did not return a hosted-link URL")
    # Hosted Link ends on Plaid's own "you can close this window" page and never hands the
    # user a public token — ``complete_hosted_link`` reads it back with this token.
    store.update({"link_token": link_token})
    return {"link": link, "link_token": link_token, "expiration": data.get("expiration")}


def _session_public_token(data: dict) -> str:
    """The public token of the most recent finished Hosted Link session in a
    ``/link/token/get`` response, else ``""`` (the user has not finished yet)."""
    for session in reversed(data.get("link_sessions") or []):
        results = session.get("results") or {}
        for item in reversed(results.get("item_add_results") or []):
            token = str(item.get("public_token") or "").strip()
            if token:
                return token
        legacy = str((session.get("on_success") or {}).get("public_token") or "").strip()
        if legacy:
            return legacy
    return ""


def complete_hosted_link() -> dict:
    """Finish the Hosted Link flow the app started: POST ``/link/token/get`` with the stored
    link token, and exchange the session's public token once the user has finished at their
    bank. Returns ``{status: "pending"}`` while they haven't, else the exchange result with
    ``status: "linked"``; the link token is cleared once used. Raises ``ValidationError`` when
    no link is in flight or Plaid refuses."""
    store = PlaidConfigStore()
    link_token = store.link_token()
    if not link_token:
        raise ValidationError("No Plaid link is in progress — click Link bank first")
    data = _post(store, "/link/token/get", {"link_token": link_token})
    public_token = _session_public_token(data)
    if not public_token:
        return {"status": "pending"}
    result = exchange_public_token(public_token)
    store.clear_link_token()
    return {"status": "linked", **result}


def exchange_public_token(public_token: str) -> dict:
    """Swap a ``public_token`` (delivered after the user finishes Hosted Link) for an
    ``access_token`` + ``item_id``, persisted as write-only secrets on the config store.

    POSTs ``/item/public_token/exchange``. Returns ``{item_id, has_access_token}`` (never
    the access token itself). Raises ``ValidationError`` on a bad/used token / auth
    failure."""
    token = (public_token or "").strip()
    if not token:
        raise ValidationError("public_token is required")

    store = PlaidConfigStore()
    data = _post(store, "/item/public_token/exchange", {"public_token": token})
    access_token = (data.get("access_token") or "").strip()
    item_id = (data.get("item_id") or "").strip()
    if not access_token:
        raise ValidationError("Plaid did not return an access token")
    store.update({"access_token": access_token, "item_id": item_id})
    return {"item_id": item_id, "has_access_token": True}


# ── transaction listing (windowed, RFC §B) ─────────────────────────────────────────────


def fetch_transactions_get(store: PlaidConfigStore, *, start_date: str, end_date: str) -> dict:
    """POST ``/transactions/get`` paged via ``count``/``offset`` until every row in
    ``[start_date, end_date]`` is fetched. Returns ``{transactions[], accounts{account_id:
    account}}``. Raises ``ValidationError`` on auth/transport failure."""
    access_token = store.access_token()
    if not access_token:
        raise ValidationError("Plaid is not linked (no access token)")

    transactions: list[dict] = []
    accounts: dict[str, dict] = {}
    offset = 0
    for _ in range(_MAX_FETCH_PAGES):
        payload = {
            "access_token": access_token,
            "start_date": start_date,
            "end_date": end_date,
            "options": {"count": _PAGE_SIZE, "offset": offset},
        }
        data = _post(store, "/transactions/get", payload)
        page = data.get("transactions") or []
        transactions.extend(page)
        for acct in data.get("accounts") or []:
            acct_id = acct.get("account_id")
            if acct_id:
                accounts[acct_id] = acct
        total = data.get("total_transactions")
        offset += len(page)
        if not page or (isinstance(total, int) and offset >= total):
            break
    return {"transactions": transactions, "accounts": accounts}


# ── normalize + fetch_window ─────────────────────────────────────────────────────────


# Plaid investment subtypes that are tax-advantaged retirement wrappers (Plaid's account-type
# schema, US + Canada); every other investment subtype is a plain brokerage account.
_RETIREMENT_SUBTYPES = frozenset(
    {
        "401a",
        "401k",
        "403b",
        "457b",
        "529",
        "ira",
        "keogh",
        "lif",
        "lira",
        "lrif",
        "lrsp",
        "pension",
        "prif",
        "profit sharing plan",
        "qshr",
        "rdsp",
        "retirement",
        "rlif",
        "roth",
        "roth 401k",
        "rrif",
        "rrsp",
        "sarsep",
        "sep ira",
        "simple ira",
        "sipp",
        "thrift savings plan",
    }
)


def _suggested_account_type(account: dict) -> str | None:
    """Plaid's ``type``/``subtype`` → the app's account type, as a SUGGESTION for the create
    form (the user still picks; nothing is stored from it). ``None`` when Plaid's type says
    nothing useful (``other``, or absent)."""
    kind = str(account.get("type") or "").lower()
    subtype = str(account.get("subtype") or "").lower()
    if kind == "depository":
        return AccountType.fixed_deposit.value if subtype == "cd" else AccountType.cash.value
    if kind == "credit":
        return AccountType.credit.value
    if kind == "loan":
        return AccountType.loan.value
    if kind == "investment":
        if subtype in _RETIREMENT_SUBTYPES:
            return AccountType.retirement.value
        return AccountType.investment.value
    return None


def _account_to_common(account: dict) -> dict:
    """A Plaid account → the common ``accounts[]`` shape (RFC §A)."""
    name = account.get("official_name") or account.get("name") or "Plaid account"
    balances = account.get("balances") or {}
    currency = (
        balances.get("iso_currency_code") or balances.get("unofficial_currency_code") or ""
    ).upper() or None
    return {
        "provider_account_id": str(account.get("account_id") or ""),
        "name": str(name),
        "mask": account.get("mask"),
        "currency": currency,
        "balance": balances.get("current"),
        "account_type": _suggested_account_type(account),
    }


def _txn_amount(plaid_txn: dict) -> float | None:
    """Pull the signed amount from a Plaid transaction. Plaid's ``amount`` is POSITIVE for
    money LEAVING the account (an expense) and NEGATIVE for money coming IN — the opposite
    of this app's own convention — so we flip it to the standard "+ = money in". Returns
    None for an unparseable/zero amount."""
    amt = plaid_txn.get("amount")
    try:
        value = float(amt)
    except (TypeError, ValueError):
        return None
    # Flip Plaid's sign so positive = money in (matches every other bank feed's convention).
    signed = -value
    return signed or None


def _txn_id(plaid_txn: dict) -> str:
    """The stable Plaid transaction id (``transaction_id``). Empty string if absent."""
    return str(plaid_txn.get("transaction_id") or "").strip()


def _txn_date(plaid_txn: dict) -> str:
    """Posted date (``date``, then ``authorized_date``), else today (UTC)."""
    raw = plaid_txn.get("date") or plaid_txn.get("authorized_date") or ""
    try:
        return datetime.fromisoformat(str(raw)).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).date().isoformat()


def _txn_description(plaid_txn: dict) -> str:
    """Best human description: merchant name, then the cleaned ``name``, then a fallback."""
    info = plaid_txn.get("merchant_name") or plaid_txn.get("name")
    return (str(info).strip() if info else "") or "Plaid"


def _txn_to_row(plaid_txn: dict, account: dict, account_id: str) -> dict | None:
    """One Plaid transaction → the common normalized row shape (RFC §A step 3).

    ``pending`` is carried through rather than dropped (RFC: pending filtering is
    ``connector_fetch``'s job, via ``include_pending``, not the provider's). A posted row
    carrying ``pending_transaction_id`` sets ``supersedes_ref`` to the SAME
    ``<account>:<id>`` shape the (now superseded) pending row was originally emitted under,
    so the caller can tell which draft it replaces. Returns None only for an
    unparseable/zero amount (a missing ``transaction_id`` still gets a derived ref —
    Plaid's schema requires one, but this stays defensive like every sibling provider)."""
    amount = _txn_amount(plaid_txn)
    if amount is None:
        return None

    currency = (
        plaid_txn.get("iso_currency_code")
        or plaid_txn.get("unofficial_currency_code")
        or account.get("currency")
        or ledger_service.base_currency()
    ).upper()
    date_str = _txn_date(plaid_txn)
    description = _txn_description(plaid_txn)
    txn_id = _txn_id(plaid_txn)
    ref = (
        f"{account_id}:{txn_id}"
        if txn_id
        else derived_ref(account_id, date_str, amount, currency, description)
    )
    pending_id = str(plaid_txn.get("pending_transaction_id") or "").strip()
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": plaid_txn,
        "provider_account_id": account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": bool(plaid_txn.get("pending")),
        "supersedes_ref": f"{account_id}:{pending_id}" if pending_id else None,
    }


def fetch_window(
    store: PlaidConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Plaid's provider-side windowed fetch (RFC §B) — ``/transactions/get`` with an
    explicit ``start_date``/``end_date``, paged via ``count``/``offset`` until every row in
    the window is fetched (``complete=True`` always: the page loop only stops once Plaid's
    own ``total_transactions`` is reached, or after ``_MAX_FETCH_PAGES`` as a safety cap —
    the latter is the one case this reports ``complete=False``, so a capped fetch never
    silently looks like a clean window for the vanished-row check). ``account`` filters
    client-side (Plaid's ``/transactions/get`` has no per-account request filter)."""
    if accounts_only:
        # A single-day window minimizes (never eliminates — Plaid has no explicit
        # accounts-only endpoint) the transaction rows an accounts-only caller pays for.
        fetched = fetch_transactions_get(store, start_date=before, end_date=before)
    else:
        fetched = fetch_transactions_get(store, start_date=since, end_date=before)
    accounts_by_id = fetched["accounts"]
    accounts = [_account_to_common(a) for a in accounts_by_id.values()]

    rows: list[dict] = []
    if not accounts_only:
        truncated = len(fetched["transactions"]) >= _PAGE_SIZE * _MAX_FETCH_PAGES
        for plaid_txn in fetched["transactions"]:
            account_id = str(plaid_txn.get("account_id") or "").strip()
            if not account_id or (account and account_id != account):
                continue
            row = _txn_to_row(plaid_txn, accounts_by_id.get(account_id, {}), account_id)
            if row is not None:
                rows.append(row)
        return FetchWindow(accounts=accounts, rows=rows, complete=not truncated)
    return FetchWindow(accounts=accounts, rows=rows, complete=True)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Plaid. ``_post`` wraps EVERY transport/HTTP failure
    into ``ValidationError`` (missing creds, a non-2xx response, and a raw connectivity
    failure alike), so the exception CLASS alone can't tell "bad credentials" apart from
    "the network is down" — inspect the message the way ``simplefin.py``/``yodlee.py`` do,
    rather than reporting every one as auth_error (which would tell a user to re-enter
    working credentials over a transient outage)."""
    if isinstance(exc, ValidationError):
        msg = str(exc).lower()
        if "could not reach plaid" in msg:
            return "connect_error"
        if "http 429" in msg:
            return "rate_limited"
        if "http 401" in msg or "http 403" in msg or "not configured" in msg:
            return "auth_error"
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
