"""YNAB aggregation adapter — imports transactions from a YNAB (https://www.ynab.com,
api.ynab.com) budget into the ledger.

YNAB ("You Need A Budget") is a third-party budgeting SaaS: the user tracks their own
accounts INSIDE YNAB (manually, or via YNAB's own bank-linking), and YNAB exposes the
resulting transaction ledger over a simple Bearer-token REST API — a **personal access
token** minted in the user's own Account Settings -> Developer Settings, no OAuth, no app
approval. Structurally the same shape as SimpleFIN's access URL / Lunch Money's access
token; this module is their close structural sibling.

**API surface verified live this session against YNAB's own current OpenAPI spec**
(``https://api.ynab.com/papi/open_api_spec.yaml``, ``info.version: 1.86.0``), not assumed:

  * **The real base path is ``/plans``, not ``/budgets``.** YNAB's top-level container —
    what every user-facing part of the product still calls a "budget" — is addressed in the
    CURRENT, documented API as a **plan**: ``GET /v1/plans``, ``GET
    /v1/plans/{plan_id}/accounts``, ``GET /v1/plans/{plan_id}/transactions``, etc. The spec
    contains zero ``/budgets`` paths. (YNAB's own docs prose claims the old
    ``/budgets/{budget_id}`` paths "continue to work... for backward compatibility, but are
    no longer documented" — that claim could not be independently verified from this
    environment, since testing it needs a real token this environment does not have, so this
    connector is built ENTIRELY against the current, documented ``/plans`` surface and never
    references the undocumented alias.) This module keeps "budget" as the USER-FACING word
    (config fields, UI copy, the ADR) since that is still what YNAB's own product and this
    app's operators call it — only the wire path underneath is ``/plans``.
  * **Auth**: ``Authorization: Bearer <token>``, confirmed both in the spec's
    ``security: [{bearer: []}]`` and live (an unauthenticated GET against ``/v1/plans``
    returns ``{"error": {"id": "401", "name": "unauthorized", "detail": "Unauthorized"}}``).
  * **Rate limit**: 200 requests/hour per token, rolling window (YNAB's docs prose; not
    itself in the OpenAPI spec, which doesn't carry rate-limit metadata). In the STEADY
    STATE this connector makes exactly ONE call per sync (``GET .../transactions`` with
    ``last_knowledge_of_server`` — see below); at the poll floor of 3600s that is at most 1
    call/hour, and at the 21600s (6h) default at most 4/day. Budget RESOLUTION (see below)
    costs one extra ``GET /plans`` call, but only on the very first sync (or an explicit
    re-pick) — nowhere close to the 200/hour ceiling even with a human mashing "Sync now".
  * **Amounts are "milliunits"**: a signed int64, ``1000`` milliunits = one currency unit
    (the spec's own worked example: an import_id of ``'YNAB:-294230:2015-12-30:1'`` names a
    transaction "in the amount of -$294.23 USD" — i.e. ``-294230 / 1000 == -294.23``). Sign
    convention: negative = outflow, positive = inflow — the SAME polarity every other
    adapter here already expects, so (unlike Lunch Money's ``debit_as_negative`` request
    flag) no sign transform is needed.
  * **Dedup id**: every transaction carries a required ``id`` (its own stable identity,
    unique within the plan) — that is what this adapter dedupes on, not the separate,
    OPTIONAL ``import_id`` field (which exists for YNAB's own direct-bank-import dedup and is
    absent on a manually-entered transaction, so it cannot serve as a universal key).
  * **Windowed fetch, no cursor** (RFC ``connectors-as-data-sources`` §B): ``since_date`` is
    real and documented (``GET /plans/{id}/transactions?since_date=YYYY-MM-DD``) — used here
    directly as the caller's own window start, never a persisted delta cursor. "New" means
    "no live ledger row carries the ref" (never "since the last run"), so
    ``last_knowledge_of_server``/``server_knowledge`` are gone entirely; every fetch is an
    explicit, repeatable ``[since, before]`` window rather than an incremental one.

**Multiple budgets** (the one real structural difference from SimpleFIN/Lunch Money, which
each have exactly one implicit account universe per credential): a personal access token can
see more than one YNAB budget. Which one to sync is a data-integrity decision — importing
into the wrong budget's transactions would be silently wrong money in the ledger, so this
NEVER guesses among several. See ``docs/adr/2026-08-17-ynab-multiple-budgets-selection.md``
for the full reasoning; short version: auto-detect + persist when the token sees EXACTLY
ONE, otherwise refuse and name every candidate (mirrors ``resolve_account_ref``'s ambiguity
contract: raise, list the real candidates, never default to "the first one").

**Category mapping: NOT attempted** — matches the Lunch Money precedent exactly. YNAB's own
category ids are the user's OWN taxonomy inside YNAB; this app's ``category`` is an OPAQUE id
(``cat_<12hex>``, CLAUDE.md) that RAISES on an unknown value, never silently coerced from an
arbitrary label. Imported transactions land with ``category=None`` (Uncategorized), like every
other connector.

**Transfers are NOT special-cased** — matches SimpleFIN/Lunch Money exactly. A transfer
between two YNAB accounts arrives as two ordinary transaction rows (one per account, each
carrying ``transfer_account_id``); each is mapped as its own income/expense leg against its
own auto-created Open Net Worth account, exactly like every other transaction. That is the
same shape SimpleFIN/Lunch Money already produce for a transfer between two of a user's own
aggregated accounts, so this is consistency with precedent, not a new limitation.

This module is the PURE protocol (``list_budgets`` / ``fetch_transactions``) + normalize
(``fetch_window``) — READ-ONLY, called from a Lucius turn via ``ynab_fetch_transactions``
(RFC ``connectors-as-data-sources``). There is no adapter class and no scheduled sync any
more — the provider-account → app-account link lives on ``Account.external_ids``, and
booking happens through ``add_transactions`` in the SAME turn that fetched, never here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.untrusted import sanitize_untrusted
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

API_BASE = "https://api.ynab.com/v1"
_HTTP_TIMEOUT = 30.0
# Hard cap on a YNAB response body we will read into memory (DoS guard — mirrors
# simplefin.py/lunch_money.py's own bound). A year of one budget's transactions is a small
# JSON document, so 8 MiB is generous.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

SOURCE_ID = "ynab"
EXTERNAL_SOURCE = "ynab"


class YnabBudgetSelectionRequired(ValidationError):
    """Raised when the token can see zero or more than one budget and none has been
    explicitly chosen. Picking a budget determines WHICH money lands in the ledger, so this
    never guesses — it always names the real candidates (mirrors
    ``resolve_account_ref``'s ambiguity contract)."""


# ── protocol ───────────────────────────────────────────────────────────────────────


class _TooLarge(Exception):
    """Internal: the response body passed the caller's byte cap."""


def _error_detail(resp: httpx.Response) -> str:
    """YNAB's own validation message ("date cannot be in the future"), for a 4xx. Third-party
    text, so it is sanitized and capped; never anything from the request, so never the token."""
    try:
        detail = resp.json()["error"]["detail"]
    except (ValueError, KeyError, TypeError):
        return ""
    return sanitize_untrusted(str(detail or ""), max_len=200).strip()


def _safe_send(
    store: YnabConfigStore,
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    *,
    max_bytes: int = _MAX_RESPONSE_BYTES,
) -> httpx.Response:
    """The ONE way this app talks to YNAB. Request wrapper that NEVER lets a raw exception
    escape (mirrors ``lunch_money._safe_get``) and bounds the response body at ``max_bytes``.
    The base URL is the fixed ``API_BASE`` and the Bearer token comes from the stored config,
    so a caller can neither redirect a request nor see the credential. Raises
    ``ValidationError`` when no token is saved, ``ExternalServiceError`` (status code / exception
    class / YNAB's own validation message only — no token) on a transport/HTTP failure OR any
    other unexpected exception raised while building/sending the request (a non-ASCII token
    raising ``UnicodeEncodeError`` out of httpx's own header encoding, for instance — not an
    ``httpx.HTTPError`` subclass, so it would otherwise reach ``last_error``/logs unsanitized;
    see the Lunch Money security-review finding this mirrors)."""
    token = store.access_token()
    if not token:
        raise ValidationError("YNAB is not configured (no access token)")
    try:
        # Streamed, so the cap bounds MEMORY: the body is abandoned the moment it passes
        # ``max_bytes`` instead of being fully buffered before a length check.
        with httpx.stream(
            method,
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT,
            **({"json": body} if body is not None else {}),
        ) as resp:
            if int(resp.headers.get("content-length") or 0) > max_bytes:
                raise _TooLarge
            payload = bytearray()
            for chunk in resp.iter_bytes():
                payload += chunk
                if len(payload) > max_bytes:
                    raise _TooLarge
            status = resp.status_code
    except _TooLarge:
        raise ExternalServiceError(
            f"YNAB response too large (> {max_bytes} bytes); refused."
        ) from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"YNAB request failed ({type(exc).__name__})") from None
    except Exception as exc:
        raise ExternalServiceError(f"YNAB request failed ({type(exc).__name__})") from None
    response = httpx.Response(status, content=bytes(payload))
    if status >= 400:
        detail = _error_detail(response)
        raise ExternalServiceError(f"YNAB HTTP {status}" + (f": {detail}" if detail else ""))
    return response


def _safe_get(
    store: YnabConfigStore, path: str, params: dict, *, max_bytes: int = _MAX_RESPONSE_BYTES
) -> httpx.Response:
    """GET through ``_safe_send``."""
    return _safe_send(store, "GET", path, params, max_bytes=max_bytes)


def list_budgets(store: YnabConfigStore) -> list[dict]:
    """The budgets ('plans', in YNAB's own current API vocabulary — see the module
    docstring) this token can see: ``[{id, name, currency}]``, ``currency`` an ISO code or
    None when YNAB has no currency format set yet for that budget. Used both by the config
    form's picker (``GET /api/ynab/budgets``) and by budget auto-detection during a sync.
    Raises ``ValidationError``/``ExternalServiceError`` on an auth/transport failure OR a
    malformed response body — a non-JSON body (``resp.json()`` raising) or a root shape other
    than the documented ``{data: {plans: [...]}}`` envelope (e.g. a bare JSON array) is a
    typed ``ExternalServiceError``, never a raw ``JSONDecodeError``/``AttributeError``
    escaping past this function (security-review finding: ``GET /api/ynab/budgets`` has no
    fetch-tool safety net under it, which would otherwise surface an unhandled 500). A
    malformed INDIVIDUAL plan entry — not a dict, or missing an
    ``id`` — is instead skipped rather than aborting the whole list: one bad entry must not
    hide every real budget, and an id-less entry would otherwise flow all the way to
    ``/plans/None/transactions``.

    ``name`` is THIRD-PARTY text — set by whoever owns that YNAB budget, not necessarily this
    app's operator (a shared/partner budget's name is outside their control) — so it is
    sanitized HERE, at the point it first enters this app's data (``core.untrusted.
    sanitize_untrusted``, the same treatment an MCP server's name gets in
    ``ai/tools/connectors.py``), before it can reach the disambiguation error message
    (``resolve_budget``/``_resolve_single_budget``), ``YnabConfigStore.budget_name``, or the
    model-facing ``get_ynab_status`` tool. Sanitizing once here means nothing downstream needs
    to remember to."""
    resp = _safe_get(store, "/plans", {})
    try:
        data = resp.json()
    except ValueError as exc:
        raise ExternalServiceError(
            f"YNAB returned a malformed response ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise ExternalServiceError("YNAB returned an unexpected response shape for /plans")
    inner = data.get("data")
    plans = inner.get("plans") if isinstance(inner, dict) else None
    if not isinstance(plans, list):
        plans = []
    out: list[dict] = []
    for p in plans:
        if not isinstance(p, dict):
            continue
        plan_id = p.get("id")
        if not plan_id:
            continue
        currency_format = p.get("currency_format")
        if not isinstance(currency_format, dict):
            currency_format = {}
        out.append(
            {
                "id": plan_id,
                "name": sanitize_untrusted(str(p.get("name") or "YNAB budget"), max_len=120),
                "currency": (currency_format.get("iso_code") or "").upper() or None,
            }
        )
    return out


def resolve_budget(store: YnabConfigStore, budget_id: str) -> dict:
    """Validate ``budget_id`` against the token's real budget list and return its
    ``{id, name, currency}`` — used at CONFIG-SAVE time (mirrors SimpleFIN's
    ``claim_setup_token``: a live check when the operator picks a budget surfaces a
    typo'd/wrong id immediately as a clean error, rather than silently failing on the next
    scheduled sync). Raises ``ValidationError`` naming the token's real budgets when
    ``budget_id`` isn't among them."""
    budgets = list_budgets(store)
    for b in budgets:
        if b["id"] == budget_id:
            return b
    available = ", ".join(f"'{b['name']}' ({b['id']})" for b in budgets) or "none"
    raise ValidationError(
        f"budget '{budget_id}' was not found for this token. Available: {available}"
    )


def _resolve_single_budget(store: YnabConfigStore) -> str:
    """Auto-detect + persist the budget when the token sees EXACTLY one; otherwise raise
    ``YnabBudgetSelectionRequired`` naming every real candidate. Never guesses among several."""
    budgets = list_budgets(store)
    if not budgets:
        raise YnabBudgetSelectionRequired(
            "This YNAB token can't see any budgets. Check the token in Connectors -> YNAB."
        )
    if len(budgets) > 1:
        names = ", ".join(f"'{b['name']}' ({b['id']})" for b in budgets)
        raise YnabBudgetSelectionRequired(
            f"This YNAB token can see {len(budgets)} budgets: {names}. Pick one in "
            "Connectors -> YNAB before syncing."
        )
    chosen = budgets[0]
    store.update(
        {
            "budget_id": chosen["id"],
            "budget_name": chosen["name"],
            "budget_currency": chosen.get("currency"),
        }
    )
    return chosen["id"]


def fetch_transactions(store: YnabConfigStore, budget_id: str, *, since_date: str) -> list[dict]:
    """GET ``/plans/{budget_id}/transactions?since_date=...`` — an explicit, repeatable
    window (RFC ``connectors-as-data-sources`` §B), never a persisted delta cursor. YNAB's
    ``since_date`` has no matching upper bound param; a caller's ``before`` is applied
    client-side by ``fetch_window``. Returns the transaction list. Raises
    ``ValidationError``/``ExternalServiceError`` on an auth/transport failure OR a malformed
    response body — same shape-guard treatment as ``list_budgets`` (a non-JSON body or a root
    shape other than the documented envelope raises a typed ``ExternalServiceError`` rather
    than a raw ``JSONDecodeError``/``AttributeError``)."""
    resp = _safe_get(store, f"/plans/{budget_id}/transactions", {"since_date": since_date})
    try:
        data = resp.json()
    except ValueError as exc:
        raise ExternalServiceError(
            f"YNAB returned a malformed response ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise ExternalServiceError(
            "YNAB returned an unexpected response shape for /plans/.../transactions"
        )
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = {}
    txns = payload.get("transactions")
    return txns if isinstance(txns, list) else []


# ── mapping helpers ──────────────────────────────────────────────────────────────────


def _account_key(ynab_txn: dict) -> str | None:
    account_id = ynab_txn.get("account_id")
    return str(account_id) if account_id else None


def _account_name(account_key: str, ynab_txn: dict) -> str:
    """Falls back to ``"YNAB account (<account_key>)"`` (never the bare literal "YNAB
    account") when YNAB sends no name — the account_key is folded into the fallback so two
    accounts degrading in the same sync never collide on the exact same literal name, which
    would otherwise make BOTH permanently unaddressable by name (``resolve_account_ref``
    raises ``ValidationError`` on an ambiguous match once two live accounts share one — the
    exact bug already found and fixed for Lunch Money, ``ce99616f``). This fallback is also
    PERMANENT for a given account: once linked, ``_resolve_account``'s ``existing_id``
    short-circuits on every later sync, so a name that starts generic is never retroactively
    renamed — same known, accepted limitation as Lunch Money's own fix."""
    name = (ynab_txn.get("account_name") or "").strip()
    return name or f"YNAB account ({account_key})"


def _account_to_common(account_key: str, name: str, currency: str) -> dict:
    """A YNAB account → the common ``accounts[]`` shape (RFC §A)."""
    return {
        "provider_account_id": account_key,
        "name": name,
        "mask": None,
        "currency": currency,
        "balance": None,
    }


def _txn_id(ynab_txn: dict) -> str:
    return str(ynab_txn.get("id") or "").strip()


def _txn_to_row(ynab_txn: dict, account_key: str, currency: str) -> dict | None:
    """One YNAB transaction → the common normalized row shape (RFC §A step 3).

    YNAB's ``amount`` is a signed int64 in MILLIUNITS (1000 = one currency unit; e.g.
    ``-294230`` == ``-$294.23`` — YNAB's own worked example, see the module docstring).
    Negative = outflow, positive = inflow — already this app's own convention, no sign
    transform needed. ``external_ref`` is the transaction's own ``id`` (required, stable,
    unique within the budget — not the separate, optional ``import_id``, which only exists
    for transactions YNAB itself imported). Returns None for a deleted row or a
    zero/unparseable amount."""
    if ynab_txn.get("deleted"):
        return None
    try:
        milliunits = int(ynab_txn.get("amount"))
    except (TypeError, ValueError):
        return None
    if milliunits == 0:
        return None
    amount = milliunits / 1000

    raw_date = str(ynab_txn.get("date") or "").strip()
    try:
        date_str = datetime.fromisoformat(raw_date).date().isoformat()
    except ValueError:
        date_str = datetime.now(timezone.utc).date().isoformat()

    description = (
        ynab_txn.get("payee_name") or ynab_txn.get("import_payee_name") or ""
    ).strip() or "YNAB"
    txn_id = _txn_id(ynab_txn)
    ref = txn_id or derived_ref(account_key, date_str, amount, currency, description)
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": ynab_txn,
        "provider_account_id": account_key,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": (ynab_txn.get("memo") or None),
        "pending": False,
        "supersedes_ref": None,
    }


def fetch_window(
    store: YnabConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """YNAB's provider-side windowed fetch (RFC §B) — ``since_date`` is a server-side param;
    ``before`` has no matching upper-bound param, so it is applied client-side. ``account``
    filters client-side (the transactions endpoint has no per-account request filter).
    Raises ``YnabBudgetSelectionRequired`` (via ``_resolve_single_budget``) when the token
    sees zero or several budgets with none chosen — never guesses. Always ``complete=True``:
    YNAB's transactions endpoint is not paginated."""
    cfg = store.get()
    budget_id = cfg.get("budget_id") or _resolve_single_budget(store)
    from backend.services import ynab_sync

    if ynab_sync.plan_in_sync(budget_id):
        raise ValidationError(
            "The two-way YNAB sync is on for this plan and already brings its transactions in "
            "(and sends yours back); fetching them again would book each one twice."
        )
    currency = (store.get().get("budget_currency") or ledger_service.base_currency()).upper()

    if accounts_only:
        # since_date=before (a single-day window) minimizes the transaction rows an
        # accounts-only caller pays for.
        txns = fetch_transactions(store, budget_id, since_date=before)
    else:
        txns = fetch_transactions(store, budget_id, since_date=since)

    account_names: dict[str, str] = {}
    rows: list[dict] = []
    for ynab_txn in txns:
        account_key = _account_key(ynab_txn)
        if not account_key:
            continue
        account_names.setdefault(account_key, _account_name(account_key, ynab_txn))
        if account and account_key != account:
            continue
        if accounts_only:
            continue
        row = _txn_to_row(ynab_txn, account_key, currency)
        if row is not None and row["date"] <= before:
            rows.append(row)

    accounts = [
        _account_to_common(key, name, currency)
        for key, name in account_names.items()
        if not account or key == account
    ]
    return FetchWindow(accounts=accounts, rows=rows, complete=True)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for YNAB."""
    if isinstance(exc, YnabBudgetSelectionRequired):
        return "needs_budget_selection"
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
