"""Saving the Enable Banking application's credentials — and switching to another application.

A bank's consent (session) belongs to the application that created it: another application
gets 404 for it (verified on the sandbox, 2026-11-13). So saving a DIFFERENT application ID
while banks are linked:

  1. checks the new credentials with Enable Banking first (``GET /application``) — a typo
     must never cost the user their banks, so a refusal changes nothing;
  2. records each bank's accounts under the old application, so the uids its renewal hands
     out map back to the same accounts here (``account_identities``);
  3. revokes each old consent with the OLD key — the only key Enable Banking accepts for it,
     and gone once the new one is saved;
  4. saves the new credentials and flags every bank ``app_changed`` in one write: the form
     shows Renew on each, and a fetch names them instead of returning an empty "ok".

Anything else (first setup, a new key for the same application) is a plain save.
"""

from __future__ import annotations

import logging

import httpx

from backend.core.errors import ValidationError
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services.aggregation.enable_banking import (
    _HTTP_TIMEOUT,
    API_BASE,
    _auth_headers,
    _remember_accounts,
    _revoke_session,
    _sign_jwt,
)

logger = logging.getLogger(__name__)


def save_credentials(changes: dict) -> dict:
    """Save the connector's settings (``application_id`` / ``private_key`` / ``enabled``).
    Returns ``{switched, revoked}`` — whether this moved to another application, and how
    many old consents Enable Banking confirmed revoked. Raises ``ValidationError`` (nothing
    saved) when switching to credentials Enable Banking refuses."""
    store = EnableBankingConfigStore()
    old_id = store.application_id()
    new_id = (changes.get("application_id") or "").strip() or old_id
    live = [b for b in store.banks() if not b.get("access_ended")]
    if not old_id or new_id == old_id or not live:
        store.update(changes)
        return {"switched": False, "revoked": 0}

    _check_application(new_id, (changes.get("private_key") or "").strip() or store.private_key())
    revoked = 0
    old_token = _old_token(old_id, store.private_key())
    if old_token:
        for bank in live:
            _remember_accounts(store, old_token, bank["session_id"], bank)
            revoked += _revoke_session(old_token, bank["session_id"])
    store.switch_application(changes)
    logger.info(
        "enable_banking: switched application; %d of %d consents revoked", revoked, len(live)
    )
    return {"switched": True, "revoked": revoked}


def _check_application(application_id: str, private_key: str) -> None:
    """Raise ``ValidationError`` unless Enable Banking accepts this ID + key pair."""
    if not private_key.strip():
        raise ValidationError("Add the private key for this application too.")
    try:
        token = _sign_jwt(application_id, private_key)
    except Exception:  # noqa: BLE001 — a malformed PEM, whatever the library calls it
        raise ValidationError("That private key isn't a valid PEM key.") from None
    try:
        resp = httpx.get(
            f"{API_BASE}/application", headers=_auth_headers(token), timeout=_HTTP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise ValidationError(
            f"Couldn't reach Enable Banking to check the new app ({type(exc).__name__}). {_KEEP}"
        ) from None
    if resp.status_code != 200:
        raise ValidationError(_refusal(resp))


_KEEP = "Nothing was changed: your banks keep syncing under your current app."


def _refusal(resp: httpx.Response) -> str:
    """Why Enable Banking refused the new pair, in its own words, and what that usually means
    (verified on the sandbox: 403 "Application does not exist" for an unknown ID, 401 "Wrong
    signature" for a key that isn't the application's)."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    reason = str((body if isinstance(body, dict) else {}).get("message") or "").strip()[:200]
    said = f"{reason} (HTTP {resp.status_code})" if reason else f"HTTP {resp.status_code}"
    hint = {
        401: " The private key has to be the new app's own.",
        403: " Check the application ID.",
    }.get(resp.status_code, "")
    return f"Enable Banking refused the new app: {said}.{hint} {_KEEP}"


def _old_token(application_id: str, private_key: str) -> str:
    """A token for the application being replaced, or "" when its saved key can't sign."""
    try:
        return _sign_jwt(application_id, private_key)
    except Exception as exc:  # noqa: BLE001 — revoking is best effort; the switch goes on
        logger.warning(
            "enable_banking: the old key can't sign (%s); consents not revoked", type(exc).__name__
        )
        return ""
