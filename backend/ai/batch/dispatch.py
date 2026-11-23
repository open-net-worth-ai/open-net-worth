"""Builds the submit/collect hooks registered on ``core.llm_seam`` — the ONE place
that decides which batch adapter runs, by ACTUAL endpoint capability
(``routing.resolve_adapter_key``), not the transport label alone. See
``routing.py``'s docstring for the incident that motivated this (a xAI batch submit
unconditionally routed to the OpenAI-native adapter and failed every call).

Handles are prefixed with their adapter key at submit time (``"xai:<real_id>"``)
so a COLLECT call for a batch minted before a config change still routes to the
adapter that actually holds it — a mid-flight batch submitted under xAI must not
be handed to whatever adapter the CURRENT config happens to resolve to after the
owner switches providers again.
"""

from __future__ import annotations

import logging
import re

from backend.ai.batch import anthropic_batch, openai_batch, routing, xai_batch
from backend.core.llm_seam import (
    SYNC_HANDLE_PREFIX,
    BatchPending,
    BatchRequest,
    BatchResult,
    sync_fallback_collect,
    sync_fallback_submit,
)

logger = logging.getLogger(__name__)

_ADAPTERS = {
    "anthropic": anthropic_batch,
    "openai": openai_batch,
    "xai": xai_batch,
}

# The real (unprefixed) handle gets interpolated straight into a URL path by some
# adapters (xai_batch's f"/batches/{handle}"). Every legitimate provider batch id
# observed (Anthropic "msgbatch_...", OpenAI "batch_...") is plain alnum/underscore
# — bound it to that shape + a sane length before it ever reaches an adapter, so a
# corrupted ledger row or a future caller can't smuggle a path-traversal/injection
# payload through the handle.
_REAL_HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _active_adapter_key(requests: list[BatchRequest]) -> str:
    from backend.db.model_config_store import ModelConfigStore

    store = ModelConfigStore()
    # A request pinned to ANOTHER provider can't ride the default's batch API (wrong
    # endpoint, key and model namespace); the sync fallback resolves its ref instead.
    if any(_pinned_elsewhere(store, r.model) for r in requests):
        return routing.SYNC_ADAPTER_KEY
    inst = store.resolved()
    return routing.resolve_adapter_key(inst["provider"], inst.get("base_url") or "")


def _pinned_elsewhere(store, model: str) -> bool:
    if ":" not in (model or ""):
        return False
    resolved = store.resolve_override(model)
    return bool(resolved) and resolved["provider_key"] not in (None, store.default_provider_key())


async def _submit(task: str, requests: list[BatchRequest]) -> str:
    adapter_key = _active_adapter_key(requests)
    adapter = _ADAPTERS.get(adapter_key)
    if adapter is None:
        return await sync_fallback_submit(requests)
    real_handle = await adapter.submit(task, requests)
    return f"{adapter_key}:{real_handle}"


async def _collect(handle: str) -> list[BatchResult] | BatchPending:
    if handle.startswith(SYNC_HANDLE_PREFIX):
        return await sync_fallback_collect(handle)
    adapter_key, sep, real_handle = handle.partition(":")
    adapter = _ADAPTERS.get(adapter_key) if sep else None
    if adapter is None:
        logger.error("batch dispatch: unrecognized handle %r — treating as failed", handle)
        return []
    if not _REAL_HANDLE_RE.match(real_handle):
        logger.error(
            "batch dispatch: handle %r has an invalid real-handle segment — refusing "
            "to interpolate it into a provider request; treating as failed",
            handle,
        )
        return []
    return await adapter.collect(real_handle)


def build_batch_hooks():
    """Return the ``(submit, collect)`` pair to register on ``core.llm_seam``."""
    return _submit, _collect
