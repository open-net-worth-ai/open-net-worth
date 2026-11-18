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
import hashlib
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.core.user_time import user_zone
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.services.aggregation import simplefin_feed as sf_feed
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

# What the Bridge's own statuses mean for the user (protocol checklist): 403 is an access URL
# revoked or disabled, 402 a lapsed SimpleFIN subscription. Each keeps "HTTP <code>" so
# ``classify_error`` still reads it.
_STATUS_TEXT = {
    402: "SimpleFIN Bridge needs payment (HTTP 402): renew your SimpleFIN subscription",
    403: (
        "SimpleFIN refused the connection (HTTP 403): it was turned off at SimpleFIN Bridge. "
        "Connect SimpleFIN again with a new setup token"
    ),
}


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
        code = exc.response.status_code
        raise ExternalServiceError(_STATUS_TEXT.get(code, f"SimpleFIN HTTP {code}")) from None
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
    _wake_sync()
    return access_url


def _wake_sync() -> None:
    """A connection just claimed: a bank sync waiting for the user runs at the next tick."""
    from backend.services.bank_data_sync import budget

    budget.access_renewed("simplefin")


def fetch_accounts(
    access_url: str,
    start_date: int | None = None,
    end_date: int | None = None,
    account: str | None = None,
    *,
    balances_only: bool = False,
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
    if balances_only:
        params["balances-only"] = 1
    else:
        params["pending"] = 1  # opt-in; never send pending=0, which the Bridge reads as 1
    params["version"] = 2  # ``errlist`` codes and ``connections`` (protocol v2)
    resp = _safe_get(url, params)
    return resp.json()


# ── normalize + fetch_window ─────────────────────────────────────────────────────────


def _ref_text(sf_txn: dict) -> str:
    """The text an id-less row's ref hashes. FROZEN: an id-less row's ref hashes it, so a change here books every such row again."""
    return (sf_txn.get("description") or sf_txn.get("payee") or "").strip() or "SimpleFIN"


def _is_pending(sf_txn: dict) -> bool:
    """Pending is the ``pending`` flag, or ``posted`` 0 beside a ``transacted_at`` (the
    protocol: posted "may be 0" while pending). A missing ``posted`` is not pending — some
    banks omit it on settled rows (Sure, ``SimplefinEntry::Processor``)."""
    if sf_txn.get("pending"):
        return True
    try:
        return (
            sf_txn.get("posted") is not None
            and int(sf_txn.get("posted") or 0) == 0
            and bool(int(sf_txn.get("transacted_at") or 0))
        )
    except (TypeError, ValueError):
        return False


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
    description = (sf_txn.get("description") or sf_txn.get("payee") or "").strip()
    txn_id = str(sf_txn.get("id") or "").strip()
    ref = (
        f"{sf_account_id}:{txn_id}"
        if txn_id
        else derived_ref(sf_account_id, date_str, amount, currency, _ref_text(sf_txn))
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
        "pending": _is_pending(sf_txn),
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


# SimpleFIN Bridge answers a range over 45 days with an error ("Requested date range exceeds
# recommended range of 45 days. In the future, this may be capped.", seen 2026-11-13), and a
# 45-day slice that crosses a clock change is an hour over it (Sure #5101): 44 at most.
_MAX_SLICE_DAYS = 44


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


# ── one request for every account ───────────────────────────────────────────────────
# The Bridge allows about 24 requests a day per connection, refreshes from the banks once a
# day, and a request per account blew that budget (Actual #3228). The bank data sync reads
# one account at a time, so a window is fetched ONCE for every account and each account's
# read in the same run takes its rows from that answer. Kept a couple of minutes: a run.
_SHARED_FOR = 120.0
_shared: dict[tuple, tuple[float, dict]] = {}
_shared_lock = threading.Lock()


def _feed(access_url: str, start: int, end: int) -> dict:
    key = (hashlib.sha256(access_url.encode()).hexdigest(), start, end)
    now = time.monotonic()
    with _shared_lock:
        for k in [k for k, (at, _) in _shared.items() if now - at > _SHARED_FOR]:
            _shared.pop(k, None)
        hit = _shared.get(key)
    if hit is not None:
        return hit[1]
    try:
        feed = fetch_accounts(access_url, start_date=start, end_date=end)
    except Exception as exc:  # noqa: BLE001 — belt-and-braces scrub, see _scrub's docstring
        raise type(exc)(_scrub(str(exc), access_url)) from exc
    with _shared_lock:
        _shared[key] = (time.monotonic(), feed)
    return feed


def _forget_shared() -> None:
    with _shared_lock:
        _shared.clear()


def fetch_window(
    store: SimpleFinConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """SimpleFIN's provider-side windowed fetch (RFC §B): ``start-date``/``end-date`` are
    server-side, in slices of at most ``_MAX_SLICE_DAYS``. Every account comes in ONE request
    per slice (``_feed``), shared by the run's per-account reads; ``account`` then picks that
    account's rows. ``complete`` is False when the Bridge reported an error about the account
    (or one with no scope), which may leave rows out. A sign-in the Bridge needs for the
    account's connection (``con.auth``) raises, so the sync asks for it instead of booking a
    feed that stopped moving."""
    access_url = store.access_url()
    if accounts_only:
        feed = fetch_accounts(access_url, balances_only=True)
        errors = sf_feed.feed_errors(feed, lambda t: _scrub(t, access_url))
        sf_feed.raise_fatal(errors)
        return FetchWindow(
            accounts=sf_feed.accounts_of(feed),
            rows=[],
            complete=not errors,
            warnings=[f"SimpleFIN: {e['msg']}" for e in errors],
        )
    accounts_by_id: dict[str, dict] = {}
    connections: dict[str, str] = {}
    rows_by_ref: dict[str, dict] = {}
    errors: list[dict] = []
    for start_ts, end_ts in _slices(since, before):
        feed = _feed(access_url, start_ts, end_ts)
        found = sf_feed.feed_errors(feed, lambda t: _scrub(t, access_url))
        sf_feed.raise_fatal(found)
        errors.extend(e for e in found if e not in errors)
        connections.update(sf_feed.connections_of(feed))
        for sf_account in feed.get("accounts") or []:
            sf_account_id = str(sf_account.get("id") or "")
            accounts_by_id[sf_account_id] = sf_account
            if account and sf_account_id != account:
                continue
            currency = sf_feed.row_currency(sf_account)
            if currency is None:
                continue  # a custom currency (points): nothing to book it in
            for sf_txn in sf_account.get("transactions") or []:
                row = _txn_to_row(sf_txn, sf_account_id, currency)
                if row is not None:
                    rows_by_ref.setdefault(row["external_refs"][0]["ref"], row)
    conn_id = str((accounts_by_id.get(account or "") or {}).get("conn_id") or "")
    mine = [e for e in errors if sf_feed.concerns(e, account, conn_id)]
    for e in mine:
        if e["code"] == "con.auth" and account:
            raise ExternalServiceError(
                f"SimpleFIN: {e['msg']} — fix the connection at SimpleFIN Bridge",
                bank_access=True,
            )
    warnings = [f"SimpleFIN: {e['msg']}" for e in mine]
    for warning in warnings:
        logger.warning("simplefin fetch: %s", warning)
    return FetchWindow(
        accounts=[sf_feed.account_to_common(a, connections) for a in accounts_by_id.values()],
        rows=list(rows_by_ref.values()),
        complete=not mine,
        warnings=warnings,
    )


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for SimpleFIN. The real fetch path raises the sanitized
    ``ExternalServiceError`` from ``_safe_get`` (no url/credential — see its docstring);
    re-derive the class from that safe message, or from the context ``fetch_window`` set on
    an error the Bridge reported inside a 200. The raw ``httpx.*`` branches below stay for
    callers/tests that raise those directly."""
    if isinstance(exc, ExternalServiceError):
        if exc.context.get("bank_access"):
            return "reconnect_needed"  # one connection's sign-in, fixed at SimpleFIN Bridge
        if exc.context.get("rate_limited"):
            return "rate_limited"
        if exc.context.get("access_refused"):
            return "auth_error"
        msg = str(exc).lower()
        if "http 401" in msg or "http 402" in msg or "http 403" in msg:
            return "auth_error"
        if "http 429" in msg:
            return "rate_limited"
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
