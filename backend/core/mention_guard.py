"""Forbidden-store guard for the mention descriptor registry.

RFC 2026-06-25-lucius-generic-entity-registry §5.3 / §14.7 R-EX5.

The guard mechanism is CORE; the forbidden set is HOST POLICY (ONW passes
``MENTION_FORBIDDEN_STORES``).  ``check_descriptor_sources`` is called by
``_collect.register()`` so secret stores can never be exposed via a descriptor,
not even transiently before an integration test.

This module is a leaf: stdlib only — no backend.* imports.
"""

from __future__ import annotations

import re

# ONW-backend-specific forbidden store class names.  Auth / credentials / connector
# configs are excluded from the mention registry at all times.
MENTION_FORBIDDEN_STORES: frozenset[str] = frozenset(
    {
        "AuthStore",
        "ModelConfigStore",
        "MemorySettingsStore",
        # NOTE: the real class is ``SimpleFinConfigStore`` (capital F) — matches
        # ``type(val).__name__`` exactly, unlike the pre-deletion guard's
        # "SimplefinConfigStore" entry, which never matched anything.
        "SimpleFinConfigStore",
        "PlaidConfigStore",
        "EnableBankingConfigStore",
        "GoCardlessConfigStore",
        "YodleeConfigStore",
        "IbkrConfigStore",
        "LunchMoneyConfigStore",
        "YnabConfigStore",
        "ActualBudgetConfigStore",
        "ImapConfigStore",
        "EmailConfigStore",
        # The connectors table (connectors-table RFC): the one data-access class and every
        # settings facade over it that can hand back a credential.
        "ConnectorStore",
        "AggregatorConfigStore",
        "AlpacaConfigStore",
        "ChannelConfigStore",
        "LunchFlowConfigStore",
        "MarketDataConfigStore",
        "McpClientStore",
        "RestConfigStore",
        "SearchConfigStore",
        "TelegramConfigStore",
        "VoiceConfigStore",
    }
)

# Secret key name regex — names matching this are refused as descriptor field keys
# regardless of the ``sensitive`` flag (defence-in-depth; the allowlist is the
# primary boundary, this is the secondary name-level guard).
_SECRET_KEY_RE = re.compile(
    r"pass|secret|token|api[-_]?key|credential|private[-_]?key|"
    r"seed|mnemonic|otp|cvv|pin|cookie|session",
    re.IGNORECASE,
)


def _callable_store_names(fn, _seen: set | None = None) -> list[str]:
    """Introspect a callable for referenced store class names.

    Walks three sources so both closure-captured and global-reference patterns
    are caught:

    1. ``__closure__`` cells — captures from an enclosing scope.
    2. ``__code__.co_names`` — global/attribute name references; this is where
       ``lambda q: AuthStore().all()`` defined at module scope stores
       ``AuthStore`` (no closure cell, just a global name load).
    3. Nested code objects in ``__code__.co_consts`` — inner lambdas /
       comprehensions embedded in the bytecode of the outer callable.

    Best-effort: misses stores routed through several dynamic indirection
    layers, but catches every pattern the descriptor convention prescribes.
    """
    names: list[str] = []
    if fn is None:
        return names

    if _seen is None:
        _seen = set()
    fn_id = id(fn)
    if fn_id in _seen:
        return names
    _seen.add(fn_id)

    qualname = getattr(fn, "__qualname__", "") or ""
    names.extend(qualname.split("."))

    # 1. Closure cells.
    closure = getattr(fn, "__closure__", None) or ()
    for cell in closure:
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if callable(val):
            names.extend(_callable_store_names(val, _seen))
        elif isinstance(val, type):
            names.append(val.__name__)
        elif isinstance(val, str):
            names.append(val)
        else:
            # Recover class name of a captured instance (e.g. ``store = SomeStore();
            # lambda q: store.all()`` — the instance is the cell, not the class).
            cls_name = type(val).__name__
            if cls_name not in ("NoneType", "int", "float", "bool", "list", "dict", "tuple"):
                names.append(cls_name)

    # 2. Global name references in the bytecode (catches module-level lambdas).
    code = getattr(fn, "__code__", None)
    if code is not None:
        names.extend(code.co_names)

        # 3. Nested code objects (inner lambdas / comprehensions in co_consts).
        for const in code.co_consts:
            if const is code:
                continue
            if hasattr(const, "co_names"):
                names.extend(const.co_names)
                for inner in const.co_consts:
                    if hasattr(inner, "co_names"):
                        names.extend(inner.co_names)

    return names


def check_descriptor_sources(
    descriptor_type: str,
    rows,
    find_one,
    project_fn,
    forbidden_sources: frozenset[str] = MENTION_FORBIDDEN_STORES,
) -> None:
    """Raise ``ValueError`` if any callable touches a forbidden store.

    Called by ``_collect.register()`` for every registered descriptor so the
    check runs at import time, before any request is served.

    ``forbidden_sources`` is the host-supplied policy set; defaults to ONW's
    ``MENTION_FORBIDDEN_STORES``.
    """
    for fn in (rows, find_one, project_fn):
        if fn is None:
            continue
        store_names = _callable_store_names(fn)
        for name in store_names:
            if name in forbidden_sources:
                raise ValueError(
                    f"EntityDescriptor for '{descriptor_type}' references forbidden store "
                    f"'{name}' — secret stores must never be exposed via the mention registry."
                )


def check_fields_keys(descriptor_type: str, fields: dict) -> None:
    """Raise ``ValueError`` if any field key matches the secret key name regex.

    The fields allowlist is the primary security boundary; this name-level check
    is a secondary defence-in-depth layer that catches innocuously-named keys that
    still carry secret semantics.
    """
    for key in fields:
        if _SECRET_KEY_RE.search(key):
            raise ValueError(
                f"EntityDescriptor for '{descriptor_type}' has a field key '{key}' that "
                f"matches the secret key denylist — use 'sensitive=True' on a renamed key, "
                f"or omit the field entirely."
            )
