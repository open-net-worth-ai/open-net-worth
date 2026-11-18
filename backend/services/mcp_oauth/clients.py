"""Who is asking: client resolution for the inbound MCP OAuth server.

Two kinds of client, nothing else (no Dynamic Client Registration):

* **CIMD** (Client ID Metadata Documents) — only when the ``client_id`` URL's host is in
  ``KNOWN_CIMD_HOSTS``. The server never fetches a URL an anonymous caller chose, and the consent
  page names the app by OUR label, never by what the document claims.
* **Manual** — credentials the owner created in Settings > MCP (a name, redirect URIs, a secret
  shown once).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from backend.core.errors import NotFoundError, ValidationError
from backend.db.mcp_oauth_store import McpOAuthStore
from backend.services.mcp_oauth.common import (
    CLIENT_PREFIX,
    SECRET_PREFIX,
    digest,
    new_secret,
    now,
    same,
    stamp,
)

logger = logging.getLogger(__name__)

# The apps whose published identity (CIMD) we accept, and the name the consent page shows.
KNOWN_CIMD_HOSTS: dict[str, str] = {"claude.ai": "Claude", "chatgpt.com": "ChatGPT"}

CIMD_MAX_BYTES = 16_384
CIMD_TIMEOUT_S = 5
CIMD_CACHE_S = 600
MAX_REDIRECT_URIS = 10
MAX_NAME_CHARS = 80

_cimd_cache: dict[str, tuple[float, dict]] = {}


def user_agent() -> str:
    """Who is fetching. An app's identity host may refuse a default ``Python-urllib`` agent:
    claude.ai's edge answers it 403 (live test, 2026-11-18)."""
    from backend.core.version import app_version

    return (
        f"OpenNetWorth/{app_version()} mcp-oauth "
        "(+https://github.com/open-net-worth-ai/open-net-worth)"
    )


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    host: str
    kind: str  # "cimd" | "manual"
    redirect_uris: tuple[str, ...]


class ClientRefused(Exception):
    """The client or its redirect URI can't be trusted — answered with an error PAGE, never a
    redirect (RFC 6749 §4.1.2.1)."""


def _is_cimd_url(client_id: str) -> bool:
    return client_id.startswith("https://")


def _valid_cimd_url(client_id: str) -> Optional[str]:
    """The host of a well-formed CIMD client id on our allowlist, else ``None``."""
    try:
        parts = urlsplit(client_id)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in KNOWN_CIMD_HOSTS or port not in (None, 443):
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    segments = parts.path.split("/")
    if not parts.path or parts.path == "/" or "." in segments or ".." in segments:
        return None
    return host


def _fetch_cimd(client_id: str) -> dict:
    """GET the metadata document through the SSRF-guarded, DNS-pinned fetch; no redirects."""
    from backend.core.pinned_http import pinned_urlopen

    cached = _cimd_cache.get(client_id)
    if cached and time.monotonic() - cached[0] < CIMD_CACHE_S:
        return cached[1]
    try:
        resp = pinned_urlopen(
            client_id,
            headers={"Accept": "application/json", "User-Agent": user_agent()},
            timeout=CIMD_TIMEOUT_S,
            max_redirects=0,
        )
        with resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None)
            raw = resp.read(CIMD_MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 — any fetch failure is a refusal, never a guess
        logger.warning(
            "mcp oauth: client metadata fetch failed for %s: %s (HTTP status %s)",
            client_id,
            type(exc).__name__,
            getattr(exc, "code", None),
        )
        raise ClientRefused("The app's identity document could not be read.") from exc
    if status != 200 or len(raw) > CIMD_MAX_BYTES:
        logger.warning(
            "mcp oauth: client metadata fetch for %s answered HTTP %s (%d bytes)",
            client_id,
            status,
            len(raw),
        )
        raise ClientRefused("The app's identity document could not be read.")
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ClientRefused("The app's identity document is not valid.") from exc
    if not isinstance(doc, dict):
        raise ClientRefused("The app's identity document is not valid.")
    _cimd_cache[client_id] = (time.monotonic(), doc)
    return doc


def _cimd_client(client_id: str, host: str) -> Client:
    doc = _fetch_cimd(client_id)
    if doc.get("client_id") != client_id:
        raise ClientRefused("The app's identity document names a different app.")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris):
        raise ClientRefused("The app's identity document lists no return addresses.")
    method = doc.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise ClientRefused("This app signs in a way this server doesn't support.")
    https_uris = tuple(u for u in uris if u.startswith("https://"))
    return Client(client_id, KNOWN_CIMD_HOSTS[host], host, "cimd", https_uris)


def _manual_client(client_id: str) -> Client:
    row = McpOAuthStore().get_client(client_id)
    if row is None or row.get("revoked_at"):
        raise ClientRefused("This app isn't registered here.")
    uris = tuple(row.get("redirect_uris") or ())
    host = (urlsplit(uris[0]).hostname or "") if uris else ""
    return Client(client_id, row["name"], host, "manual", uris)


def _loopback(uri: str) -> Optional[tuple[str, str, str]]:
    parts = urlsplit(uri)
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return (parts.hostname, parts.path or "/", parts.query)
    return None


def redirect_allowed(client: Client, redirect_uri: str) -> bool:
    """Exact match; a registered loopback URI matches any port (RFC 8252 §7.3)."""
    if redirect_uri in client.redirect_uris:
        return True
    if client.kind != "manual":
        return False
    wanted = _loopback(redirect_uri)
    return wanted is not None and any(_loopback(u) == wanted for u in client.redirect_uris)


def resolve_client(client_id: str, redirect_uri: str) -> Client:
    """The client and a checked redirect URI, or ``ClientRefused``."""
    if not client_id or len(client_id) > 512:
        raise ClientRefused("This app isn't registered here.")
    if _is_cimd_url(client_id):
        host = _valid_cimd_url(client_id)
        if host is None:
            raise ClientRefused(
                "This app isn't one Open Net Worth recognises. Create app credentials for it "
                "in Settings > MCP instead."
            )
        client = _cimd_client(client_id, host)
    elif client_id.startswith(CLIENT_PREFIX):
        client = _manual_client(client_id)
    else:
        raise ClientRefused("This app isn't registered here.")
    if not redirect_uri or not redirect_allowed(client, redirect_uri):
        raise ClientRefused("The app asked to return somewhere it didn't register.")
    return client


def lookup_client(client_id: str) -> Optional[Client]:
    """The client for the token endpoint, without a redirect check (the code carries it)."""
    try:
        if _is_cimd_url(client_id):
            host = _valid_cimd_url(client_id)
            return Client(client_id, KNOWN_CIMD_HOSTS[host], host, "cimd", ()) if host else None
        if client_id.startswith(CLIENT_PREFIX):
            return _manual_client(client_id)
    except ClientRefused:
        return None
    return None


def authenticate_manual(client_id: str, secret: Optional[str]) -> bool:
    row = McpOAuthStore().get_client(client_id)
    if row is None or row.get("revoked_at") or not secret:
        return False
    return same(digest(secret), row["secret_hash"])


# ── owner-created credentials (Settings > MCP) ─────────────────────────────────────
def _check_redirect_uri(uri: str) -> str:
    uri = (uri or "").strip()
    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        raise ValidationError(f"{uri!r} isn't a valid address.") from exc
    if parts.fragment or parts.username or parts.password:
        raise ValidationError("A return address can't contain a fragment or a password.")
    if parts.scheme == "https" and parts.hostname:
        return uri
    if _loopback(uri) is not None:
        return uri
    raise ValidationError("A return address must be https://, or http://127.0.0.1 / localhost.")


def create_client(name: str, redirect_uris: list[str]) -> dict:
    """Create app credentials. The secret is returned ONCE and stored only as a hash."""
    label = " ".join((name or "").split())[:MAX_NAME_CHARS]
    if not label:
        raise ValidationError("Give the app a name.")
    uris = [_check_redirect_uri(u) for u in redirect_uris if (u or "").strip()]
    if not uris:
        raise ValidationError("Add the app's return (redirect) address.")
    if len(uris) > MAX_REDIRECT_URIS:
        raise ValidationError(f"At most {MAX_REDIRECT_URIS} return addresses.")
    client_id = new_secret(CLIENT_PREFIX)[:31]
    secret = new_secret(SECRET_PREFIX)
    created = stamp(now())
    McpOAuthStore().insert_client(
        {
            "client_id": client_id,
            "name": label,
            "secret_hash": digest(secret),
            "redirect_uris": uris,
            "created_at": created,
        }
    )
    return {
        "client_id": client_id,
        "client_secret": secret,
        "name": label,
        "redirect_uris": uris,
        "created_at": created,
    }


def list_clients() -> list[dict]:
    return [
        {k: row[k] for k in ("client_id", "name", "redirect_uris", "created_at")}
        for row in McpOAuthStore().list_clients()
    ]


def delete_client(client_id: str) -> None:
    """Revoke the credentials and every grant made with them."""
    from backend.services.mcp_oauth import grants

    if not McpOAuthStore().revoke_client(client_id, stamp(now())):
        raise NotFoundError("No such app credentials.")
    grants.revoke_for_client(client_id, "client_deleted")


def reset_cache_for_tests() -> None:
    _cimd_cache.clear()
