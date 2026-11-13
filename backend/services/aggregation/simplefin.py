"""SimpleFIN aggregation provider — the FIRST real bank/card account-aggregation data
source.

SimpleFIN (https://www.simplefin.org/) is a cheap (~$15/yr), OAuth-free, US/Canada
bank-sync protocol: a one-time **setup token** is claimed for a long-lived **access URL**
(HTTP-basic creds embedded), and `GET {access_url}/accounts` returns accounts + their
transactions as JSON, windowed by `start-date`/`end-date` (unix seconds) and optionally
filtered by `account` — all server-side, no client-side windowing needed.

This module is the PURE protocol (``claim_setup_token`` / ``fetch_accounts``) + normalize
(``fetch_window``) — READ-ONLY, called from a Lucius turn via
``simplefin_fetch_transactions`` (RFC ``connectors-as-data-sources``). There is no adapter
class, no auto-created account, and no scheduled sync any more: the provider-account →
app-account link lives on ``Account.external_ids``, and booking happens through
``add_transactions`` in the SAME turn that fetched, never here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.core.user_time import user_zone
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.mcc import merchant_category
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
# SimpleFIN "type" → an Open Net Worth account type/kind. Most SimpleFIN orgs are banks /
# cards; we default to a bank-style asset and let the user re-classify in the UI. A
# negative balance on a card/loan account stays raw-negative (the net-worth invariant).
_DEFAULT_ACCOUNT_TYPE = "cash"
# Hard cap on a SimpleFIN response body we will read into memory (DoS guard — a hostile
# or runaway endpoint cannot make us buffer an unbounded response). SimpleFIN /accounts
# feeds are small JSON documents (accounts + a rolling window of transactions), so 8 MiB
# is generous — orders of magnitude above any realistic feed, well below ibkr.py's 32 MiB
# (a Flex activity statement can legitimately be much bigger).
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

SOURCE_ID = "simplefin"
EXTERNAL_SOURCE = "simplefin"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── protocol ───────────────────────────────────────────────────────────────────────


def _scrub(text: str, access_url: str) -> str:
    """Strip the SimpleFIN access URL — and the HTTP-basic credential riding in its netloc
    — out of any text reaching ``last_error``/logs. Belt-and-braces alongside ``_safe_get``/
    ``claim_setup_token``'s own sanitization: even if a credential-bearing string sneaks into
    a message some other way (a future bug, a third-party library), we never persist or log
    it verbatim. Mirrors ``ibkr._scrub``."""
    if not text or not access_url:
        return text
    text = text.replace(access_url, "***")
    parts = urlsplit(access_url)
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "")
        text = text.replace(userinfo, "***")
    return text


def _safe_get(url: str, params: dict) -> httpx.Response:
    """GET wrapper for the SimpleFIN endpoints that NEVER lets a raw httpx exception escape.

    ``httpx`` puts the full request URL into the string form of ``HTTPStatusError`` /
    request errors — and unlike IBKR's token-in-query-param, SimpleFIN's HTTP-basic
    credentials live IN the URL's netloc (``https://user:pass@host/...``), so
    ``repr()``/``str()`` of the URL never masks them (only ``httpx.URL.__repr__`` does — the
    exception string does not). The ingestion orchestrator stores ``str(exc)[:300]`` into
    ``last_error`` (surfaced by the ungated ``get_simplefin_status``/bulk ``get_connector_status``
    AI tools) and logs it, so an unsanitized exception hands the model the bank credential. We
    catch every httpx error here and re-raise ``ExternalServiceError`` carrying ONLY the HTTP
    status code / exception class name — no URL, no credential. Mirrors ``ibkr._safe_get``.

    Also bounds the response body at ``_MAX_RESPONSE_BYTES`` (DoS guard) before returning.
    """
    try:
        resp = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"SimpleFIN HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"SimpleFIN request failed ({type(exc).__name__})") from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"SimpleFIN response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    return resp


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time SimpleFIN **setup token** for a long-lived **access URL**.

    The setup token is base64(claim_url). POST (empty body) to the claim URL; the 200
    response body IS the access URL (HTTP-basic creds embedded). The decoded claim URL is
    validated against ``core.net_guard.assert_public_url`` BEFORE any request is made — a
    setup token is often pasted in from outside the app (support docs, a forum post), so its
    claim URL is attacker-influenceable the same way a malicious ``access_url`` is (see
    ``fetch_accounts``). Raises ``ValidationError`` on a malformed token, an unsafe claim URL,
    or a failed/duplicate claim (setup tokens are single-use); ``ExternalServiceError``
    (sanitized — no URL, no credential) on a transport failure.
    """
    token = (setup_token or "").strip()
    if not token:
        raise ValidationError("setup token is empty")
    try:
        claim_url = base64.b64decode(token, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("setup token is not valid base64") from exc
    if not claim_url.lower().startswith(("http://", "https://")):
        raise ValidationError("setup token does not decode to a claim URL")
    try:
        claim_url = assert_public_url(claim_url)
    except UnsafeUrlError as exc:
        raise ValidationError(f"claim URL failed the network safety check: {exc}") from exc

    try:
        resp = httpx.post(claim_url, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ExternalServiceError(
            f"could not reach the SimpleFIN claim URL ({type(exc).__name__})"
        ) from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"SimpleFIN claim response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    if resp.status_code != 200:
        # 403 is the classic "already claimed" (setup tokens are single-use).
        hint = " (already claimed? setup tokens are one-time)" if resp.status_code == 403 else ""
        raise ValidationError(f"claiming the setup token failed: HTTP {resp.status_code}{hint}")

    access_url = (resp.text or "").strip()
    if not access_url.lower().startswith(("http://", "https://")):
        raise ValidationError("claim did not return a valid access URL")
    return access_url


def fetch_accounts(
    access_url: str,
    start_date: int | None = None,
    end_date: int | None = None,
    account: str | None = None,
) -> dict:
    """GET the SimpleFIN ``/accounts`` feed. The access URL carries HTTP-basic creds; we
    append ``/accounts`` and the optional ``start-date``/``end-date`` (unix seconds) +
    ``account`` window/filter params — all server-side, per SimpleFIN's own protocol.
    ``access_url`` is whatever the claim response body said (see ``claim_setup_token``) —
    fully attacker-influenceable if the user was socially engineered into pasting a
    malicious setup token — so it is validated against ``core.net_guard.assert_public_url``
    before every fetch, not just once at claim time (this can run on every Lucius turn that
    calls the fetch tool). Returns the parsed JSON ``{errors:[...], accounts:[...]}``.
    Raises ``ValidationError`` on an unsafe access URL, ``ExternalServiceError``
    (sanitized — no URL, no credential) on a transport/HTTP failure — never silently
    fabricates data."""
    base = access_url.rstrip("/")
    url = f"{base}/accounts"
    try:
        url = assert_public_url(url)
    except UnsafeUrlError as exc:
        raise ValidationError(
            f"SimpleFIN access URL failed the network safety check: {exc}"
        ) from exc
    params: dict = {}
    if start_date is not None:
        params["start-date"] = int(start_date)
    if end_date is not None:
        params["end-date"] = int(end_date)
    if account:
        params["account"] = account
    params["pending"] = 1
    resp = _safe_get(url, params)
    return resp.json()


# ── normalize + fetch_window ─────────────────────────────────────────────────────────


def _account_to_common(sf_account: dict) -> dict:
    """A SimpleFIN account → the common ``accounts[]`` shape (RFC §A)."""
    org = sf_account.get("org") or {}
    org_name = org.get("name") or org.get("domain") or ""
    name = sf_account.get("name") or "SimpleFIN account"
    display = f"{org_name} · {name}".strip(" ·") if org_name else name
    try:
        balance = float(sf_account.get("balance"))
    except (TypeError, ValueError):
        balance = None
    return {
        "provider_account_id": str(sf_account.get("id") or ""),
        "name": display,
        "mask": None,
        "currency": (sf_account.get("currency") or "").upper() or None,
        "balance": balance,
    }


def _txn_to_row(sf_txn: dict, sf_account_id: str, currency: str) -> dict | None:
    """One SimpleFIN transaction → the common normalized row shape (RFC §A step 3).

    SimpleFIN ``amount`` is a signed decimal STRING: positive = money in, negative = money
    out — already this app's own convention, no sign flip needed. Returns None only for an
    unparseable/zero amount (never for a missing id — SimpleFIN's own ``id`` is required by
    its schema, but a defensive derived ref still covers it)."""
    try:
        amount = float(sf_txn.get("amount"))
    except (TypeError, ValueError):
        return None
    if amount == 0:
        return None

    date_str = _txn_date(sf_txn)
    if date_str is None:
        return None
    description = (sf_txn.get("description") or sf_txn.get("payee") or "").strip() or "SimpleFIN"
    txn_id = str(sf_txn.get("id") or "").strip()
    ref = (
        f"{sf_account_id}:{txn_id}"
        if txn_id
        else derived_ref(sf_account_id, date_str, amount, currency, description)
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": sf_txn,
        "provider_account_id": sf_account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "payee": _text(sf_txn.get("payee")),
        "memo": _text(sf_txn.get("memo")),
        "category_hint": merchant_category(sf_txn.get("mcc")),
        "pending": bool(sf_txn.get("pending")),
        "supersedes_ref": None,
    }


def _text(value: object) -> Optional[str]:
    text = " ".join(str(value or "").split())
    return text[:200] or None


def _txn_date(sf_txn: dict) -> Optional[str]:
    """The day the money moved, in the USER's zone — a UTC date put an evening purchase on
    the next day. ``posted`` is 0 for a pending row, which carries ``transacted_at``
    instead. None when neither is a timestamp: a row is never dated today by guesswork."""
    zone = user_zone()  # None = the server's own zone, as user_today() reads it
    for key in ("posted", "transacted_at"):
        try:
            ts = int(sf_txn.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if ts > 0:
            try:
                return datetime.fromtimestamp(ts, tz=zone).date().isoformat()
            except (OverflowError, OSError, ValueError):
                continue
    return None


# SimpleFIN Bridge answers a longer range with an error ("Requested date range exceeds
# recommended range of 45 days. In the future, this may be capped.", seen 2026-11-13), so a
# window is fetched in slices no longer than this.
_MAX_SLICE_DAYS = 45


def _slices(since: str, before: str) -> list[tuple[int, int]]:
    """``[since, before]`` (inclusive days) as ``(start, end)`` unix-second pairs no longer
    than ``_MAX_SLICE_DAYS``, at midnight in the user's zone. SimpleFIN's ``end-date`` is
    exclusive, so the last slice ends at the midnight AFTER ``before`` — ending at
    ``before`` itself dropped that whole day."""
    zone = user_zone()  # None = the server's own zone, as user_today() reads it
    first = datetime.fromisoformat(since).date()
    last = datetime.fromisoformat(before).date()
    out: list[tuple[int, int]] = []
    day = first
    while day <= last:
        stop = min(day + timedelta(days=_MAX_SLICE_DAYS), last + timedelta(days=1))
        out.append((_midnight(day, zone), _midnight(stop, zone)))
        day = stop
    return out


def _midnight(day: date, zone) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())


def _feed_errors(feed: dict, access_url: str) -> list[str]:
    """SimpleFIN reports a failed bank connection INSIDE a 200 response: ``errors`` (strings)
    and, from newer bridges, ``errlist`` (``{code, msg, ...}``). Ignoring them made a feed
    missing a bank look complete. Server text, so scrubbed, flattened and capped."""
    found: list[str] = []
    for item in feed.get("errors") or []:
        found.append(str(item))
    for item in feed.get("errlist") or []:
        if isinstance(item, dict):
            found.append(" ".join(str(item.get(k) or "") for k in ("code", "msg")).strip())
        else:
            found.append(str(item))
    out: list[str] = []
    for text in found:
        clean = " ".join(_scrub(text, access_url).split())[:300]
        if clean and clean not in out:
            out.append(f"SimpleFIN: {clean}")
    return out


def fetch_window(
    store: SimpleFinConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """SimpleFIN's provider-side windowed fetch (RFC §B) — ``start-date``/``end-date``/
    ``account`` are all server-side SimpleFIN params, so the window and account filter
    apply BEFORE the data ever reaches us. The feed is not paginated; it is ``complete``
    unless SimpleFIN reported an error for some connection, which leaves rows out."""
    access_url = store.access_url()
    slices = _slices(since, before)
    if accounts_only:
        slices = [(slices[0][0], slices[0][0])]  # zero-width — accounts still come back in full
    accounts_by_id: dict[str, dict] = {}
    rows_by_ref: dict[str, dict] = {}
    warnings: list[str] = []
    for start_ts, end_ts in slices:
        try:
            feed = fetch_accounts(access_url, start_date=start_ts, end_date=end_ts, account=account)
        except Exception as exc:  # noqa: BLE001 — belt-and-braces scrub, see _scrub's docstring
            raise type(exc)(_scrub(str(exc), access_url)) from exc
        warnings.extend(w for w in _feed_errors(feed, access_url) if w not in warnings)
        for sf_account in feed.get("accounts") or []:
            sf_account_id = str(sf_account.get("id") or "")
            accounts_by_id[sf_account_id] = sf_account
            if accounts_only:
                continue
            currency = (sf_account.get("currency") or ledger_service.base_currency()).upper()
            for sf_txn in sf_account.get("transactions") or []:
                row = _txn_to_row(sf_txn, sf_account_id, currency)
                if row is not None:
                    rows_by_ref.setdefault(row["external_refs"][0]["ref"], row)
    for warning in warnings:
        logger.warning("simplefin fetch: %s", warning)
    return FetchWindow(
        accounts=[_account_to_common(a) for a in accounts_by_id.values()],
        rows=list(rows_by_ref.values()),
        complete=not warnings,
        warnings=warnings,
    )


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for SimpleFIN. The real fetch path raises the sanitized
    ``ExternalServiceError`` from ``_safe_get`` (no url/credential — see its docstring);
    re-derive the class from that safe message. The raw ``httpx.*`` branches below stay for
    callers/tests that raise those directly."""
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
