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
  * One requisition per linked bank; a renewal is a new requisition that takes over from the
    old one once it is linked, so an abandoned renewal never costs a working bank.
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
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.gocardless_config_store import ENDED, LINKED, GoCardlessConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation import balance_kinds, parallel
from backend.services.aggregation.mcc import merchant_category
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
            message = (
                "GoCardless rejected the secret ID / secret key — check them in "
                "bankaccountdata.gocardless.com → User secrets"
            )
            store.note_credentials(rejected_with=message)
            raise ValidationError(message) from None
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
    store.note_credentials(rejected_with=None)
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


def _institution(store: GoCardlessConfigStore, institution_id: str) -> dict:
    """``GET /institutions/{id}/`` — its name and limits. Best effort: ``{}`` on failure."""
    try:
        return _json(_authed(store, "GET", f"/institutions/{institution_id}/")) or {}
    except (ExternalServiceError, ValidationError) as exc:
        logger.info("gocardless: institution details unavailable (%s)", exc)
        return {}


def _agreement(
    store: GoCardlessConfigStore, institution_id: str, inst: dict
) -> tuple[Optional[str], int]:
    """Ask for as much history and access as the bank allows (``inst`` is its
    ``/institutions/{id}/``). Best effort: any failure falls back to GoCardless's default
    agreement (90 days of each). Returns ``(agreement_id, access_days)``."""
    if not inst:
        return None, _DEFAULT_ACCESS_DAYS
    history = min(730, _int(inst.get("transaction_total_days")) or _DEFAULT_HISTORY_DAYS)
    access = min(730, _int(inst.get("max_access_valid_for_days")) or _DEFAULT_ACCESS_DAYS)
    body = {
        "institution_id": institution_id,
        "max_historical_days": history,
        "access_valid_for_days": access,
        "access_scope": ["balances", "details", "transactions"],
    }
    try:
        agreement = _json(_authed(store, "POST", "/agreements/enduser/", json=body)) or {}
    except (ExternalServiceError, ValidationError) as exc:
        logger.info("gocardless: default agreement used (%s)", exc)
        return None, _DEFAULT_ACCESS_DAYS
    agreement_id = str(agreement.get("id") or "").strip()
    return (agreement_id or None), (access if agreement_id else _DEFAULT_ACCESS_DAYS)


def _redirect(redirect: str) -> str:
    url = (redirect or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("redirect must be an http(s) URL")
    return url


def _institution_id(institution_id: str) -> str:
    inst = (institution_id or "").strip()
    if not _INSTITUTION_ID.match(inst):
        raise ValidationError("institution_id must be a GoCardless bank id (e.g. ACME_BANK_GB)")
    return inst


def _new_bank(
    store: GoCardlessConfigStore,
    institution_id: str,
    country: str,
    redirect: str,
    *,
    replaces: Optional[str] = None,
) -> dict:
    """Create a requisition and list it as a bank → ``{link, key}``. Nothing is stored unless
    GoCardless returned a usable link."""
    inst, code, redirect_url = (
        _institution_id(institution_id),
        _country(country),
        _redirect(redirect),
    )
    details = _institution(store, inst)
    agreement_id, access_days = _agreement(store, inst, details)
    body = {"institution_id": inst, "redirect": redirect_url, "user_language": "EN"}
    if agreement_id:
        body["agreement"] = agreement_id
    data = _json(_authed(store, "POST", "/requisitions/", json=body)) or {}
    requisition_id = str(data.get("id") or "").strip()
    link = str(data.get("link") or "").strip()
    if not requisition_id or not link.startswith("https://"):
        raise ExternalServiceError("GoCardless did not return a bank link")
    key = store.add_bank(
        requisition_id,
        institution_id=inst,
        institution_name=str(details.get("name") or "").strip()[:120] or inst,
        country=code,
        consent_expires_at=_iso(_now() + timedelta(days=access_days)),
        replaces=replaces,
    )
    return {"link": link, "key": key}


def start_link(institution_id: str, country: str, redirect: str) -> dict:
    """Link one more bank → ``{link, key}``: the bank page the user opens and the new bank's
    public key. The banks already linked are untouched."""
    return _new_bank(GoCardlessConfigStore(), institution_id, country, redirect)


def relink_bank(key: str, redirect: str) -> dict:
    """Start a new consent for a listed bank → ``{link, key}``. An old link that reads nothing
    any more (expired, refused, unfinished) is revoked and replaced at once; a still-working
    one keeps fetching until the new consent is linked and is revoked then (``_refresh``), so
    a renewal the user abandons never costs a working bank."""
    store = GoCardlessConfigStore()
    old = store.bank(key)
    if old is None:
        raise ValidationError("That bank is not linked")
    working = old["status"] == LINKED
    result = _new_bank(
        store,
        old["institution_id"] or "",
        old["country"] or "",
        redirect,
        replaces=old["requisition_id"] if working else None,
    )
    if not working:
        _revoke(store, old)
        store.drop_bank(key)
    return result


def check_links() -> dict:
    """Re-read every bank's requisition after the user comes back from a bank. Returns the
    store's public view. It never touches ``enabled``: a connector the user paused stays
    paused."""
    store = GoCardlessConfigStore()
    if not store.banks():
        raise ValidationError("No bank linked yet — choose a bank and select Link bank first")
    _refresh(store)
    return store.public()


def _revoke(store: GoCardlessConfigStore, bank: dict) -> bool:
    """Delete one requisition at GoCardless. True once it is gone there (a 404 counts)."""
    try:
        _authed(store, "DELETE", f"/requisitions/{bank['requisition_id']}/")
    except ExternalServiceError as exc:
        if "HTTP 404" in str(exc):
            return True
        logger.warning("gocardless: could not delete a requisition at GoCardless (%s)", exc)
        return False
    except ValidationError as exc:
        logger.warning("gocardless: could not delete a requisition at GoCardless (%s)", exc)
        return False
    return True


def remove_bank(key: str) -> dict:
    """Unlink ONE bank: delete its requisition at GoCardless, then drop it here. Only a
    confirmed delete removes the local entry — otherwise the grant would stay live at
    GoCardless with no way back to it — so a failure raises and nothing changes."""
    store = GoCardlessConfigStore()
    bank = store.bank(key)
    if bank is None:
        raise ValidationError("That bank is not linked")
    if not _revoke(store, bank):
        raise ValidationError(
            "Couldn't reach GoCardless to remove that bank — nothing changed. Try again in a moment."
        )
    store.drop_bank(key)
    return {"removed": True, "banks_left": len(store.banks())}


def remove_all_banks(store: GoCardlessConfigStore) -> None:
    """Revoke every bank at GoCardless — the connector's Remove hook, before the row and its
    secrets are erased. Best effort per bank: one failure never blocks the rest."""
    if not store.has_credentials():
        return
    for bank in store.banks():
        _revoke(store, bank)


def _read_requisition(store: GoCardlessConfigStore, bank: dict) -> None:
    """``GET /requisitions/{id}/`` → record its status and, once linked, its account ids."""
    data = _json(_authed(store, "GET", f"/requisitions/{bank['requisition_id']}/")) or {}
    status = str(data.get("status") or "").strip().upper()
    fields: dict = {"status": status}
    if status == LINKED:
        fields["account_ids"] = [str(a) for a in (data.get("accounts") or []) if a]
        fields["linked_at"] = bank["linked_at"] or _iso(_now())
    bank.update(fields)
    store.update_bank(bank["key"], **fields)


def _bank_name(bank: dict) -> str:
    return bank.get("institution_name") or bank.get("institution_id") or "A GoCardless bank"


def _refresh(store: GoCardlessConfigStore) -> list[str]:
    """Re-read every requisition, then let each linked renewal take over from the bank it
    replaces. Returns a warning per bank GoCardless could not be asked about. Rejected
    credentials raise — they stop every bank alike."""
    warnings: list[str] = []
    banks = store.banks()
    for bank in banks:
        try:
            _read_requisition(store, bank)
        except ExternalServiceError as exc:
            warnings.append(f"{_bank_name(bank)}: {exc}")
    by_requisition = {b["requisition_id"]: b for b in banks}
    for bank in banks:
        old = by_requisition.get(bank["replaces"] or "")
        if bank["status"] == LINKED and old is not None:
            _revoke(store, old)
            store.drop_bank(old["key"])
            store.update_bank(bank["key"], replaces=None)
    return warnings


def _unusable(bank: dict) -> str:
    status = bank.get("status") or ""
    if status in ENDED:
        return f"{_bank_name(bank)}: bank access {ENDED[status]} — link it again in Connectors → GoCardless"
    return (
        f"{_bank_name(bank)}: the bank link is not finished (GoCardless status "
        f"{status or 'unknown'}) — finish it at the bank, then select Check links in "
        "Connectors → GoCardless"
    )


def _linked_banks(store: GoCardlessConfigStore) -> tuple[list[dict], list[str]]:
    """The banks that can be read now, plus a warning for each one that can't (a renewal
    still in progress is not one: the bank it renews is still read). Raises
    ``ValidationError`` when none can — the status then says why instead of reading ``ok``."""
    if not store.banks():
        raise ValidationError("No bank linked — link one in Connectors → GoCardless")
    warnings = _refresh(store)
    banks = store.banks()
    usable = [b for b in banks if b["status"] == LINKED]
    warnings += [_unusable(b) for b in banks if b["status"] != LINKED and not b["replaces"]]
    if not usable:
        raise ValidationError("; ".join(warnings) or "No bank is linked yet")
    return usable, warnings


# ── accounts + transactions ──────────────────────────────────────────────────────────


def _account_meta(
    store: GoCardlessConfigStore, account_id: str, bank: dict, cache: dict, *, fresh: bool = False
) -> dict:
    """Name/currency/type for one account. ``fresh`` (listing the accounts): always read from
    GoCardless, the stored copy only when the bank refuses. Otherwise (a sync) the stored copy
    is used, so a sync never spends a ``/details/`` read (rate-limited like transactions)."""
    if account_id in cache and not fresh:
        return cache[account_id]
    fallback = cache.get(account_id) or {
        "name": f"{_bank_name(bank)} account {account_id[-4:]}",
        "currency": None,
        "mask": None,
    }
    try:
        details = (_json(_authed(store, "GET", f"/accounts/{account_id}/details/")) or {}).get(
            "account"
        ) or {}
    except ExternalServiceError as exc:
        logger.warning("gocardless: account details unavailable (%s)", exc)
        return fallback
    iban = str(details.get("iban") or details.get("bban") or details.get("maskedPan") or "")
    meta = {
        "name": str(
            details.get("name") or details.get("displayName") or details.get("product") or ""
        ).strip()
        or fallback["name"],
        "currency": (str(details.get("currency") or "").upper() or None),
        "mask": iban[-4:] or None,
        "account_type": _ACCOUNT_TYPES.get(str(details.get("cashAccountType") or "").upper()),
    }
    with _META_LOCK:  # accounts are read side by side; the stored copy is one shared map
        store.cache_account_meta(account_id, meta)
        cache[account_id] = meta
    return meta


_META_LOCK = threading.Lock()


def _account_to_common(account_id: str, meta: dict) -> dict:
    return {
        "provider_account_id": account_id,
        "name": meta.get("name"),
        "mask": meta.get("mask"),
        "currency": meta.get("currency") or ledger_service.base_currency(),
        "balance": None,
        "account_type": meta.get("account_type"),
    }


# Booked balances first (what the ledger's cleared balance should match), then the rest.
_BALANCE_PREFERENCE = (
    "closingBooked",
    "interimBooked",
    "expected",
    "interimAvailable",
    "openingBooked",
)


def _account_balance(store: GoCardlessConfigStore, account_id: str) -> dict:
    """``{balance, cleared_balance, working_balance, available_to_spend}`` (``balance_kinds``;
    ``balance`` the one figure, booked preferred). The account's balance from ``/accounts/{id}/balances/`` — read with its transactions, so
    the bank sync can compare it with the book. The bank limits this endpoint on its own (often
    4 a day, like transactions), and a sync never calls it more often than transactions; a
    refused or failed read is just no figure this time, never a failed fetch."""
    try:
        data = _json(_authed(store, "GET", f"/accounts/{account_id}/balances/")) or {}
    except ExternalServiceError:
        return {"balance": None, **balance_kinds.split({})}
    by_type: dict[str, float] = {}
    for b in data.get("balances") or []:
        try:
            amount = float(((b or {}).get("balanceAmount") or {}).get("amount"))
        except (TypeError, ValueError):
            continue
        by_type.setdefault(str((b or {}).get("balanceType") or ""), amount)
    best = next((by_type[k] for k in _BALANCE_PREFERENCE if k in by_type), None)
    if best is None:
        best = next(iter(by_type.values()), None)
    return {"balance": best, **balance_kinds.split(by_type)}


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


def _txn_payee(txn: dict, amount: float) -> Optional[str]:
    """The counterparty's own name — who was paid (money out) or who paid (money in) — or
    ``None`` when the bank names neither. It becomes the row's payee; the description keeps
    the bank's wording, which payee matching learns from."""
    keys = ("creditorName", "debtorName") if amount < 0 else ("debtorName", "creditorName")
    for key in keys:
        name = " ".join(str(txn.get(key) or "").split())
        if name:
            return name[:120]
    return None


def _original_charge(txn: dict) -> tuple[Optional[float], Optional[str]]:
    """A purchase made in another currency: what the merchant charged (``instructedAmount``),
    beside the converted amount the account was debited. ``(None, None)`` for a row in the
    account's own currency. It goes in the row's ``original_amount``/``original_currency`` —
    the ledger's own fields for it, which the duplicate check compares — never in the memo."""
    booked_in = str((txn.get("transactionAmount") or {}).get("currency") or "").upper()
    for fx in _currency_exchanges(txn):
        paid = fx.get("instructedAmount") if isinstance(fx.get("instructedAmount"), dict) else {}
        currency = str(paid.get("currency") or fx.get("sourceCurrency") or "").upper()
        try:
            amount = abs(float(paid.get("amount")))
        except (TypeError, ValueError):
            continue
        if currency and currency != booked_in and amount:
            return amount, currency
    return None, None


def _currency_exchanges(txn: dict) -> list[dict]:
    raw = txn.get("currencyExchange")
    if isinstance(raw, dict):
        return [raw]
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


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
    original_amount, original_currency = _original_charge(txn)
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
        "payee": _txn_payee(txn, amount),
        "original_amount": original_amount,
        "original_currency": original_currency,
        "category_hint": merchant_category(txn.get("merchantCategoryCode")),
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
    """Every linked bank's accounts (+ their transactions in ``[since, before]``). A bank that
    can't be read, or one account's failure, is isolated: it becomes a warning naming the bank
    and marks the fetch incomplete, so the vanished-row check never reads its missing rows as
    deleted. If EVERY account fails, the last error is raised so the status says why (a 429
    reads ``rate_limited``, not ``ok``)."""
    banks, warnings = _linked_banks(store)
    owned = [(b, a) for b in banks for a in b["account_ids"] if not account or a == account]
    cache = store.accounts_meta()
    # Listing the accounts (the connector page, Lucius's list tool) reads them from GoCardless:
    # details and balance, live. A sync reads the balance beside its transactions, below.
    # Every account at once (parallel.each): one request per account, a second or more each.
    found = parallel.each(
        lambda ba: _account_meta(store, ba[1], ba[0], cache, fresh=accounts_only), owned
    )
    metas = {a: meta for (_, a), meta in zip(owned, found)}
    accounts = [_account_to_common(a, metas[a]) for _, a in owned]
    if accounts_only:
        figures = parallel.each(
            lambda acc: _account_balance(store, acc["provider_account_id"]), accounts
        )
        for acc, figure in zip(accounts, figures):
            acc.update(figure)
        return FetchWindow(accounts=accounts, rows=[], complete=not warnings, warnings=warnings)

    rows: list[dict] = []
    failures: list[ExternalServiceError] = []
    by_id = {a["provider_account_id"]: a for a in accounts}
    for bank, account_id in owned:
        currency = metas[account_id].get("currency") or ledger_service.base_currency()
        try:
            rows.extend(_account_rows(store, account_id, currency, since, before))
        except ExternalServiceError as exc:
            failures.append(exc)
            warnings.append(f"{metas[account_id].get('name')} ({_bank_name(bank)}): {exc}")
            continue
        by_id[account_id].update(_account_balance(store, account_id))
    if owned and len(failures) == len(owned):
        raise failures[-1]
    return FetchWindow(accounts=accounts, rows=rows, complete=not warnings, warnings=warnings)


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
