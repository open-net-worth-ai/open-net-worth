"""Feature flags — the ONE place a flag is declared, and the order its value is resolved in.

A flag is a boolean that selects between two COMPLETE code paths at a single seam, so a feature
big enough to need one can ship dark and be switched on by the owner. Off means exactly the old
behaviour, running the old code (ADR ``2026-10-25-feature-flags-off-means-the-old-code``; the
developer rules are in ``docs/FEATURE_FLAGS.md``).

Leaf module, stdlib + ``core`` only. It holds the declarations and the pure resolution order;
the stored value lives in ``app_config`` and is read by ``services.feature_flags``, which is
the accessor every caller uses (``enabled(name)``, named after OpenFeature's boolean
evaluation so a later swap stays cheap). Code below ``services`` that needs a flag takes the
choice from its caller — the seam always sits above it.

Resolution, first match wins:

1. a test override (``override()``, used by the pytest helpers) — tests only;
2. the public demo (``DEMO_MODE``) — always the declared default, nothing below is read;
3. the environment, ``ONW_FLAG_<NAME>`` = ``1``/``0``/``true``/``false`` — dev, CI and QA;
4. the owner's stored choice (Settings → Developer → Experimental);
5. the declared default.

An unknown name RAISES (``UnknownFeatureFlagError``), never reads as ``False``.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Literal, Mapping, Optional

from backend.core.errors import UnknownFeatureFlagError, ValidationError

_log = logging.getLogger(__name__)

FlagKind = Literal["release", "kill_switch"]
FlagSource = Literal["test", "demo", "env", "stored", "default"]

ENV_PREFIX = "ONW_FLAG_"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_KINDS = ("release", "kill_switch")
_TRUE = {"1", "true"}
_FALSE = {"0", "false"}


@dataclass(frozen=True)
class Flag:
    """One declared flag. ``description`` is shown to the owner — plain words, what turning it
    on changes. ``remove_by`` is when the old path and the flag must be gone (CI fails after)."""

    name: str
    description: str
    default: bool
    owner: str
    added: date
    remove_by: date
    kind: FlagKind = "release"


# THE REGISTRY. Declare a flag here and nowhere else; retire it by deleting its entry in the
# same PR that deletes its old code path. The test suite declares its own example flag
# (``tests/flags.py``) on top of these.
FLAGS: tuple[Flag, ...] = (
    Flag(
        name="permissions_v2",
        description=(
            "A new permissions model. Each tool has one setting: Always allow, Ask or Block. "
            "In Auto mode a model judges each Ask call and only stops you when it is unsure. "
            "Approval cards answer Approve, Always allow or Deny. Automations get their own "
            "Always allowed and Blocked tool lists. Settings > Permissions lists the built-in "
            "tools, and a connector's tools are set on its own page. The YOLO and "
            "unattended-script switches are retired while this is on. "
            "Details: Lucius > Permissions v2."
        ),
        default=False,
        owner="owner",
        added=date(2026, 10, 31),
        remove_by=date(2027, 1, 31),
        kind="release",
    ),
)


def build_registry(flags: tuple[Flag, ...]) -> dict[str, Flag]:
    """Validate a declaration list and index it by name. Raises on any malformed entry."""
    registry: dict[str, Flag] = {}
    for flag in flags:
        if not _NAME_RE.match(flag.name):
            raise ValidationError(f"feature flag name {flag.name!r} must be snake_case")
        if flag.name in registry:
            raise ValidationError(f"feature flag {flag.name!r} is declared twice")
        if flag.kind not in _KINDS:
            raise ValidationError(f"feature flag {flag.name!r} has unknown kind {flag.kind!r}")
        if not flag.description.strip() or not flag.owner.strip():
            raise ValidationError(f"feature flag {flag.name!r} needs a description and an owner")
        if flag.remove_by <= flag.added:
            raise ValidationError(f"feature flag {flag.name!r}: remove_by must follow added")
        registry[flag.name] = flag
    return registry


REGISTRY: dict[str, Flag] = build_registry(FLAGS)

_test_overrides: dict[str, bool] = {}


def get(name: str) -> Flag:
    flag = REGISTRY.get(name)
    if flag is None:
        raise UnknownFeatureFlagError(f"unknown feature flag {name!r}", name=name)
    return flag


def all_flags() -> list[Flag]:
    return sorted(REGISTRY.values(), key=lambda f: f.name)


def env_var(name: str) -> str:
    return ENV_PREFIX + name.upper()


def env_value(name: str, environ: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    """The ``ONW_FLAG_<NAME>`` override, or None when unset. A value that is not
    1/0/true/false is logged and IGNORED — one bad line in a compose file must not take every
    flag (and the Experimental list) down with it. Tests choose flags with ``--flag``, which
    does refuse a bad value."""
    raw = (environ if environ is not None else os.environ).get(env_var(name))
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    _log.warning("%s=%r ignored: use 1, 0, true or false", env_var(name), raw)
    return None


@dataclass(frozen=True)
class Resolution:
    value: bool
    source: FlagSource


def resolve(
    name: str,
    *,
    stored: Optional[bool],
    demo: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> Resolution:
    """The value of ``name`` and where it came from, in the order the module docstring lists."""
    flag = get(name)
    if name in _test_overrides:
        return Resolution(_test_overrides[name], "test")
    if demo:
        return Resolution(flag.default, "demo")
    from_env = env_value(name, environ)
    if from_env is not None:
        return Resolution(from_env, "env")
    if stored is not None:
        return Resolution(bool(stored), "stored")
    return Resolution(flag.default, "default")


@contextmanager
def override(**values: bool) -> Iterator[None]:
    """Force flags for the duration of a ``with`` block — the test seam behind the pytest
    helpers. It beats every other source, the demo included. Process-wide, not context-local,
    so a ``TestClient`` request served on another thread sees it too."""
    for name in values:
        get(name)
    saved = dict(_test_overrides)
    _test_overrides.update({k: bool(v) for k, v in values.items()})
    try:
        yield
    finally:
        _test_overrides.clear()
        _test_overrides.update(saved)


def expired(today: date, flags: Optional[list[Flag]] = None) -> list[Flag]:
    """Flags whose ``remove_by`` has passed — the CI lint fails on any."""
    return [f for f in (flags if flags is not None else all_flags()) if f.remove_by < today]
