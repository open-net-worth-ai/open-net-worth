"""Repo guard: no bare stdlib HTTPS call outside ``backend/core/``.

Every ``urllib.request.urlopen(...)`` / ``build_opener(...)`` that reaches the public
internet must carry an explicit verified TLS context from ``core.pinned_http.https_context``
(or an ``HTTPSHandler`` built from it). A bare call uses Python's default trust store,
which is EMPTY on a python.org macOS build whose ``Install Certificates.command`` was never
run — the palette's market search, Telegram, webhooks and MCP all died silently on one
user's machine that way (2026-09-21). The boot hook ``ensure_default_trust_store`` covers
the process too; this guard is the belt to that brace, so a new call site cannot regress it.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

# Loopback-only callers — plain http://127.0.0.1, never TLS, never the public internet.
ALLOWED_BARE = {
    "backend/cli/serve.py",  # waits on its own http://127.0.0.1/api/health before opening a browser
    "backend/cli/status.py",  # reads its own http://127.0.0.1/api/health for `onw status`
    "backend/scripts/perf_bench.py",  # localhost benchmark harness
}
OWNER = "backend/core/pinned_http.py"  # the one place that BUILDS the context


def _callee(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _bare_calls() -> list[str]:
    offenders: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND.parent).as_posix()
        if rel == OWNER or rel in ALLOWED_BARE or "/_bundled/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            if name not in ("urlopen", "build_opener"):
                continue
            if any(kw.arg == "context" for kw in node.keywords):
                continue
            # build_opener(HTTPSHandler(context=...)) carries it one level down.
            if name == "build_opener" and "context=" in ast.unparse(node):
                continue
            offenders.append(f"{rel}:{node.lineno} {name}(...) without a verified context")
    return offenders


def test_every_outbound_urllib_call_carries_a_verified_tls_context():
    assert _bare_calls() == []
