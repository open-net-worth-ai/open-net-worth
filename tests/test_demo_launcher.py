"""Safety and lifecycle contracts for the public synthetic demo launcher."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_demo.sh"
# The launcher runs the repo's OWN interpreter on purpose (it seeds and boots the app, so it
# needs the project's dependencies) and refuses to start without it. CI installs those deps into
# the runner's system Python instead of a .venv, so on CI the launcher cannot run at all and this
# was a guaranteed red — a missing environment precondition, not a product failure. Declare the
# precondition instead of asserting through it.
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"
MAKEFILE = ROOT / "Makefile"
posix_launcher = pytest.mark.skipif(
    os.name == "nt",
    reason="run_demo.sh is a bash launcher; on a Windows PATH `bash` is the WSL stub",
)


def test_launcher_isolated_from_owner_data_and_credentials() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")

    assert 'FRONTEND_PORT="5175"' in text
    assert 'BACKEND_PORT="8175"' in text
    assert "mktemp -d" in text
    assert "backend.scripts.seed_demo" in text
    assert "clone_data_for_qa" not in text
    assert "qa-data" not in text
    # NOT ONW_QA_MODE: that flag also skips the one-time price backfill and benchmark-
    # instrument seeding (backend/main.py) — needed for a real, functioning public demo,
    # not merely a data-safety switch. Isolation here is DATA_DIR/CACHE_DIR/LOGS_DIR
    # (a fresh `mktemp -d`, asserted above), never the live-instance owner's tree.
    assert 'DEMO_MODE="1"' in text
    assert 'AUTH_DISABLED="false"' in text
    assert 'AUTH_TRUST_LOOPBACK="false"' in text
    # Visitors need no login: both flags on, and the book keeps a password nobody is told —
    # a random one per launch, never the old published string.
    assert 'DEMO_OPEN_ACCESS="1"' in text
    assert 'AUTH_PASSWORD="${DEMO_UNPUBLISHED_PASSWORD}"' in text
    assert "secrets.token_urlsafe" in text
    assert "demo@opennetworth.ai" not in text
    assert 'OPENAI_API_KEY=""' in text
    assert 'ONW_DISABLE_TELEGRAM="1"' in text
    assert "--host 127.0.0.1" in text
    assert "--host 0.0.0.0" in text
    assert "trap cleanup EXIT INT TERM" in text


def test_the_demo_never_auto_reloads() -> None:
    """A hosted demo must not restart or hot-reload under its visitors.

    `--reload` drops every open session the moment any file on the host changes, which reads
    as an outage; the Vite DEV server additionally recompiles modules on a visitor's first
    click and pushes HMR updates into their tab. The demo builds once and serves static
    assets through `vite preview`, whose own config block carries the demo hostname and the
    /api proxy (preview does NOT inherit `server.proxy`).
    """
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "--reload" not in text
    assert "npx vite --host" not in text, "that is the dev server, with HMR"
    assert "npm run build" in text
    assert "npx vite preview --host 0.0.0.0" in text
    assert "VITE_API_URL=" in text, "preview needs its own proxy target"


def test_the_frontend_is_not_rebuilt_on_every_start() -> None:
    """A rebuild renames every chunk, which breaks every tab still open on the old build.

    Vite content-hashes each lazy chunk, so rebuilding turns a visitor's next tab click into
    "Failed to fetch dynamically imported module" for a bundle that usually did not change.
    A demo reset is about the DATA; the bundle only needs rebuilding when it is missing or
    the operator asks.
    """
    text = LAUNCHER.read_text(encoding="utf-8")

    assert 'if [[ ! -f "${REPO_ROOT}/frontend/dist/index.html"' in text
    assert "ONW_DEMO_REBUILD_FRONTEND" in text


def test_provider_key_reaches_the_demo_only_by_explicit_opt_in() -> None:
    """The demo process must never inherit the operator's ambient provider key.

    ``ONW_DEMO_ANTHROPIC_API_KEY`` is a DIFFERENT variable name on purpose: the launcher runs
    the app under ``env -i``, so the only way a key reaches a public instance is the operator
    naming that opt-in variable. A bare ``ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"`` would
    hand a live billed key to the internet the first time someone ran the demo from a shell
    that happened to have one exported.
    """
    text = LAUNCHER.read_text(encoding="utf-8")

    assert 'ANTHROPIC_API_KEY="${ONW_DEMO_ANTHROPIC_API_KEY:-}"' in text
    assert 'ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY' not in text


def test_hosted_root_is_never_deleted_by_cleanup() -> None:
    """Only the temp dir the launcher minted itself is removable.

    ``ONW_DEMO_ROOT`` is an operator-chosen path — a hosted demo's real directory. Cleanup is
    gated on the EPHEMERAL flag as well as the mktemp name pattern, so a Ctrl-C on a hosted
    demo can never ``rm -rf`` the operator's directory.
    """
    text = LAUNCHER.read_text(encoding="utf-8")

    assert 'if [[ -n "${EPHEMERAL}" && -n "${DEMO_ROOT}" && -d "${DEMO_ROOT}" ]]; then' in text
    assert '"${DEMO_TMP_BASE}"/onw-demo.*) rm -rf "${DEMO_ROOT}" ;;' in text


@posix_launcher
@pytest.mark.parametrize(
    "layout, expected",
    [
        (["data/onw.db"], "ALLOW"),  # a real instance root
        (["data/onw.db", "backups", ".instance.lock"], "ALLOW"),  # + what the app adds itself
        ([], "ALLOW"),  # empty: nothing to destroy
        (["Documents", "taxes.txt"], "REFUSE"),  # somebody's actual directory
        (["notes.md"], "REFUSE"),
    ],
)
def test_rebuild_only_touches_something_that_looks_like_an_instance(tmp_path, layout, expected):
    """``--rebuild`` runs ``rm -rf`` on an operator-supplied path.

    The rule is POSITIVE — `data/onw.db` present proves this is an Open Net Worth instance —
    rather than an allowlist of permitted filenames. An allowlist has to enumerate everything
    the app might create, and every omission refuses a legitimate reset: `backups/` and
    `.instance.lock` each broke `make demo-reset` that way before this was inverted.
    """
    root = tmp_path / "root"
    root.mkdir()
    for name in layout:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x") if "." in target.name else target.mkdir(exist_ok=True)
    assert _guard_verdict(str(root)) == expected


@posix_launcher
@pytest.mark.parametrize("path", ["", "/", "$HOME", "$REPO"])
def test_rebuild_refuses_the_obviously_catastrophic_paths(path) -> None:
    resolved = {"$HOME": str(Path.home()), "$REPO": str(ROOT)}.get(path, path)
    assert _guard_verdict(resolved) == "REFUSE"


def _guard_verdict(target: str) -> str:
    """Run the launcher's own guard function, lifted out of the script, against ``target``."""
    body = re.search(r"^assert_rebuildable\(\) \{.*?^\}", LAUNCHER.read_text("utf-8"), re.S | re.M)
    assert body, "the launcher no longer defines assert_rebuildable"
    script = f'REPO_ROOT="{ROOT}"\n{body.group(0)}\nassert_rebuildable "$1"'
    done = subprocess.run(["bash", "-c", script, "bash", target], capture_output=True, text=True)
    return "ALLOW" if done.returncode == 0 else "REFUSE"


def test_make_demo_delegates_to_guarded_launcher() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "demo:" in text
    assert "./scripts/run_demo.sh" in text


@pytest.mark.skipif(
    not VENV_PYTHON.exists(),
    reason=f"{VENV_PYTHON} is absent (run `make install`); run_demo.sh requires the repo venv",
)
def test_smoke_mode_builds_a_fabricated_book_and_cleans_up() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "--smoke"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["profile_name"] == "Alex Morgan"
    assert report["is_demo"] is True
    assert report["seed_source"] == "backend.scripts.seed_demo"
    assert report["accounts"] >= 10
    assert report["transactions"] > 1000
    assert report["data_dir_removed"] is True


@posix_launcher
def test_launcher_rejects_an_unknown_argument() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "--nope"], cwd=ROOT, text=True, capture_output=True, timeout=30
    )
    assert result.returncode == 2
    assert "unknown argument" in result.stderr
