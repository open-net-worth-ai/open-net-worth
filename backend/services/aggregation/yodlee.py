"""Yodlee (Envestnet | Yodlee) aggregation provider — the 6th bank account-aggregation
data source, for a broad global market (Yodlee is one of the largest financial data
aggregators, serving US, Canada, and international markets).

Yodlee API (https://developer.envestnet.com/yodlee) uses a **cobrand + user** session
model:

  1. **Cobrand login** — POST ``/v1/cobrand/login`` with ``cobrandLogin`` + ``cobrandPassword``
     (= ``client_id`` + ``secret``) → a ``cobrandSessionToken`` (short-lived, ~30min).
  2. **User login** — POST ``/v1/user/login`` with ``loginName`` + ``password`` and the
     ``cobrandSessionToken`` as a header → a ``userSession.userSessionToken``. This is the
     credential needed to call all data APIs.
  3. **Accounts** — GET ``/v1/accounts`` with the user session token → list of linked
     financial accounts.
  4. **Transactions** — GET ``/v1/transactions`` with ``fromDate`` / ``toDate`` (YYYY-MM-DD)
     paginated via ``skip`` + ``top``. We sync a rolling lookback window (``fromDate`` =
     last synced date or 90 days ago on first sync).

Unlike Plaid there is no cursor — Yodlee transactions are date-windowed. Dedup via
``external_source = "yodlee"`` + ``external_ref = "<account_id>:<transaction_id>"`` makes
re-syncs idempotent.

This module is the PURE protocol + normalize (``fetch_window``) — READ-ONLY, called from a
Lucius turn via ``yodlee_fetch_transactions`` (RFC ``connectors-as-data-sources``; Yodlee is
also policy-disabled, ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing`` — its tool is
registered but hidden/refused). There is no adapter class, no auto-created account, and no
scheduled sync any more — the provider-account → app-account link lives on
``Account.external_ids``, and booking happens through ``add_transactions`` in the SAME turn
that fetched, never here.

Public entrypoints:
  * ``authenticate(store)`` — obtain fresh cobrand + user session tokens; called at the
    start of every fetch (Yodlee sessions are short-lived). Mutates the store.
  * ``fetch_window`` — the fetch tool's entrypoint. Dormant unless enabled+configured.

v1 scope: transaction aggregation only. Investment-holdings sync (positions + prices)
is explicitly out of scope for v1 — Yodlee's ``/v1/holdings`` and ``/v1/accounts?container=investment``
are richer but require more complex mapping; a TODO marker is left below.

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
from backend.db.yodlee_config_store import YodleeConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow

logger = logging.getLogger(__name__)

SOURCE_ID = "yodlee"
EXTERNAL_SOURCE = "yodlee"

_HTTP_TIMEOUT = 30.0
# Number of transactions per page (Yodlee ``top`` param).
_PAGE_SIZE = 500
# Guard against a pathological never-terminating pagination loop.
_MAX_PAGES = 50


# ── low-level GET / POST helpers ─────────────────────────────────────────────────


def _headers(cobrand_token: str, user_token: str = "") -> dict:
    """Build the Authorization header Yodlee expects.

    All authenticated requests carry ``Authorization: cobSession=<cobrand_token>``.
    Data-API calls additionally carry ``Authorization: cobSession=<cobrand>,userSession=<user>``.
    """
    if user_token:
        return {"Authorization": f"cobSession={cobrand_token},userSession={user_token}"}
    return {"Authorization": f"cobSession={cobrand_token}"}


def _post(url: str, payload: dict, *, extra_headers: dict | None = None) -> dict:
    """POST JSON, raise ``ValidationError`` on a non-2xx / transport failure."""
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Yodlee: {exc}") from exc
    if resp.status_code not in (200, 201):
        detail = ""
        try:
            data = resp.json()
            err = data.get("errorCode") or data.get("title") or ""
            detail = err
        except Exception:
            pass
        raise ValidationError(
            f"Yodlee POST {url} failed: HTTP {resp.status_code}"
            + (f" — {detail}" if detail else "")
        )
    return resp.json() or {}


def _get(url: str, *, params: dict | None = None, extra_headers: dict | None = None) -> dict:
    """GET JSON, raise ``ValidationError`` on a non-2xx / transport failure."""
    headers: dict = {}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = httpx.get(url, params=params or {}, headers=headers, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Yodlee: {exc}") from exc
    if resp.status_code not in (200, 201):
        detail = ""
        try:
            data = resp.json()
            err = data.get("errorCode") or data.get("title") or ""
            detail = err
        except Exception:
            pass
        raise ValidationError(
            f"Yodlee GET {url} failed: HTTP {resp.status_code}" + (f" — {detail}" if detail else "")
        )
    return resp.json() or {}


# ── auth: cobrand + user login ────────────────────────────────────────────────────


def authenticate(store: YodleeConfigStore) -> tuple[str, str]:
    """Obtain a fresh cobrand session token + user session token.

    Yodlee sessions are short-lived (~30min) so we refresh both at the start of
    every sync rather than trying to reuse a cached token.

    Returns ``(cobrand_session_token, user_session_token)``. Raises
    ``ValidationError`` on bad credentials / transport failure (the orchestrator
    catches + records it)."""
    client_id = store.client_id()
    secret = store.secret()
    username = store.username()
    password = store.password()
    if not client_id or not secret:
        raise ValidationError("Yodlee client_id / secret are not configured")
    if not username or not password:
        raise ValidationError("Yodlee username / password are not configured")

    base = store.api_base()

    # Step 1: cobrand login
    cobrand_data = _post(
        f"{base}/v1/cobrand/login",
        {
            "cobrand": {
                "cobrandLogin": client_id,
                "cobrandPassword": secret,
            }
        },
    )
    cobrand_token = ((cobrand_data.get("session") or {}).get("cobSession") or "").strip()
    if not cobrand_token:
        raise ValidationError("Yodlee cobrand login did not return a session token")

    # Step 2: user login
    user_data = _post(
        f"{base}/v1/user/login",
        {
            "user": {
                "loginName": username,
                "password": password,
            }
        },
        extra_headers=_headers(cobrand_token),
    )
    user_session = user_data.get("user", {}).get("session") or {}
    user_token = (user_session.get("userSession") or "").strip()
    if not user_token:
        raise ValidationError("Yodlee user login did not return a user session token")

    # Persist the fresh tokens (write-only — never returned to the frontend).
    store.update(
        {
            "cobrand_session_token": cobrand_token,
            "user_session_token": user_token,
        }
    )
    return cobrand_token, user_token


# ── accounts + transactions fetch ─────────────────────────────────────────────────


def fetch_accounts(store: YodleeConfigStore, cobrand_token: str, user_token: str) -> list[dict]:
    """Fetch all linked accounts for the user.

    GET ``/v1/accounts``. Returns the ``account`` list from the response.
    ``container`` is intentionally not filtered so all accounts (bank, card, loan,
    investment, insurance) are returned; we map only the transactional containers
    to ledger accounts. Raises ``ValidationError`` on transport/auth failure.

    TODO(yodlee-v2): investment-holdings sync via ``container=investment`` +
    ``/v1/holdings`` — out of scope for v1.
    """
    base = store.api_base()
    data = _get(
        f"{base}/v1/accounts",
        extra_headers=_headers(cobrand_token, user_token),
    )
    return data.get("account") or []


def fetch_transactions(
    store: YodleeConfigStore,
    cobrand_token: str,
    user_token: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Fetch all transactions in [from_date, to_date] via paginated GET ``/v1/transactions``.

    Yodlee paginates via ``skip`` + ``top`` (not a cursor).  We loop until the
    page is shorter than the page-size or we hit ``_MAX_PAGES``. Returns the
    combined ``transaction`` list. Raises ``ValidationError`` on auth/transport
    failure.
    """
    base = store.api_base()
    auth = _headers(cobrand_token, user_token)
    all_txns: list[dict] = []
    skip = 0
    pages = 0
    while True:
        params = {
            "fromDate": from_date,
            "toDate": to_date,
            "skip": skip,
            "top": _PAGE_SIZE,
        }
        data = _get(f"{base}/v1/transactions", params=params, extra_headers=auth)
        batch = data.get("transaction") or []
        all_txns.extend(batch)
        pages += 1
        if len(batch) < _PAGE_SIZE or pages >= _MAX_PAGES:
            break
        skip += len(batch)
    return all_txns


# ── mapping helpers ──────────────────────────────────────────────────────────────


def _account_to_common(yodlee_account: dict) -> dict:
    """A Yodlee account → the common ``accounts[]`` shape (RFC §A)."""
    name = (
        yodlee_account.get("accountName") or yodlee_account.get("accountType") or "Yodlee account"
    )
    # Yodlee currency: nested {"currency": "USD"} or top-level "currency".
    currency = (
        (yodlee_account.get("currentBalance") or {}).get("currency")
        or (yodlee_account.get("balance") or {}).get("currency")
        or yodlee_account.get("currency")
    )
    resolved_currency = (
        currency.upper() if isinstance(currency, str) else ledger_service.base_currency()
    )
    return {
        "provider_account_id": str(yodlee_account.get("id") or ""),
        # The balance sync must not read the base it falls back to as a reported currency.
        "currency_inferred": not isinstance(currency, str),
        "name": str(name),
        "mask": None,
        "currency": resolved_currency,
        "balance": (yodlee_account.get("currentBalance") or {}).get("amount"),
    }


def _txn_amount(yodlee_txn: dict) -> float | None:
    """Pull the signed amount from a Yodlee transaction.

    Yodlee stores the amount as a nested ``{"amount": <float>}`` under ``amount``.
    The ``baseType`` field indicates whether the transaction is a CREDIT (money in)
    or DEBIT (money out). We normalize to the convention: positive = money in,
    negative = money out. Returns None for unparseable/zero amounts.
    """
    raw_amount = yodlee_txn.get("amount") or {}
    try:
        value = float(raw_amount.get("amount", 0))
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    base_type = (yodlee_txn.get("baseType") or "").upper()
    # CREDIT = money in (positive); DEBIT = money out (negative).
    if base_type == "DEBIT":
        value = -abs(value)
    else:
        value = abs(value)
    return value or None


def _txn_id(yodlee_txn: dict) -> str:
    """The stable Yodlee transaction id. Empty string if absent."""
    return str(yodlee_txn.get("id") or "").strip()


def _txn_date(yodlee_txn: dict) -> str:
    """Transaction date (``date``, then ``transactionDate``), else today (UTC).

    Yodlee returns dates as ``YYYY-MM-DD`` strings."""
    raw = yodlee_txn.get("date") or yodlee_txn.get("transactionDate") or ""
    try:
        return datetime.fromisoformat(str(raw)).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).date().isoformat()


def _txn_description(yodlee_txn: dict) -> str:
    """Best human description: merchant name, then description, then a fallback."""
    info = (
        yodlee_txn.get("merchant", {}).get("name")
        if isinstance(yodlee_txn.get("merchant"), dict)
        else None
    )
    if not info:
        info = (
            yodlee_txn.get("description", {}).get("original")
            if isinstance(yodlee_txn.get("description"), dict)
            else yodlee_txn.get("description")
        )
    return (str(info).strip() if info else "") or "Yodlee"


def _txn_currency(yodlee_txn: dict, account_currency: str) -> str:
    """Transaction currency (from ``amount.currency`` or account currency)."""
    raw = yodlee_txn.get("amount") or {}
    cur = (raw.get("currency") or account_currency or "").upper()
    return cur or ledger_service.base_currency()


def _txn_to_row(yodlee_txn: dict, account_id: str, currency_hint: str) -> dict | None:
    """One Yodlee transaction → the common normalized row shape (RFC §A step 3).

    Returns None for an unparseable/zero amount, or a still-pending/scheduled row
    (pending filtering proper is ``connector_fetch``'s job via ``include_pending``, but a
    Yodlee ``PENDING``/``SCHEDULED`` row may not even have a stable id yet, so it is
    dropped here rather than risking a duplicate when it settles)."""
    status = (yodlee_txn.get("status") or "").upper()
    if status in ("PENDING", "SCHEDULED"):
        return None
    txn_id = _txn_id(yodlee_txn)
    if not txn_id:
        return None
    amount = _txn_amount(yodlee_txn)
    if amount is None:
        return None
    currency = _txn_currency(yodlee_txn, currency_hint)
    date_str = _txn_date(yodlee_txn)
    description = _txn_description(yodlee_txn)
    ref = f"{account_id}:{txn_id}"
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": yodlee_txn,
        "provider_account_id": account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }


def fetch_window(
    store: YodleeConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Yodlee's provider-side windowed fetch (RFC §B, unchanged from before this RFC) —
    ``fromDate``/``toDate`` are server-side params; ``account`` filters client-side (the
    transactions endpoint has no per-account request filter). Always ``complete=True``:
    ``fetch_transactions`` itself pages to exhaustion or ``_MAX_PAGES``, matching every
    sibling provider's own safety cap."""
    cobrand_token, user_token = authenticate(store)
    accounts_raw = fetch_accounts(store, cobrand_token, user_token)
    if account:
        accounts_raw = [a for a in accounts_raw if str(a.get("id") or "") == account]
    accounts_by_id = {str(a.get("id") or ""): a for a in accounts_raw if a.get("id")}
    accounts = [_account_to_common(a) for a in accounts_raw]

    rows: list[dict] = []
    if not accounts_only:
        txns = fetch_transactions(store, cobrand_token, user_token, since, before)
        for yodlee_txn in txns:
            acct_id = str(yodlee_txn.get("accountId") or "").strip()
            if not acct_id or (account and acct_id != account):
                continue
            yodlee_acct = accounts_by_id.get(acct_id, {})
            currency_hint = (_account_to_common(yodlee_acct).get("currency") or "").strip()
            row = _txn_to_row(yodlee_txn, acct_id, currency_hint)
            if row is not None:
                rows.append(row)
    return FetchWindow(accounts=accounts, rows=rows, complete=True)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Yodlee."""
    if isinstance(exc, ValidationError):
        msg = (exc.message or str(exc)).lower()
        if "credentials" in msg or "login" in msg or "session" in msg or "auth" in msg:
            return "auth_error"
        return "error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
