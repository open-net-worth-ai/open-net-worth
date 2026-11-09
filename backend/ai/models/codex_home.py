"""An isolated ``CODEX_HOME`` for every Codex child process we spawn.

Clearing ``CODEX_HOME`` from the child environment is NOT isolation — the CLI simply falls
back to the operator's real ``~/.codex``, which is where their plugins, skills and
marketplaces live. Measured on ``codex-cli 0.146.0`` with the exact flags the ``exec``
transport passes, the prompt ``"What is 17*23?"`` cost **53,983 input tokens** and made the
model run two ``/bin/zsh -lc "sed -n '1,240p' ~/.codex/plugins/cache/…"`` commands to read
skill files before answering. The same prompt against an isolated home costs ~14k, and ~10k
once ``baseInstructions`` also replaces Codex's built-in agent preamble.

``-c mcp_servers={}`` and ``-c project_doc_max_bytes=0`` do not cover plugins or skills, and
``codex app-server`` has no ``--ignore-user-config`` flag at all. Pointing ``CODEX_HOME`` at a
directory we own is the only lever that covers every config source — and it is what both
external harnesses that wrap Codex do (OpenClaw sets a per-agent home BY DEFAULT and treats
sharing the operator's as an explicit opt-in; Hermes overlays only ``CODEX_HOME`` and
``RUST_LOG``). ADR ``2026-08-01-codex-app-server-transport``.

Two things are deliberately NOT isolated:

* ``HOME`` — left as the operator's. Codex resolves ``~/.gitconfig`` and friends through it,
  and Hermes documents the same choice. Our isolation target is Codex's own config tree.
* ``auth.json`` — SYMLINKED to the operator's, not copied. The whole point of this transport
  is spending the user's existing ``codex login``, and a copy would drift the moment the
  refresh token rotates. A symlink keeps one source of truth; if a future Codex replaces it
  via atomic rename, the link degrades to a private copy rather than corrupting the original.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from backend.core.paths import CODEX_HOME_DIR

log = logging.getLogger(__name__)

# Cache, not data: it holds no user facts we could not rebuild, and it is legitimately
# disposable — a deleted home is re-seeded on the next spawn.
_HOME_DIR = CODEX_HOME_DIR


def _operator_home() -> Path:
    """Where the operator's real ``codex login`` state lives."""
    env = (os.environ.get("CODEX_HOME") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def _refresh_private_copy(src: Path, dst: Path) -> None:
    """A copy instead of a link (no symlinks here, or Codex renamed over ours) must still follow
    a new sign-in. Newer wins, so the tokens Codex rotated into the copy are never overwritten."""
    try:
        if src.stat().st_mtime > dst.stat().st_mtime:
            dst.write_bytes(src.read_bytes())
    except OSError:
        log.debug("codex_home: could not refresh auth.json from %s", src, exc_info=True)


def _seed_auth(home: Path) -> None:
    """Link the operator's ``auth.json`` in, so ``codex login`` still authenticates us."""
    src = _operator_home() / "auth.json"
    dst = home / "auth.json"
    if dst.is_symlink():
        return
    if dst.exists():
        _refresh_private_copy(src, dst)
        return
    if not src.exists():
        # Not an error: the user may be running on a stored API key instead of a login.
        log.debug("codex_home: no operator auth.json at %s — relying on a stored API key", src)
        return
    try:
        dst.symlink_to(src)
    except (OSError, NotImplementedError):
        # Windows without developer mode, or a filesystem that refuses links. A copy is a
        # correct fallback; it only costs us a re-login if the refresh token ever rotates.
        try:
            dst.write_bytes(src.read_bytes())
            dst.chmod(0o600)
        except OSError:
            log.warning("codex_home: could not seed auth.json from %s", src, exc_info=True)


# The isolated home's config. NOT empty: the shared ``auth.json`` carries the operator's ChatGPT
# account, and with it the account's connected apps (``feature.apps`` — Gmail, Google Drive,
# Calendar, GitHub…) surface as ``codex_apps`` tools no settings file lists. On 2026-10-03 an
# unattended run in a book with no mailbox searched the operator's real Gmail through them,
# outside every Open Net Worth permission. Lucius's tools come from us; Codex brings none.
#
# ``code_mode_host`` is OFF for the same reason. On gpt-6 models Codex answers tool use through
# CODE MODE — the model writes code in one native ``exec`` tool and looks tools up in an
# ``ALL_TOOLS`` namespace — instead of the ``<tool_call>`` text protocol our manifest teaches
# (``cli_tool_protocol``). Our tools are never registered with Codex, so inside code mode
# ``find_tools`` and every other app tool does not exist: the model reports no tools and calls
# none, with no error (owner's book, gpt-6-luna follow-ups, 2026-10-03; the gpt-5.6 run on the
# same book used the text protocol and worked).
#
# ``shell_tool`` is OFF too: with code mode off, gpt-6 models still reached for Codex's native
# ``exec`` (its shell tool) first and never emitted a ``<tool_call>`` for our tools — the same
# silent "no tools" outcome (2026-10-03 19:02, luna). Lucius has no use for a shell here.
_MANAGED_CONFIG = (
    "# Managed by Open Net Worth — rewritten on every spawn; do not edit.\n"
    "# This is the isolated CODEX_HOME for the app's Codex transport; the operator's\n"
    "# own ~/.codex config, plugins, skills and ChatGPT connected apps must NOT leak into\n"
    "# our model turns. See backend/ai/models/codex_home.py and ADR 2026-08-01.\n"
    "\n"
    "[features]\n"
    "apps = false\n"
    "code_mode_host = false\n"
    "shell_tool = false\n"
)


# The home native-tool turns run in. Identical except ``code_mode_host``: on 0.160 the app-server
# delivers EVERY client-registered (dynamic) tool through code mode, so with the host off the
# model finds our tools "unavailable" and calls nothing (measured, gpt-6-luna and gpt-5.6-sol,
# 2026-10-03). Code mode is safe to enable ONLY because the tools in it are the ones we
# registered — but a code-mode script sees EVERY tool the thread offers, Codex's own included.
# Measured live on 0.160 (gpt-6-luna, `Object.keys(ALL_TOOLS)`): with only `apps`/`shell_tool`
# off the namespace also held `web__run` (which really ran a web search), `image_gen__imagegen`,
# `get_goal`/`create_goal`/`update_goal` and `clock__curr_time`. So every built-in is switched
# off here and again in the thread config (:data:`NATIVE_THREAD_CONFIG`); what remains is our
# tools plus a read-only clock that no flag removes. The home is separate (hence a separate
# child, see ``AppServerClient``) because the text-protocol fallback needs the host OFF, or gpt-6
# returns to answering through code mode.
NATIVE_BUILTIN_FEATURES = (
    "apps",
    "shell_tool",
    "unified_exec",
    "goals",
    "image_generation",
    "view_image",
    "multi_agent",
    "browser_use",
    "computer_use",
    "in_app_browser",
    "memories",
    "plugins",
    "hooks",
    "skill_search",
    "tool_suggest",
    "web_search_cached",
    "web_search_request",
)
NATIVE_THREAD_CONFIG = {
    "web_search": "disabled",
    "features": {name: False for name in NATIVE_BUILTIN_FEATURES},
}
_NATIVE_MANAGED_CONFIG = (
    _MANAGED_CONFIG.split("[features]")[0]
    + 'web_search = "disabled"\n\n[features]\ncode_mode_host = true\n'
    + "".join(f"{name} = false\n" for name in NATIVE_BUILTIN_FEATURES)
)
assert "code_mode_host = true" in _NATIVE_MANAGED_CONFIG
_NATIVE_HOME_NAME = "native-tools"
_NATIVE_WORKSPACE_NAME = "native-workspace"


def native_workspace() -> str:
    """The working directory of a native-tools thread: empty, and NOT the home — that holds the
    ``auth.json`` symlink, which nothing the model runs may be able to read."""
    workspace = _HOME_DIR / _NATIVE_WORKSPACE_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace)


def has_login(native_tools: bool = False) -> bool:
    """True when the home turns run in holds a login — seeding it first, so a ``codex login``
    made since the last spawn counts. A link to a file that is gone is no login."""
    return (Path(isolated_home(native_tools)) / "auth.json").exists()


def isolated_home(native_tools: bool = False) -> str:
    """Path to our own ``CODEX_HOME``, seeded on first use. Returns a string for ``env``.

    ``native_tools`` selects the sibling home whose config lets the app-server deliver our
    registered tools (see ``_NATIVE_MANAGED_CONFIG``)."""
    home = _HOME_DIR / _NATIVE_HOME_NAME if native_tools else _HOME_DIR
    managed = _NATIVE_MANAGED_CONFIG if native_tools else _MANAGED_CONFIG
    home.mkdir(parents=True, exist_ok=True)
    # Never a missing file (that makes Codex look for the operator's) and never a stale one:
    # rewritten whenever it differs, so a home seeded by an older release gets the apps switch.
    config = home / "config.toml"
    try:
        current = config.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != managed:
        config.write_text(managed, encoding="utf-8")
    _seed_auth(home)
    return str(home)
