"""Whether a model endpoint is a process on this machine (Ollama, LM Studio, vLLM): self-hosted, so
its calls cost nothing per token. Stdlib only — the one definition ``ai.models.pricing`` and
``services.trading_agents`` share.

The host is parsed, never prefix-matched: ``http://localhost.evil.example`` and
``http://localhost@api.example/v1`` are remote endpoints that merely START like a local one.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_SCHEMES = frozenset({"http", "https"})
# Connecting to the unspecified IPv4 address reaches this machine, so it counts as local.
_THIS_HOST = ipaddress.IPv4Address("0.0.0.0")


def is_local_base_url(base_url: str) -> bool:
    """True only for ``http(s)://`` with no userinfo whose host is exactly ``localhost`` or a
    loopback IP address (``0.0.0.0`` included)."""
    try:
        parts = urlsplit((base_url or "").strip())
        host = parts.hostname
        _ = parts.port  # a malformed port is a malformed URL
    except ValueError:
        return False
    if parts.scheme.lower() not in _SCHEMES or not host or "@" in parts.netloc:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address == _THIS_HOST
