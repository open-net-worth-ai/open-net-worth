"""When the inbound MCP OAuth server exists, and the one address every URL comes from.

``availability()`` is the ONE predicate every OAuth route and the ``/mcp`` gate ask. Every URL the
server publishes — issuer, endpoints, the protected resource, the consent page — is built from
``public_url()``, never from the request's ``Host`` or ``X-Forwarded-*`` headers.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.core.config import gateway_active, get_settings
from backend.core.errors import ValidationError
from backend.db.app_config_store import AppConfigStore
from backend.db.auth_store import AuthStore

FLAG = "mcp_oauth"
MCP_PATH = "/mcp"


def normalize_public_url(value: str) -> str:
    """Validate an owner-supplied public address and return it as a bare origin.

    Must be ``https://host[:port]`` — no path, query, fragment or userinfo — and not a loopback,
    private or link-local literal (claude.ai and ChatGPT call it from the internet). Origin-only
    keeps the RFC 8414 well-known paths at the root, where a reverse proxy already forwards them.
    Raises ``ValidationError`` with a plain reason."""
    raw = (value or "").strip()
    if raw.endswith("/"):
        raw = raw[:-1]
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValidationError("The public address isn't a valid URL.") from exc
    if parts.scheme != "https":
        raise ValidationError("The public address must start with https://.")
    if parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValidationError(
            "The public address must be just the origin, like https://wealth.example.com."
        )
    host = (parts.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        raise ValidationError("The public address must be reachable from the internet.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise ValidationError("The public address must be reachable from the internet.")
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def _configured_public_url() -> tuple[str, str]:
    """``(value, source)`` — the env wins over the stored setting."""
    env = get_settings().public_url
    if env:
        return env, "env"
    return AppConfigStore().get_mcp_public_url(), "stored"


def public_url() -> str:
    """The validated public origin, or ``""`` when unset or invalid."""
    value, _ = _configured_public_url()
    if not value:
        return ""
    try:
        return normalize_public_url(value)
    except ValidationError:
        return ""


def resource_url() -> str:
    return public_url() + MCP_PATH


@dataclass(frozen=True)
class Availability:
    available: bool
    reasons: tuple[str, ...]
    public_url: str
    public_url_source: str


def flag_on() -> bool:
    """THE one read of the ``mcp_oauth`` flag (docs/FEATURE_FLAGS.md: one seam)."""
    from backend.services.feature_flags import enabled

    return enabled(FLAG)


def availability() -> Availability:
    """Whether the OAuth server is on, and if not, every reason why (Settings shows them)."""
    settings = get_settings()
    reasons: list[str] = []
    if not flag_on():
        reasons.append("flag_off")
    raw, source = _configured_public_url()
    url = public_url()
    if not raw:
        reasons.append("no_public_url")
    elif not url:
        reasons.append("invalid_public_url")
    store = AuthStore()
    if not store.is_password_set():
        reasons.append("no_password")
    if not settings.auth_enabled or store.auth_disabled():
        reasons.append("auth_disabled")
    if store.trust_loopback():
        reasons.append("loopback_trusted")
    if settings.demo_mode:
        reasons.append("demo")
    if gateway_active():
        reasons.append("gateway")
    if settings.desktop_mode:
        reasons.append("desktop")
    return Availability(not reasons, tuple(reasons), url, source)


def is_available() -> bool:
    return availability().available
