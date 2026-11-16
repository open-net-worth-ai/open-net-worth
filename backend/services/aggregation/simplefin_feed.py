"""What a SimpleFIN ``/accounts`` feed says besides its transactions: its accounts (named for
their bank from protocol v2's ``connections``), their currencies, and the errors the Bridge
reports inside a 200 (ADR 2026-11-13, Amendment 18). Pure: no network, no credential —
``simplefin`` hands in a scrub for the server's text."""

from __future__ import annotations

import re
from typing import Callable, Optional

from backend.core.errors import ExternalServiceError
from backend.services import ledger as ledger_service


def account_to_common(sf_account: dict, connections: dict[str, str] | None = None) -> dict:
    """A SimpleFIN account → the common ``accounts[]`` shape (RFC §A). The bank's name comes
    from protocol v2's ``connections`` (by ``conn_id``), else v1's deprecated ``org``."""
    org = sf_account.get("org") or {}
    org_name = (connections or {}).get(str(sf_account.get("conn_id") or "")) or (
        org.get("name") or org.get("domain") or ""
    )
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
        "currency": currency_of(sf_account),
        "balance": balance,
        "institution": org_name or None,
    }


def currency_of(sf_account: dict) -> Optional[str]:
    """The account's ISO currency, or None. SimpleFIN allows a custom currency given as a URL
    (points, miles): never booked as the base currency."""
    code = str(sf_account.get("currency") or "").strip().upper()
    return code if _ISO_CURRENCY.fullmatch(code) else None


_ISO_CURRENCY = re.compile(r"[A-Z]{3}")


def row_currency(sf_account: dict) -> Optional[str]:
    """The currency to book the account's rows in: its ISO code, the base currency when it
    names none (as before), None for a custom one."""
    if not str(sf_account.get("currency") or "").strip():
        return ledger_service.base_currency().upper()
    return currency_of(sf_account)


# ── the Bridge's errors ──────────────────────────────────────────────────────────────
# A failed connection comes back INSIDE a 200: v2 ``errlist`` ({code, msg, conn_id,
# account_id}) and v1 ``errors`` (strings, no scope). Ignoring them made a feed missing a bank
# look complete (Actual #7333). gen.* fails the read; con.auth is one connection's sign-in;
# act.* one account's data. Rate-limit text fails it too: past the warnings the Bridge turns
# the access URL off.
_RATE_LIMITED = re.compile(
    r"too many requests|make fewer requests|once every 24 hours|rate limit", re.I
)


def feed_errors(feed: dict, scrub: Callable[[str], str]) -> list[dict]:
    """Every error the feed carries, ``{code, msg, conn_id, account_id}``, its text scrubbed,
    flattened and capped (server text)."""
    found: list[dict] = []
    for item in feed.get("errlist") or []:
        if isinstance(item, dict):
            found.append(
                {
                    "code": str(item.get("code") or ""),
                    "msg": str(item.get("msg") or item.get("code") or ""),
                    "conn_id": str(item.get("conn_id") or ""),
                    "account_id": str(item.get("account_id") or ""),
                }
            )
        else:
            found.append({"code": "", "msg": str(item), "conn_id": "", "account_id": ""})
    v2 = {e["msg"] for e in found}
    for item in feed.get("errors") or []:
        if str(item) not in v2:  # v2 repeats its messages in ``errors`` for v1 readers
            found.append({"code": "", "msg": str(item), "conn_id": "", "account_id": ""})
    out: list[dict] = []
    for e in found:
        msg = " ".join(scrub(e["msg"]).split())[:300]
        if msg and all(o["msg"] != msg for o in out):
            out.append({**e, "msg": msg})
    return out


def raise_fatal(errors: list[dict]) -> None:
    """A read the Bridge refused as a whole (``gen.*``, a rate-limit warning) raises."""
    for e in errors:
        if _RATE_LIMITED.search(e["msg"]):
            raise ExternalServiceError(f"SimpleFIN: {e['msg']}", rate_limited=True)
        if e["code"] == "gen.auth":
            raise ExternalServiceError(f"SimpleFIN: {e['msg']}", access_refused=True)
        if e["code"].startswith("gen."):
            raise ExternalServiceError(f"SimpleFIN: {e['msg']}")


def concerns(e: dict, account: Optional[str], conn_id: str) -> bool:
    """Whether error ``e`` is about ``account`` (None = every account): one with no scope
    (v1) is about all of them."""
    if account is None or not (e["account_id"] or e["conn_id"]):
        return True
    return e["account_id"] == account or (bool(conn_id) and e["conn_id"] == conn_id)


def connections_of(feed: dict) -> dict[str, str]:
    return {
        str(c.get("conn_id") or ""): str(c.get("name") or "")
        for c in feed.get("connections") or []
        if isinstance(c, dict) and c.get("conn_id")
    }


def accounts_of(feed: dict) -> list[dict]:
    connections = connections_of(feed)
    return [account_to_common(a, connections) for a in feed.get("accounts") or []]
