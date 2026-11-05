"""OAuth orchestration + persistence for one external MCP server (RFC ``2026-08-14-mcp-oauth``
§4.1/§4.2/§4.4/§4.5/§4.7).

The ONLY module that writes an ``McpOAuth`` field — everything downstream of ``mcp/oauth.py``'s
pure protocol functions (staging a flow, exchanging a code, persisting the result, deciding
whether a registration must be discarded) lives here. Reads + writes ``McpClientStore``; stages
+ completes flows via ``mcp/oauth_pending.py``; derives the redirect URI from a caller-supplied
base URL (never a FastAPI ``Request`` — that stays a router concern, ``api/mcp_oauth_api.py``
calls ``api/shortcuts.detect_base_url`` and passes the resulting string in).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

from backend.core.config import get_settings
from backend.core.untrusted import sanitize_untrusted
from backend.db.mcp_client_store import McpClientStore
from backend.mcp import oauth, oauth_pending
from backend.models.mcp_client import (
    McpOAuth,
    McpServerConfig,
    issuer_host,
    slug_server_id,
    unique_server_id,
)

logger = logging.getLogger(__name__)

_CALLBACK_PATH = "/api/mcp-oauth/callback"

# Proactive refresh skew (RFC §4.7) — refresh when a KNOWN expiry is within this many seconds.
REFRESH_SKEW_SECONDS = 60

# A token whose lifetime we were never told (`expires_at is None`) is NOT refreshed on every
# single dispatch — some vendors never advertise `expires_in` at all, and a genuinely long-lived
# token would otherwise be refreshed before every tool call, which is needless chatter against
# the AS and, for a rotating-refresh-token vendor, needlessly invalidates the previous token on
# every call. Recheck it at most this often; the REACTIVE 401->refresh->retry path
# (`mcp/client.py::_rpc`) is the real safety net for the gap this leaves.
UNKNOWN_EXPIRY_RECHECK_SECONDS = 3600


class _NoRegistrationPossible(Exception):
    """Internal signal: no way to obtain a client identity for this authorization server."""


def _fail(reason: str, message: str, **extra: object) -> dict:
    return {"ok": False, "reason": reason, "error": message, **extra}


def _is_loopback_host(host: str) -> bool:
    lowered = (host or "").lower().rstrip(".")
    if lowered in {"127.0.0.1", "::1", "localhost"} or lowered.endswith(".localhost"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _redirect_uris(request_base_url: str) -> Optional[list[str]]:
    """The redirect URI(s) for this flow (RFC §4.3's remote/VPS answer): the browser-derived
    base URL FIRST (what ``detect_base_url`` resolved — used both to register at DCR time and
    as the ``redirect_uri`` actually sent), plus the loopback fallback registered ALONGSIDE it
    so a later local-console session works without re-registering. ``None`` when the derived
    base is neither https nor loopback — an OAuth redirect over plaintext to a non-loopback host
    is a token-interception risk."""
    base = (request_base_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    host = parts.hostname or ""
    if not host:
        return None
    if parts.scheme.lower() != "https" and not _is_loopback_host(host):
        return None
    primary = f"{base}{_CALLBACK_PATH}"
    loopback = f"http://127.0.0.1:{get_settings().port}{_CALLBACK_PATH}"
    return [primary] if primary == loopback else [primary, loopback]


def _resolve_client_identity(
    store: McpClientStore,
    as_meta: oauth.AuthServerMetadata,
    manual_client_id: Optional[str],
    mcp_url: str,
    redirect_uris: list[str],
    *,
    as_url: str,
) -> oauth.ClientRegistration:
    """RFC §4.2 step 4's priority order: a stored registration for this ISSUER (reuse) → a
    manually supplied ``client_id`` → CIMD (not implemented — see the branch below) → DCR →
    honest failure (``_NoRegistrationPossible``).

    ``as_meta.issuer`` is already required to equal ``as_url`` by
    ``oauth.fetch_authorization_server_metadata`` (HIGH finding #4a — RFC 8414 §3.3 issuer
    validation, the root fix for a forged issuer). The ``as_url``-host comparison below is
    DEFENSE IN DEPTH on top of that (HIGH finding #4b): reuse of a stored registration must
    independently re-verify the issuer is anchored to the address AS metadata was actually
    fetched from, so this function does not have to simply trust that the caller already ran
    (a) correctly — it stays safe even if some future bypass of that check is found."""
    same_origin = (urlsplit(as_meta.issuer).hostname or "").lower() == (
        urlsplit(as_url).hostname or ""
    ).lower()
    if same_origin:
        for existing in store.all():
            ob = existing.oauth
            if ob is not None and ob.issuer == as_meta.issuer and ob.client_id:
                return oauth.ClientRegistration(
                    client_id=ob.client_id,
                    client_secret=ob.client_secret,
                    token_endpoint_auth_method=ob.token_endpoint_auth_method,
                )
    if manual_client_id:
        return oauth.ClientRegistration(
            client_id=manual_client_id, client_secret="", token_endpoint_auth_method="none"
        )
    if as_meta.client_id_metadata_document_supported:
        # CIMD (draft-ietf-oauth-client-id-metadata-document-00) is the spec-preferred
        # successor to DCR, but not one of the catalog's vendors advertises support for it
        # today (RFC §2.4) — this branch is the resolver's ONE place to add a CIMD fetcher
        # later (RFC §4.5); deliberately not built here (explicit non-goal).
        pass
    if as_meta.registration_endpoint:
        return oauth.register_client(as_meta.registration_endpoint, mcp_url, redirect_uris)
    raise _NoRegistrationPossible()


def begin(
    *,
    url: Optional[str] = None,
    server_id: Optional[str] = None,
    client_id: Optional[str] = None,
    request_base_url: str,
) -> dict:
    """The RFC §4.2 "Begin" sequence. Returns ``{ok: True, authorize_url, flow_id, expires_in}``
    on success, or ``{ok: False, reason, error, ...}`` on ANY failure (§4.6's degradation
    table) — never raises, so the route can answer 200 either way (matching
    ``probe_mcp_client``'s "the request succeeded, the server is what didn't answer"
    convention)."""
    store = McpClientStore()
    server: Optional[McpServerConfig] = None
    if server_id:
        server = store.find(server_id)
        if server is None:
            return _fail("unknown_server", f"unknown mcp server: {server_id}")
        if server.transport != "http":
            return _fail(
                "unsupported_transport", "OAuth sign-in is only available for remote MCP servers."
            )
        mcp_url = server.url
    else:
        mcp_url = (url or "").strip()
        if not mcp_url:
            return _fail("bad_request", "a url or server_id is required")

    redirect_uris = _redirect_uris(request_base_url)
    if redirect_uris is None:
        return _fail(
            "insecure_redirect",
            "Sign-in needs a secure address. Reach this instance over HTTPS, or from the "
            "machine it runs on.",
        )

    try:
        challenge = oauth.discover_www_authenticate(mcp_url)
    except oauth.NotOAuthProtectedError:
        return _fail(
            "not_oauth",
            "This server didn't ask for a sign-in. Try None — it may not need credentials.",
        )
    except oauth.OAuthSecurityError as exc:
        return _fail("blocked_url", exc.detail)

    try:
        prm = oauth.fetch_protected_resource_metadata(mcp_url, challenge)
    except oauth.OAuthSecurityError as exc:
        return _fail("blocked_url", exc.detail)
    except oauth.OAuthDiscoveryError:
        return _fail(
            "no_metadata",
            "This server doesn't publish OAuth sign-in details, so we can't sign you in "
            "automatically. Use an API key if the vendor issues one.",
        )

    as_url = prm.authorization_servers[0]
    try:
        as_meta = oauth.fetch_authorization_server_metadata(as_url, mcp_url)
    except oauth.OAuthSecurityError as exc:
        return _fail("blocked_url", exc.detail)
    except oauth.OAuthDiscoveryError:
        # `as_url` is the MCP server's own (vendor-controlled) claim of where its
        # authorization server lives — MEDIUM finding #6: sanitize + cap before it ever
        # reaches this message, same discipline as `DcrRejectedError`/`TokenExchangeError`'s
        # `detail`, which are already sanitized at the point they're raised.
        safe_host = sanitize_untrusted(issuer_host(as_url), max_len=oauth.MAX_ERROR_DETAIL_CHARS)
        return _fail(
            "as_metadata_unreachable",
            f"Found the sign-in service at {safe_host} but couldn't read its configuration.",
        )

    try:
        client = _resolve_client_identity(
            store, as_meta, client_id, mcp_url, redirect_uris, as_url=as_url
        )
    except _NoRegistrationPossible:
        return _fail(
            "no_registration",
            "This server needs an app registration we can't create automatically.",
            needs_client_id=True,
        )
    except oauth.DcrRejectedError as exc:
        return _fail("dcr_rejected", exc.detail, redirect_uris=list(exc.redirect_uris))
    except oauth.OAuthSecurityError as exc:
        return _fail("blocked_url", exc.detail)

    pkce = oauth.generate_pkce()
    scope = oauth.select_scope(challenge.scope, prm.scopes_supported)
    state = oauth_pending.stage(
        server_id=server.id if server is not None else None,
        url=None if server is not None else mcp_url,
        code_verifier=pkce.verifier,
        redirect_uri=redirect_uris[0],
        expected_issuer=as_meta.issuer,
        resource=prm.resource,
        scope=scope,
        client=client,
        as_meta=as_meta,
    )
    authorize_url = oauth.build_authorize_url(
        as_meta,
        client,
        redirect_uri=redirect_uris[0],
        state=state,
        pkce=pkce,
        resource=prm.resource,
        scope=scope,
    )
    return {
        "ok": True,
        "authorize_url": authorize_url,
        "flow_id": state,
        "expires_in": oauth_pending.TTL_SECONDS,
    }


def _host_derived_id(mcp_url: str, taken: list[str]) -> tuple[str, str]:
    """A provisional (name, id) for a brand-new server minted straight off its URL — there is
    no ``serverInfo`` yet at this point (only the unauthenticated 401 probe has run, not a real
    handshake), mirroring ``client_manager._endpoint_derived_name``'s http branch."""
    host = urlsplit(mcp_url).hostname or "server"
    name = host[4:] if host.startswith("www.") else host
    return name, unique_server_id(slug_server_id(name), taken)


def complete(
    *, state: str, code: Optional[str], iss: Optional[str] = None, error: Optional[str] = None
) -> dict:
    """The RFC §4.2 "Callback" / §4.3 logic. Pops the staged flow by ``state`` (single-use).
    Returns ``{"ok": bool}`` — the callback route renders the SAME generic page regardless of
    which branch fired (RFC §4.3: no oracle), so the reason is recorded to the STATUS map for
    the poll, never echoed in this return value's shape."""
    flow = oauth_pending.pop(state)
    if flow is None:
        return {"ok": False}
    try:
        return _finish_popped_flow(flow, state=state, code=code, iss=iss, error=error)
    except Exception:
        # The poll would otherwise read `pending` until the status expired.
        oauth_pending.record_status(state, "error", "The sign-in could not be completed.")
        raise


def _finish_popped_flow(
    flow: oauth_pending.PendingFlow,
    *,
    state: str,
    code: Optional[str],
    iss: Optional[str],
    error: Optional[str],
) -> dict:
    if error:
        oauth_pending.record_status(state, "error", "The vendor declined or cancelled the sign-in.")
        return {"ok": False}
    if not code:
        oauth_pending.record_status(
            state, "error", "The sign-in did not return an authorization code."
        )
        return {"ok": False}
    # RFC 9207 `iss` check — exact string comparison, and on mismatch we do NOT surface
    # whatever `error_description` the (possibly hostile) party attached. Named security
    # requirement, not a UX nicety (RFC §4.3).
    if iss and iss != flow.expected_issuer:
        oauth_pending.record_status(state, "error", "Sign-in could not be verified.")
        return {"ok": False}

    store = McpClientStore()
    mcp_url = flow.url
    if flow.server_id:
        existing = store.find(flow.server_id)
        if existing is None:
            oauth_pending.record_status(state, "error", "The server was removed while signing in.")
            return {"ok": False}
        mcp_url = existing.url

    client = oauth.ClientRegistration(
        client_id=flow.client_id,
        client_secret=flow.client_secret,
        token_endpoint_auth_method=flow.token_endpoint_auth_method,
    )
    as_meta = oauth.AuthServerMetadata(
        issuer=flow.expected_issuer,
        authorization_endpoint=flow.authorization_endpoint,
        token_endpoint=flow.token_endpoint,
        revocation_endpoint=flow.revocation_endpoint,
    )
    try:
        token = oauth.exchange_code_for_token(
            as_meta,
            client,
            mcp_url,
            code=code,
            code_verifier=flow.code_verifier,
            redirect_uri=flow.redirect_uri,
            resource=flow.resource,
        )
    except oauth.OAuthDiscoveryError:
        oauth_pending.record_status(state, "error", "The sign-in could not be completed.")
        return {"ok": False}

    now = time.time()
    oauth_block = McpOAuth(
        issuer=flow.expected_issuer,
        resource=flow.resource,
        authorization_endpoint=flow.authorization_endpoint,
        token_endpoint=flow.token_endpoint,
        revocation_endpoint=flow.revocation_endpoint,
        client_id=flow.client_id,
        client_secret=flow.client_secret,
        token_endpoint_auth_method=flow.token_endpoint_auth_method,
        scope=token.scope or flow.scope,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=(now + token.expires_in) if token.expires_in is not None else None,
        obtained_at=now,
    )

    if flow.server_id:
        server_id = flow.server_id
    else:
        # A brand-new server, added via `url` — persist it here (dormant, disabled — the same
        # "a server starts dormant until the user enables it" rule `create_mcp_client` follows,
        # so we do NOT run a `tools/list` discovery on the user's behalf yet). This is the
        # module's own judgment call on a case the RFC names but does not fully spell out — see
        # the agent's final report for the reasoning and the alternative considered.
        name, server_id = _host_derived_id(mcp_url, [s.id for s in store.all()])
        store.create(
            McpServerConfig(
                id=server_id,
                name=name,
                transport="http",
                url=mcp_url,
                auth_type="oauth",
                enabled=False,
            )
        )

    store.set_oauth(server_id, oauth_block)
    store.record_oauth_status(server_id, "ok", None)
    oauth_pending.record_status(state, "ok", None)
    return {"ok": True, "server_id": server_id}


def _is_stale(ob: McpOAuth, *, now: float) -> bool:
    if ob.expires_at is not None:
        return ob.expires_at <= now + REFRESH_SKEW_SECONDS
    last_known = ob.obtained_at or 0.0
    return (now - last_known) >= UNKNOWN_EXPIRY_RECHECK_SECONDS


def dead_sign_in_reason() -> str:
    return "Connection has expired. You can reconnect to re-authenticate."


# Per-server (NOT per-table) in-flight-refresh coalescing lock — LOW finding #8. Guards only
# the read-check-network-call phase of `ensure_fresh_token` for ONE server id, so two truly
# concurrent callers refreshing the SAME server still make exactly one network call (same
# guarantee the old single-lock design gave), while callers for DIFFERENT servers — and any
# unrelated connector write via `locked_unit_of_work` — no longer contend on that same
# process-wide table lock for the whole round trip.
_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _refresh_lock(server_id: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(server_id)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[server_id] = lock
        return lock


def ensure_fresh_token(
    server: McpServerConfig, *, force: bool = False, stale_access_token: Optional[str] = None
) -> McpServerConfig:
    """Proactive (``force=False``) or reactive (``force=True``, a live 401) refresh (RFC §4.7).

    The network round trip happens OUTSIDE ``db.engine``'s process-wide connector table
    locks (LOW finding #8): a refresh can run several seconds across up to
    ``_MAX_PRIVATE_REDIRECTS``/``pinned_http.MAX_REDIRECTS`` hops at ``_TIMEOUT_SECONDS`` each,
    and the OLD design held that global lock for the whole window — blocking every OTHER
    server's dispatch-time refresh and every REST create/update/delete of ANY MCP server for as
    long as this one refresh took. Two phases instead:

      1. An UNLOCKED read decides whether a refresh is even still needed, then a PER-SERVER
         lock (``_refresh_lock`` — this server id only) serializes the network call itself.
         Two truly concurrent callers for the SAME server still make exactly ONE network call
         (the loser blocks on this lock, then re-reads and finds the winner's token already
         fresh) — this preserves the pre-existing "exactly one network call" guarantee without
         needing the global table lock to get it.
      2. ``store.transaction(server_id)`` (the connector table locks, the database writer lock
         and the server's row lock, all taken before the re-read) is held ONLY for the brief
         compare-and-swap write at the end — re-reading right before writing and refusing to
         overwrite if the row's ``access_token`` no longer matches what we refreshed FROM (some
         OTHER writer, e.g. a REST ``PATCH``/``DELETE``, changed it while the network call was
         in flight). This is the SAME re-check the old design already did for the "losing"
         concurrent caller; this restructuring makes it apply to every path, not just the race
         case, exactly as intended.

    ``force=True`` skips the ``expires_at``-based staleness gate (the caller already knows the
    token just failed) but still honors the re-read: if ``stale_access_token`` no longer
    matches what's stored, someone already fixed it.
    """
    if server.auth_type != "oauth" or server.oauth is None:
        return server
    if not force and not _is_stale(server.oauth, now=time.time()):
        return server

    store = McpClientStore()
    with _refresh_lock(server.id):
        current = store.find(server.id)
        if current is None or current.auth_type != "oauth" or current.oauth is None:
            return server
        ob = current.oauth
        if stale_access_token is not None and ob.access_token != stale_access_token:
            return current  # someone else already refreshed it
        if not force and not _is_stale(ob, now=time.time()):
            return current  # someone else already refreshed it
        if not ob.refresh_token:
            return current  # nothing to refresh with — leave it; a 401 needs a human reconnect

        as_meta = oauth.AuthServerMetadata(
            issuer=ob.issuer,
            authorization_endpoint=ob.authorization_endpoint,
            token_endpoint=ob.token_endpoint,
            revocation_endpoint=ob.revocation_endpoint,
        )
        client = oauth.ClientRegistration(
            client_id=ob.client_id,
            client_secret=ob.client_secret,
            token_endpoint_auth_method=ob.token_endpoint_auth_method,
        )

        token: Optional[oauth.TokenResponse] = None
        invalid_client = False
        try:
            token = oauth.refresh_token(
                as_meta, client, current.url, refresh_token=ob.refresh_token
            )
        except oauth.OAuthUnreachableError as exc:
            # The network failed, not the sign-in. Recording auth_error here told the user a
            # DNS blip was an expired sign-in and sent them to link the account again.
            logger.info("mcp '%s' token refresh could not run: %s", server.id, exc.detail)
            return current
        except oauth.TokenExchangeError as exc:
            invalid_client = exc.code == "invalid_client"
            # The one record of WHY a sign-in died: nothing else keeps the server's answer.
            logger.warning(
                "mcp '%s' token refresh refused: %s (%s)",
                server.id,
                exc.code or "no error code",
                exc.detail,
            )
        except oauth.OAuthDiscoveryError as exc:
            logger.warning("mcp '%s' token refresh failed: %s", server.id, exc.detail)

        now = time.time()
        with store.transaction(server.id) as conn:
            latest = store.find(server.id, conn=conn)
            if latest is None or latest.auth_type != "oauth" or latest.oauth is None:
                return current
            if latest.oauth.access_token != ob.access_token:
                return latest  # some OTHER writer changed this row while we refreshed

            if token is not None:
                updated = latest.oauth.model_copy(
                    update={
                        "access_token": token.access_token,
                        "refresh_token": token.refresh_token or latest.oauth.refresh_token,
                        "expires_at": (now + token.expires_in)
                        if token.expires_in is not None
                        else None,
                        "obtained_at": now,
                        "scope": token.scope or latest.oauth.scope,
                    }
                )
                result = store.set_oauth(server.id, updated, conn=conn)
                store.record_oauth_status(server.id, "ok", None, conn=conn)
                return result if result is not None else current

            if invalid_client:
                store.clear_oauth(server.id, conn=conn)
                store.record_oauth_status(
                    server.id,
                    "auth_error",
                    f"{current.name or current.id}'s app registration was revoked — reconnect it in Connectors.",
                    conn=conn,
                )
                return store.find(server.id, conn=conn) or current

            store.record_oauth_status(server.id, "auth_error", dead_sign_in_reason(), conn=conn)
            return store.find(server.id, conn=conn) or current


def revoke_on_delete(server: McpServerConfig) -> None:
    """Best-effort bounded revoke, called BEFORE the row delete (RFC §4.4). Failure NEVER
    blocks the delete — the caller (``api/mcp_client_api.delete_mcp_client``) proceeds either
    way."""
    if server.auth_type != "oauth" or server.oauth is None:
        return
    ob = server.oauth
    if not ob.revocation_endpoint or not ob.refresh_token:
        return
    try:
        oauth.revoke_token(
            ob.revocation_endpoint,
            oauth.ClientRegistration(
                client_id=ob.client_id,
                client_secret=ob.client_secret,
                token_endpoint_auth_method=ob.token_endpoint_auth_method,
            ),
            server.url,
            token=ob.refresh_token,
        )
    except Exception:  # noqa: BLE001 — a revoke that fails must never block the delete
        logger.warning(
            "oauth revoke failed for mcp server '%s' (non-fatal)", server.id, exc_info=True
        )
