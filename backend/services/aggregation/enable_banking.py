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
from urllib.parse import urlparse

import httpx

from backend.core.errors import ExternalServiceError, RedirectNotRegisteredError, ValidationError
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation import balance_kinds, parallel
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

SOURCE_ID = "enable_banking"
EXTERNAL_SOURCE = "enable_banking"

API_BASE = "https://api.enablebanking.com"
_HTTP_TIMEOUT = 30.0
# The JWT is short-lived; mint a fresh one per call (cheap) with a small validity window.
_JWT_TTL_SECONDS = 3600
_JWT_ISS = "enablebanking.com"
_JWT_AUD = "api.enablebanking.com"
# The redirect when the caller sends none: the user chose to link by pasting the code. The
# setup docs have always asked for it, so an older setup has it registered.
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


def _app_redirect(redirect: str) -> str:
    """The caller's return URL (the app's own ``/connectors``), or "" for none. Only an
    absolute http(s) URL is ever sent to Enable Banking."""
    url = (redirect or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("redirect must be an http(s) URL")
    return url


def _post_auth(token: str, payload: dict) -> httpx.Response:
    try:
        return httpx.post(
            f"{API_BASE}/auth", json=payload, headers=_auth_headers(token), timeout=_HTTP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Enable Banking: {exc}") from exc


def _redirect_refused(resp: httpx.Response) -> bool:
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("error") == "REDIRECT_URI_NOT_ALLOWED"


def start_authorization(aspsp: str, country: str, redirect: str) -> dict:
    """Start the bank-consent flow for ``aspsp`` (a bank name from ``list_institutions``).

    POSTs ``/auth``; Enable Banking returns the consent ``url`` the USER opens. The bank sends
    them back to ``redirect`` (the app's own ``/connectors/callback/enable_banking`` — the path
    is what tells the app the return is Enable Banking's; the control panel refuses a redirect
    with a query string) with ``state`` and ``code`` added. Enable Banking accepts only a
    redirect registered on the application, matched exactly, and says so at once
    (``REDIRECT_URI_NOT_ALLOWED``) — before any bank page exists. That refusal raises
    ``RedirectNotRegisteredError`` naming the address to add: nobody is sent to a bank whose
    answer would land on a page that cannot load. No ``redirect`` at all is the user's own
    choice to link by pasting the code; it uses ``_DEFAULT_REDIRECT``.

    The ``state`` sent is remembered with its bank (``pending_links``) so ``create_session``
    can tell the return belongs to a link started here, and which bank it linked. Returns
    ``{url, authorization_id, aspsp, returns_to_app, redirect_url}``. Raises
    ``ValidationError`` on a bad bank / auth / transport failure."""
    bank = (aspsp or "").strip()
    if not bank:
        raise ValidationError("aspsp (bank) is required")
    code = (country or "").strip().upper()
    if len(code) != 2:
        raise ValidationError("country must be a 2-letter ISO code (e.g. FI, DE, FR)")
    app_url = _app_redirect(redirect)

    store = EnableBankingConfigStore()
    token = _bearer(store)
    # Consent validity is capped at ~90 days for most banks; request the documented max.
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(timespec="seconds")
    state = str(uuid.uuid4())
    # Verified against the sandbox (issue #103): `state` is required (422 without it) and
    # `aspsp` needs both name and country.
    payload = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": bank, "country": code},
        "state": state,
        "redirect_url": app_url or _DEFAULT_REDIRECT,
        "psu_type": "personal",
    }
    resp = _post_auth(token, payload)
    if app_url and _redirect_refused(resp):
        raise RedirectNotRegisteredError(
            f"Enable Banking doesn't know this address yet. Add {app_url} to your app's "
            "redirect URLs at enablebanking.com, then try again.",
            redirect_url=app_url,
        )
    if resp.status_code not in (200, 201):
        raise _api_error(resp, "starting the bank link", redirect_url=payload["redirect_url"])

    data = resp.json() or {}
    url = (data.get("url") or data.get("authorization_url") or "").strip()
    authorization_id = (data.get("authorization_id") or "").strip()
    if not url:
        raise ValidationError("Enable Banking did not return a consent URL")

    store.update({"aspsp": bank, "country": code})
    store.add_pending_link(state, aspsp=bank, country=code, now=datetime.now(timezone.utc))
    return {
        "url": url,
        "authorization_id": authorization_id,
        "aspsp": bank,
        "returns_to_app": payload["redirect_url"] == app_url,
        "redirect_url": payload["redirect_url"],
    }


def create_session(code: str, state: str = "") -> dict:
    """Exchange the post-consent auth ``code`` for a session.

    ``state`` comes with an automatic return to the app and must be the one the last
    ``start_authorization`` sent: a return that isn't (a stale tab, a link started on another
    install, a forged URL) is refused before any call. A hand-pasted code carries no state.

    POSTs ``/sessions`` ``{code}``; Enable Banking returns ``{session_id, accounts[], access}``.
    The session becomes one more linked bank, named by the bank the link was started for.
    Linking a bank that is already listed renews it: the new session takes its place and the
    old one is revoked at Enable Banking. Returns ``{session_id, accounts, bank_key}``. Raises
    ``ValidationError`` on a bad code / state / auth / transport failure."""
    auth_code = (code or "").strip()
    if not auth_code:
        raise ValidationError("the authorization code is required")

    store = EnableBankingConfigStore()
    now = datetime.now(timezone.utc)
    returned = (state or "").strip()
    pending = store.pending_link(returned, now=now) if returned else None
    if returned and pending is None:
        raise ValidationError(
            "This bank link doesn't match one started here, or it was already completed or"
            " has expired. Click Link bank to start again."
        )
    if pending is None:
        pending = store.latest_pending_link(now=now) or {}
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

    cfg = store.get()
    # The previous app's consents were remembered and revoked at the switch; this app can't
    # read them.
    other_app = {b["session_id"] for b in store.banks() if b.get("access_ended") == "app_changed"}
    named = data.get("aspsp") if isinstance(data.get("aspsp"), dict) else {}
    accounts = data.get("accounts") or []
    key, replaced = store.add_bank(
        session_id,
        aspsp=str(pending.get("aspsp") or named.get("name") or cfg.get("aspsp") or ""),
        country=str(pending.get("country") or named.get("country") or cfg.get("country") or ""),
        valid_until=(data.get("access") or {}).get("valid_until"),
        linked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        account_ids=[_account_uid(a) for a in accounts if isinstance(a, dict)],
    )
    if pending.get("state"):
        store.drop_pending_link(pending["state"])
    for old_session in (s for s in replaced if s not in other_app):
        # Renewal: remember the old consent's accounts first, so the new session's uids map
        # back to them — even on a book that never listed its accounts since it was linked.
        _remember_accounts(store, token, old_session, store.bank(key) or {})
        _revoke_session(token, old_session)
    return {"session_id": session_id, "accounts": accounts, "bank_key": key}


class ConsentEndedError(ExternalServiceError):
    """Enable Banking no longer answers for a bank's consent: it was revoked, ran out, or
    belongs to another application. Only renewing that bank brings it back."""


def _remember_accounts(
    store: EnableBankingConfigStore, token: str, session_id: str, bank: dict
) -> None:
    """Record a session's accounts' ids (``_stable_account_id``). Best effort: a session that
    can't be read is one whose ids this book has most likely recorded already."""
    try:
        for account in session_accounts_data(token, session_id):
            _stable_account_id(store, bank, account)
    except (ExternalServiceError, httpx.HTTPError, ValueError) as exc:
        logger.warning("enable_banking: an old consent's accounts are unavailable: %s", exc)


def session_accounts_data(token: str, session_id: str) -> list[dict]:
    """A session's ``accounts_data`` — each account's uid and ``identification_hash``, which
    is all ``_stable_account_id`` needs. Verified on the sandbox: still there once the consent
    is closed or ran out (its account endpoints refuse by then), and the same hash the
    details endpoint gives; so a renewal of a consent that already ended still keeps its
    accounts' ids."""
    data = _safe_get(token, f"/sessions/{session_id}", {}).json() or {}
    return [a for a in data.get("accounts_data") or [] if isinstance(a, dict)]


def _revoke_session(token: str, session_id: str) -> bool:
    """Revoke a bank's consent at Enable Banking (``DELETE /sessions/{id}``). Best effort:
    the bank is forgotten here either way, and a consent that can't be revoked still ends
    by itself. Logs the status only — never the session id."""
    try:
        resp = httpx.delete(
            f"{API_BASE}/sessions/{session_id}", headers=_auth_headers(token), timeout=_HTTP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        logger.warning("enable_banking: revoking a session failed (%s)", type(exc).__name__)
        return False
    if resp.status_code not in (200, 204):
        logger.warning("enable_banking: revoking a session failed (HTTP %s)", resp.status_code)
        return False
    return True


def remove_all_banks(store: EnableBankingConfigStore) -> int:
    """Revoke every linked bank's consent (the connector's Remove). Returns how many were
    revoked; the caller then erases the row."""
    banks = store.banks()
    if not banks:
        return 0
    token = _bearer(store)
    return sum(_revoke_session(token, b["session_id"]) for b in banks)


def remove_bank(key: str) -> dict:
    """Unlink one bank: revoke its consent at Enable Banking, then forget it here. Its
    already-imported transactions and linked accounts stay. Returns ``{removed, revoked,
    banks_left}``. Raises ``ValidationError`` for a bank that isn't listed."""
    store = EnableBankingConfigStore()
    bank = store.bank((key or "").strip())
    if bank is None:
        raise ValidationError("That bank isn't linked to Enable Banking.")
    revoked = _revoke_session(_bearer(store), bank["session_id"])
    store.drop_bank(bank["key"])
    return {"removed": True, "revoked": revoked, "banks_left": len(store.banks())}


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


def fetch_session_accounts(
    token: str, session_id: str, *, with_balances: bool = False
) -> list[dict]:
    """GET ``/sessions/{id}`` → the consented accounts, each enriched from
    ``/accounts/{uid}/details`` (name, currency, type).

    Verified on the sandbox (issue #103): the session lists ``accounts`` as bare uid STRINGS
    (``accounts_data`` holds only uid + hashes), so keeping only dicts dropped every account.
    A details failure keeps ``{"uid": uid}`` so transactions still fetch. Raises
    ``ExternalServiceError`` (never the raw httpx exception — see ``_safe_get``) when the
    session itself can't be read."""
    try:
        resp = _safe_get(token, f"/sessions/{session_id}", {})
    except ExternalServiceError as exc:
        # Another application's session, or one Enable Banking has forgotten.
        if "HTTP 404" in str(exc):
            raise ConsentEndedError(
                "Enable Banking no longer knows this consent (HTTP 404)"
            ) from None
        raise
    data = resp.json() or {}
    # Verified on the sandbox: a revoked consent still answers 200 and lists its accounts,
    # with status CLOSED — only its transactions refuse (401 CLOSED_SESSION).
    status = str(data.get("status") or "").strip().upper()
    if status and status != "AUTHORIZED":
        raise ConsentEndedError(f"Enable Banking says this consent is {status.lower()}")
    uids: list[str] = []
    for entry in data.get("accounts") or []:
        uid = _account_uid(entry) if isinstance(entry, dict) else str(entry or "").strip()
        if uid and uid not in uids:
            uids.append(uid)

    def with_details(uid: str) -> dict:
        try:
            details = _safe_get(token, f"/accounts/{uid}/details", {}).json()
        except (ExternalServiceError, ValueError) as exc:
            logger.warning("enable_banking account details failed: %s", exc)
            details = None
        return {**details, "uid": uid} if isinstance(details, dict) else {"uid": uid}

    # Listing every account: each one's details AND balance in the same round (the balance
    # needs only the session's uid), not one round after the other — ~1.5 s less on the sandbox.
    tasks = [(uid, "details") for uid in uids]
    if with_balances:
        tasks += [(uid, "balances") for uid in uids]
    done = parallel.each(
        lambda t: with_details(t[0]) if t[1] == "details" else fetch_account_balances(token, t[0]),
        tasks,
    )
    accounts = done[: len(uids)]
    if with_balances:
        for account, figures in zip(accounts, done[len(uids) :]):
            account["_balances"] = figures
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


# ISO 20022 cash account type → the app's suggested account type (as GoCardless maps it).
_ACCOUNT_TYPES = {"CARD": "credit", "LOAN": "loan", "CACC": "cash", "SVGS": "cash", "TRAN": "cash"}
# Booked balances first (what the ledger should match), then the rest in a stable order.
_BALANCE_PREFERENCE = ("CLBD", "ITBD", "XPCD", "ITAV", "CLAV", "OPBD", "PRCD", "INFO")


def _account_mask(eb_account: dict) -> Optional[str]:
    acct_id = eb_account.get("account_id")
    iban = acct_id.get("iban") if isinstance(acct_id, dict) else None
    return str(iban)[-4:] if iban else None


def _account_to_common(
    eb_account: dict, balance: Optional[float] = None, kinds: Optional[dict] = None
) -> dict:
    """An Enable Banking account → the common ``accounts[]`` shape (RFC §A). ``kinds``: its
    balance by kind (``balance_kinds.split``)."""
    kind = str(eb_account.get("cash_account_type") or "").upper()
    return {
        **(kinds or {}),
        "provider_account_id": _account_uid(eb_account),
        "name": _account_name(eb_account),
        "mask": _account_mask(eb_account),
        "currency": _account_currency(eb_account),
        "balance": balance,
        "account_type": _ACCOUNT_TYPES.get(kind),
        "institution": eb_account.get("_institution"),
        # No currency from the bank means _account_currency filled in the base: a balance must
        # never be booked as if the bank had said so (account_value_sync fails closed on this).
        "currency_inferred": not eb_account.get("currency"),
    }


def fetch_account_balances(token: str, account_uid: str) -> tuple[Optional[float], dict]:
    """GET ``/accounts/{uid}/balances`` → ``(one signed figure, booked preferred; {cleared_balance,
    working_balance, available_to_spend})`` (``balance_kinds``; verified on the sandbox:
    ``[{balance_type: "CLBD", balance_amount: {amount, currency}}]``). Never raises — a bank
    that won't give balances leaves the account without one."""
    try:
        data = _safe_get(token, f"/accounts/{account_uid}/balances", {}).json() or {}
    except (ExternalServiceError, ValueError) as exc:
        logger.warning("enable_banking balances unavailable: %s", exc)
        return None, balance_kinds.split({})
    by_type: dict[str, float] = {}
    for b in data.get("balances") or []:
        if not isinstance(b, dict):
            continue
        try:
            amount = float((b.get("balance_amount") or {}).get("amount"))
        except (TypeError, ValueError):
            continue
        by_type.setdefault(str(b.get("balance_type") or "").upper(), amount)
    best = next((by_type[k] for k in _BALANCE_PREFERENCE if k in by_type), None)
    if best is None:
        best = next(iter(by_type.values()), None)
    return best, balance_kinds.split(by_type)


def _txn_id(eb_txn: dict) -> str:
    """The bank's immutable id for a transaction, ``entry_reference``, or "" without one.
    Never ``transaction_id``: Enable Banking documents it as a fetch handle that "can not be
    used to uniquely identify transactions and may change if the list of transactions is
    requested again" — keyed on it, a re-listed row came back as new and booked twice."""
    return str(eb_txn.get("entry_reference") or "").strip()


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
    """Best human description, from the first of these the bank filled: remittance info
    (string or list), the counterparty's name, the bank's own wording for the kind of
    transaction (``bank_transaction_code.description``, e.g. "Card payment"), the creditor
    reference (``reference_number``) — else empty. Some banks send only the transaction
    code: reading just the first two left every such row a bare "Enable Banking" (reported
    2026-11-13). Empty, never a placeholder: a shared placeholder reads as the bank's own
    words and lets one rule or payee catch unrelated rows. ``note`` is the account holder's
    own note, not the bank's, so it is never the description. A row's ref never reads it
    (``_ref_text``): improving it re-keys nothing."""
    info = eb_txn.get("remittance_information")
    if isinstance(info, list):
        info = " ".join(str(x).strip() for x in info if x and str(x).strip())
    if not (isinstance(info, str) and info.strip()):
        info = (
            _name_of(eb_txn.get("creditor"))
            or _name_of(eb_txn.get("debtor"))
            or _text_of((eb_txn.get("bank_transaction_code") or {}), "description")
            or _text_of(eb_txn, "reference_number")
        )
    return " ".join(str(info or "").split())


def _name_of(party: object) -> str:
    return _text_of(party, "name") if isinstance(party, dict) else ""


def _text_of(obj: object, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value.strip() if isinstance(value, str) else ""


# ── the key of a row the bank gives no entry_reference ─────────────────────────────────
# Such a row is keyed by a hash of its content, text included. That text is FROZEN apart
# from the description: when 0.1.50 improved the description, the text moved with it, and
# every id-less row booked under 0.1.49 came back as new and was booked a second time.

_REF_PLACEHOLDER = "Enable Banking"


def _frozen_text(obj: object, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value.strip() if isinstance(value, str) else ""


def _ref_text(eb_txn: dict) -> str:
    """The text an id-less row's ref hashes, FROZEN at the rules of 0.1.50: the remittance
    parts, each stripped, blanks skipped; else the creditor's name, the debtor's, the bank's
    wording for the kind of transaction, the reference number; whitespace collapsed; else the
    placeholder. Never change it — change ``_txn_description``, which nothing keys on."""
    info = eb_txn.get("remittance_information")
    if isinstance(info, list):
        info = " ".join(str(x).strip() for x in info if x and str(x).strip())
    if not (isinstance(info, str) and info.strip()):
        creditor, debtor = eb_txn.get("creditor"), eb_txn.get("debtor")
        info = (
            (_frozen_text(creditor, "name") if isinstance(creditor, dict) else "")
            or (_frozen_text(debtor, "name") if isinstance(debtor, dict) else "")
            or _frozen_text(eb_txn.get("bank_transaction_code") or {}, "description")
            or _frozen_text(eb_txn, "reference_number")
        )
    return " ".join(str(info or "").split()) or _REF_PLACEHOLDER


def _ref_text_0149(eb_txn: dict) -> str:
    """The text 0.1.49 hashed: the remittance parts joined as sent, else the creditor's or the
    debtor's name; stripped; else the placeholder. Frozen too: it only recognises old rows."""
    info = eb_txn.get("remittance_information")
    if isinstance(info, list):
        info = " ".join(str(x) for x in info if x)
    if not info:
        creditor = eb_txn.get("creditor") or {}
        debtor = eb_txn.get("debtor") or {}
        info = (creditor.get("name") if isinstance(creditor, dict) else None) or (
            debtor.get("name") if isinstance(debtor, dict) else None
        )
    return (str(info).strip() if info else "") or _REF_PLACEHOLDER


def _ref_0149(eb_txn: dict, account_uid: str, date_str: str, amount: float, currency: str) -> str:
    """The ref 0.1.49 booked this row under: its ``entry_reference``, else its
    ``transaction_id`` (a fetch handle, dropped in 0.1.50 as unstable), else the hash of
    ``_ref_text_0149``."""
    handle = str(eb_txn.get("entry_reference") or eb_txn.get("transaction_id") or "").strip()
    if handle:
        return f"{account_uid}:{handle}"
    return derived_ref(account_uid, date_str, amount, currency, _ref_text_0149(eb_txn))


def _txn_payee(eb_txn: dict, amount: float) -> Optional[str]:
    """The counterparty's own name — who was paid (money out) or who paid (money in) — or
    None when the bank names neither. It becomes the row's payee; the description keeps the
    bank's wording, which payee matching learns from (as GoCardless does)."""
    first, second = ("creditor", "debtor") if amount < 0 else ("debtor", "creditor")
    for key in (first, second):
        party = eb_txn.get(key)
        name = " ".join(str(party.get("name") or "").split()) if isinstance(party, dict) else ""
        if name:
            return name[:120]
    return None


# Transaction `status` (verified on the sandbox: GET .../transactions returns EVERY status by
# default). Money that never moved is dropped — a cancelled, rejected or scheduled row booked
# as spending lowers the balance for nothing; one not yet final is pending. BOOK, OTHR and a
# missing status are final.
_NOT_MONEY_STATUSES = frozenset({"CNCL", "RJCT", "SCHD"})
_PENDING_STATUSES = frozenset({"PDNG", "HOLD"})


def _original_charge(eb_txn: dict, booked_in: str) -> tuple[Optional[float], Optional[str]]:
    """A purchase made in another currency: what the merchant charged (``exchange_rate.
    instructed_amount``, e.g. USD 9.39 beside the EUR 7.05 the account paid), as GoCardless's
    ``_original_charge`` reads its ``instructedAmount``. ``(None, None)`` for a row in the
    account's own currency. It goes in the row's ``original_amount``/``original_currency``."""
    fx = eb_txn.get("exchange_rate") if isinstance(eb_txn.get("exchange_rate"), dict) else {}
    paid = fx.get("instructed_amount") if isinstance(fx.get("instructed_amount"), dict) else {}
    currency = str(paid.get("currency") or fx.get("unit_currency") or "").upper()
    try:
        amount = abs(float(paid.get("amount")))
    except (TypeError, ValueError):
        return None, None
    if currency and currency != (booked_in or "").upper() and amount:
        return amount, currency
    return None, None


def _txn_to_row(eb_txn: dict, account_uid: str, currency_hint: str) -> dict | None:
    """One Enable Banking transaction → the common normalized row shape (RFC §A step 3).
    Returns None for an unparseable/zero amount or an unrecognized credit/debit indicator
    (``_txn_amount`` already logs that case)."""
    status = str(eb_txn.get("status") or "").upper()
    if status in _NOT_MONEY_STATUSES:
        return None
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
        else derived_ref(account_uid, date_str, amount, currency, _ref_text(eb_txn))
    )
    # A row booked under 0.1.49 carries the ref it had then; the fetch still knows it by it.
    old_ref = _ref_0149(eb_txn, account_uid, date_str, amount, currency)
    original_amount, original_currency = _original_charge(eb_txn, currency)
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": eb_txn,
        "provider_account_id": account_uid,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "payee": _txn_payee(eb_txn, amount),
        "memo": None,
        "original_amount": original_amount,
        "original_currency": original_currency,
        "pending": status in _PENDING_STATUSES,
        "supersedes_ref": None,
        "ref_aliases": [old_ref] if old_ref != ref else [],
    }


def _bank_label(bank: dict, banks: list[dict]) -> Optional[str]:
    """The bank an account is shown (and filed) under: its name, plus its country only when
    another linked bank shares that name (Nordea FI and Nordea SE are two banks; a lone Nordea
    stays "Nordea", which is also what Add files a new account under)."""
    name = (bank.get("aspsp") or "").strip()
    if not name:
        return None
    twins = [b for b in banks if (b.get("aspsp") or "").strip() == name]
    country = (bank.get("country") or "").strip().upper()
    return f"{name} ({country})" if len(twins) > 1 and country else name


_RENEW = "renew it in Connectors to keep it syncing"


def _bank_unread(store: EnableBankingConfigStore, bank: dict, label: str, exc: Exception) -> str:
    """The warning for a bank that couldn't be read. A consent Enable Banking says has ended
    is flagged on the bank, so the form shows Renew on it too."""
    logger.warning("enable_banking: a bank's accounts are unavailable: %s", exc)
    if isinstance(exc, ConsentEndedError):
        store.mark_access_ended([bank["key"]], "ended")
        return f"{label}: access ended — {_RENEW}"
    return f"{label}: couldn't be read ({exc})"


def _accounts_of_every_bank(
    token: str, store: EnableBankingConfigStore, *, with_balances: bool = False
) -> tuple[list[dict], bool, list[str]]:
    """Every linked bank's accounts, each tagged with its bank (``institution``). One bank
    that can't be read (its consent ended, the bank is down) never hides the others: it is
    skipped, named in a warning, and the result is marked incomplete. When no bank at all can
    be read, that is the fetch's error — never an "ok" with nothing in it."""
    out: list[dict] = []
    complete = True
    warnings: list[str] = []
    seen: set[str] = set()
    banks = store.banks()
    for bank in banks:
        label = _bank_label(bank, banks) or "A bank"
        if bank.get("access_ended") == "app_changed":
            warnings.append(f"{label}: linked under your previous Enable Banking app — {_RENEW}")
            complete = False
            continue
        try:
            accounts = fetch_session_accounts(
                token, bank["session_id"], with_balances=with_balances
            )
        except (ExternalServiceError, httpx.HTTPError) as exc:
            warnings.append(_bank_unread(store, bank, label, exc))
            complete = False
            continue
        if bank.get("access_ended"):
            # It answers again (back on the app it was linked under): nothing to renew.
            store.mark_access_ended([bank["key"]], None)
        for a in accounts:
            api_uid = _account_uid(a)
            uid = _stable_account_id(store, bank, a)
            if uid in seen:
                continue
            seen.add(uid)
            out.append(
                {**a, "uid": uid, "_api_uid": api_uid, "_institution": _bank_label(bank, banks)}
            )
    if banks and len(warnings) == len(banks):
        raise ValidationError("No bank could be read. " + "; ".join(warnings))
    return out, complete, warnings


def _stable_account_id(store: EnableBankingConfigStore, bank: dict, eb_account: dict) -> str:
    """The account's id here for good (see ``account_identities``): its first uid, found again
    through Enable Banking's ``identification_hash`` after a renewal hands it a new one. An
    account without that hash keeps its session uid, as before."""
    uid = _account_uid(eb_account)
    identification = str(eb_account.get("identification_hash") or "").strip()
    if not uid or not identification:
        return uid
    return store.account_id(
        aspsp=str(bank.get("aspsp") or ""),
        country=str(bank.get("country") or ""),
        identification_hash=identification,
        uid=uid,
    )


def _api_uid(eb_account: dict) -> str:
    """The uid Enable Banking's own endpoints know this account by in its current session."""
    return str(eb_account.get("_api_uid") or _account_uid(eb_account))


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
    # Every account wanted: their balances come with their details. One account (a sync): only
    # its own balance is read — a bank limits balance reads too.
    eb_accounts, complete, warnings = _accounts_of_every_bank(
        token, store, with_balances=account is None
    )
    if account:
        eb_accounts = [a for a in eb_accounts if _account_uid(a) == account]
    balances = parallel.each(
        lambda a: a.pop("_balances", None) or fetch_account_balances(token, _api_uid(a)),
        eb_accounts,
    )
    accounts = [_account_to_common(a, *b) for a, b in zip(eb_accounts, balances)]

    rows: list[dict] = []
    for eb_account in eb_accounts:
        account_uid = _account_uid(eb_account)
        if not account_uid or accounts_only:
            continue
        try:
            txns, account_complete = fetch_account_transactions(
                token, _api_uid(eb_account), date_from=since, date_to=before
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
    return FetchWindow(accounts=accounts, rows=rows, complete=complete, warnings=warnings)


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
