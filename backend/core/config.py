"""Typed application settings, resolved from environment variables.

This is the single source of truth for environment-configurable knobs. It lives
in ``core/`` and depends on nothing internal, so any layer may read it without
inverting the dependency rule (api/ai → services → db → core).

Defaults are chosen so a FRESH CLONE runs with ZERO configuration (2026-08-23 —
``docs/adr/2026-08-23-zero-config-default-data-dir.md``):

- ``data_dir``     → ``~/.open-net-worth/data`` (outside the git checkout).
- ``cache_dir``    → ``~/.open-net-worth/cache``.
- ``log_dir``      → ``~/.open-net-worth/logs``.
- ``cors_origins`` → the two dev origins that were previously hardcoded.
- ``host``/``port``→ ``127.0.0.1`` / ``8080`` (the documented dev defaults).

Before 2026-08-23 these defaulted to ``backend/{data,cache,logs}`` — INSIDE the
repo checkout, so a fresh clone's financial database sat where ``git clean`` /
``git pull`` could destroy it, and ``backend/data/`` was simultaneously the
live data dir AND git-tracked (bundled reference data — still true; see
``SHIPPED_EXTENSIONS_DIR`` in ``core/paths.py``,
fixed to ``backend/data/...`` independent of ``DATA_DIR`` — so this move
needed no change on that side). The new default is the SAME directory this module
already called "the user's production instance directory" in
``_REAL_DATA_DIRS`` below — the unconfigured path and the documented real path
are now the same path.

Override any of them via ``DATA_DIR``, ``CACHE_DIR``, ``LOGS_DIR``,
``CORS_ORIGINS``, ``HOST``, ``PORT`` in the environment (or ``.env``) — an env
var ALWAYS wins over the default, unchanged by the move above.

Legacy-install guard (DATA_DIR only)
-------------------------------------
An install that predates 2026-08-23 and never set ``DATA_DIR`` explicitly has
its real data sitting at the OLD default, ``backend/data/`` — not only
``onw.db``: also the Tier-4 blob pool (``files/`` — every receipt image,
statement PDF, parsed sidecar) and, for an install that predates the storage
engine entirely, a flat ``*.json`` file per store with no ``onw.db`` at all yet.
Silently switching that install's default to the new location on its next boot
would make the book (or its attachments) look empty — the data isn't gone, but
nothing would tell the operator that. ``get_settings()`` refuses to resolve
(prints a fix-it message to stderr and exits 1) when, with ``DATA_DIR`` NOT set
in the environment (an explicit override is the operator's own choice and is
never second-guessed), ANY of these three signals is both present at the OLD
location and not yet present at the NEW one:

  * ``onw.db`` exists and is non-empty — gated ALSO on ``ONW_DATABASE_URL`` NOT
    being set (a non-default DB target makes ``DATA_DIR``'s ``onw.db``
    irrelevant to where the structured rows live);
  * ``files/`` exists and has any content — NOT gated on ``ONW_DATABASE_URL``:
    a blob lives on disk regardless of which database engine holds the row
    that points at it, so a Postgres install still orphans its receipts/
    statements/attachments if this signal were suppressed too;
  * any top-level ``*.json`` file exists directly under the OLD location — NOT
    gated on ``ONW_DATABASE_URL`` either, same reasoning.

See the legacy-directory fallback in ``_resolve_instance_root`` and the ADR for why this is
a one-time refusal (with an exact fix-it command) rather than an automatic
move: an unattended rename of a live financial database as a side effect of
resolving settings is exactly the kind of silent, unreviewable data operation
this codebase's "be careful with live data" convention exists to avoid. Scoped
to ``DATA_DIR`` only — ``CACHE_DIR``/``LOGS_DIR`` hold regenerable caches and
rotating logs, never the thing an operator would call "my data".

QA data-safety guard
--------------------
When ``ONW_QA_MODE=1`` is set the app refuses to start if ``DATA_DIR`` resolves
to either of the two "real" data dirs:

  * ``~/.open-net-worth/data``   — the user's live production instance
  * ``backend/data``             — the in-repo seed/example data (tracked; writes
                                    here corrupt the repo working tree)

It ALSO refuses to start if ``CACHE_DIR`` or ``LOGS_DIR`` resolve anywhere inside
the live ``~/.open-net-worth/`` tree. ``.env`` points all three of DATA_DIR /
CACHE_DIR / LOGS_DIR at that production tree, and ``load_dotenv`` fills in any the
caller left unset — so a QA boot that isolates DATA_DIR but forgets CACHE_DIR /
LOGS_DIR would silently write QA caches and logs into the live instance. Guarding
all three makes that fail-closed: a QA env MUST point every runtime dir at an
isolated location.

It ALSO refuses to start if ``ONW_DATABASE_URL`` is a sqlite URL whose path
resolves inside that same live tree — the three dir guards above cannot catch
this because the database location can be overridden independently of
DATA_DIR. A non-sqlite URL (e.g. a deliberately provisioned QA Postgres
server) is left alone; that is a legitimate, isolated target the dir-based
check cannot evaluate by path containment.

The boot script (``qa/browser-use/boot_app.sh``) always exports ``ONW_QA_MODE=1``
and sets all three dirs to isolated temp paths; the pytest conftest redirects all
three before any import, so normal test runs are unaffected. A production
``uvicorn`` boot that never sets ``ONW_QA_MODE`` is also unaffected.

Call ``assert_qa_data_dir_safe`` / ``assert_qa_cache_dir_safe`` /
``assert_qa_log_dir_safe`` / ``assert_qa_database_url_safe`` early in any
process that sets this flag (``core/paths.py`` does so at import time).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from backend.core.errors import ConfigurationError

# Load .env from the project root regardless of where the process is launched.
# main.py also calls load_dotenv(), but settings may be read before main runs
# (e.g. tests, scripts, or any module that imports backend.paths first), so we
# load here too — load_dotenv is idempotent and never overrides already-set vars.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if os.environ.get("ONW_DESKTOP_MODE") != "1":
    load_dotenv(_PROJECT_ROOT / ".env")

# The repository's backend/ directory — still the fixed, git-tracked home of the
# seed/example dataset and shipped extension packs
# (core/paths.py's EXAMPLES_DIR / SHIPPED_EXTENSIONS_DIR
# both resolve from this constant, never from DATA_DIR) — no longer the default
# home of DATA_DIR/CACHE_DIR/LOGS_DIR themselves (2026-08-23).
_BACKEND = Path(__file__).resolve().parent.parent

# The instance root a fresh install lands in. NOT inside the repo any more (2026-08-23):
# `backend/data` was both the tracked seed/example data AND the live default, so a self-hoster
# who cloned and ran without a .env kept their real financial database inside a git working
# tree — one `git clean` or one re-clone from losing it. That double duty is also why
# .gitignore carried ~58 hand-maintained `backend/data/...` lines.
#
# `backend/data` now has exactly one job: the bundled reference data under `extensions/`
# The demo dataset that used to live beside them under `examples/`
# is GENERATED now (backend/scripts/demo/), not checked in — see ADR
# 2026-08-23-the-demo-book-is-generated-not-a-fixture.
_INSTANCE_ROOT = Path.home() / ".open-net-worth"


_DEFAULT_DATA_DIR = _INSTANCE_ROOT / "data"
_DEFAULT_CACHE_DIR = _INSTANCE_ROOT / "cache"
_DEFAULT_LOG_DIR = _INSTANCE_ROOT / "logs"
_DEFAULT_BACKUPS_DIR = _INSTANCE_ROOT / "backups"


# Where a chosen data location is recorded. The app writes this itself when someone picks a
# folder in first run or moves the instance later — it is never hand-edited, and it exists so
# the choice survives a restart WITHOUT anyone having to keep a .env in sync. Kept outside the
# data directory on purpose: it is the thing that says where that directory IS.
POINTER_FILE = Path.home() / ".config" / "open-net-worth" / "location.json"


def read_pointer() -> Optional[Path]:
    """The INSTANCE ROOT recorded by a previous pick, or None.

    READ-ONLY by design (2026-08-23). Nothing in the app writes this file: it is machine-WIDE,
    shared by every instance on the machine, and a disposable QA instance writing it silently
    repointed the operator's real install at an empty folder. The folder is now the default or
    ONW_ROOT, and an existing pointer is still honoured so installs that recorded one keep
    working.

    A root, not a data directory: an instance is four sibling folders — data/, cache/,
    logs/, backups/ — and recording only the data half would scatter a moved instance,
    leaving cache and logs behind at the old default. Never raises; a damaged pointer falls
    through to the default rather than taking the app down.
    """
    try:
        raw = json.loads(POINTER_FILE.read_text(encoding="utf-8"))
        # `data_dir` is the pre-2026-08-23 key, written before the pointer recorded a root.
        # Its value was always "<root>/data", so its parent is the root.
        chosen = str(raw.get("instance_root") or "").strip()
        if chosen:
            return Path(chosen).expanduser()
        legacy = str(raw.get("data_dir") or "").strip()
        return Path(legacy).expanduser().parent if legacy else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def instance_root() -> Path:
    """The WORKING FOLDER this instance runs from — the one folder holding data/, cache/, logs/.

    A recorded pick names it outright. Otherwise it is INFERRED from the data directory, which
    is only honest when that directory is the conventional ``data/`` child: an explicit
    ``DATA_DIR=/somewhere/else`` has no working folder above it (its siblings may be anywhere),
    so the data directory itself is the truest answer available.
    """
    root = _resolve_root()
    if root:
        return root
    data = get_settings().data_dir
    return data.parent if data.name == "data" else data


def _resolve_root() -> Optional[Path]:
    """The configured WORKING FOLDER, or None to fall through to the legacy resolution.

    ONW_ROOT is the ONE variable worth setting: an instance is one folder holding data/,
    cache/, logs/ and backups/, so naming the root names all four and a moved instance moves
    whole. It beats a recorded pick because an operator who names a root on the command line
    (a container, the QA harness) must not be dragged onto a previously-recorded instance —
    that is the difference between an isolated test boot and one writing to real money data.
    """
    explicit = os.environ.get("ONW_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return read_pointer()


def _resolve_sibling(env_var: str, name: str, fallback: Path) -> Path:
    """One folder inside the working folder.

    Per-folder overrides (DATA_DIR/CACHE_DIR/LOGS_DIR/BACKUPS_DIR) predate the folder picker
    and are LEGACY: they still work, and still beat a recorded pick so an explicit boot stays
    isolated, but ONW_ROOT beats them because naming a root and then contradicting one of its
    children is never what someone means.
    """
    root = os.environ.get("ONW_ROOT", "").strip()
    if root:
        return Path(root).expanduser() / name
    explicit = os.environ.get(env_var, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    # A legacy boot names DATA_DIR but not necessarily every sibling (the test harness and the
    # QA targets both predate BACKUPS_DIR being one). Infer the root from it rather than
    # falling through to the GLOBAL default — that would point an ISOLATED boot's backups at
    # the real instance's folder, which is the exact accident this whole seam exists to
    # prevent.
    data = os.environ.get("DATA_DIR", "").strip()
    if data:
        d = Path(data).expanduser()
        return (d.parent if d.name == "data" else d) / name
    pointed = read_pointer()
    return pointed / name if pointed else fallback


def _resolve_data_dir() -> Path:
    """Resolution order: ONW_ROOT env > DATA_DIR env (legacy) > recorded pointer > platform
    default, with a legacy in-repo instance still winning over an EMPTY default.

    Moving the default silently re-points an existing unconfigured install at an empty
    directory, which looks exactly like data loss to the person it happens to. So an
    in-repo `onw.db` with no instance root beside it keeps being used, and says so loudly.
    """
    root = os.environ.get("ONW_ROOT", "").strip()
    if root:
        return Path(root).expanduser() / "data"
    explicit = os.environ.get("DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    pointed = read_pointer()
    if pointed:
        return pointed / "data"
    return _DEFAULT_DATA_DIR


# ── QA data-safety constants ──────────────────────────────────────────────────
# These two paths must never be DATA_DIR during a QA/test run.
#   1. The user's production instance directory — also the zero-config DEFAULT
#      since 2026-08-23, so this now doubles as "the thing a QA boot that
#      forgets to override DATA_DIR would fall through to" rather than falling
#      through to the harmless in-repo seed dir it used to.
#   2. The repo's seed data (tracked by git; writes corrupt the tree).
_REAL_DATA_DIRS: tuple[Path, ...] = (
    _INSTANCE_ROOT / "data",
    _BACKEND / "data",
)
_DEFAULT_CORS_ORIGINS = ["http://localhost:5174", "http://localhost:8080"]
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_DEFAULT_SESSION_TTL = 2592000  # 30 days (single-user convenience)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


_GATEWAY_TOKEN_MIN_LEN = 32


def _env_gateway_token() -> str | None:
    """``ONW_GATEWAY_TOKEN`` — the shared secret a trusted authenticating reverse proxy sends.

    Unset/blank means gateway mode is off. A set-but-short value REFUSES to resolve: a weak
    secret on a mode that replaces the login screen is worse than no boot."""
    token = os.environ.get("ONW_GATEWAY_TOKEN", "").strip()
    if not token:
        return None
    if len(token) < _GATEWAY_TOKEN_MIN_LEN:
        raise ConfigurationError(
            f"ONW_GATEWAY_TOKEN must be at least {_GATEWAY_TOKEN_MIN_LEN} characters "
            f"(got {len(token)}); refusing to start."
        )
    return token


def _env_int(name: str, default: int) -> int:
    """An int from the environment; a blank or unparseable value keeps the default.

    Never raises: a typo in a demo host's env must not stop the app from booting — it falls
    back to the shipped limit, which is the safe direction for a quota.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def _env_cors_origins() -> list[str]:
    """Comma-separated origins; falls back to the historical dev pair.

    A literal ``*`` (case-insensitive) means "allow all origins".
    """
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if not raw:
        return list(_DEFAULT_CORS_ORIGINS)
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass(frozen=True)
class Settings:
    """Resolved settings snapshot. Construct once via ``get_settings()``."""

    data_dir: Path = field(default_factory=_resolve_data_dir)
    cache_dir: Path = field(
        default_factory=lambda: _resolve_sibling("CACHE_DIR", "cache", _DEFAULT_CACHE_DIR)
    )
    log_dir: Path = field(
        default_factory=lambda: _resolve_sibling("LOGS_DIR", "logs", _DEFAULT_LOG_DIR)
    )
    # A SIBLING of data/, not a child of it. Backups living inside the folder they back up is
    # one lost directory away from losing both — the failure this app has already had once.
    backups_dir: Path = field(
        default_factory=lambda: _resolve_sibling("BACKUPS_DIR", "backups", _DEFAULT_BACKUPS_DIR)
    )
    cors_origins: list[str] = field(default_factory=_env_cors_origins)
    # Extra hostnames (no port) a loopback-trusted request may present as ``Host`` — a
    # reverse proxy on this machine forwards the public hostname, which DNS rebinding also
    # looks like unless it is named here. The hostnames of ``cors_origins`` count too.
    allowed_hosts: list[str] = field(
        default_factory=lambda: [
            h.strip().lower()
            for h in os.environ.get("ONW_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
    )
    host: str = field(default_factory=lambda: os.environ.get("HOST", "").strip() or _DEFAULT_HOST)
    port: int = field(
        default_factory=lambda: int(os.environ.get("PORT", "").strip() or _DEFAULT_PORT)
    )
    desktop_mode: bool = field(default_factory=lambda: os.environ.get("ONW_DESKTOP_MODE") == "1")

    # ── Instance authentication (RFC P3-AUTH §5) ────────────────────────────────
    # Auth is ON by default; AUTH_DISABLED=true is the dev escape hatch. Loopback is
    # trusted by default (AUTH_TRUST_LOOPBACK=true) so the local operator never has to
    # log in; set it false to gate localhost like any remote client. Password seeds:
    # AUTH_PASSWORD (plaintext, hashed into auth.json on first boot) or the preferred
    # AUTH_PASSWORD_HASH (pbkdf2$..., used as-is); the store materialises/persists these.
    auth_disabled: bool = field(default_factory=lambda: _env_bool("AUTH_DISABLED", False))
    auth_trust_loopback: bool = field(
        default_factory=lambda: _env_bool("AUTH_TRUST_LOOPBACK", True)
    )
    auth_password: str = field(default_factory=lambda: os.environ.get("AUTH_PASSWORD", "").strip())
    auth_password_hash: str = field(
        default_factory=lambda: os.environ.get("AUTH_PASSWORD_HASH", "").strip()
    )
    auth_api_token: str = field(
        default_factory=lambda: os.environ.get("AUTH_API_TOKEN", "").strip()
    )
    auth_session_key: str = field(
        default_factory=lambda: os.environ.get("AUTH_SESSION_KEY", "").strip()
    )
    auth_session_ttl: int = field(
        default_factory=lambda: int(
            os.environ.get("AUTH_SESSION_TTL", "").strip() or _DEFAULT_SESSION_TTL
        )
    )

    # ── Demo mode (DEMO_MODE) ───────────────────────────────────────────────────
    # Marks this instance as a PUBLIC demo. Its one job today is to arm the per-visitor
    # AI quotas below: a demo whose provider actually works is an open, unmetered door to
    # somebody's account, and the web chat surface has no rate limit of its own (only the
    # login route does). Off by default, so a self-hoster on their laptop is never throttled.
    #
    # (Historically this flag also auto-seeded the example dataset at boot. That seeding is
    # explicit now — `scripts/run_demo.sh` builds the book before the server starts — so the
    # flag had no reader at all until the quotas below gave it one.)
    demo_mode: bool = field(default_factory=lambda: _env_bool("DEMO_MODE", False))
    # Second, separate opt-in: a demo's visitors need no password. It means nothing without
    # `demo_mode` (read ONLY through `demo_open_access_active()`), so DEMO_MODE on its own — which
    # `.env.example` once described as an innocuous "seed the example data" switch — never opens
    # an instance. Env-only on purpose: no stored setting, header or request can turn it on.
    demo_open_access: bool = field(default_factory=lambda: _env_bool("DEMO_OPEN_ACCESS", False))

    # Per-visitor AI quotas, enforced only while `demo_mode` is on. Three limits, because
    # each one alone has an obvious hole:
    #   • a burst window stops one visitor hammering Send, but not a patient one;
    #   • a per-IP daily token budget stops the patient one, but not fifty of them;
    #   • an instance-wide daily ceiling is the only thing that actually bounds what the
    #     demo can cost its owner, which is the number that matters.
    # Tokens (not dollars) are the unit on purpose: a Claude Code CLI demo runs on a
    # subscription, where the recorded dollar figure is a computed list price rather than a
    # bill, so a spend cap would never bite. Tokens are the real consumed quota either way.
    #
    # SIZE THEM AGAINST A REAL TURN, not intuition. One turn here costs roughly 35k tokens
    # (a large system prompt + the tool schemas, mostly cache-creation on the first turn of a
    # session, cheaper after). A 200k/day per-visitor budget therefore meant about FIVE
    # messages — a limit that reads as "the demo is broken", not "the demo is metered".
    # These defaults are ~100 turns per visitor per day, which is a real exploration session.
    demo_turns_per_window: int = field(
        default_factory=lambda: _env_int("DEMO_TURNS_PER_WINDOW", 30)
    )
    demo_turn_window_s: int = field(default_factory=lambda: _env_int("DEMO_TURN_WINDOW_S", 300))
    demo_tokens_per_ip_daily: int = field(
        default_factory=lambda: _env_int("DEMO_TOKENS_PER_IP_DAILY", 3_000_000)
    )
    demo_tokens_total_daily: int = field(
        default_factory=lambda: _env_int("DEMO_TOKENS_TOTAL_DAILY", 30_000_000)
    )
    # Voice-call caps, enforced only while `demo_mode` is on (`services/demo_calls`). A call
    # spends the demo's LiveKit inference by the MINUTE whether or not Lucius is asked anything
    # (one person's testing used ~100 minutes of a project's credit), so calls are metered in
    # minutes, per visitor and instance-wide, beside the token budgets above.
    demo_call_max_s: int = field(default_factory=lambda: _env_int("DEMO_CALL_MAX_S", 600))
    demo_calls_per_visitor_daily: int = field(
        default_factory=lambda: _env_int("DEMO_CALLS_PER_VISITOR_DAILY", 3)
    )
    demo_call_minutes_per_visitor_daily: int = field(
        default_factory=lambda: _env_int("DEMO_CALL_MINUTES_PER_VISITOR_DAILY", 15)
    )
    demo_call_minutes_total_daily: int = field(
        default_factory=lambda: _env_int("DEMO_CALL_MINUTES_TOTAL_DAILY", 120)
    )
    demo_calls_concurrent: int = field(default_factory=lambda: _env_int("DEMO_CALLS_CONCURRENT", 2))
    # How many reverse proxies sit in front of a demo and APPEND the address they saw to
    # `X-Forwarded-For`. The visitor key is the Nth entry from the RIGHT: everything left of it
    # is whatever the client chose to send. 0 (the default) ignores the header and keys on the
    # network peer, so an instance with no proxy cannot be lied to; the Cloudflare-tunnelled
    # launchers set 1 (Cloudflare's edge appends the connecting client address).
    demo_trusted_proxy_hops: int = field(
        default_factory=lambda: max(0, _env_int("DEMO_TRUSTED_PROXY_HOPS", 0))
    )

    # ── QA data-safety guard (ONW_QA_MODE) ───────────────────────────────────────
    # When ONW_QA_MODE=1, the app refuses to start / refuses writes if DATA_DIR
    # resolves to a "real" data dir (the user's live instance or the repo seed).
    # The QA boot harness always sets this flag. Normal production boots never do,
    # so a real-user deployment is completely unaffected.
    onw_qa_mode: bool = field(default_factory=lambda: _env_bool("ONW_QA_MODE", False))

    # ── Gateway mode (ONW_GATEWAY_TOKEN) ─────────────────────────────────────────
    # Set when the app runs behind a trusted authenticating reverse proxy: every request must
    # carry the token (api/gateway.py) and the proxy, not a login screen, is the authority.
    gateway_token: str | None = field(default_factory=_env_gateway_token)

    # ── Terminal (ONW_TERMINAL_ENABLED) ──────────────────────────────────────────
    # A shell in the browser (api/terminal.py) is on in gateway mode and OFF everywhere else,
    # unless a self-hoster who understands what a shell on this machine means turns it on.
    terminal_enabled: bool = field(default_factory=lambda: _env_bool("ONW_TERMINAL_ENABLED", False))

    # ── Turn lifetime (concurrent-conversations RFC Phase 3) ────────────────────
    # Wall-clock bound on one server-owned turn (backend.ai.turns). Not a spend cap — an
    # idle/no-event watchdog was considered and rejected (a legitimately long tool call,
    # e.g. a big statement import, emits nothing for minutes and would be killed by one).
    # 1200s is a guess calibrated to "longer than any turn observed in the cost log", which
    # is why it is an env var rather than a constant.
    turn_timeout_s: int = field(
        default_factory=lambda: int(os.environ.get("ONW_TURN_TIMEOUT_S", "").strip() or 1200)
    )

    @property
    def auth_enabled(self) -> bool:
        """Auth gate active unless explicitly disabled via AUTH_DISABLED=true."""
        return not self.auth_disabled


_settings: Settings | None = None


def demo_open_access_active() -> bool:
    """True when a PUBLIC demo lets every visitor in without a password.

    Requires BOTH ``DEMO_MODE`` and ``DEMO_OPEN_ACCESS`` — the one place the pair is combined, so
    no caller can read just one. Resolved once from the process environment, which is what makes
    it fail closed: nothing a request carries can reach it."""
    settings = get_settings()
    return settings.demo_mode and settings.demo_open_access


def gateway_active() -> bool:
    """True when gateway mode is on: a token is configured, so the proxy is the authority."""
    return get_settings().gateway_token is not None


def get_settings() -> Settings:
    """Return the process-wide settings singleton (resolved on first access)."""
    global _settings
    if _settings is None:
        candidate = Settings()
        _settings = candidate
    return _settings


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _real_instance_root() -> Path:
    """The live instance tree (``~/.open-net-worth``) whose subdirs QA must never touch."""
    return _resolve(_INSTANCE_ROOT)


def _is_inside_real_instance(path: Path) -> bool:
    """True if ``path`` lives inside the operator's live ``~/.open-net-worth`` tree."""
    try:
        _resolve(path).relative_to(_real_instance_root())
        return True
    except ValueError:
        return False


def _live_instance_roots() -> list[Path]:
    """Every folder holding an operator's REAL data on this machine, resolved at call time:
    the default instance root, the root a folder pick recorded (the pointer wins the default
    for an unconfigured process, so it is just as live), and the in-repo seed data."""
    roots = [_INSTANCE_ROOT, _BACKEND / "data"]
    pointed = read_pointer()
    if pointed is not None:
        roots.append(pointed)
    return [_resolve(r) for r in roots]


def is_live_instance_path(path: Path) -> bool:
    """True if ``path`` is, or lives inside, a live instance — real data, never a scratch copy.

    Independent of ``ONW_QA_MODE``: the ``assert_qa_*`` guards refuse to BOOT on these paths
    when the flag is set, but a process that never set the flag (a script whose isolation
    variable was misspelled, so ``DATA_DIR`` fell through to the default) gets no protection
    from them. This predicate is what a write path consults to refuse ONE dangerous
    operation on the live instance whatever the mode — ``db.migrate`` is the first.
    """
    resolved = _resolve(path)
    return any(resolved.is_relative_to(root) for root in _live_instance_roots())


def assert_qa_data_dir_safe(data_dir: Path) -> None:
    """Abort if ONW_QA_MODE=1 and DATA_DIR is a real/protected directory.

    Called at ``core/paths.py`` import time so it fires for BOTH:
      * ``uvicorn backend.main:app`` (covers the boot_app.sh path)
      * ``from fastapi.testclient import TestClient; import backend.main``
        (covers any test that forgets to redirect DATA_DIR via the conftest)

    A normal production boot (no ONW_QA_MODE) is a no-op.
    """
    if not get_settings().onw_qa_mode:
        return

    resolved = _resolve(data_dir)
    for real_dir in _REAL_DATA_DIRS:
        if resolved == _resolve(real_dir):
            _die_real_dir(resolved, real_dir, "DATA_DIR")
    # Also reject any path that *contains* ~/.open-net-worth/ (catches subdirs).
    if _is_inside_real_instance(data_dir):
        _die_real_dir(resolved, _real_instance_root(), "DATA_DIR")


def assert_qa_cache_dir_safe(cache_dir: Path) -> None:
    """Abort if ONW_QA_MODE=1 and CACHE_DIR lives inside the live instance tree.

    A QA boot that isolates DATA_DIR but lets CACHE_DIR fall through to the
    production value (e.g. via ``.env``) would write QA caches into the live
    ``~/.open-net-worth/cache``. This makes that fail-closed. No-op outside QA mode.
    """
    if not get_settings().onw_qa_mode:
        return
    if _is_inside_real_instance(cache_dir):
        _die_real_dir(_resolve(cache_dir), _real_instance_root(), "CACHE_DIR")


def assert_qa_log_dir_safe(log_dir: Path) -> None:
    """Abort if ONW_QA_MODE=1 and LOGS_DIR lives inside the live instance tree.

    Same rationale as ``assert_qa_cache_dir_safe`` — a QA boot must never write
    logs into the operator's live ``~/.open-net-worth/logs``. No-op outside QA mode.
    """
    if not get_settings().onw_qa_mode:
        return
    if _is_inside_real_instance(log_dir):
        _die_real_dir(_resolve(log_dir), _real_instance_root(), "LOGS_DIR")


def assert_qa_database_url_safe(database_url: str | None) -> None:
    """Abort if ONW_QA_MODE=1 and ONW_DATABASE_URL is a sqlite path inside the
    live instance tree.

    The three dir guards above cannot catch this: the database location can be
    overridden independently of DATA_DIR via ONW_DATABASE_URL, so a QA boot
    that isolates DATA_DIR/CACHE_DIR/LOGS_DIR but leaves ONW_DATABASE_URL
    pointing at the live sqlite file would still write QA rows into the
    operator's real database. Plain string parsing only — no ``sqlalchemy``
    import, ``core/`` is a stdlib-only leaf — mirroring the ``sqlite:///``
    convention every URL in this codebase is built with (see
    ``backend/db/engine.py``). A non-sqlite URL (e.g. ``postgresql://...``) is
    left alone: a deliberately provisioned QA Postgres server is legitimate
    and cannot be judged by path containment. No-op outside QA mode, or when
    ONW_DATABASE_URL is unset/empty (the default falls back to
    DATA_DIR/onw.db, already covered by ``assert_qa_data_dir_safe``).
    """
    if not get_settings().onw_qa_mode:
        return
    if not database_url or not database_url.startswith("sqlite"):
        return
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return  # bare `sqlite://` (in-memory) — no filesystem path to guard
    path_str = database_url[len(prefix) :]
    if not path_str or path_str == ":memory:":
        return
    # .expanduser() BEFORE containment: Path.resolve() alone does NOT expand `~` — it
    # resolves relative to the CWD, so an unexpanded `sqlite:///~/.open-net-worth/...`
    # (e.g. typed straight into .env, never passed through a shell) would resolve to
    # something like `<cwd>/~/.open-net-worth/...` and silently sail past containment.
    # Mirrors `_env_path`, which already expands DATA_DIR/CACHE_DIR/LOGS_DIR for the
    # sibling dir-guards above (Plan 1 parked residual).
    path = Path(path_str).expanduser()
    if _is_inside_real_instance(path):
        _die_real_dir(_resolve(path), _real_instance_root(), "ONW_DATABASE_URL")


def _die_real_dir(resolved: Path, matched: Path, label: str) -> None:
    msg = (
        f"\n!!! ONW_QA_MODE=1 but {label} points to a REAL / PROTECTED dir.\n"
        "    Refusing to start — this would touch live owner data.\n"
        f"    {label} (resolved): {resolved}\n"
        f"    Matched protected:  {matched}\n"
        "    Fix: point DATA_DIR / CACHE_DIR / LOGS_DIR at isolated temp dirs —\n"
        "         export DATA_DIR=$(make qa-data) clones live data to /tmp; set\n"
        "         CACHE_DIR / LOGS_DIR to isolated paths too.\n"
    )
    print(msg, file=sys.stderr)
    sys.exit(1)
