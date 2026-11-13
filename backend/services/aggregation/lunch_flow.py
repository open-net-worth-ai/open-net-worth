"""Lunch Flow aggregation provider — the fetch half of the Lunch Flow connector (the
OAuth half lives in ``lunch_flow_oauth.py``).

Lunch Flow (https://lunchflow.app/docs/api/platform-api-overview, base
``https://lunchflow.app/api/platform/v1``) is a multi-institution, OAuth-based bank/investment
aggregation PLATFORM across 30+ countries — architecturally closer to Plaid/Enable Banking
than to a single-token bridge like SimpleFIN/Lunch Money. Once a bank is linked (see
``lunch_flow_oauth.py``), syncing is a plain Bearer-token REST pull:

  * ``GET /accounts`` → ``{accounts: [{id, connection_id, name, institution_name,
    institution_logo, provider, currency, status}], total}``.
  * ``GET /accounts/{id}/transactions`` → ``{transactions: [{id, accountId, amount,
    currency, date, merchant, description, isPending}], total}`` (``include_pending``
    defaults to ``false`` server-side, so pending rows are already excluded before we ever
    see them — the defensive ``isPending`` check below covers a server that ships them
    anyway, matching Plaid's own belt-and-suspenders pending skip).
  * ``DELETE /connections/{id}`` → unlink a whole institution (``disconnect_connection``
    below) — Lunch Flow groups accounts under a CONNECTION (one per linked institution), not
    one linked item at a time.

This module is the PURE protocol + normalize (``fetch_window``) — READ-ONLY, called from a
Lucius turn via ``lunch_flow_fetch_transactions`` (RFC ``connectors-as-data-sources``).
There is no adapter class, no auto-created account, and no scheduled sync any more — the
provider-account → app-account link lives on ``Account.external_ids``, and booking happens
through ``add_transactions`` in the SAME turn that fetched, never here. No provider-side
window either: like before, Lunch Flow's transactions endpoint is a plain unbounded GET
with no documented date filter, so ``fetch_window`` fetches each account's WHOLE available
history and filters to ``[since, before]`` client-side.

**UNVERIFIED SIGN CONVENTION** — the actual decision + reasoning live on ``_txn_to_row``
below (search there, not here — a security review found this docstring-only note effectively
invisible at the point that matters). This paragraph is only a pointer for a reader scanning
the module. ``fetch_window`` also runs a runtime smell test (``_polarity_smell_test``) over
every fetch's own income/expense split — a cheap tripwire for a wrong guess, since a green
test suite alone proves nothing here (the fixtures encode the same unverified assumption).
See the connector's ADR (security-review addendum) for the full trail. **Compounding note**:
a credit or loan type account linked to this connector inherits BOTH the raw-negative-balance
convention AND this sign guess, so a wrong call on either axis doubles up rather than cancels
out for that account.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from backend.core.errors import ValidationError
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation import lunch_flow_oauth
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

SOURCE_ID = "lunch_flow"
EXTERNAL_SOURCE = "lunch_flow"

API_BASE = "https://lunchflow.app/api/platform/v1"
_HTTP_TIMEOUT = 30.0


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


class _LunchFlowUnauthorized(ValidationError):
    """Internal signal: the vendor answered a request with HTTP 401 — a LIVE token rejection,
    distinct from every other API failure. Caught exactly once by ``LunchFlowAdapter.fetch``,
    which force-refreshes and retries the SAME request once before giving up (mirrors
    ``mcp/client.py``'s ``_OAuthUnauthorized``/one-shot reactive-401 retry)."""


def _api_error(resp: httpx.Response, action: str) -> ValidationError:
    detail = ""
    try:
        data = resp.json()
        detail = data.get("message") or data.get("error") or ""
    except Exception:
        detail = ""
    message = f"Lunch Flow {action} failed: HTTP {resp.status_code}" + (
        f" — {detail}" if detail else ""
    )
    if resp.status_code == 401:
        return _LunchFlowUnauthorized(message)
    return ValidationError(message)


# ── account / transaction fetch ──────────────────────────────────────────────────────


def fetch_accounts(access_token: str) -> list[dict]:
    """``GET /accounts`` → the linked account list. Raises ``ValidationError``/
    ``httpx.HTTPError`` on failure (the orchestrator catches it)."""
    resp = httpx.get(
        f"{API_BASE}/accounts", headers=_auth_headers(access_token), timeout=_HTTP_TIMEOUT
    )
    if resp.status_code != 200:
        raise _api_error(resp, "listing accounts")
    data = resp.json() or {}
    return [a for a in (data.get("accounts") or []) if isinstance(a, dict)]


def fetch_account_transactions(access_token: str, account_id: object) -> list[dict]:
    """``GET /accounts/{id}/transactions`` → this account's transaction list (``
    include_pending`` left at its documented ``false`` default). Raises ``ValidationError``/
    ``httpx.HTTPError`` on failure."""
    resp = httpx.get(
        f"{API_BASE}/accounts/{account_id}/transactions",
        headers=_auth_headers(access_token),
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise _api_error(resp, f"fetching transactions for account {account_id}")
    data = resp.json() or {}
    return [t for t in (data.get("transactions") or []) if isinstance(t, dict)]


def disconnect_connection(connection_id: int) -> dict:
    """``DELETE /connections/{id}`` — unlink a whole institution. On success, forgets that
    connection's accounts from the local bookkeeping (``LunchFlowConfigStore.
    forget_connection``) — the Open Net Worth account(s) themselves are left untouched, same
    non-destructive posture as every other aggregation connector. Raises ``ValidationError``
    on a bad/expired token or a Lunch Flow-side failure."""
    store = LunchFlowConfigStore()
    access_token = lunch_flow_oauth.ensure_fresh_token()
    try:
        resp = httpx.delete(
            f"{API_BASE}/connections/{connection_id}",
            headers=_auth_headers(access_token),
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Lunch Flow: {exc}") from exc
    if resp.status_code not in (200, 204):
        raise _api_error(resp, "disconnecting the bank")
    forgotten = store.forget_connection(connection_id)
    return {"ok": True, "connection_id": connection_id, "accounts_unlinked": len(forgotten)}


# ── mapping helpers ──────────────────────────────────────────────────────────────────


def _account_to_common(lf_account: dict) -> dict:
    """A Lunch Flow account → the common ``accounts[]`` shape (RFC §A)."""
    name = lf_account.get("name") or lf_account.get("institution_name") or "Lunch Flow account"
    return {
        "provider_account_id": str(lf_account.get("id")),
        "name": str(name),
        "mask": None,
        "currency": (lf_account.get("currency") or "").upper() or None,
        "balance": None,
    }


def _txn_id(lf_txn: dict) -> str:
    """The stable Lunch Flow transaction id (``id``). Empty string if absent."""
    return str(lf_txn.get("id") or "").strip()


def _txn_amount(lf_txn: dict) -> float | None:
    """Pull the signed native-currency amount — see the module docstring's UNVERIFIED SIGN
    CONVENTION note. Returns None for an unparseable/zero amount."""
    raw = lf_txn.get("amount")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def _txn_date(lf_txn: dict) -> str:
    """Posted date (``date``), else today (UTC)."""
    raw = lf_txn.get("date") or ""
    try:
        return datetime.fromisoformat(str(raw)).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).date().isoformat()


def _txn_description(lf_txn: dict) -> str:
    """The bank's own wording (``description``), then the merchant, then a fallback."""
    info = lf_txn.get("description") or lf_txn.get("merchant")
    return (str(info).strip() if info else "") or "Lunch Flow"


def _ref_text(lf_txn: dict) -> str:
    info = lf_txn.get("merchant") or lf_txn.get("description")
    return (str(info).strip() if info else "") or "Lunch Flow"


def _txn_payee(lf_txn: dict) -> str | None:
    """The merchant Lunch Flow names, as the row's payee. It used to REPLACE the
    description, so the bank's wording — what payee matching learns from — was lost."""
    name = " ".join(str(lf_txn.get("merchant") or "").split())
    return name[:120] or None


def _txn_to_row(lf_txn: dict, account_id: str, currency_hint: str) -> dict | None:
    """One Lunch Flow transaction → the common normalized row shape (RFC §A step 3).

    UNVERIFIED SIGN CONVENTION — see the module docstring for the pointer. Lunch Flow's
    docs show "amount": 0 as a placeholder with no signed worked example; this assumes the
    GoCardless/Nordigen Open Banking "standard" polarity (POSITIVE = money IN), already this
    app's own convention, so NO flip is applied here. Never confirmed against a live
    account — ``fetch_window``'s polarity smell test is the runtime tripwire for a wrong
    guess. Returns None for an unparseable/zero amount (pending filtering is
    ``connector_fetch``'s job via ``include_pending``, not this function's)."""
    amount = _txn_amount(lf_txn)
    if amount is None:
        return None
    currency = (lf_txn.get("currency") or currency_hint).upper()
    date_str = _txn_date(lf_txn)
    description = _txn_description(lf_txn)
    txn_id = _txn_id(lf_txn)
    ref = (
        f"{account_id}:{txn_id}"
        if txn_id
        # Hashed on the merchant-first text it always was: an id-less row booked before the
        # description changed must keep its key, or the next fetch books it again.
        else derived_ref(account_id, date_str, amount, currency, _ref_text(lf_txn))
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": lf_txn,
        "provider_account_id": account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "payee": _txn_payee(lf_txn),
        "memo": None,
        "pending": bool(lf_txn.get("isPending")),
        "supersedes_ref": None,
    }


# ── polarity smell test (security review, 2026-08-17) ──────────────────────────────────

_POLARITY_SAMPLE_FLOOR = 10  # below this, a lopsided split is unremarkable — not enough signal
_POLARITY_INCOME_SHARE_CEILING = 0.8  # a real bank feed is debit-heavy; > 80% income is a smell


def _polarity_smell_test(income_count: int, expense_count: int) -> str | None:
    """A cheap RUNTIME signal for a backwards sign convention (see ``_txn_to_draft``'s inline
    comment for the underlying assumption this is watching).

    A real bank feed is debit-heavy and essentially never one-sided over any meaningful
    window — a run of >= 10 mapped rows with an implausibly high income share, or literally
    every row landing on one side, is the exact signature ``amount > 0``/``amount < 0`` being
    swapped would produce (a feed that is normally mostly debits would come out mostly
    "income" under a flipped sign). Advisory only: this never blocks a commit, it only makes
    a wrong guess IMPOSSIBLE to miss — see ``LunchFlowAdapter.map``/``record_status`` for how
    the caution reaches the operator regardless of the run's otherwise-"ok" status.

    Returns a human-readable caution string, or ``None`` when the split looks plausible."""
    total = income_count + expense_count
    if total < _POLARITY_SAMPLE_FLOOR:
        return None
    income_share = income_count / total
    one_sided = income_count == 0 or expense_count == 0
    if income_share <= _POLARITY_INCOME_SHARE_CEILING and not one_sided:
        return None
    return (
        f"{income_count} of {total} fetched transactions ({income_share:.0%}) this run "
        "mapped to income — a real bank feed is debit-heavy, so this looks like a backwards "
        "amount sign (see the UNVERIFIED SIGN CONVENTION note in _txn_to_row). Please check "
        "a few of these against your real Lunch Flow account before trusting the amounts."
    )


def _reactive_401_retry(access_token: str, call):
    """Run ``call(access_token)``; on a LIVE 401 (``_LunchFlowUnauthorized``), force a
    refresh and retry ``call`` exactly once with the fresh token — never a second retry
    (a 401 right after a forced refresh means something deeper is wrong — e.g. a revoked
    connection — and should surface as ``auth_error``, not loop). Returns ``(result,
    access_token)``: the token may have been refreshed, and every later call in this fetch
    should keep using whichever one came back. This is the reactive half the module
    docstring promises alongside ``ensure_fresh_token()``'s own proactive (pre-expiry)
    refresh — see ``mcp/client.py``'s ``_rpc``/``_OAuthUnauthorized`` for the pattern this
    mirrors."""
    try:
        return call(access_token), access_token
    except _LunchFlowUnauthorized:
        access_token = lunch_flow_oauth.ensure_fresh_token(force=True)
        return call(access_token), access_token


def fetch_window(
    store: LunchFlowConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Lunch Flow has no documented date-window param (RFC §B) — fetch each account's WHOLE
    available history and filter to ``[since, before]`` client-side. A per-account fetch
    failure is isolated: that account is skipped, the rest proceed. Every request gets ONE
    reactive retry on a live 401 (``_reactive_401_retry``); a second straight 401 escalates
    to a whole-fetch ``auth_error`` via ``classify_error``. Runs the polarity smell test
    (module docstring) over the window's own income/expense split and surfaces a hit via
    ``FetchWindow.warnings`` — advisory only, never a hard gate."""
    since_date = date.fromisoformat(since)
    before_date = date.fromisoformat(before)
    access_token = lunch_flow_oauth.ensure_fresh_token()
    lf_accounts, access_token = _reactive_401_retry(access_token, lambda tok: fetch_accounts(tok))
    if account:
        lf_accounts = [a for a in lf_accounts if str(a.get("id")) == account]
    accounts = [_account_to_common(a) for a in lf_accounts]

    rows: list[dict] = []
    income_count = 0
    expense_count = 0
    for lf_account in lf_accounts:
        account_id = lf_account.get("id")
        if account_id is None:
            continue
        if accounts_only:
            continue
        try:
            txns, access_token = _reactive_401_retry(
                access_token, lambda tok, aid=account_id: fetch_account_transactions(tok, aid)
            )
        except httpx.HTTPError as exc:
            # A second straight 401 (surviving the one reactive retry above) is NOT caught
            # here — it propagates to classify_error as a whole-fetch auth_error, same as
            # fetch_accounts' own unrecovered 401 does, rather than silently swallowed as
            # "just this one account failed".
            logger.warning("lunch_flow account fetch failed (%s): %s", account_id, exc)
            continue
        currency_hint = (lf_account.get("currency") or ledger_service.base_currency()).upper()
        for lf_txn in txns:
            row = _txn_to_row(lf_txn, str(account_id), currency_hint)
            if row is None:
                continue
            if not (since_date.isoformat() <= row["date"] <= before_date.isoformat()):
                continue
            rows.append(row)
            if not row["pending"]:
                if row["amount"] > 0:
                    income_count += 1
                else:
                    expense_count += 1

    warnings: list[str] = []
    caution = _polarity_smell_test(income_count, expense_count)
    if caution:
        logger.warning("lunch_flow polarity smell test fired: %s", caution)
        warnings.append(caution)
    return FetchWindow(accounts=accounts, rows=rows, complete=True, warnings=warnings)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Lunch Flow."""
    if isinstance(exc, ValidationError):
        return "auth_error"  # ensure_fresh_token/_api_error surface auth/creds failures
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
