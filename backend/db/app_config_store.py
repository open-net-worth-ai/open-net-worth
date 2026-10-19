"""AppConfigStore — instance branding / white-label config (P3-ON / ON3).

A single dict holding the operator's branding: ``{app_name, accent_color,
logo_url}``. This is INSTANCE config, not user data — so it is deliberately:

  - NOT in ``core.portable.PORTABLE_FILES`` (branding doesn't travel in a user's
    data export),
  - checkpoint-EXCLUDED (below),
  - not a secret.

Env (``APP_NAME`` / ``APP_ACCENT_COLOR`` / ``APP_LOGO_URL``) seeds the defaults
the first time the config is read, then the store is the single source of
truth — mirroring the ``EmailConfigStore`` "env seeds, store is authoritative"
pattern, so a self-hoster can rename/recolor their instance from the running UI
without a ``.env`` edit + restart.

Also holds the **script-bundling-skills kill switch** (S4):
``script_skills_enabled`` (default ``False``) — when off, NO script-skill run ever
reaches the sandbox subprocess. The enforcement gate is the ``run_script`` tool dispatch.
Human-only: no Lucius tool writes the flag.

Dumb storage: validation (e.g. accent must be ``#rrggbb``) lives in the API route,
not here — this layer only reads/merges/persists.

WAVE 5D2a (JSON->DB): replaces the hand-rolled ``app_config.json`` file I/O with a
row in the ``runtime_state`` table, namespace ``"app_config"`` — checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES``, via the whole ``runtime_state`` table,
which already carried that exclusion since Wave 5C), NOT a secret (not in
``core.sensitive.SECRET_TABLES``), NOT portable. Pre-migration this store was never
actually reached by an active checkpoint turn (no Lucius tool ever writes it — REST
only), so it was "excluded" only in the sense of never being touched during one;
landing in the already-excluded ``runtime_state`` table makes that a STRUCTURAL
guarantee instead, a strict tightening, not a behavior change. The bespoke
``threading.Lock`` around every read-modify-write is replaced by
``NamespacedSettingsStore``'s own table lock + one transaction per mutator
(``_use_conn``) — same "two concurrent writers must not silently clobber each
other" guarantee the original lock existed for, now enforced at the DB layer.
"""

from __future__ import annotations

import os
from typing import Optional

from backend.core.config import get_settings
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

DEFAULT_APP_NAME = "Open Net Worth"

DEFAULTS: dict = {
    "app_name": DEFAULT_APP_NAME,
    "accent_color": "",  # "" → fall back to the built-in --accent token
    "logo_url": "",  # "" → render the text brand
}

# Branding fields surfaced by ``get()`` / the public ``GET /api/config``.
_FIELDS = ("app_name", "accent_color", "logo_url")

# Script-bundling-skills S4: the instance KILL SWITCH for all script execution (§4.3).
# Default False — a fresh instance NEVER executes a bundled script until the operator
# explicitly flips this ON. Human-only: no Lucius tool may write this flag. The
# enforcement gate is the ``run_script`` tool dispatch (S3).
SCRIPT_SKILLS_FLAG = "script_skills_enabled"

# Ad-hoc code execution (RFC ad-hoc-code-execution §4.3): ONE instance flag, the KILL SWITCH —
# does the feature exist at all. Default False → ``run_code`` hard-refuses regardless of its
# permission value. The operator's global off-ramp: an enable/disable, not an approval layer.
# (Whether the owner is asked is the ordinary Always allow / Ask / Block value on the tool; the
# old instance-wide "always allow" flag became a stored ``run_code`` row — revision
# ``202610201400``.) Human-only (no Lucius tool writes it) and kept OUT of the public branding
# ``get()`` projection so it never rides the auth-exempt ``GET /api/config``.
CODE_EXEC_ENABLED_FLAG = "code_execution_enabled"

# Read-only SQL access (docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md): the
# instance KILL SWITCH for `run_sql_query` / `describe_db_schema`. Human-only, like every flag
# above, and kept OUT of the public branding `get()` projection.
#
# **Default TRUE**, unlike the two code-execution flags above — and the difference is the
# point, not an inconsistency. `run_code` executes model-authored code, so the safe default is
# "this feature does not exist until you say so". A SQL query cannot write, cannot reach a
# credential table, and cannot leave the process; it reads data Lucius can already reach
# through ~200 existing tools, just in combinations they cannot express. Defaulting that OFF
# would mean the capability silently does nothing for every owner who never finds the toggle —
# which is the outage-that-calls-itself-security shape the confirm-gate arming note warns about.
# The switch exists as a genuine off-ramp, not as an opt-in gate.
SQL_QUERY_FLAG = "sql_query_enabled"

# Monthly AI-spend budget (USD).  Stored as a positive float or absent when unset.
# Exposed via GET/PUT /api/cost/budget (auth-gated; never on the public GET /api/config).
AI_SPEND_BUDGET_KEY = "ai_spend_budget_usd"

# Full-data disaster-recovery backup (services/backup.py). BACKUP_ENABLED_FLAG defaults
# TRUE (opt-out, not opt-in) — unlike the agent/script kill switches above, a silent data
# incident with no rollback trail is the failure mode we're guarding against, so the safe
# default is "on". BACKUP_RETENTION_DAYS_KEY is a positive int (default 30); read/write via
# get_backup_retention_days()/set_backup_retention_days() below.
BACKUP_ENABLED_FLAG = "backup_enabled"
BACKUP_RETENTION_DAYS_KEY = "backup_retention_days"
DEFAULT_BACKUP_RETENTION_DAYS = 30

# Unattended-write notification toggle (Settings > Permissions). Default TRUE (opt-out, not
# opt-in) — unlike the script/code-exec kill switches above, an unattended write's immediate
# per-write Signals row + outbound notify() ping is TODAY'S established behavior (see
# ai/_confirm_gate.py::surface_unattended_write), so flipping this off is a deliberate choice
# to go quieter, not a safety default. When OFF, BOTH the Signals/Inbox row and the outbound
# push are suppressed for a routine successful unattended write — the agent's own end-of-run
# notify() summary is a separate call and is unaffected either way, as is
# surface_unattended_refusal (a write refused outside its envelope always notifies,
# regardless of this flag).
NOTIFY_UNATTENDED_WRITE_FLAG = "notify_unattended_write_enabled"

# How long one Lucius turn (chat or Automation run) may run before it is stopped, in seconds.
# Absent = the env default (``Settings.turn_timeout_s``, 1200). ``0`` = no limit: a slow model
# provider can take minutes per call, and the owner, not a constant, decides how long to wait.
TURN_TIME_LIMIT_KEY = "turn_time_limit_s"


def _seed_from_env() -> dict:
    """Seed branding from APP_* env vars — used once, when the row doesn't exist yet."""
    seed: dict = {}
    for env_key, cfg_key in (
        ("APP_NAME", "app_name"),
        ("APP_ACCENT_COLOR", "accent_color"),
        ("APP_LOGO_URL", "logo_url"),
    ):
        val = (os.getenv(env_key) or "").strip()
        if val:
            seed[cfg_key] = val
    return seed


class AppConfigStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("app_config", table=runtime_state, engine=engine)

    def _read_raw(self, *, conn=None) -> dict:
        """The stored object (env-seeded when the row doesn't exist yet), before any
        projection."""
        data = super().get(conn=conn)
        return data if data else _seed_from_env()

    def get(self) -> dict:
        """The public BRANDING object: defaults merged under whatever is stored; env seeds
        the defaults (in memory only) when the row doesn't exist yet. Deliberately excludes
        non-branding keys (e.g. the AG4 kill switch) so the auth-exempt ``GET /api/config``
        only ever exposes branding."""
        data = self._read_raw()
        merged = {**DEFAULTS, **{k: data[k] for k in _FIELDS if k in data}}
        # An empty stored app_name is meaningless — fall back to the default name.
        if not (merged.get("app_name") or "").strip():
            merged["app_name"] = DEFAULT_APP_NAME
        return merged

    def update(self, changes: dict) -> dict:
        """Persist a partial BRANDING patch (only the known branding fields). Returns the
        merged branding. Preserves any non-branding keys already stored (e.g. the kill
        switch). Read and write compose in ONE transaction (``_use_conn``)."""
        with self._use_conn(None) as conn:
            raw = self._read_raw(conn=conn)
            for k in _FIELDS:
                if k in changes and changes[k] is not None:
                    raw[k] = str(changes[k]).strip()
            if not (raw.get("app_name") or "").strip():
                raw["app_name"] = DEFAULT_APP_NAME
            self.save(raw, conn=conn)
        return self.get()

    def get_flag(self, key: str, default: bool = False) -> bool:
        """Read a boolean instance flag (e.g. the AG4 kill switch) — kept OUT of the public
        branding ``get()`` projection."""
        val = self._read_raw().get(key, default)
        return bool(val)

    def set_flag(self, key: str, value: bool) -> bool:
        """Persist a boolean instance flag, preserving branding + any other keys stored.
        Returns the stored value."""
        with self._use_conn(None) as conn:
            raw = self._read_raw(conn=conn)
            raw[key] = bool(value)
            self.save(raw, conn=conn)
        return bool(value)

    def get_backup_retention_days(self) -> int:
        """How many days of daily backups to keep (services/backup.py). Default 30;
        clamped to [1, 365] on read so a bad stored value can't disable pruning entirely
        or discard everything on the next run."""
        try:
            days = int(
                self._read_raw().get(BACKUP_RETENTION_DAYS_KEY, DEFAULT_BACKUP_RETENTION_DAYS)
            )
        except (TypeError, ValueError):
            return DEFAULT_BACKUP_RETENTION_DAYS
        return max(1, min(365, days))

    def set_backup_retention_days(self, days: int) -> int:
        """Persist the backup retention window (clamped to [1, 365])."""
        clamped = max(1, min(365, int(days)))
        with self._use_conn(None) as conn:
            raw = self._read_raw(conn=conn)
            raw[BACKUP_RETENTION_DAYS_KEY] = clamped
            self.save(raw, conn=conn)
        return clamped

    def get_turn_time_limit_s(self) -> Optional[int]:
        """The stored turn time limit in seconds (``0`` = no limit), or ``None`` when unset."""
        val = self._read_raw().get(TURN_TIME_LIMIT_KEY)
        if val is None:
            return None
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return None

    def set_turn_time_limit_s(self, value: Optional[int]) -> Optional[int]:
        """Persist the turn time limit (``0`` = no limit); ``None`` clears it back to the
        default. Returns the stored value."""
        with self._use_conn(None) as conn:
            raw = self._read_raw(conn=conn)
            if value is None:
                raw.pop(TURN_TIME_LIMIT_KEY, None)
            else:
                raw[TURN_TIME_LIMIT_KEY] = max(0, int(value))
            self.save(raw, conn=conn)
        return self.get_turn_time_limit_s()

    def get_budget(self) -> Optional[float]:
        """Return the monthly AI-spend budget in USD, or ``None`` if not set."""
        val = self._read_raw().get(AI_SPEND_BUDGET_KEY)
        if val is None:
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    def set_budget(self, value: Optional[float]) -> Optional[float]:
        """Persist the monthly AI-spend budget.  Pass ``None`` or 0 to clear it.
        Returns the stored value (``None`` when cleared)."""
        with self._use_conn(None) as conn:
            raw = self._read_raw(conn=conn)
            if value is None or value <= 0:
                raw.pop(AI_SPEND_BUDGET_KEY, None)
            else:
                raw[AI_SPEND_BUDGET_KEY] = float(value)
            self.save(raw, conn=conn)
        return self.get_budget()


def script_skills_enabled() -> bool:
    """The global script-bundling-skills kill switch (S4 §4.3). Default ``False`` —
    when off, NO script runs through the sandbox. The canonical read for the
    ``run_script`` tool's enforcement gate (S3). Human-only: no Lucius tool may set
    this flag."""
    # A public demo's visitors are all the operator: the sandbox is no jail, so it stays off.
    if get_settings().demo_mode:
        return False
    return AppConfigStore().get_flag(SCRIPT_SKILLS_FLAG, default=False)


def code_execution_enabled() -> bool:
    """The ad-hoc code-execution KILL SWITCH (RFC §4.3). Default ``False`` — when off,
    ``run_code`` hard-refuses before reaching the sandbox, regardless
    of the tool's permission value. Human-only; no Lucius tool writes it."""
    # A public demo's visitors are all the operator: the sandbox is no jail, so it stays off.
    if get_settings().demo_mode:
        return False
    return AppConfigStore().get_flag(CODE_EXEC_ENABLED_FLAG, default=False)


def sql_query_enabled() -> bool:
    """The read-only-SQL KILL SWITCH. Default ``True`` — when off, ``run_sql_query`` and
    ``describe_db_schema`` hard-refuse before reaching the executor. Human-only; no Lucius
    tool writes it (self-escalation), so the only ways to change it are Settings > Developer
    and the auth-gated REST endpoint behind it."""
    return AppConfigStore().get_flag(SQL_QUERY_FLAG, default=True)


def set_sql_query_enabled(value: bool) -> bool:
    """Flip the kill switch. Returns the stored value."""
    return AppConfigStore().set_flag(SQL_QUERY_FLAG, bool(value))


def backup_enabled() -> bool:
    """Whether the daily full-data backup job should run (services/backup.py). Default
    ``True`` — opt-out, not opt-in, since the failure mode being guarded against is a
    silent data incident with no rollback trail at all."""
    return AppConfigStore().get_flag(BACKUP_ENABLED_FLAG, default=True)


def notify_unattended_write_enabled() -> bool:
    """Whether a routine unattended agent write (inside its envelope) surfaces a Signals row
    + outbound notify() ping. Default ``True``. The canonical read for
    ``ai._confirm_gate.surface_unattended_write``. REST-only: no Lucius tool may set this
    flag (it must not be able to silence the mechanism that reports on its own writes)."""
    return AppConfigStore().get_flag(NOTIFY_UNATTENDED_WRITE_FLAG, default=True)


def turn_time_limit_s() -> int:
    """The turn time limit in force, in seconds; ``0`` means none. The stored setting wins,
    else the env default. Never raises: a turn must not fail because this read did."""
    try:
        stored = AppConfigStore().get_turn_time_limit_s()
    except Exception:  # noqa: BLE001 — fall back to the default, never fail the turn
        stored = None
    return get_settings().turn_timeout_s if stored is None else stored
