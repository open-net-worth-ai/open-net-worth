"""Feature flags — the accessor every caller uses. Declarations live in ``core.feature_flags``.

``enabled(name)`` is the one read; call it at the flag's single switch point
(``gate = new_gate if enabled("x") else old_gate``), never sprinkled through a code path.
``set_flag`` / ``reset_flag`` are the owner's writes, reached only from the auth-gated
``PUT /api/feature-flags/{name}``: no Lucius tool may import them
(``tests/test_feature_flags_guard.py``), and the public demo refuses them here as well as at
the route.
"""

from __future__ import annotations

from typing import Optional

from backend.core import data_events
from backend.core import feature_flags as registry
from backend.core.config import get_settings
from backend.core.errors import FeatureFlagLockedError
from backend.db.app_config_store import AppConfigStore


def _demo() -> bool:
    return get_settings().demo_mode


def _stored(name: str, demo: bool) -> Optional[bool]:
    # The demo never reads the store: its answer is the declared default whatever is stored.
    return None if demo else AppConfigStore().get_feature_flags().get(name)


def resolve(name: str) -> registry.Resolution:
    registry.get(name)
    demo = _demo()
    return registry.resolve(name, stored=_stored(name, demo), demo=demo)


def enabled(name: str) -> bool:
    """Whether ``name`` is on. Raises ``UnknownFeatureFlagError`` for an undeclared name."""
    return resolve(name).value


def _row(flag: registry.Flag, stored: dict[str, bool], demo: bool) -> dict:
    res = registry.resolve(flag.name, stored=None if demo else stored.get(flag.name), demo=demo)
    return {
        "name": flag.name,
        "description": flag.description,
        "kind": flag.kind,
        "default": flag.default,
        "enabled": res.value,
        "source": res.source,
        "stored": None if demo else stored.get(flag.name),
        "env_var": registry.env_var(flag.name),
        "owner": flag.owner,
        "added": flag.added.isoformat(),
        "remove_by": flag.remove_by.isoformat(),
    }


def list_flags() -> dict:
    """Every declared flag with its current value and where that value came from."""
    demo = _demo()
    stored = {} if demo else AppConfigStore().get_feature_flags()
    return {
        "flags": [_row(f, stored, demo) for f in registry.all_flags()],
        "locked": demo,
    }


def describe(name: str) -> dict:
    flag = registry.get(name)
    demo = _demo()
    return _row(flag, {} if demo else AppConfigStore().get_feature_flags(), demo)


def set_flag(name: str, value: Optional[bool]) -> dict:
    """Store the owner's choice for ``name``; ``None`` resets it to the declared default.
    Returns the flag's row — whose ``source`` may still say ``env`` when an environment
    override outranks the stored choice."""
    registry.get(name)
    if _demo():
        raise FeatureFlagLockedError("Experimental features are fixed on the public demo.")
    AppConfigStore().set_feature_flag(name, value)
    # Every open screen re-reads its flags (the web server has no REST-write fan-out of its own).
    data_events.publish(["feature_flags"])
    return describe(name)


def reset_flag(name: str) -> dict:
    return set_flag(name, None)
