"""The Codex CLI's OWN model catalog — read from the CLI, never hardcoded here.

The Codex CLI maintains ``$CODEX_HOME/models_cache.json`` (default ``~/.codex``): the
model list it fetched from OpenAI for THIS install, with each model's real reasoning
ladder. That file is the only honest answer to "what can this CLI run", because the CLI
has no ``--list-models`` command and no /models endpoint to call.

We used to ship a hand-written list instead. It rotted completely: by 2026-07-31 every one
of its five ids (``gpt-5.1-codex``, ``gpt-5.1-codex-mini``, ``gpt-5.1``, ``gpt-5-codex``,
``gpt-5``) had been retired, so the picker offered five models and the CLI could run none
of them. A curated list for a tool that publishes its own list is a list that is always
one release from being wrong — so the static list here is a floor for when the cache is
unreadable, not the source of truth.

Two homes hold that file, and the freshest one wins. Every Codex child we spawn runs
under OUR isolated home (``ai/models/codex_home.py``), so a Codex used only through Lucius
refreshes ITS catalog there and never in ``~/.codex``. Reading the operator's home alone
showed a stale or missing list to exactly those users (2026-11-11: a picker stuck on the
static floor while the CLI could run ``gpt-6-luna``).

This module never writes the CLI's file. When no list is fresh it asks the CLI for one
(``model/list``, see ``codex_model_list.py``: a catalog request, not a model turn) under the
isolated home and keeps the answer in memory as one more candidate — so a Docker install
signed in with ``codex login`` alone shows the real list on Test connection, not after a
first chat.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.core.child_env import scrubbed_environ
from backend.core.paths import CODEX_HOME_DIR

logger = logging.getLogger(__name__)

# Used only when the CLI's cache is missing/unreadable (fresh install that has not fetched
# yet, a sandbox with no HOME). Deliberately tiny — a floor, not a catalog.
#
# Verified 2026-08-24 against a real `~/.codex/models_cache.json` (CLI 0.147.0): it offered
# terra, luna, 5.5 and 5.4-mini. The floor's own lead entry was `gpt-5.6-sol`, which that
# install could NOT run — the exact rot the module docstring above describes, returned after
# one release. Ordered to match the CLI's own ordering, which leads with its default.
#
# If this needs updating again, read the cache rather than guessing:
#   python3 -c "import json,pathlib;print([m['slug'] for m in
#   json.loads((pathlib.Path.home()/'.codex'/'models_cache.json').read_text())['models']])"
#
# Last verified 2026-11-11 against CLI 0.164.0's cache: gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna.
FALLBACK_MODELS: tuple[str, ...] = ("gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna")

_CACHE_FILENAME = "models_cache.json"


def codex_home() -> Path:
    """``$CODEX_HOME`` when set, else ``~/.codex`` — the CLI's own resolution order."""
    override = (os.getenv("CODEX_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".codex"


def _private_home() -> Path:
    """The isolated home our spawned Codex children use — and refresh their catalog in."""
    return CODEX_HOME_DIR


def catalog_paths() -> list[Path]:
    """Every place the CLI's catalog may live, operator's home first."""
    return [codex_home() / _CACHE_FILENAME, _private_home() / _CACHE_FILENAME]


def _load(path: Path) -> Optional[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("codex models cache unavailable at %s (%s)", path, exc)
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("models"), list) else None


def _freshness(raw: dict, path: Path) -> str:
    """The CLI's own ``fetched_at`` (ISO-8601 UTC, so it sorts as text); file mtime if absent."""
    stamp = raw.get("fetched_at")
    if isinstance(stamp, str) and stamp.strip():
        return stamp.strip()
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""


# The last list the CLI gave us over model/list, shaped like its cache file: {fetched_at, models}.
_fetched: Optional[dict] = None


def _newest() -> Optional[tuple[dict, str]]:
    found = [(raw, _freshness(raw, path)) for path in catalog_paths() if (raw := _load(path))]
    if _fetched is not None:
        found.append((_fetched, _fetched["fetched_at"]))
    return max(found, key=lambda pair: pair[1]) if found else None


def _read_cache() -> Optional[dict]:
    newest = _newest()
    return newest[0] if newest else None


# A list this old is re-fetched when the picker next resolves it; Test connection always does.
STALE_AFTER = timedelta(days=1)
_REFRESH_TIMEOUT_S = 20
# After a failed fetch the picker waits this long before trying again on its own, so an
# offline or signed-out CLI doesn't add the fetch timeout to every picker load.
_RETRY_AFTER_FAILURE_S = 10 * 60
_refresh_lock = threading.Lock()
_last_failure: Optional[float] = None


def _is_stale() -> bool:
    newest = _newest()
    if newest is None or not available_models():
        return True
    try:
        fetched = datetime.fromisoformat(newest[1].replace("Z", "+00:00"))
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched > STALE_AFTER


def _list_models(binary: str, env: dict) -> Optional[list[dict]]:
    """The one place this module starts the CLI — the test suite replaces it (no network)."""
    from backend.ai.models.codex_model_list import list_models

    return list_models(binary, env, _REFRESH_TIMEOUT_S)


def refresh(binary: str, *, force: bool = False) -> bool:
    """Ask the CLI for its model list now. True when it answered with one.

    Skipped (False) when a fresh list is already readable and ``force`` is off. Best-effort:
    an old CLI, no sign-in, no network or a timeout leave everything as it was, and the
    caller reads whatever list there is. Never raises.
    """
    from backend.ai.models.codex_home import has_login, isolated_home

    global _last_failure, _fetched
    if not binary or not (force or _is_stale()):
        return False
    # Signed out, model/list answers with the list BUILT INTO the binary (measured on 0.164.0:
    # eight models, against the account's three) — offering that would put models the
    # account cannot run in the picker. The static floor, with its warning, is the honest
    # answer until there is a sign-in.
    if not has_login():
        return False
    with _refresh_lock:
        if not force:
            recent = _last_failure is not None and (
                time.monotonic() - _last_failure < _RETRY_AFTER_FAILURE_S
            )
            if recent or not _is_stale():
                return False
        env = scrubbed_environ()
        env["CODEX_HOME"] = isolated_home()
        rows = _list_models(binary, env)
        if not rows:
            _last_failure = time.monotonic()
            return False
        _fetched = {"fetched_at": datetime.now(timezone.utc).isoformat(), "models": rows}
        _last_failure = None
        return True


def available_models() -> list[dict]:
    """``[{id, display_name, effort_levels}]`` in the CLI's own priority order.

    Hidden models are dropped (``visibility`` other than ``"list"`` — e.g. the internal
    ``codex-auto-review``, which is not a model a user should be able to pick for chat).
    ``supported_in_api`` is deliberately NOT filtered on: we drive the CLI, not the API,
    so a model the API cannot serve is still perfectly runnable here.

    Returns ``[]`` when the cache is absent or malformed — callers fall back to
    ``FALLBACK_MODELS``. Never raises.
    """
    raw = _read_cache()
    rows = (raw or {}).get("models")
    if not isinstance(rows, list):
        return []

    out: list[tuple[int, dict]] = []
    for i, m in enumerate(rows):
        if not isinstance(m, dict):
            continue
        slug = (m.get("slug") or "").strip()
        if not slug or m.get("visibility") != "list":
            continue
        # priority is the CLI's own ordering (1 = flagship). Missing → sort last, but keep
        # the file's relative order among such rows rather than collapsing them together.
        priority = m.get("priority")
        rank = priority if isinstance(priority, int) else 10_000 + i
        out.append(
            (
                rank,
                {
                    "id": slug,
                    "display_name": (m.get("display_name") or "").strip() or slug,
                    "effort_levels": _levels(m.get("supported_reasoning_levels")),
                },
            )
        )
    out.sort(key=lambda pair: pair[0])
    return [row for _, row in out]


def _levels(raw: object) -> list[str]:
    """``supported_reasoning_levels`` → our effort ladder, clamped to the rungs we render.

    The CLI's list is ``[{effort, description}, …]`` and can carry rungs beyond ours (it
    already ships ``ultra``); ``_ladder_intersect`` drops anything we cannot present rather
    than surfacing a rung the effort dial has no option for.
    """
    from backend.ai.models.capabilities import _ladder_intersect

    if not isinstance(raw, list):
        return []
    efforts = [
        (e.get("effort") or "").strip() for e in raw if isinstance(e, dict) and e.get("effort")
    ]
    return _ladder_intersect(efforts)


def effort_levels_for(model_id: str) -> Optional[list[str]]:
    """This model's real ladder per the CLI's cache, or ``None`` when it isn't listed."""
    target = (model_id or "").strip().lower()
    if not target:
        return None
    for row in available_models():
        if row["id"].lower() == target:
            return row["effort_levels"]
    return None


def catalog_readable() -> bool:
    """True when the list comes from the CLI itself, False when it is the static floor."""
    return bool(available_models())


def missing_catalog_message() -> str:
    """What to tell the owner when only the floor is on offer."""
    where = " or ".join(str(p) for p in catalog_paths())
    return (
        "Codex couldn't fetch its model list, so these are a built-in short list that may be "
        f"out of date. Codex keeps the list at {where}. Check that Codex is signed in and this "
        "machine can reach OpenAI, then test again."
    )


def model_ids() -> list[str]:
    """The pickable model ids, CLI order — falling back to the static floor."""
    ids = [m["id"] for m in available_models()]
    return ids or list(FALLBACK_MODELS)
