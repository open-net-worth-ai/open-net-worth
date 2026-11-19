"""Every connected bank/broker connector's accounts, for linking FROM an account.

The Connectors screen links one connector's accounts at a time (``GET /api/connectors/{id}/
accounts``). The Accounts screen starts from the other end — "which bank account feeds
CA_Monzo?" — so it needs every connector's list at once. This is that list, built from the
same ``connector_fetch.provider_accounts`` read, so there is no second definition of what a
connector can see or of what is linked. Linking itself is the ordinary account update
(``external_ids``), exactly what the Connectors screen's Link… writes.

A connector that is paused, not set up, or withdrawn by policy (Yodlee) is left out; one whose
provider fails still appears, with its ``status`` and the accounts already linked to it, so
the picker can say why it is empty instead of hiding it.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from backend.core.errors import OpenNetWorthError
from backend.services.aggregation.providers import PROVIDERS
from backend.services.connector_catalog import CATALOG

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 4


def _connected_providers() -> list[str]:
    keys: list[str] = []
    for key, provider in PROVIDERS.items():
        kind = CATALOG.get(key)
        if kind is not None and kind.disabled:
            continue
        store = provider.store()
        if store.is_configured() and store.get().get("enabled"):
            keys.append(key)
    return keys


def _group(key: str) -> dict:
    from backend.services.connector_fetch import provider_accounts

    try:
        result = provider_accounts(key)
    except OpenNetWorthError as exc:
        logger.info("connector_linking: %s accounts unavailable (%s)", key, exc)
        result = {"status": "error", "accounts": []}
    return {
        "provider": key,
        "provider_label": PROVIDERS[key].label,
        "status": result.get("status", "error"),
        "accounts": result.get("accounts") or [],
    }


def linkable_accounts() -> list[dict]:
    """``[{provider, provider_label, status, accounts}]`` for every connected connector, in
    catalog order. Providers are asked in parallel: each is a live call (Plaid, SimpleFIN…)
    and the picker waits on the slowest."""
    keys = _connected_providers()
    if not keys:
        return []
    with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL, len(keys))) as pool:
        return list(pool.map(_group, keys))
