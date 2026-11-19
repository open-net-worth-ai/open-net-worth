"""Whether a model endpoint is a process on this machine (Ollama, LM Studio, vLLM): self-hosted, so
its calls cost nothing per token. Stdlib only — the one definition ``ai.models.pricing`` and
``services.trading_agents`` share."""

from __future__ import annotations

_LOCAL_PREFIXES = (
    "http://localhost",
    "https://localhost",
    "http://127.",
    "https://127.",
    "http://[::1]",
    "http://0.0.0.0",
)


def is_local_base_url(base_url: str) -> bool:
    return (base_url or "").lower().strip().startswith(_LOCAL_PREFIXES)
