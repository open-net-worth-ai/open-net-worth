"""GoCardless Bank Account Data (formerly Nordigen) — EU/UK PSD2 open banking, read-only.

GoCardless closed Bank Account Data to NEW signups in 2025; an existing account keeps working,
which is who this connector is for (ADR ``2026-11-02-restore-gocardless-for-existing-accounts``).

Protocol (``https://bankaccountdata.gocardless.com/api/v2``, checked against its OpenAPI spec
2026-11):

  * ``POST /token/new/`` ``{secret_id, secret_key}`` → ``access`` (24h) — cached as a minted
    secret and re-minted on expiry or a 401.
  * ``GET /institutions/?country=XX`` → the bank picker; ``GET /institutions/{id}/`` → its
    history/access limits, used to ask for the most history the bank allows in
    ``POST /agreements/enduser/`` (best effort — GoCardless's own 90/90 default otherwise).
  * ``POST /requisitions/`` ``{institution_id, redirect, agreement}`` → a ``link`` the user
    opens; ``GET /requisitions/{id}/`` → ``status`` (``LN`` = linked) + ``accounts[]``. There
    is no code to exchange: the app re-reads the requisition when the user comes back.
  * ``GET /accounts/{id}/details/`` (cached in ``accounts_meta``) and
    ``GET /accounts/{id}/transactions/?date_from&date_to`` → ``{booked: [], pending: []}``.

Banks may cap the account endpoints at 4 successful calls per account per day; a 429 is
``rate_limited`` and names when the limit resets. Only ``fetch_window`` reaches the account
endpoints, and only a Lucius turn calls it (``gocardless_fetch_transactions``) — booking is the
model's own ``add_transactions`` call, never here.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.gocardless_config_store import ENDED, LINKED, GoCardlessConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

SOURCE_ID = "gocardless"
EXTERNAL_SOURCE = "gocardless"

API_BASE = "https://bankaccountdata.gocardless.com/api/v2"
_HTTP_TIMEOUT = 30.0
_TOKEN_MARGIN_SECONDS = 60
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_DEFAULT_ACCESS_DAYS = 90
_DEFAULT_HISTORY_DAYS = 90
_RESET_HEADERS = ("HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_RESET", "X-RateLimit-Account-Success-Reset")
_INSTITUTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
# ISO 20022 cashAccountType → one of the seven app account types.
_ACCOUNT_TYPES = {"CARD": "credit", "LOAN": "loan", "CACC": "cash", "SVGS": "cash", "TRAN": "cash"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── HTTP ─────────────────────────────────────────────────────────────────────────────


def _request(method: str, path: str, *, token: Optional[str] = None, **kwargs) -> httpx.Response:
    """Every GoCardless call. Raises ``ExternalServiceError`` carrying only the status code
    (and a 429's reset time) — never httpx's own message, which embeds the URL and with it a
    requisition or account id."""
    headers = {"accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=_HTTP_TIMEOUT, **kwargs
        )
    except Exception as exc:  # noqa: BLE001 — re-raised without the URL
        raise ExternalServiceError(f"GoCardless request failed ({type(exc).__name__})") from None
    if resp.status_code == 429:
        raise ExternalServiceError(f"GoCardless HTTP 429 — {_rate_limit_hint(resp)}")
    if resp.status_code >= 400:
        raise ExternalServiceError(f"GoCardless HTTP {resp.status_code}")
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError("GoCardless response too large; refused.")
    return resp


def _rate_limit_hint(resp: httpx.Response) -> str:
    for name in _RESET_HEADERS:
        raw = resp.headers.get(name)
        try:
            seconds = int(float(raw)) if raw is not None else None
        except ValueError:
            seconds = None
        if seconds is not None:
            return f"the bank's daily limit for this account resets in about {max(1, seconds // 60)} min"
    return "the bank's daily request limit was reached; try again later"


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        raise ExternalServiceError("GoCardless returned a response that is not JSON") from None


# ── auth ─────────────────────────────────────────────────────────────────────────────


def _token_valid(expires_at: Optional[str]) -> bool:
    try:
        exp = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return _now() < exp - timedelta(seconds=_TOKEN_MARGIN_SECONDS)


def _bearer(store: GoCardlessConfigStore, *, fresh: bool = False) -> str:
    """A valid access token — the cached one, or a newly minted one. Raises
    ``ValidationError`` for missing or rejected credentials."""
    cfg = store.get()
    cached = (cfg.get("access_token") or "").strip()
    if cached and not fresh and _token_valid(cfg.get("access_expires_at")):
        return cached
    secret_id, secret_key = store.secret_id(), store.secret_key()
    if not secret_id or not secret_key:
        raise ValidationError("GoCardless secret ID / secret key are not configured")
    try:
        resp = _request(
            "POST", "/token/new/", json={"secret_id": secret_id, "secret_key": secret_key}
        )
    except ExternalServiceError as exc:
        if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
            raise ValidationError(
                "GoCardless rejected the secret ID / secret key — check them in "
                "bankaccountdata.gocardless.com → User secrets"
            ) from None
        raise
    data = _json(resp) or {}
    access = str(data.get("access") or "").strip()
    if not access:
        raise ExternalServiceError("GoCardless did not return an access token")
    try:
        lifetime = int(data.get("access_expires") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    store.save_token(access, _iso(_now() + timedelta(seconds=lifetime)) if lifetime else None)
    return access


def _authed(store: GoCardlessConfigStore, method: str, path: str, **kwargs) -> httpx.Response:
    """One authenticated call, retried once with a fresh token when the cached one is refused."""
    try:
        return _request(method, path, token=_bearer(store), **kwargs)
    except ExternalServiceError as exc:
        if "HTTP 401" not in str(exc):
            raise
    return _request(method, path, token=_bearer(store, fresh=True), **kwargs)


# ── bank picker + consent link ───────────────────────────────────────────────────────


def _country(country: str) -> str:
    code = (country or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValidationError("country must be a 2-letter ISO code (e.g. GB, DE, FR)")
    return code


def list_institutions(country: str) -> list[dict]:
    """The banks GoCardless covers in ``country`` → ``[{id, name, bic, logo, history_days}]``."""
    code = _country(country)
    rows = _json(
        _authed(GoCardlessConfigStore(), "GET", "/institutions/", params={"country": code})
    )
    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "bic": r.get("bic"),
            "logo": r.get("logo"),
            "history_days": _int(r.get("transaction_total_days")),
        }
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("id")
    ]


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _agreement(store: GoCardlessConfigStore, institution_id: str) -> tuple[Optional[str], int]:
    """Ask for as much history and access as the bank allows. Best effort: any failure falls
    back to GoCardless's default agreement (90 days of each). Returns ``(agreement_id,
    access_days)``."""
    try:
        inst = _json(_authed(store, "GET", f"/institutions/{institution_id}/")) or {}
        history = min(730, _int(inst.get("transaction_total_days")) or _DEFAULT_HISTORY_DAYS)
        access = min(730, _int(inst.get("max_access_valid_for_days")) or _DEFAULT_ACCESS_DAYS)
        body = {
            "institution_id": institution_id,
            "max_historical_days": history,
            "access_valid_for_days": access,
            "access_scope": ["balances", "details", "transactions"],
        }
        agreement = _json(_authed(store, "POST", "/agreements/enduser/", json=body)) or {}
        agreement_id = str(agreement.get("id") or "").strip()
        return (agreement_id or None), (access if agreement_id else _DEFAULT_ACCESS_DAYS)
    except (ExternalServiceError, ValidationError) as exc:
        logger.info("gocardless: default agreement used (%s)", exc)
        return None, _DEFAULT_ACCESS_DAYS


def _redirect(redirect: str) -> str:
    url = (redirect or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("redirect must be an http(s) URL")
    return url


def start_link(institution_id: str, country: str, redirect: str) -> dict:
    """Create a requisition for ``institution_id`` → ``{link}``, the bank page the user opens.
    Once the new one exists, the previous requisition is deleted at GoCardless, so re-linking
    never leaves a stale grant behind and a failed attempt never loses a working link."""
    inst = (institution_id or "").strip()
    if not _INSTITUTION_ID.match(inst):
        raise ValidationError("institution_id must be a GoCardless bank id (e.g. ACME_BANK_GB)")
    code = _country(country)
    redirect_url = _redirect(redirect)
    store = GoCardlessConfigStore()
    agreement_id, access_days = _agreement(store, inst)
    body = {"institution_id": inst, "redirect": redirect_url, "user_language": "EN"}
    if agreement_id:
        body["agreement"] = agreement_id
    data = _json(_authed(store, "POST", "/requisitions/", json=body)) or {}
    requisition_id = str(data.get("id") or "").strip()
    link = str(data.get("link") or "").strip()
    if not requisition_id or not link.startswith("https://"):
        raise ExternalServiceError("GoCardless did not return a bank link")
    delete_requisition(store)
    store.start_link(inst, code, requisition_id, _iso(_now() + timedelta(days=access_days)))
    return {"link": link}


def check_link() -> dict:
    """Re-read the requisition after the user comes back from the bank. Returns the store's
    public view. It never touches ``enabled``: a connector the user paused stays paused."""
    store = GoCardlessConfigStore()
    if not store.requisition_id():
        raise ValidationError("No bank link started — choose a bank and select Link bank first")
    _requisition(store)
    return store.public()


def delete_requisition(store: GoCardlessConfigStore) -> None:
    """Revoke the current requisition at GoCardless. Never raises: the Remove and re-link paths
    clear the local handle either way."""
    requisition_id = store.requisition_id()
    if not requisition_id or not store.has_credentials():
        return
    try:
        _authed(store, "DELETE", f"/requisitions/{requisition_id}/")
    except Exception:  # noqa: BLE001 — best effort, logged without the id
        logger.warning("gocardless: could not delete the previous requisition at GoCardless")


def _requisition(store: GoCardlessConfigStore) -> list[str]:
    """``GET /requisitions/{id}/`` → its account ids, recording the status it reports."""
    data = _json(_authed(store, "GET", f"/requisitions/{store.requisition_id()}/")) or {}
    status = str(data.get("status") or "").strip().upper()
    store.record_requisition_status(status)
    return [str(a) for a in (data.get("accounts") or []) if a] if status == LINKED else []


# ── accounts + transactions ──────────────────────────────────────────────────────────


def _linked_account_ids(store: GoCardlessConfigStore) -> list[str]:
    ids = _requisition(store)
    status = store.get().get("requisition_status") or ""
    if status == LINKED:
        return ids
    if status in ENDED:
        raise ValidationError(
            f"The bank consent is {ENDED[status]} — link the bank again in Connectors → GoCardless"
        )
    raise ValidationError(
        f"The bank link is not finished (GoCardless status {status or 'unknown'}) — finish it "
        "at the bank, then select Check link in Connectors → GoCardless"
    )


def _account_meta(store: GoCardlessConfigStore, account_id: str, cache: dict) -> dict:
    """Name/currency/type for one account — cached, because ``/details/`` is rate-limited."""
    if account_id in cache:
        return cache[account_id]
    try:
        details = (_json(_authed(store, "GET", f"/accounts/{account_id}/details/")) or {}).get(
            "account"
        ) or {}
    except ExternalServiceError as exc:
        logger.warning("gocardless: account details unavailable (%s)", exc)
        return {"name": f"GoCardless account {account_id[-4:]}", "currency": None, "mask": None}
    iban = str(details.get("iban") or details.get("bban") or details.get("maskedPan") or "")
    meta = {
        "name": str(
            details.get("name") or details.get("displayName") or details.get("product") or ""
        ).strip()
        or f"GoCardless account {account_id[-4:]}",
        "currency": (str(details.get("currency") or "").upper() or None),
        "mask": iban[-4:] or None,
        "account_type": _ACCOUNT_TYPES.get(str(details.get("cashAccountType") or "").upper()),
    }
    store.cache_account_meta(account_id, meta)
    cache[account_id] = meta
    return meta


def _account_to_common(account_id: str, meta: dict) -> dict:
    return {
        "provider_account_id": account_id,
        "name": meta.get("name"),
        "mask": meta.get("mask"),
        "currency": meta.get("currency") or ledger_service.base_currency(),
        "balance": None,
        "account_type": meta.get("account_type"),
    }


def _txn_id(txn: dict) -> str:
    return str(txn.get("transactionId") or txn.get("internalTransactionId") or "").strip()


def _txn_date(txn: dict) -> Optional[str]:
    for key in ("bookingDate", "valueDate", "bookingDateTime", "valueDateTime"):
        raw = txn.get(key)
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                continue
    return None


def _txn_description(txn: dict) -> str:
    info = txn.get("remittanceInformationUnstructured")
    if not info:
        for key in (
            "remittanceInformationUnstructuredArray",
            "remittanceInformationStructuredArray",
        ):
            arr = txn.get(key)
            if isinstance(arr, list) and arr:
                info = " ".join(str(x) for x in arr if x)
                break
    info = info or txn.get("remittanceInformationStructured")
    info = (
        info or txn.get("creditorName") or txn.get("debtorName") or txn.get("additionalInformation")
    )
    return (str(info).strip() if info else "") or "GoCardless"


def _txn_to_row(txn: dict, account_id: str, currency_hint: str, *, pending: bool) -> Optional[dict]:
    """One GoCardless transaction → the common row. ``transactionAmount.amount`` is already
    signed, positive = money in. A row with no amount or no date is dropped."""
    amount_obj = txn.get("transactionAmount") or {}
    try:
        amount = float(amount_obj.get("amount"))
    except (TypeError, ValueError):
        return None
    date_str = _txn_date(txn)
    if not amount or not date_str:
        return None
    currency = str(amount_obj.get("currency") or currency_hint).upper()
    description = _txn_description(txn)
    txn_id = _txn_id(txn)
    ref = (
        f"{account_id}:{txn_id}"
        if txn_id
        else derived_ref(account_id, date_str, amount, currency, description)
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": txn,
        "provider_account_id": account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": pending,
        "supersedes_ref": None,
    }


def _account_rows(store, account_id: str, currency: str, since: str, before: str) -> list[dict]:
    path = f"/accounts/{account_id}/transactions/"
    params = {"date_from": since, "date_to": before}
    data = (_json(_authed(store, "GET", path, params=params)) or {}).get("transactions") or {}
    rows: list[dict] = []
    for key, pending in (("booked", False), ("pending", True)):
        for txn in data.get(key) or []:
            if isinstance(txn, dict):
                row = _txn_to_row(txn, account_id, currency, pending=pending)
                if row is not None:
                    rows.append(row)
    return rows


def fetch_window(
    store: GoCardlessConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """The consented accounts (+ their transactions in ``[since, before]``). One account's
    failure is isolated and marks the fetch incomplete; if EVERY account fails, the last
    error is raised so the status says why (a 429 reads ``rate_limited``, not ``ok``)."""
    account_ids = _linked_account_ids(store)
    if account:
        account_ids = [a for a in account_ids if a == account]
    cache = store.accounts_meta()
    metas = {a: _account_meta(store, a, cache) for a in account_ids}
    accounts = [_account_to_common(a, metas[a]) for a in account_ids]
    if accounts_only:
        return FetchWindow(accounts=accounts, rows=[], complete=True)

    rows: list[dict] = []
    failures: list[ExternalServiceError] = []
    for account_id in account_ids:
        currency = metas[account_id].get("currency") or ledger_service.base_currency()
        try:
            rows.extend(_account_rows(store, account_id, currency, since, before))
        except ExternalServiceError as exc:
            logger.warning("gocardless: transactions unavailable for one account (%s)", exc)
            failures.append(exc)
    if account_ids and len(failures) == len(account_ids):
        raise failures[-1]
    return FetchWindow(accounts=accounts, rows=rows, complete=not failures)


def classify_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "auth_error"
    if isinstance(exc, ExternalServiceError):
        msg = str(exc).lower()
        if "http 401" in msg or "http 403" in msg:
            return "auth_error"
        if "http 429" in msg:
            return "rate_limited"
        return "connect_error"
    return "error"
