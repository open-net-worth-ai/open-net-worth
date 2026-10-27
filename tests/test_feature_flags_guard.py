"""Only the signed-in owner flips a feature flag; Lucius never can.

The one writer is the auth-gated ``PUT /api/feature-flags/{name}``. A static scan of ALL of
``backend/`` (tools, MCP, connectors, and any service a tool could reach) proves nothing else
can reach a writer: outside the four files that implement flags, the only flag imports allowed
are the READS (``from backend.services.feature_flags import enabled | resolve``) and
``main.py`` mounting the router. Attribute access to a ``feature_flags`` module, the store's
flag methods, relative imports and ``importlib`` strings are all caught — as is the raw
``"feature_flags"`` key, which ``AppConfigStore.set_flag`` would otherwise overwrite.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
IMPLEMENTATION = {
    "core/feature_flags.py",
    "services/feature_flags.py",
    "api/feature_flags.py",
    "db/app_config_store.py",
}
ALLOWED_FROM = {
    "backend.services.feature_flags": {"enabled", "resolve"},
    "backend.api.feature_flags": {"router"},
}
STORE_ATTRS = {"set_feature_flag", "get_feature_flags", "FEATURE_FLAGS_KEY", "_test_overrides"}


def _absolute(module: str | None, level: int, package: str) -> str:
    if not level:
        return module or ""
    base = package.split(".")[: len(package.split(".")) - level + 1]
    return ".".join(base + ([module] if module else []))


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _violations(source: str, package: str = "backend.ai.tools") -> list[str]:
    tree = ast.parse(source)
    docs = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if "feature_flags" in a.name]
        elif isinstance(node, ast.ImportFrom):
            mod = _absolute(node.module, node.level, package)
            names = {a.name for a in node.names}
            if mod.endswith("feature_flags"):
                extra = names - ALLOWED_FROM.get(mod, set())
                if extra:
                    found.append(f"from {mod} import {sorted(extra)}")
            elif "feature_flags" in names or names & STORE_ATTRS:
                found.append(f"from {mod} import {sorted(names)}")
        elif isinstance(node, ast.Attribute):
            if node.attr == "feature_flags" or node.attr in STORE_ATTRS:
                found.append(f".{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "feature_flags" in node.value and id(node) not in docs:
                found.append(f"string {node.value!r}")
    return found


def test_the_scan_catches_every_known_evasion():
    for bad in (
        "from backend.services.feature_flags import set_flag",
        "from backend.services.feature_flags import reset_flag as r",
        "from backend.services import feature_flags",
        "import backend.services.feature_flags",
        "from backend import services\nservices.feature_flags.set_flag('x', True)",
        "import backend.services as s\ns.feature_flags.reset_flag('x')",
        "import importlib\nimportlib.import_module('backend.services.feature_flags')",
        "from ...services.feature_flags import set_flag",
        "from backend.core.feature_flags import override",
        "from backend.core import feature_flags",
        "AppConfigStore().set_feature_flag('x', True)",
        "AppConfigStore().set_flag('feature_flags', False)",
    ):
        assert _violations(bad), bad
    assert not _violations("from backend.services.feature_flags import enabled, resolve")
    assert not _violations("from backend.api.feature_flags import router as r")
    assert not _violations('"""Mentions core.feature_flags in a docstring."""')


def test_nothing_outside_the_implementation_can_write_a_flag():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in IMPLEMENTATION:
            continue
        package = ".".join(("backend", *path.relative_to(ROOT).parent.parts))
        for hit in _violations(path.read_text(encoding="utf-8"), package):
            offenders.append(f"{rel}: {hit}")
    assert not offenders, offenders
