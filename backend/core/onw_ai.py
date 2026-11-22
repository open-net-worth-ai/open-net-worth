"""Open Net Worth AI — the provider facts every layer shares, and the Private Cloud environment.

A Private Cloud agent is handed its AI by the environment (``ONW_AI_BASE_URL``, ``ONW_AI_KEY``,
``ONW_AI_MODELS``, optionally ``ONW_AI_REQUEST_EXTRAS`` and ``ONW_AI_CREDIT_URL``). When the first
three are present the provider is MANAGED: endpoint, key and models come from here, win over
anything stored, and are not editable. Otherwise the owner may paste a key in Settings, stored
like every other provider key (``db/model_config_store.py``).

Read at call time, never cached, so a test (or a restarted agent with new env) sees the change.
Stdlib only: every layer may import it.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

PROVIDER_KEY = "onw_ai"
LABEL = "Open Net Worth AI"
PUBLIC_BASE_URL = "https://ai.opennetworth.ai/v1"
# The Private Cloud overlay's hub: the relay answers there over the tunnel, in plain http.
OVERLAY_HOST = "10.99.0.1"
# What a fresh Private Cloud agent runs when nothing else is chosen.
PREFERRED_MODEL = "claude-haiku-5.5"


@dataclass(frozen=True)
class Managed:
    base_url: str
    api_key: str
    models: tuple[dict, ...]
    request_extras: dict = field(default_factory=dict)
    credit_url: str = ""

    @property
    def model_ids(self) -> list[str]:
        return [m["id"] for m in self.models]

    def default_model(self) -> str:
        ids = self.model_ids
        return PREFERRED_MODEL if PREFERRED_MODEL in ids else ids[0]


def _parse_models(raw: str) -> tuple[dict, ...]:
    try:
        data = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    out: list[dict] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            model_id = item["id"].strip()
            label = item.get("label") if isinstance(item.get("label"), str) else ""
            out.append({"id": model_id, "label": label.strip() or model_id})
    return tuple(out)


def _parse_extras(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("ONW_AI_REQUEST_EXTRAS is not JSON; ignored")
        return {}
    return data if isinstance(data, dict) else {}


def managed() -> Optional[Managed]:
    """The Private Cloud configuration, or None when the environment does not provide one.

    All three of base URL, key and a non-empty model list are required — a partial environment
    is not a managed provider (the owner can still paste a key)."""
    base_url = os.environ.get("ONW_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("ONW_AI_KEY", "").strip()
    models = _parse_models(os.environ.get("ONW_AI_MODELS", ""))
    if not (base_url and api_key and models):
        return None
    return Managed(
        base_url=base_url,
        api_key=api_key,
        models=models,
        request_extras=_parse_extras(os.environ.get("ONW_AI_REQUEST_EXTRAS", "")),
        credit_url=os.environ.get("ONW_AI_CREDIT_URL", "").strip(),
    )


def same_endpoint(a: str, b: str) -> bool:
    return bool(a) and (a or "").strip().rstrip("/") == (b or "").strip().rstrip("/")


def request_extras_for(base_url: str) -> dict:
    """The request extras to merge into a call to ``base_url``: the managed provider's, and only
    for its own endpoint. The app never interprets them."""
    cfg = managed()
    if cfg is None or not same_endpoint(cfg.base_url, base_url):
        return {}
    return cfg.request_extras


def deep_merge(base: dict, extra: dict) -> dict:
    """``extra`` merged into a copy of ``base``; nested dicts merge, anything else replaces."""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def base_url_allowed(url: str) -> bool:
    """A pasted endpoint must be https, except the overlay hub or this machine (plain http)."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    if host in (OVERLAY_HOST, "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# LiteLLM's budget refusal (1.110.2: BudgetExceededError → HTTP 422, type "budget_exceeded",
# message "Budget has been exceeded! …"; older releases answered 400 "ExceededBudget"; the code
# is configurable, 429 restoring the old one). A 402 from our relay always means no credit.
_OUT_OF_CREDIT_MARKERS = (
    "budget has been exceeded",
    "exceededbudget",
    "budget_exceeded",
    "insufficient credit",
    "insufficient_credit",
)
_OUT_OF_CREDIT_STATUSES = frozenset({400, 402, 422, 429})


def is_out_of_credit(status: Optional[int], text: str) -> bool:
    if status == 402:
        return True
    if status is not None and status not in _OUT_OF_CREDIT_STATUSES:
        return False
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _OUT_OF_CREDIT_MARKERS)
