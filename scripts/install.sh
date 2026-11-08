#!/bin/sh
# Open Net Worth — install (or update) on macOS / Linux with nothing preinstalled.
#
#   curl -fsSL https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/install-server.sh | sh
#   curl -fsSL ...install-server.sh | sh -s -- --autostart   # also start at login/boot, restart on crash
#   curl -fsSL ...install-server.sh | sh -s -- --auto-upgrade   # the same, and stay on the newest release
#
# This is the SERVER installer (a release asset named install-server.sh). The desktop app has its own,
# https://opennetworth.ai/install.sh.
#
# What it does, and nothing else:
#   1. installs uv (https://docs.astral.sh/uv/) into ~/.local/bin if it is missing
#   2. `uv tool install open-net-worth` on uv's OWN Python 3.12 (--managed-python), never a
#      system/Homebrew one — a `brew upgrade` deletes the old interpreter and would leave the
#      install dangling. No system Python or Node.js is needed; everything lands in uv's tool dir
#   3. with --autostart: `onw autostart install`; with --auto-upgrade: `onw start --autostart --auto-upgrade`
# Re-running it upgrades to the newest release. Your data lives in ~/.open-net-worth/, outside
# the install, so upgrades and reinstalls never touch it. Windows: use Docker (docs, Installation).
set -eu

say() { printf '==> %s\n' "$1"; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

INSTALL_DOCS="https://docs.opennetworth.ai/getting-started/installation/"
DOCKER_HINT="use Docker instead: $INSTALL_DOCS"
case "$(uname -s 2>/dev/null || echo unknown)" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*) die "native Windows is not supported (the backend needs POSIX file locking) — $DOCKER_HINT" ;;
  *) die "unsupported platform '$(uname -s)' — $DOCKER_HINT" ;;
esac

AUTOSTART=0
AUTO_UPGRADE=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    --auto-upgrade) AUTOSTART=1; AUTO_UPGRADE=1 ;;
    *) die "unknown option: $arg (the options are --autostart and --auto-upgrade)" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl is required to download uv"

# Read the caller's PATH BEFORE extending it: uv's installer and `uv tool update-shell` both skip
# the shell profile when ~/.local/bin is already on PATH, so prepending it first left a clean Mac
# with no `onw` in any terminal.
LOCAL_BIN="$HOME/.local/bin"
USER_PATH="$PATH"
case ":$USER_PATH:" in
  *":$LOCAL_BIN:"*) ON_USER_PATH=1 ;;
  *) ON_USER_PATH=0 ;;
esac

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$LOCAL_BIN/uv" ]; then
  say "Installing uv (the Python app installer) into ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --quiet
fi
export PATH="$LOCAL_BIN:$PATH"
UV="$(command -v uv 2>/dev/null || true)"
[ -n "$UV" ] || die "uv did not end up in ~/.local/bin — install it by hand (https://docs.astral.sh/uv/) and re-run"
say "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# The package may not be on PyPI yet (publishing is switched on release by release): say so and
# point at what works today instead of letting uv print a bare "not found in the package registry".
if [ "$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/open-net-worth/json)" = 404 ]; then
  die "open-net-worth is not published on PyPI yet. The desktop app (https://opennetworth.ai) and the Docker image work today: $INSTALL_DOCS"
fi

say "Installing open-net-worth (the first run downloads a few hundred MB of dependencies) ..."
uv tool install --managed-python --python 3.12 --upgrade open-net-worth

ONW="$(command -v onw 2>/dev/null || true)"
[ -n "$ONW" ] || ONW="$LOCAL_BIN/onw"
[ -x "$ONW" ] || die "installed, but 'onw' was not found — run 'uv tool update-shell', open a new terminal and re-run"
say "Installed $("$ONW" version)"

if [ "$ON_USER_PATH" = 0 ]; then
  # Under the caller's PATH, or update-shell sees ~/.local/bin already there and writes nothing.
  PATH="$USER_PATH" "$UV" tool update-shell >/dev/null 2>&1 \
    || say "Could not add ~/.local/bin to your shell profile — run: uv tool update-shell"
fi

if [ "$AUTO_UPGRADE" = 1 ]; then
  "$ONW" start --autostart --auto-upgrade
elif [ "$AUTOSTART" = 1 ]; then
  "$ONW" autostart install
fi

# What to type next. A release older than `onw help` lacks some of these commands, so they're only
# listed when the installed copy has them.
VERSION="$("$ONW" version 2>/dev/null | awk '{print $NF}')"
if "$ONW" help >/dev/null 2>&1; then CURRENT=1; else CURRENT=0; fi

echo ""
say "Done. Open Net Worth $VERSION is installed."
echo ""
if [ "$ON_USER_PATH" = 0 ]; then
  # A pipe into sh can't change the PATH of the terminal that ran it. Reloading that shell rereads
  # its profile, whichever shell it is, and keeps the window and folder.
  echo "    This terminal opened before onw was on your PATH. Reload it:"
  # shellcheck disable=SC2016  # printed for the user to run, not expanded here
  echo '        exec "$SHELL" -l'
  echo "    (or open a new terminal)"
  echo ""
fi
if [ "$AUTOSTART" = 1 ]; then
  echo "    It's running now, and starts by itself at every login:"
  echo "        http://127.0.0.1:8080"
  if [ "$AUTO_UPGRADE" = 1 ]; then
    echo "    It also keeps itself up to date."
  else
    echo "    To keep it up to date too:"
    echo "        onw start --autostart --auto-upgrade"
  fi
else
  echo "    Start it now:"
  echo "        onw start --open"
  echo "    Always on, and kept up to date:"
  echo "        onw start --autostart --auto-upgrade"
  if [ "$CURRENT" = 1 ]; then
    echo "    Without this terminal, until you stop it or restart:"
    echo "        onw start --background"
  fi
fi
echo ""
if [ "$CURRENT" = 1 ]; then
  echo "    Stop it:        onw stop"
fi
echo "    Is it running?  onw status"
echo "    Update now:     onw upgrade"
if [ "$CURRENT" = 1 ]; then
  echo "    Every command:  onw help"
else
  echo "    Every command:  onw --help"
fi
echo ""
echo "    Your data: ~/.open-net-worth/ (updates never touch it)"
