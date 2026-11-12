"""Enable Banking (EU PSD2 open banking) aggregation provider — the sole EU/UK bank
account-aggregation data source.

Enable Banking (https://enablebanking.com, API ``https://api.enablebanking.com``) is a PSD2
open-banking aggregator. Its consent flow is OAuth-redirect-shaped, with two notable
differences from a typical secret-id/secret-key aggregator:

  * **App auth is a short-lived RS256 JWT** signed with the application's RSA private key
    (header ``{alg: RS256, kid: <application_id>}``, body ``{iss: "enablebanking.com",
    aud: "api.enablebanking.com", iat, exp}``) — NOT a secret-id/secret-key pair. We sign it
    per-call with ``cryptography`` (already a transitive dep; no PyJWT — see RFC §7).
  * **The token-exchange step is a "session"** (``POST /sessions`` with the post-consent
    auth code → ``{session_id, accounts[]}``) rather than a plain access-token exchange.

This module is the PURE protocol + normalize (``fetch_window``) — READ-ONLY, called from a
Lucius turn via ``enable_banking_fetch_transactions`` (RFC ``connectors-as-data-sources``):

  * ``_sign_jwt`` / ``_bearer`` — mint the per-call RS256 bearer from the stored RSA key.
  * ``list_institutions(country)`` — the bank picker feed (``GET /aspsps?country=XX``).
  * ``start_authorization(aspsp, country, redirect)`` — start the consent flow; returns the
    auth ``url`` the user opens (``POST /auth``) + persists ``aspsp``/``country``.
  * ``create_session(code)`` — exchange the post-consent code for a session
    (``POST /sessions``); persists the ``session_id`` (the per-bank consent handle).

There is no adapter class, no auto-created account, and no scheduled sync any more: the
provider-account → app-account link lives on ``Account.external_ids``, and booking happens
through ``add_transactions`` in the SAME turn that fetched, never here.

**``date_from``/``date_to``/``continuation_key`` VERIFIED live against Enable Banking's own
API reference (2026-09, RFC ``connectors-as-data-sources`` §B)**: ``GET
/accounts/{id}/transactions`` really does accept a server-side ``date_from``/``date_to``
window ("including the date, UTC timezone is assumed") and pages via ``continuation_key``
("iterate over multiple API pages") — the client-side-windowing fallback this module's
earlier design reserved for an unsupported date param was not needed.

NOTE ON OTHER ENDPOINT SHAPES: the remaining URL paths + JSON field names below (``/aspsps``,
``/auth``, ``/sessions``, the transaction object's own field names) follow Enable Banking's
documented v1 API but are NOT independently re-verified here. Where a precise field name or
required PSU header is uncertain it is marked ``# TODO(enable-banking):`` and a sensible
default/fallback is used. The connector is dormant-unless-configured, so an instance without
credentials is unaffected, and ``enable_banking_fetch_transactions`` never raises past its
own gate. Confirm against the live API + sandbox before relying on production data.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

SOURCE_ID = "enable_banking"
EXTERNAL_SOURCE = "enablebanking"

API_BASE = "https://api.enablebanking.com"
_HTTP_TIMEOUT = 30.0
# The JWT is short-lived; mint a fresh one per call (cheap) with a small validity window.
_JWT_TTL_SECONDS = 3600
_JWT_ISS = "enablebanking.com"
_JWT_AUD = "api.enablebanking.com"
# A default redirect when the caller doesn't supply one (the link flow needs a redirect URI
# registered with the application).
_DEFAULT_REDIRECT = "https://localhost/connectors"
# Guard against a pathological never-terminating continuation_key page loop.
_MAX_FETCH_PAGES = 50
# Hard cap on an Enable Banking response body we will read into memory (DoS guard — mirrors
# simplefin.py/lunch_money.py/ynab.py/ibkr.py's own bound). A page of one account's
# transactions is a small JSON document, so 8 MiB is generous.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# ── JWT app auth (hand-rolled RS256 via cryptography — no PyJWT, RFC §7) ──────────────


def _b64url(raw: bytes) -> str:
    """Base64url WITHOUT padding (JWT segment encoding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign_jwt(application_id: str, private_key_pem: str) -> str:
    """Sign a short-lived RS256 JWT for the Enable Banking application.

    Header ``{alg: RS256, kid: <application_id>, typ: JWT}``; body
    ``{iss, aud, iat, exp}``. Signed with the application's RSA private key (PEM). Raises
    ``ValidationError`` if the key can't be loaded / isn't RSA (the orchestrator catches +
    records it as an auth_error)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:  # pragma: no cover - cryptography is a dependency
        raise ValidationError("the cryptography package is required for Enable Banking") from exc

    try:
        key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise ValidationError("the Enable Banking private key is not a valid PEM RSA key") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValidationError("the Enable Banking private key must be an RSA key")

    now = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "RS256", "kid": application_id, "typ": "JWT"}
    body = {"iss": _JWT_ISS, "aud": _JWT_AUD, "iat": now, "exp": now + _JWT_TTL_SECONDS}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    )
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def _bearer(store: EnableBankingConfigStore) -> str:
    """A fresh JWT for the configured application. Raises ``ValidationError`` on missing
    creds (the orchestrator catches + records it)."""
    application_id = store.application_id()
    private_key = store.private_key()
    if not application_id or not private_key.strip():
        raise ValidationError("Enable Banking application_id / private_key are not configured")
    return _sign_jwt(application_id, private_key)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "accept": "application/json"}


def _api_error(resp: httpx.Response, what: str, *, redirect_url: str = "") -> ValidationError:
    """A ``ValidationError`` carrying Enable Banking's own reason (``message`` + ``error``
    code + the rejected field names), never only the status — a bare "HTTP 422" left issue
    #103 undiagnosable. The request ``input`` echoed in ``detail`` is never repeated."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    reason = str(body.get("message") or "").strip()[:200]
    code = str(body.get("error") or "").strip()[:60]
    fields = (
        [
            ".".join(str(p) for p in (d.get("loc") or [])[1:])
            for d in body.get("detail") or []
            if isinstance(d, dict)
        ]
        if isinstance(body.get("detail"), list)
        else []
    )
    text = f"{what} failed: HTTP {resp.status_code}"
    if reason:
        text += f" — {reason}"
    if code:
        text += f" ({code})"
    if fields:
        text += f": {', '.join(f for f in fields if f)[:200]}"
    if code == "REDIRECT_URI_NOT_ALLOWED" and redirect_url:
        text += (
            f". Add {redirect_url} to your application's allowed redirect URLs in the"
            " Enable Banking control panel."
        )
    return ValidationError(text)


# ── institution picker + consent link ────────────────────────────────────────────────


def list_institutions(country: str) -> list[dict]:
    """List the banks (ASPSPs) Enable Banking supports in ``country`` (ISO 3166 alpha-2,
    e.g. ``FI``, ``DE``, ``FR``). Returns a trimmed ``[{id, name, country, logo}]`` list for
    the UI picker. Raises ``ValidationError`` on a bad country / auth / transport failure."""
    code = (country or "").strip().upper()
    if len(code) != 2:
        raise ValidationError("country must be a 2-letter ISO code (e.g. FI, DE, FR)")

    store = EnableBankingConfigStore()
    token = _bearer(store)
    try:
        resp = httpx.get(
            f"{API_BASE}/aspsps",
            params={"country": code},
            headers=_auth_headers(token),
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Enable Banking: {exc}") from exc
    if resp.status_code != 200:
        raise _api_error(resp, "listing banks")

    data = resp.json() or {}
    # TODO(enable-banking): confirm the response shape — documented as {"aspsps": [...]}.
    rows = (
        data.get("aspsps") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    )
    out: list[dict] = []
    for r in rows or []:
        # An ASPSP is identified by (name, country); there is no opaque id, so we use the name
        # as the stable picker id (what start_authorization sends back).
        name = r.get("name")
        if not name:
            continue
        out.append(
            {
                "id": name,
                "name": name,
                "country": r.get("country") or code,
                "logo": r.get("logo"),
            }
        )
    return out


def start_authorization(aspsp: str, country: str, redirect: str) -> dict:
    """Start the bank-consent flow for ``aspsp`` (a bank name from ``list_institutions``).

    POSTs ``/auth`` with the chosen bank + a redirect URI; Enable Banking returns a consent
    ``url`` (the bank-auth URL the USER opens) and an ``authorization_id``/``state`` handle.
    We persist the chosen bank + country so a later ``create_session`` + sync can read the
    accounts the user consents to. Returns ``{url, authorization_id, aspsp}``. Raises
    ``ValidationError`` on a bad bank / auth / transport failure."""
    bank = (aspsp or "").strip()
    if not bank:
        raise ValidationError("aspsp (bank) is required")
    code = (country or "").strip().upper()
    if len(code) != 2:
        raise ValidationError("country must be a 2-letter ISO code (e.g. FI, DE, FR)")
    redirect_url = (redirect or "").strip() or _DEFAULT_REDIRECT

    store = EnableBankingConfigStore()
    token = _bearer(store)
    # Consent validity is capped at ~90 days for most banks; request the documented max.
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(timespec="seconds")
    # Verified against the sandbox (issue #103): `state` is required (422 without it) and
    # `aspsp` needs both name and country.
    payload = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": bank, "country": code},
        "state": str(uuid.uuid4()),
        "redirect_url": redirect_url,
        "psu_type": "personal",
    }
    try:
        resp = httpx.post(
            f"{API_BASE}/auth",
            json=payload,
            headers=_auth_headers(token),
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Enable Banking: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise _api_error(resp, "starting the bank link", redirect_url=redirect_url)

    data = resp.json() or {}
    url = (data.get("url") or data.get("authorization_url") or "").strip()
    authorization_id = (data.get("authorization_id") or data.get("state") or "").strip()
    if not url:
        raise ValidationError("Enable Banking did not return a consent URL")

    store.update({"aspsp": bank, "country": code})
    return {"url": url, "authorization_id": authorization_id, "aspsp": bank}


def create_session(code: str) -> dict:
    """Exchange the post-consent auth ``code`` for a session.

    POSTs ``/sessions`` ``{code}``; Enable Banking returns ``{session_id, accounts[]}``. We
    persist the ``session_id`` (the per-bank consent handle the accounts/transactions
    endpoints need). Returns ``{session_id, accounts}``. Raises ``ValidationError`` on a bad
    code / auth / transport failure."""
    auth_code = (code or "").strip()
    if not auth_code:
        raise ValidationError("the authorization code is required")

    store = EnableBankingConfigStore()
    token = _bearer(store)
    try:
        resp = httpx.post(
            f"{API_BASE}/sessions",
            json={"code": auth_code},
            headers=_auth_headers(token),
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Enable Banking: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise _api_error(resp, "completing the bank link")

    data = resp.json() or {}
    session_id = (data.get("session_id") or data.get("id") or "").strip()
    if not session_id:
        raise ValidationError("Enable Banking did not return a session id")

    store.update({"session_id": session_id})
    accounts = data.get("accounts") or []
    return {"session_id": session_id, "accounts": accounts}


# ── account / transaction fetch ──────────────────────────────────────────────────────


def _safe_get(token: str, path: str, params: dict) -> httpx.Response:
    """GET wrapper for the account/transaction endpoints that NEVER lets a raw exception
    escape (mirrors ``lunch_money._safe_get``/``ynab._safe_get``) and bounds the response
    body at ``_MAX_RESPONSE_BYTES``. Raises ``ExternalServiceError`` (status code /
    exception class only — no URL, no session/account id) on a transport/HTTP failure OR
    any other unexpected exception raised while building/sending the request.

    Security review S-F1: ``path`` here is ``/sessions/{session_id}`` or
    ``/accounts/{account_uid}/transactions`` — an unwrapped ``resp.raise_for_status()``
    puts httpx's own exception message, which embeds the FULL request URL, straight into
    ``connector_fetch.py``'s ``error``/``last_error`` (``public()`` deliberately hides
    ``session_id`` from that same surface). Never call ``httpx.get`` directly for these two
    endpoints — always through this."""
    try:
        resp = httpx.get(
            f"{API_BASE}{path}",
            params=params,
            headers=_auth_headers(token),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"Enable Banking HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(
            f"Enable Banking request failed ({type(exc).__name__})"
        ) from None
    except Exception as exc:
        raise ExternalServiceError(
            f"Enable Banking request failed ({type(exc).__name__})"
        ) from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"Enable Banking response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    return resp


def fetch_session_accounts(token: str, session_id: str) -> list[dict]:
    """GET ``/sessions/{id}`` → the consented accounts, each enriched from
    ``/accounts/{uid}/details`` (name, currency, type).

    Verified on the sandbox (issue #103): the session lists ``accounts`` as bare uid STRINGS
    (``accounts_data`` holds only uid + hashes), so keeping only dicts dropped every account.
    A details failure keeps ``{"uid": uid}`` so transactions still fetch. Raises
    ``ExternalServiceError`` (never the raw httpx exception — see ``_safe_get``) when the
    session itself can't be read."""
    resp = _safe_get(token, f"/sessions/{session_id}", {})
    data = resp.json() or {}
    uids: list[str] = []
    for entry in data.get("accounts") or []:
        uid = _account_uid(entry) if isinstance(entry, dict) else str(entry or "").strip()
        if uid and uid not in uids:
            uids.append(uid)
    accounts: list[dict] = []
    for uid in uids:
        try:
            details = _safe_get(token, f"/accounts/{uid}/details", {}).json()
        except (ExternalServiceError, ValueError) as exc:
            logger.warning("enable_banking account details failed: %s", exc)
            details = None
        accounts.append({**details, "uid": uid} if isinstance(details, dict) else {"uid": uid})
    return accounts


def fetch_account_transactions(
    token: str, account_uid: str, *, date_from: str, date_to: str
) -> tuple[list[dict], bool]:
    """GET ``/accounts/{uid}/transactions?date_from=...&date_to=...`` → the account's
    transaction list for the window, paged via ``continuation_key`` until exhausted or
    ``_MAX_FETCH_PAGES`` (a safety cap — hitting it returns ``complete=False``). Verified
    live against Enable Banking's own API reference 2026-09 (RFC
    ``connectors-as-data-sources`` §B): ``date_from``/``date_to`` ("including the date, UTC
    timezone is assumed") and ``continuation_key`` ("iterate over multiple API pages") are
    both real, documented, server-side params — the client-side-windowing fallback this
    function's earlier TODO reserved is not needed. Returns ``(transactions, complete)``.
    Raises ``ExternalServiceError`` (never the raw httpx exception — see ``_safe_get``) on
    a transport/HTTP failure."""
    transactions: list[dict] = []
    continuation_key: Optional[str] = None
    for _ in range(_MAX_FETCH_PAGES):
        params: dict = {"date_from": date_from, "date_to": date_to}
        if continuation_key:
            params["continuation_key"] = continuation_key
        resp = _safe_get(token, f"/accounts/{account_uid}/transactions", params)
        data = resp.json() or {}
        page = data.get("transactions") or []
        transactions.extend(page)
        continuation_key = data.get("continuation_key")
        if not continuation_key:
            return transactions, True
    return transactions, False


# ── mapping helpers ──────────────────────────────────────────────────────────────────


def _account_uid(eb_account: dict) -> str:
    """The stable Enable Banking account identifier (``uid`` preferred)."""
    return str(
        eb_account.get("uid") or eb_account.get("account_id") or eb_account.get("id") or ""
    ).strip()


def _account_name(eb_account: dict) -> str:
    acct_id = eb_account.get("account_id")
    iban = acct_id.get("iban") if isinstance(acct_id, dict) else None
    name = eb_account.get("name") or eb_account.get("product") or iban
    return (str(name).strip() if name else "") or "Enable Banking account"


def _account_currency(eb_account: dict) -> str:
    return (eb_account.get("currency") or ledger_service.base_currency()).upper()


def _account_to_common(eb_account: dict) -> dict:
    """An Enable Banking account → the common ``accounts[]`` shape (RFC §A)."""
    return {
        "provider_account_id": _account_uid(eb_account),
        "name": _account_name(eb_account),
        "mask": None,
        "currency": _account_currency(eb_account),
        "balance": None,
    }


def _txn_id(eb_txn: dict) -> str:
    """The stable Enable Banking transaction id (``entry_reference`` preferred, then
    ``transaction_id``). Empty string if neither is present."""
    return str(eb_txn.get("entry_reference") or eb_txn.get("transaction_id") or "").strip()


def _txn_amount(eb_txn: dict) -> tuple[float | None, str]:
    """Pull the signed amount + currency from a transaction.

    ``transaction_amount`` is ``{amount: "<decimal string>", currency}``; the sign is given by
    ``credit_debit_indicator`` (``CRDT`` = money in, ``DBIT`` = money out). Returns
    ``(signed_amount, currency)`` — ``(None, "")`` for an unparseable/zero amount."""
    amt_obj = eb_txn.get("transaction_amount") or {}
    raw = amt_obj.get("amount")
    try:
        magnitude = abs(float(raw))
    except (TypeError, ValueError):
        return None, ""
    if not magnitude:
        return None, ""
    currency = (amt_obj.get("currency") or "").upper()
    # Read indicator from snake_case preferred, then camelCase fallback (banks vary).
    # TODO(enable-banking): confirm which key the live API sends.
    raw_indicator = eb_txn.get("credit_debit_indicator") or eb_txn.get("creditDebitIndicator")
    indicator = (raw_indicator or "").upper().strip()
    if indicator == "CRDT":
        signed = magnitude
    elif indicator == "DBIT":
        signed = -magnitude
    else:
        # Unknown or absent indicator — cannot determine direction safely; skip.
        logger.warning(
            "enable_banking: unknown credit_debit_indicator %r for txn %r — skipped",
            raw_indicator,
            eb_txn.get("entry_reference") or eb_txn.get("transaction_id") or "",
        )
        return None, ""
    return signed, currency


def _txn_date(eb_txn: dict) -> str:
    """Booking date (``booking_date``, then ``value_date``), else today (UTC)."""
    raw = eb_txn.get("booking_date") or eb_txn.get("value_date") or ""
    try:
        return datetime.fromisoformat(str(raw)).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).date().isoformat()


def _txn_description(eb_txn: dict) -> str:
    """Best human description: remittance info (string or list), then the counterparty name,
    then a generic fallback."""
    info = eb_txn.get("remittance_information")
    if isinstance(info, list):
        info = " ".join(str(x) for x in info if x)
    if not info:
        creditor = eb_txn.get("creditor") or {}
        debtor = eb_txn.get("debtor") or {}
        info = (creditor.get("name") if isinstance(creditor, dict) else None) or (
            debtor.get("name") if isinstance(debtor, dict) else None
        )
    return (str(info).strip() if info else "") or "Enable Banking"


def _txn_to_row(eb_txn: dict, account_uid: str, currency_hint: str) -> dict | None:
    """One Enable Banking transaction → the common normalized row shape (RFC §A step 3).
    Returns None for an unparseable/zero amount or an unrecognized credit/debit indicator
    (``_txn_amount`` already logs that case)."""
    amount, currency = _txn_amount(eb_txn)
    if amount is None:
        return None
    currency = currency or currency_hint
    date_str = _txn_date(eb_txn)
    description = _txn_description(eb_txn)
    txn_id = _txn_id(eb_txn)
    ref = (
        f"{account_uid}:{txn_id}"
        if txn_id
        else derived_ref(account_uid, date_str, amount, currency, description)
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": eb_txn,
        "provider_account_id": account_uid,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }


def fetch_window(
    store: EnableBankingConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Enable Banking's provider-side windowed fetch (RFC §B, verified live against the API
    reference 2026-09) — ``date_from``/``date_to`` are real, documented, server-side params;
    paging via ``continuation_key``. A per-account fetch failure is isolated: that account is
    skipped, the rest proceed. ``complete`` is False if ANY account's page loop hit
    ``_MAX_FETCH_PAGES`` without exhausting its ``continuation_key`` — a truncated fetch must
    never look complete to the vanished-row check."""
    token = _bearer(store)
    eb_accounts = fetch_session_accounts(token, store.session_id())
    if account:
        eb_accounts = [a for a in eb_accounts if _account_uid(a) == account]
    accounts = [_account_to_common(a) for a in eb_accounts]

    rows: list[dict] = []
    complete = True
    for eb_account in eb_accounts:
        account_uid = _account_uid(eb_account)
        if not account_uid or accounts_only:
            continue
        try:
            txns, account_complete = fetch_account_transactions(
                token, account_uid, date_from=since, date_to=before
            )
        except (ExternalServiceError, httpx.HTTPError) as exc:
            logger.warning("enable_banking account fetch failed (%s): %s", account_uid, exc)
            complete = False
            continue
        complete = complete and account_complete
        currency_hint = _account_currency(eb_account)
        for eb_txn in txns:
            row = _txn_to_row(eb_txn, account_uid, currency_hint)
            if row is not None:
                rows.append(row)
    return FetchWindow(accounts=accounts, rows=rows, complete=complete)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Enable Banking. ``_safe_get`` wraps every
    account/transaction transport/HTTP failure into ``ExternalServiceError`` carrying only
    the HTTP status code (no url/session id — see its docstring); re-derive the class from
    that safe message the way ``simplefin.py``/``ynab.py`` do. The consent-flow functions
    (``list_institutions``/``start_authorization``/``create_session``) still raise
    ``ValidationError`` directly."""
    if isinstance(exc, ValidationError):
        return "auth_error"  # _bearer/consent-flow surfaces creds/key failures this way
    if isinstance(exc, ExternalServiceError):
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
